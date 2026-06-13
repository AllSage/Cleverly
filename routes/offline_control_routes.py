"""Admin-only offline control, proof, model import, and legal metadata routes."""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import ModelEndpoint, SessionLocal
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.constants import DATA_DIR
from src.offline_policy import evaluate_offline_policy, is_local_model_url
from src.settings import load_settings, offline_mode, save_settings


class RegisterLocalModelRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    base_url: str = Field(default="http://ollama:11434/v1", max_length=300)
    model: str = Field(min_length=1, max_length=240)
    set_default: bool = True
    shared: bool = True


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
        if key in seen or not resolved.exists():
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
        if (root / "config.json").exists():
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


def setup_offline_control_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/offline-control",
        tags=["offline-control"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/status")
    def offline_status():
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
        return {
            "ok": True,
            "policy": report,
            "checks": checks,
            "summary": report.get("summary") or {},
            "runtime": runtime,
            "models": _model_endpoint_summary(),
            "timestamp": datetime.now().isoformat(),
        }

    @router.post("/egress-test")
    def egress_test():
        target = ("1.1.1.1", 80)
        try:
            with socket.create_connection(target, timeout=2.5):
                pass
        except OSError as exc:
            return {
                "ok": True,
                "blocked": True,
                "status": "ok",
                "detail": f"Outbound TCP test was blocked: {type(exc).__name__}",
            }
        return {
            "ok": True,
            "blocked": False,
            "status": "fail",
            "detail": "Outbound TCP to 1.1.1.1:80 succeeded. Run the Docker offline overlay before handling sensitive data.",
        }

    @router.get("/models/local")
    def local_models():
        return {"ok": True, "models": _scan_local_models(), "roots": [str(p) for p in _candidate_roots()]}

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
        return {
            "ok": True,
            "id": endpoint_id,
            "created": created,
            "name": endpoint_name,
            "base_url": base_url,
            "model": body.model,
            "default": body.set_default,
        }

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
