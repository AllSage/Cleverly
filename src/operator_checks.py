"""Operator-facing startup and air-gap checks."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urlparse

from src.constants import DATA_DIR
from src.offline_policy import evaluate_offline_policy
from src.settings import get_effective_code_workspace_model_key


def _check(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _clean(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _docker_like() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _compose_service(
    compose_service: str,
    container_name: str,
    role: str,
    *,
    required: bool,
    profile: str = "",
    source: str = "docker-compose.yml",
) -> dict[str, Any]:
    return {
        "compose_service": compose_service,
        "container_name": container_name,
        "role": role,
        "required": required,
        "profile": profile,
        "source": source,
    }


def _service(
    service_id: str,
    label: str,
    state: str,
    detail: str,
    *,
    required: bool,
    kind: str,
    target: str = "",
    latency_ms: int | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": service_id,
        "label": label,
        "state": state,
        "detail": detail,
        "required": required,
        "kind": kind,
        "target": target,
    }
    if latency_ms is not None:
        record["latency_ms"] = latency_ms
    return record


def _alert_row(
    *,
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str = "open-local-services-map",
    action_label: str = "Services",
    requires_approval: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": requires_approval,
        "executes": False,
        "restarts_services": False,
        "starts_services": False,
        "pulls_images": False,
        "runs_docker": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
    }


def _is_local_probe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in {
        "localhost",
        "127.0.0.1",
        "::1",
        "host.docker.internal",
        "cleverly",
        "cleverly-proxy",
        "cleverly_code_worker",
        "cleverly-code-worker",
        "ollama",
        "chromadb",
        "searxng",
        "ntfy",
    }:
        return True
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2:
            try:
                return 16 <= int(parts[1]) <= 31
            except ValueError:
                return False
    return "." not in host


def _http_probe(
    service_id: str,
    label: str,
    urls: list[str],
    *,
    required: bool,
    kind: str = "http",
    timeout: float = 1.5,
) -> dict[str, Any]:
    candidates = [url for url in urls if url]
    if not candidates:
        return _service(service_id, label, "loading", "No local endpoint configured", required=required, kind=kind)
    skipped_external: list[str] = []
    last_error = ""
    for url in candidates:
        if not _is_local_probe_url(url):
            skipped_external.append(url)
            continue
        started = time.perf_counter()
        try:
            req = url_request.Request(url, headers={"User-Agent": "Cleverly-Local-Service-Check/1"})
            with url_request.urlopen(req, timeout=timeout) as response:
                response.read(256)
                status = getattr(response, "status", 200)
            latency = round((time.perf_counter() - started) * 1000)
            if 200 <= int(status) < 500:
                return _service(
                    service_id,
                    label,
                    "ok",
                    f"Local endpoint returned HTTP {status}",
                    required=required,
                    kind=kind,
                    target=url,
                    latency_ms=latency,
                )
            last_error = f"HTTP {status}"
        except url_error.HTTPError as exc:
            latency = round((time.perf_counter() - started) * 1000)
            if 200 <= int(exc.code) < 500:
                return _service(
                    service_id,
                    label,
                    "ok",
                    f"Local endpoint returned HTTP {exc.code}",
                    required=required,
                    kind=kind,
                    target=url,
                    latency_ms=latency,
                )
            last_error = f"HTTP {exc.code}"
        except (url_error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = str(reason)[:240]
    if skipped_external and not last_error:
        return _service(
            service_id,
            label,
            "warn",
            "External endpoint configured but not probed by local service snapshot",
            required=required,
            kind=kind,
            target=skipped_external[0],
        )
    return _service(
        service_id,
        label,
        "error" if required else "loading",
        last_error or "Local endpoint did not respond",
        required=required,
        kind=kind,
        target=candidates[0],
    )


def _path_service(service_id: str, label: str, path: Path, *, required: bool, kind: str) -> dict[str, Any]:
    exists = path.exists()
    if exists:
        detail = f"{path} exists"
        try:
            if path.is_dir():
                detail = f"{path} exists; {sum(1 for _ in path.iterdir())} entries"
        except OSError:
            detail = f"{path} exists; contents unavailable"
        return _service(service_id, label, "ok", detail, required=required, kind=kind, target=str(path))
    return _service(
        service_id,
        label,
        "error" if required else "warn",
        f"{path} is missing",
        required=required,
        kind=kind,
        target=str(path),
    )


def _ollama_urls() -> list[str]:
    raw = [_env("OLLAMA_BASE_URL"), _env("OLLAMA_URL")]
    urls: list[str] = []
    for item in raw:
        if not item:
            continue
        root = item.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        urls.extend([f"{root}/api/tags", f"{root}/v1/models"])
    if _docker_like():
        urls.extend(["http://ollama:11434/api/tags", "http://ollama:11434/v1/models"])
    return urls


def _searxng_urls() -> list[str]:
    raw = [_env("SEARXNG_INSTANCE"), _env("SEARXNG_BASE_URL")]
    urls = [item.rstrip("/") + "/" for item in raw if item]
    if _docker_like():
        urls.append("http://searxng:8080/")
    return urls


def _container_status_evidence_path(data_dir: Path) -> Path:
    return Path(_env("CLEVERLY_CONTAINER_STATUS_FILE") or data_dir / "operator_container_status.json")


def _container_status_record(record: dict[str, Any]) -> dict[str, str]:
    row: dict[str, str] = {}
    for key in ("name", "names", "container", "container_name", "compose_service", "service", "image", "status", "state", "health"):
        value = _clean(record.get(key), 360)
        if value:
            row[key] = value
    return row


def _read_container_status_evidence(data_dir: Path) -> dict[str, Any]:
    path = _container_status_evidence_path(data_dir)
    result: dict[str, Any] = {
        "path": str(path),
        "source": "not captured",
        "captured_at": "",
        "note": "No captured host container status evidence is available.",
        "rows": [],
    }
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return result
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        result["source"] = "invalid evidence file"
        result["note"] = "Container status evidence file is not valid JSON."
        return result
    if isinstance(payload, list):
        records = payload
        result["source"] = "container status evidence file"
    elif isinstance(payload, dict):
        records = payload.get("containers") or payload.get("container_status") or payload.get("rows") or []
        result["source"] = _clean(payload.get("source") or "container status evidence file", 180)
        result["captured_at"] = _clean(payload.get("captured_at") or payload.get("generated_at"), 80)
        result["note"] = _clean(payload.get("note") or "Captured host container status evidence only.", 240)
    else:
        records = []
        result["source"] = "invalid evidence file"
        result["note"] = "Container status evidence file must be a list or object."
    result["rows"] = [
        row
        for row in (_container_status_record(item) for item in records[:80] if isinstance(item, dict))
        if row
    ]
    if result["rows"] and result["note"] == "No captured host container status evidence is available.":
        result["note"] = "Captured host container status evidence only."
    return result


def _service_alert_rows(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for service in services:
        state = service.get("state")
        if state not in {"error", "warn"}:
            continue
        required = service.get("required") is True
        rows.append(_alert_row(
            row_id=f"service-snapshot-{service.get('id') or len(rows)}",
            state="error" if state == "error" and required else "warn",
            badge=service.get("kind") or ("core" if required else "opt"),
            title=f"{service.get('label') or service.get('id') or 'Local service'} needs review",
            detail=_clean(service.get("detail") or service.get("target") or "Local service probe is not healthy.", 260),
            action="open-container-repair-plan" if state == "error" else "open-local-services-map",
            action_label="Repair" if state == "error" else "Services",
            requires_approval=state == "error",
        ))
    return rows[:10]


def _container_unhealthy(row: dict[str, str]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("status", "state", "health")).lower()
    if not text:
        return False
    return any(token in text for token in ("exited", "dead", "unhealthy", "restarting", "error", "fail"))


def _container_alert_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if plan.get("docker_socket_mounted"):
        rows.append(_alert_row(
            row_id="checks-docker-socket-mounted",
            state="warn",
            badge="dock",
            title="Docker socket mounted",
            detail="Host Docker socket appears mounted; keep host repair commands behind explicit approval.",
            action="open-trust-controls",
            action_label="Trust",
            requires_approval=True,
        ))
    if not plan.get("container_status"):
        rows.append(_alert_row(
            row_id="checks-container-status-missing",
            state="warn",
            badge="dock",
            title="No captured container status evidence",
            detail=_clean(plan.get("container_status_note") or "Run an approved host-side status capture before repairing containers.", 260),
            action="open-container-repair-plan",
            action_label="Repair",
        ))
    for index, row in enumerate(plan.get("container_status") or []):
        if not isinstance(row, dict) or not _container_unhealthy(row):
            continue
        name = row.get("name") or row.get("names") or row.get("container") or row.get("compose_service") or f"container {index + 1}"
        status = row.get("status") or row.get("state") or row.get("health") or "unhealthy"
        rows.append(_alert_row(
            row_id=f"checks-container-unhealthy-{index}",
            state="error",
            badge="dock",
            title=f"Container status needs review: {name}",
            detail=_clean(status, 260),
            action="open-container-repair-plan",
            action_label="Repair",
            requires_approval=True,
        ))
    if any(command.get("risk") == "approval-required" for command in plan.get("host_commands") or []):
        rows.append(_alert_row(
            row_id="checks-host-command-approval",
            state="warn",
            badge="ask",
            title="Host Docker commands require approval",
            detail="Repair/recreate/start commands are listed as evidence only and must be run explicitly by the owner.",
            action="open-trust-controls",
            action_label="Trust",
            requires_approval=True,
        ))
    return rows[:10]


def run_operator_service_snapshot() -> dict[str, Any]:
    """Return read-only health signals for local services without Docker control."""
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    logs_dir = Path(os.getenv("LOG_DIR") or data_dir.parent / "logs")
    runner = (os.getenv("CODE_WORKSPACE_RUNNER") or "in-process").strip().lower()
    worker_dir = Path(os.getenv("CODE_WORKSPACE_WORKER_DIR") or data_dir / "code-workspaces" / ".worker")
    chroma_host = _env("CHROMADB_HOST", "localhost")
    chroma_port = _env("CHROMADB_PORT", "8100")
    services = [
        _service(
            "cleverly-api",
            "Cleverly app API",
            "ok",
            "Operator service snapshot generated in-process",
            required=True,
            kind="app",
            target="in-process",
        ),
        _path_service("data-dir", "App data volume", data_dir, required=True, kind="path"),
        _path_service("logs-dir", "App logs volume", logs_dir, required=False, kind="path"),
        _path_service(
            "code-worker-queue",
            "Code Workspace worker queue",
            worker_dir,
            required=runner == "worker",
            kind="queue",
        ),
        _http_probe("ollama", "Ollama local model service", _ollama_urls(), required=False),
        _http_probe(
            "chromadb",
            "ChromaDB vector service",
            [
                f"http://{chroma_host}:{chroma_port}/api/v2/heartbeat",
                f"http://{chroma_host}:{chroma_port}/",
            ],
            required=False,
        ),
        _http_probe("searxng", "SearXNG search service", _searxng_urls(), required=False),
        _http_probe(
            "ntfy",
            "ntfy notification service",
            [
                _env("NTFY_BASE_URL").rstrip("/") + "/v1/health" if _env("NTFY_BASE_URL") else "",
                "http://ntfy:80/v1/health" if _docker_like() else "",
                "http://ntfy:80/" if _docker_like() else "",
            ],
            required=False,
        ),
    ]
    summary = {
        "ok": sum(1 for item in services if item["state"] == "ok"),
        "warn": sum(1 for item in services if item["state"] == "warn"),
        "error": sum(1 for item in services if item["state"] == "error"),
        "loading": sum(1 for item in services if item["state"] == "loading"),
        "required": sum(1 for item in services if item["required"]),
    }
    alert_rows = _service_alert_rows(services)
    summary["service_snapshot_alert_count"] = len(alert_rows)
    summary["critical_service_snapshot_alert_count"] = sum(1 for row in alert_rows if row.get("state") == "error")
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "docker_like": _docker_like(),
        "runner": runner,
        "services": services,
        "summary": summary,
        "alert_rows": alert_rows,
        "note": "Read-only local service probes only; no Docker socket, restarts, pulls, deletes, or host shell commands are used.",
    }


def _container_plan() -> dict[str, Any]:
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    container_status = _read_container_status_evidence(data_dir)
    project = _env("COMPOSE_PROJECT_NAME", "cleverly")
    support_container = lambda service: f"{project}-{service}-1"
    services = [
        _compose_service(
            "cleverly",
            _env("CLEVERLY_CONTAINER_NAME", "cleverly"),
            "app API and command center",
            required=True,
        ),
        _compose_service(
            "cleverly_code_worker",
            _env("CLEVERLY_CODE_WORKER_CONTAINER_NAME", "cleverly-code-worker"),
            "networkless code workspace worker",
            required=True,
        ),
        _compose_service(
            "cleverly_proxy",
            _env("CLEVERLY_PROXY_CONTAINER_NAME", "cleverly-proxy"),
            "loopback web proxy",
            required=True,
        ),
        _compose_service(
            "chromadb",
            _env("CLEVERLY_CHROMADB_CONTAINER_NAME", support_container("chromadb")),
            "optional vector database support service",
            required=False,
            profile="support",
        ),
        _compose_service(
            "searxng",
            _env("CLEVERLY_SEARXNG_CONTAINER_NAME", support_container("searxng")),
            "optional local search support service",
            required=False,
            profile="support",
        ),
        _compose_service(
            "ntfy",
            _env("CLEVERLY_NTFY_CONTAINER_NAME", support_container("ntfy")),
            "optional notification support service",
            required=False,
            profile="support",
        ),
        _compose_service(
            "ollama",
            _env("CLEVERLY_OLLAMA_CONTAINER_NAME", "cleverly-ollama"),
            "optional bundled local model service",
            required=False,
            source="docker/ollama-offline.yml",
        ),
    ]
    host_commands = [
        {
            "label": "List running containers",
            "risk": "read-only",
            "command": 'docker ps --format "table {{.Names}}\\t{{.Image}}\\t{{.Status}}"',
        },
        {
            "label": "List compose service state",
            "risk": "read-only",
            "command": "docker compose ps",
        },
        {
            "label": "Inspect core service logs",
            "risk": "read-only",
            "command": "docker compose logs --tail=120 cleverly cleverly_code_worker cleverly_proxy",
        },
        {
            "label": "Recreate app service only",
            "risk": "approval-required",
            "command": "docker compose up -d --force-recreate --no-deps cleverly",
        },
        {
            "label": "Start optional support services without pulling",
            "risk": "approval-required",
            "command": "docker compose --profile support up -d --no-build --pull never",
        },
    ]
    plan = {
        "source": "compose manifest and environment",
        "docker_socket_mounted": Path("/var/run/docker.sock").exists(),
        "compose_project": project,
        "services": services,
        "container_status": container_status["rows"],
        "container_status_path": container_status["path"],
        "container_status_source": container_status["source"],
        "container_status_captured_at": container_status["captured_at"],
        "container_status_note": container_status["note"],
        "host_commands": host_commands,
        "note": "Cleverly does not execute host Docker commands from this route; use host commands as approval-gated repair evidence and the container status file as captured evidence only.",
    }
    alert_rows = _container_alert_rows(plan)
    plan["alert_rows"] = alert_rows
    plan["summary"] = {
        "expected_service_count": len(services),
        "container_status_count": len(container_status["rows"]),
        "host_command_count": len(host_commands),
        "checks_container_alert_count": len(alert_rows),
        "critical_checks_container_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
        "executes": False,
        "restarts_services": False,
        "starts_services": False,
        "pulls_images": False,
        "runs_docker": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return plan


def run_operator_checks() -> dict[str, Any]:
    policy = evaluate_offline_policy(include_db=True)
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    runner = (os.getenv("CODE_WORKSPACE_RUNNER") or "in-process").strip().lower()
    worker_dir = Path(os.getenv("CODE_WORKSPACE_WORKER_DIR") or data_dir / "code-workspaces" / ".worker")
    model_key = (get_effective_code_workspace_model_key() or "").strip()

    checks = list(policy["checks"])
    checks.append(_check(
        "code-model-key",
        "Code Workspace model key",
        "ok" if model_key else "warn",
        f"Resolved as {model_key}" if model_key else "Not set; Code agent will refuse to run until set",
    ))
    checks.append(_check(
        "code-worker-dir-ready",
        "Code Workspace worker queue",
        "ok" if runner == "worker" and worker_dir.exists() else ("warn" if runner == "worker" else "warn"),
        f"runner={runner}; worker_dir={worker_dir}",
    ))
    container_plan = _container_plan()
    alert_rows = [
        _alert_row(
            row_id=f"checks-policy-{item.get('id') or index}",
            state="error" if item["status"] == "fail" else "warn",
            badge="check",
            title=f"{item['label']} needs review",
            detail=item["detail"],
            action="open-offline" if "offline" in item["id"] or "policy" in item["id"] else "open-local-services-map",
            action_label="Policy" if "offline" in item["id"] or "policy" in item["id"] else "Services",
            requires_approval=item["status"] == "fail",
        )
        for index, item in enumerate(checks)
        if item["status"] in {"warn", "fail"}
    ][:10]
    summary = {
        "ok": sum(1 for item in checks if item["status"] == "ok"),
        "warn": sum(1 for item in checks if item["status"] == "warn"),
        "fail": sum(1 for item in checks if item["status"] == "fail"),
        "checks_alert_count": len(alert_rows),
        "critical_checks_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
        "checks_container_alert_count": container_plan["summary"]["checks_container_alert_count"],
        "critical_checks_container_alert_count": container_plan["summary"]["critical_checks_container_alert_count"],
    }
    return {
        "checks": checks,
        "summary": summary,
        "alert_rows": alert_rows,
        "strict": policy.get("strict", True),
        "offline": policy.get("offline", False),
        "break_glass": policy.get("break_glass", False),
        "container_plan": container_plan,
    }
