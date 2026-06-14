"""Admin-only offline control, proof, model import, and legal metadata routes."""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import html
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core.database import ModelEndpoint, SessionLocal
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.constants import DATA_DIR
from src.endpoint_resolver import build_chat_url, normalize_base
from src.local_audit import append_audit, read_audit
from src.offline_policy import evaluate_offline_policy, is_local_model_url
from src.settings import load_settings, offline_mode, save_settings


class RegisterLocalModelRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    base_url: str = Field(default="http://ollama:11434/v1", max_length=300)
    model: str = Field(min_length=1, max_length=240)
    set_default: bool = True
    shared: bool = True


class ModelBenchmarkRequest(BaseModel):
    base_url: str = Field(default="http://ollama:11434/v1", max_length=300)
    model: str = Field(min_length=1, max_length=240)
    prompt: str = Field(default="Reply with exactly: ready", max_length=500)
    max_tokens: int = Field(default=32, ge=1, le=256)


class AuditEventRequest(BaseModel):
    action: str = Field(min_length=1, max_length=80)
    detail: dict[str, Any] = Field(default_factory=dict)


MODEL_RECOMMENDATIONS: list[dict[str, Any]] = [
    {
        "id": "cpu",
        "label": "CPU-only safe starter",
        "quality_profile": "CPU Safe",
        "model": "llama3.2:3b",
        "size": "2.0GB",
        "min_gpu_gb": 0,
        "max_gpu_gb": 4,
        "hardware": "0-3GB GPU VRAM or CPU-only",
        "best_for": "Reliable offline startup on CPU-only or very small GPU machines.",
        "source_url": "https://ollama.com/library/llama3.2",
    },
    {
        "id": "gpu-4",
        "label": "Low VRAM local chat",
        "quality_profile": "Low VRAM",
        "model": "qwen3:4b",
        "size": "2.5GB",
        "min_gpu_gb": 4,
        "max_gpu_gb": 8,
        "hardware": "4-7GB GPU VRAM",
        "best_for": "Better local reasoning than the CPU starter while keeping VRAM headroom.",
        "source_url": "https://ollama.com/library/qwen3",
    },
    {
        "id": "gpu-8",
        "label": "Balanced 8GB GPU",
        "quality_profile": "Balanced",
        "model": "qwen3:8b",
        "size": "5.2GB",
        "min_gpu_gb": 8,
        "max_gpu_gb": 12,
        "hardware": "8-11GB GPU VRAM",
        "best_for": "General chat, summaries, and modest code tasks on consumer GPUs.",
        "source_url": "https://ollama.com/library/qwen3",
    },
    {
        "id": "gpu-12",
        "label": "Stronger local reasoning",
        "quality_profile": "Stronger",
        "model": "qwen3:14b",
        "size": "9.3GB",
        "min_gpu_gb": 12,
        "max_gpu_gb": 16,
        "hardware": "12-15GB GPU VRAM",
        "best_for": "Better reasoning and coding when 8B-class models are not enough.",
        "source_url": "https://ollama.com/library/qwen3",
    },
    {
        "id": "gpu-16",
        "label": "Local reasoning workstation",
        "quality_profile": "Reasoning",
        "model": "gpt-oss:20b",
        "size": "14GB",
        "min_gpu_gb": 16,
        "max_gpu_gb": 24,
        "hardware": "16-23GB GPU VRAM",
        "best_for": "Open-weight local reasoning and agent workflows.",
        "source_url": "https://ollama.com/library/gpt-oss",
    },
    {
        "id": "gpu-24",
        "label": "24GB coding workstation",
        "quality_profile": "Code",
        "model": "qwen3-coder:30b",
        "size": "19GB",
        "min_gpu_gb": 24,
        "max_gpu_gb": 80,
        "hardware": "24-79GB GPU VRAM",
        "best_for": "Best default for local repo editing and Code Workspace on a 24GB GPU.",
        "source_url": "https://ollama.com/library/qwen3-coder",
    },
    {
        "id": "gpu-80",
        "label": "80GB reasoning server",
        "quality_profile": "Max",
        "model": "gpt-oss:120b",
        "size": "65GB",
        "min_gpu_gb": 80,
        "max_gpu_gb": None,
        "hardware": "80GB+ GPU VRAM",
        "best_for": "Large local reasoning model on workstation/server-class GPU memory.",
        "source_url": "https://ollama.com/library/gpt-oss",
    },
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _docker_like() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        return any(x in Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore") for x in ("docker", "containerd", "kubepods"))
    except Exception:
        return False


def _read_text(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _read_primary_model_manifest() -> dict[str, Any]:
    for path in (
        Path(os.getenv("DATA_DIR") or DATA_DIR) / "cleverly-primary-model.json",
        _repo_root() / "data" / "cleverly-primary-model.json",
    ):
        try:
            if path.exists():
                raw = path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
        except Exception:
            continue
    return {}


def _detected_gpu_gb() -> float:
    env_value = (os.getenv("CLEVERLY_GPU_GB") or "").strip()
    if env_value:
        try:
            return max(0.0, round(float(env_value), 1))
        except ValueError:
            pass
    manifest = _read_primary_model_manifest()
    try:
        if manifest.get("detected_gpu_gb") is not None:
            return max(0.0, round(float(manifest.get("detected_gpu_gb")), 1))
    except (TypeError, ValueError):
        pass
    try:
        from services.hwfit.hardware import detect_system

        system = detect_system(fresh=False)
        if system.get("has_gpu"):
            return max(0.0, round(float(system.get("gpu_vram_gb") or 0), 1))
    except Exception:
        pass
    return 0.0


def _model_profile_for_gpu(gpu_gb: float) -> dict[str, Any]:
    for item in MODEL_RECOMMENDATIONS:
        min_gpu = float(item.get("min_gpu_gb") or 0)
        max_gpu = item.get("max_gpu_gb")
        if gpu_gb >= min_gpu and (max_gpu is None or gpu_gb < float(max_gpu)):
            return item
    return MODEL_RECOMMENDATIONS[0]


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except Exception:
        return path.name


def _candidate_roots() -> list[Path]:
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    roots = [
        data_dir / "models",
        data_dir / "huggingface",
        data_dir / "training" / "finetune" / "base-models",
        data_dir / "training" / "finetune" / "adapters",
        data_dir / "ollama",
        Path(os.getenv("HF_HOME") or ""),
        Path(os.getenv("TRANSFORMERS_CACHE") or ""),
        Path(os.getenv("FASTEMBED_CACHE_PATH") or ""),
        Path("/app/.cache/huggingface"),
        Path("/root/.cache/huggingface"),
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        if not str(root):
            continue
        try:
            resolved = root.expanduser().resolve()
        except Exception:
            resolved = root
        key = str(resolved)
        if key in seen or not _path_exists(resolved):
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _scan_local_models(limit: int = 80) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    env_model = (os.getenv("OLLAMA_MODEL") or "").strip()
    if env_model:
        candidates.append({
            "name": env_model,
            "model_id": env_model,
            "kind": "ollama",
            "path": "",
            "size": None,
            "registerable": True,
            "note": "Configured by OLLAMA_MODEL",
        })

    for root in _candidate_roots():
        try:
            root_stat = root.stat()
        except Exception:
            root_stat = None
        if _path_exists(root / "config.json"):
            candidates.append({
                "name": root.name,
                "model_id": root.name,
                "kind": "huggingface",
                "path": str(root),
                "size": root_stat.st_size if root_stat else None,
                "registerable": False,
                "note": "Hugging Face model directory",
            })
        for current, dirs, files in os.walk(root):
            if len(candidates) >= limit:
                return candidates
            cur = Path(current)
            try:
                depth = len(cur.relative_to(root).parts)
            except Exception:
                depth = 0
            if depth > 5:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "blobs", "refs"}]
            if "config.json" in files and cur != root:
                candidates.append({
                    "name": cur.name,
                    "model_id": cur.name,
                    "kind": "huggingface",
                    "path": str(cur),
                    "size": None,
                    "registerable": False,
                    "note": f"Found under {_safe_rel(cur, root)}",
                })
            for filename in files:
                suffix = Path(filename).suffix.lower()
                if suffix not in {".gguf", ".safetensors"}:
                    continue
                path = cur / filename
                try:
                    size = path.stat().st_size
                except Exception:
                    size = None
                candidates.append({
                    "name": path.stem,
                    "model_id": path.stem,
                    "kind": suffix.lstrip("."),
                    "path": str(path),
                    "size": size,
                    "registerable": False,
                    "note": f"Found under {_safe_rel(path, root)}",
                })
                if len(candidates) >= limit:
                    return candidates
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (str(item.get("kind")), str(item.get("path") or item.get("model_id")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:limit]


def _model_endpoint_summary() -> dict[str, Any]:
    db = SessionLocal()
    try:
        rows = db.query(ModelEndpoint).all()
        local = [r for r in rows if is_local_model_url(getattr(r, "base_url", "") or "")]
        external = [r for r in rows if not is_local_model_url(getattr(r, "base_url", "") or "")]
        enabled_local = [r for r in local if getattr(r, "is_enabled", False)]
        enabled_external = [r for r in external if getattr(r, "is_enabled", False)]
        return {
            "total": len(rows),
            "local": len(local),
            "external": len(external),
            "enabled_local": len(enabled_local),
            "enabled_external": len(enabled_external),
            "default_endpoint_id": load_settings().get("default_endpoint_id", ""),
            "default_model": load_settings().get("default_model", ""),
            "code_workspace_model_key": load_settings().get("code_workspace_model_key", ""),
        }
    finally:
        db.close()


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _auth_configured(request: Request) -> bool:
    mgr = getattr(getattr(request.app, "state", None), "auth_manager", None)
    return bool(getattr(mgr, "is_configured", False))


def _last_egress_result() -> dict[str, Any] | None:
    for item in read_audit(100):
        if item.get("action") == "egress_test":
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            return {
                "timestamp": item.get("timestamp", ""),
                "blocked": bool(detail.get("blocked")),
                "status": detail.get("status", ""),
                "detail": detail.get("detail", ""),
            }
    return None


def _status_for_check(report: dict[str, Any], check_id: str) -> str:
    for item in report.get("checks") or []:
        if item.get("id") == check_id:
            return item.get("status", "")
    return ""


def _readiness_score(report: dict[str, Any], runtime: dict[str, Any], models: dict[str, Any], request: Request) -> dict[str, Any]:
    last_egress = _last_egress_result()
    items = [
        {
            "id": "auth-configured",
            "label": "Auth configured",
            "status": "ok" if _auth_configured(request) else "fail",
            "detail": "Authentication is configured" if _auth_configured(request) else "Create the first admin user before using sensitive data",
        },
        {
            "id": "offline-mode",
            "label": "Offline mode",
            "status": _status_for_check(report, "offline-mode") or "fail",
            "detail": "Offline startup policy is active",
        },
        {
            "id": "loopback-bind",
            "label": "Loopback UI",
            "status": _status_for_check(report, "loopback-bind") or "fail",
            "detail": f"APP_BIND={runtime.get('app_bind', '')}",
        },
        {
            "id": "sealed-data",
            "label": "Sealed data",
            "status": "ok" if runtime.get("sealed_mode") else "warn",
            "detail": "Docker named volume data mode" if runtime.get("sealed_mode") else "Host-visible data mode is active or native storage is used",
        },
        {
            "id": "local-model",
            "label": "Local model",
            "status": "ok" if (models.get("enabled_local") or 0) > 0 and (models.get("enabled_external") or 0) == 0 else "warn",
            "detail": f"{models.get('enabled_local') or 0} local enabled, {models.get('enabled_external') or 0} external enabled",
        },
        {
            "id": "code-worker",
            "label": "Code worker",
            "status": _status_for_check(report, "code-worker") or "warn",
            "detail": f"runner={runtime.get('code_workspace_runner', '')}",
        },
        {
            "id": "egress-proof",
            "label": "Egress proof",
            "status": "ok" if last_egress and last_egress.get("blocked") else "warn",
            "detail": last_egress.get("detail") if last_egress else "Run Test No Internet before sensitive work",
        },
    ]
    points = sum(1 for item in items if item["status"] == "ok")
    partial = sum(1 for item in items if item["status"] == "warn") * 0.5
    score = round(((points + partial) / len(items)) * 100)
    return {
        "score": score,
        "status": "green" if score >= 90 and not report.get("summary", {}).get("fail") else ("yellow" if score >= 65 else "red"),
        "label": "Ready" if score >= 90 else ("Needs review" if score >= 65 else "Not ready"),
        "items": items,
        "last_egress": last_egress,
    }


def _storage_visibility(runtime: dict[str, Any]) -> dict[str, Any]:
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    sealed = bool(runtime.get("sealed_mode"))
    host_data = _truthy(os.getenv("CLEVERLY_HOST_DATA")) or _truthy(os.getenv("CLEVERLY_USE_HOST_DATA"))
    return {
        "mode": "sealed" if sealed else ("host-data" if host_data else "native"),
        "sealed": sealed,
        "host_data_enabled": host_data,
        "paths": {
            "data_dir": str(data_dir),
            "logs_dir": str(Path(os.getenv("LOG_DIR") or _repo_root() / "logs")),
            "cache_home": runtime.get("cache_home", ""),
            "hf_home": runtime.get("hf_home", ""),
            "fastembed_cache_path": runtime.get("fastembed_cache_path", ""),
            "code_workspace_worker_dir": runtime.get("code_workspace_worker_dir", ""),
            "audit_log": str(Path(os.getenv("DATA_DIR") or DATA_DIR) / "audit" / "local-audit.jsonl"),
        },
        "docker_volumes": [
            "cleverly-data",
            "cleverly-logs",
            "cleverly-ssh",
            "cleverly-cache",
            "cleverly-huggingface",
            "cleverly-local",
            "cleverly-npm-cache",
            "cleverly-ollama",
        ],
        "notes": [
            "Docker named volumes are sealed from the project folder, not encrypted by themselves.",
            "Host-data mode intentionally exposes ./data and ./logs to the host filesystem.",
            "Encrypted backup export is the portable path for moving app data.",
        ],
    }


def _status_payload(request: Request) -> dict[str, Any]:
    report = evaluate_offline_policy(include_db=True)
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    checks = report.get("checks") or []
    runtime = {
        "offline": offline_mode(),
        "strict": report.get("strict", False),
        "break_glass": report.get("break_glass", False),
        "app_bind": os.getenv("APP_BIND", "127.0.0.1"),
        "data_dir": str(data_dir),
        "cache_home": os.getenv("XDG_CACHE_HOME", ""),
        "hf_home": os.getenv("HF_HOME", ""),
        "fastembed_cache_path": os.getenv("FASTEMBED_CACHE_PATH", ""),
        "code_workspace_runner": os.getenv("CODE_WORKSPACE_RUNNER", "worker" if _docker_like() else "in-process"),
        "code_workspace_worker_dir": os.getenv("CODE_WORKSPACE_WORKER_DIR", str(data_dir / "code-workspaces" / ".worker")),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "hostname": platform.node(),
        "docker_like": _docker_like(),
        "sealed_mode": str(data_dir).replace("\\", "/").endswith("/app/data") and not _truthy(os.getenv("CLEVERLY_USE_HOST_DATA")),
    }
    models = _model_endpoint_summary()
    storage = _storage_visibility(runtime)
    readiness = _readiness_score(report, runtime, models, request)
    return {
        "ok": True,
        "policy": report,
        "checks": checks,
        "summary": report.get("summary") or {},
        "readiness": readiness,
        "runtime": runtime,
        "storage": storage,
        "models": models,
        "timestamp": datetime.now().isoformat(),
    }


def _report_payload(request: Request) -> dict[str, Any]:
    status = _status_payload(request)
    root = _repo_root()
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(),
        "product": "Cleverly",
        "git_commit": _git_commit(root),
        "status": status,
        "audit": read_audit(100),
        "sensitive_machine_checklist": [
            "Prepare images and models on a connected non-sensitive machine.",
            "Move only the offline bundle to the sensitive machine.",
            "Use sealed Docker volumes unless host folders are intentional.",
            "Confirm readiness score and Offline Control checks before importing data.",
            "Run Test No Internet and keep this report.",
        ],
    }


def _html_report(report: dict[str, Any]) -> str:
    readiness = report.get("status", {}).get("readiness", {})
    checks = readiness.get("items") or []
    rows = "\n".join(
        f"<tr><td>{html.escape(str(item.get('status', '')))}</td><td>{html.escape(str(item.get('label', '')))}</td><td>{html.escape(str(item.get('detail', '')))}</td></tr>"
        for item in checks
    )
    audit_rows = "\n".join(
        f"<tr><td>{html.escape(str(item.get('timestamp', '')))}</td><td>{html.escape(str(item.get('action', '')))}</td><td>{html.escape(json.dumps(item.get('detail', {}), default=str)[:600])}</td></tr>"
        for item in report.get("audit", [])[:30]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cleverly Offline Report</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;background:#101216;color:#eef1f5;margin:24px}}table{{border-collapse:collapse;width:100%;margin:14px 0}}td,th{{border:1px solid #2a2f38;padding:8px;text-align:left;vertical-align:top}}.score{{font-size:42px;font-weight:800}}</style></head>
<body><h1>Cleverly Offline Report</h1><p>Generated: {html.escape(str(report.get('generated_at','')))}</p>
<div class="score">{html.escape(str(readiness.get('score','?')))}%</div><p>{html.escape(str(readiness.get('label','')))}</p>
<h2>Readiness Checks</h2><table><thead><tr><th>Status</th><th>Check</th><th>Detail</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Recent Audit</h2><table><thead><tr><th>Time</th><th>Action</th><th>Detail</th></tr></thead><tbody>{audit_rows}</tbody></table>
</body></html>"""


def setup_offline_control_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/offline-control",
        tags=["offline-control"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/status")
    def offline_status(request: Request):
        return _status_payload(request)

    @router.get("/storage")
    def storage_status(request: Request):
        return {"ok": True, "storage": _status_payload(request)["storage"]}

    @router.get("/audit")
    def audit_log(limit: int = 100):
        return {"ok": True, "events": read_audit(limit)}

    @router.post("/audit")
    def audit_event(body: AuditEventRequest, request: Request):
        return {
            "ok": True,
            "event": append_audit(body.action, body.detail, user=get_current_user(request) or "", source="ui"),
        }

    @router.get("/report")
    def report_json(request: Request):
        report = _report_payload(request)
        append_audit("offline_report_exported", {"format": "json", "score": report["status"]["readiness"]["score"]}, user=get_current_user(request) or "")
        return report

    @router.get("/report/html", response_class=HTMLResponse)
    def report_html(request: Request):
        report = _report_payload(request)
        append_audit("offline_report_exported", {"format": "html", "score": report["status"]["readiness"]["score"]}, user=get_current_user(request) or "")
        return HTMLResponse(_html_report(report))

    @router.get("/help")
    def offline_help():
        return {
            "ok": True,
            "sections": [
                {
                    "title": "Sensitive machine checklist",
                    "items": [
                        "Prepare images and models only on a connected, non-sensitive machine.",
                        "Move the offline bundle by trusted removable media.",
                        "Run load-cleverly.cmd, then seal-data.cmd if model/data files were included.",
                        "Start with sealed mode and confirm zero failed Offline Control checks.",
                        "Run Test No Internet and export a local report before importing sensitive data.",
                    ],
                },
                {
                    "title": "What leaves the container",
                    "items": [
                        "In default offline Docker mode, model calls stay on local Docker networks.",
                        "Encrypted backup export leaves the app only when you download the encrypted file.",
                        "Host-data mode intentionally exposes data/log folders on the host filesystem.",
                        "Network break-glass changes the threat model and is logged when used through the UI.",
                    ],
                },
                {
                    "title": "Model onboarding",
                    "items": [
                        "Pull models on a connected prep machine, not on the sensitive target.",
                        "Register only local model endpoints in Offline Control.",
                        "Run the benchmark after registration to see first-token and throughput behavior.",
                    ],
                },
            ],
        }

    @router.post("/egress-test")
    def egress_test(request: Request):
        target = ("1.1.1.1", 80)
        try:
            with socket.create_connection(target, timeout=2.5):
                pass
        except OSError as exc:
            result = {
                "ok": True,
                "blocked": True,
                "status": "ok",
                "detail": f"Outbound TCP test was blocked: {type(exc).__name__}",
            }
            append_audit("egress_test", result, user=get_current_user(request) or "")
            return result
        result = {
            "ok": True,
            "blocked": False,
            "status": "fail",
            "detail": "Outbound TCP to 1.1.1.1:80 succeeded. Run the Docker offline overlay before handling sensitive data.",
        }
        append_audit("egress_test", result, user=get_current_user(request) or "")
        return result

    @router.get("/models/local")
    def local_models():
        return {"ok": True, "models": _scan_local_models(), "roots": [str(p) for p in _candidate_roots()]}

    @router.get("/models/recommendations")
    def model_recommendations():
        detected_gpu_gb = _detected_gpu_gb()
        selected = _model_profile_for_gpu(detected_gpu_gb)
        manifest = _read_primary_model_manifest()
        prepared_model = str(manifest.get("primary_model") or "")
        commands = []
        for item in MODEL_RECOMMENDATIONS:
            model = item["model"]
            commands.append({
                **item,
                "selected": item["id"] == selected["id"],
                "prepared": bool(prepared_model and prepared_model == model),
                "setup_command": f".\\Cleverly.ps1 setup -AllowConnectedPrep -Model {model}",
                "prep_command": f".\\Cleverly.ps1 prep -AllowConnectedPrep -Model {model}",
                "bundle_command": f".\\Cleverly.ps1 bundle -AllowConnectedPrep -Model {model}",
                "auto_setup_command": ".\\Cleverly.ps1 setup -AllowConnectedPrep",
                "auto_prep_command": ".\\Cleverly.ps1 prep -AllowConnectedPrep",
                "auto_bundle_command": ".\\Cleverly.ps1 bundle -AllowConnectedPrep",
                "gpu_override_setup_command": f".\\Cleverly.ps1 setup -AllowConnectedPrep -GpuGB {item['min_gpu_gb']}",
                "gpu_override_prep_command": f".\\Cleverly.ps1 prep -AllowConnectedPrep -GpuGB {item['min_gpu_gb']}",
                "register_base_url": "http://ollama:11434/v1",
            })
        return {
            "ok": True,
            "detected_gpu_gb": detected_gpu_gb,
            "selected_profile": selected,
            "prepared_model": prepared_model,
            "offline_warning": "Run setup, prep, or bundle only on a connected, non-sensitive machine. Do not run these commands on the offline target machine.",
            "recommendations": commands,
        }

    @router.post("/models/register")
    def register_local_model(body: RegisterLocalModelRequest, request: Request):
        base_url = body.base_url.strip().rstrip("/")
        if not is_local_model_url(base_url):
            raise HTTPException(403, "Only local model endpoints can be registered from Offline Control")
        user = get_current_user(request) or None
        owner = None if body.shared else user
        cached_models = json.dumps([body.model])
        endpoint_name = body.name.strip() or f"Local {body.model}"
        db = SessionLocal()
        try:
            existing = (
                db.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url == base_url)
                .filter(ModelEndpoint.owner.is_(owner) if owner is None else ModelEndpoint.owner == owner)
                .first()
            )
            if existing:
                existing.name = endpoint_name
                existing.cached_models = cached_models
                existing.model_type = "llm"
                existing.is_enabled = True
                endpoint_id = existing.id
                created = False
            else:
                endpoint_id = str(uuid.uuid4())[:8]
                db.add(ModelEndpoint(
                    id=endpoint_id,
                    name=endpoint_name,
                    base_url=base_url,
                    api_key=None,
                    is_enabled=True,
                    cached_models=cached_models,
                    model_type="llm",
                    supports_tools=None,
                    owner=owner,
                ))
                created = True
            db.commit()
        finally:
            db.close()
        if body.set_default:
            settings = load_settings()
            settings["default_endpoint_id"] = endpoint_id
            settings["default_model"] = body.model
            save_settings(settings)
        result = {
            "ok": True,
            "id": endpoint_id,
            "created": created,
            "name": endpoint_name,
            "base_url": base_url,
            "model": body.model,
            "default": body.set_default,
        }
        append_audit("model_endpoint_registered", result, user=get_current_user(request) or "")
        return result

    @router.post("/models/benchmark")
    def benchmark_model(body: ModelBenchmarkRequest, request: Request):
        base_url = body.base_url.strip().rstrip("/")
        if not is_local_model_url(base_url):
            raise HTTPException(403, "Only local model endpoints can be benchmarked in offline mode")
        try:
            import httpx
        except Exception as exc:
            raise HTTPException(500, f"httpx unavailable: {type(exc).__name__}")

        chat_url = build_chat_url(normalize_base(base_url))
        payload = {
            "model": body.model,
            "messages": [{"role": "user", "content": body.prompt}],
            "temperature": 0,
            "max_tokens": body.max_tokens,
            "stream": True,
        }
        started = time.perf_counter()
        first_token_ms = None
        chunks = 0
        chars = 0
        content_parts: list[str] = []
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                with client.stream("POST", chat_url, json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            item = json.loads(line)
                        except Exception:
                            continue
                        delta = (((item.get("choices") or [{}])[0].get("delta") or {}).get("content") or "")
                        if delta:
                            if first_token_ms is None:
                                first_token_ms = round((time.perf_counter() - started) * 1000)
                            chunks += 1
                            chars += len(delta)
                            content_parts.append(delta)
        except Exception as exc:
            # Some local servers do not stream. Fall back to a normal request.
            payload["stream"] = False
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                    response = client.post(chat_url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                chars = len(text)
                chunks = 1 if text else 0
                first_token_ms = None
                content_parts = [text]
            except Exception as fallback_exc:
                detail = {"model": body.model, "base_url": base_url, "error": str(fallback_exc or exc)[:500]}
                append_audit("model_benchmark_failed", detail, user=get_current_user(request) or "")
                raise HTTPException(502, f"Local model benchmark failed: {type(fallback_exc).__name__}")
        total_ms = round((time.perf_counter() - started) * 1000)
        result = {
            "ok": True,
            "base_url": base_url,
            "model": body.model,
            "first_token_ms": first_token_ms,
            "total_ms": total_ms,
            "chunks": chunks,
            "chars": chars,
            "chars_per_second": round(chars / max(total_ms / 1000, 0.001), 2),
            "sample": "".join(content_parts)[:240],
            "local_only": True,
        }
        append_audit("model_benchmark", {k: v for k, v in result.items() if k != "sample"}, user=get_current_user(request) or "")
        return result

    @router.get("/about")
    def about():
        root = _repo_root()
        package = {}
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except Exception:
            package = {}
        license_dir = root / "licenses"
        notice_files = []
        if license_dir.exists():
            for path in sorted(p for p in license_dir.iterdir() if p.is_file()):
                notice_files.append({
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                })
        return {
            "ok": True,
            "product": "Cleverly",
            "package": {
                "name": package.get("name", ""),
                "version": package.get("version", ""),
                "license": package.get("license", ""),
            },
            "git_commit": _git_commit(root),
            "license": _read_text(root / "LICENSE"),
            "acknowledgments": _read_text(root / "ACKNOWLEDGMENTS.md"),
            "notice_files": notice_files,
        }

    return router
