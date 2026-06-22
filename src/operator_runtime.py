"""Read-only runtime resource evidence for the Cleverly operator console."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from src.constants import DATA_DIR
from src.settings import offline_mode

LOW_SPACE_PERCENT = 10.0
LOW_SPACE_BYTES = 5 * 1024 * 1024 * 1024
LOW_MEMORY_PERCENT = 15.0


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _docker_like() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        text = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
        return any(marker in text for marker in ("docker", "containerd", "kubepods"))
    except Exception:
        return False


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _memory_info() -> dict[str, int | None]:
    total = available = None
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, rest = line.partition(":")
            raw = rest.strip().split()[0] if rest.strip() else ""
            if raw.isdigit():
                values[key] = int(raw) * 1024
        total = values.get("MemTotal")
        available = values.get("MemAvailable") or values.get("MemFree")
    except OSError:
        pass
    if total is None:
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            total_pages = os.sysconf("SC_PHYS_PAGES")
            available_pages = os.sysconf("SC_AVPHYS_PAGES")
            total = int(page) * int(total_pages)
            available = int(page) * int(available_pages)
        except (AttributeError, OSError, ValueError):
            pass
    return {"total_bytes": total, "available_bytes": available}


def _cgroup_info() -> dict[str, Any]:
    return {
        "memory_limit_bytes": _read_int(Path("/sys/fs/cgroup/memory.max"))
        or _read_int(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")),
        "memory_current_bytes": _read_int(Path("/sys/fs/cgroup/memory.current"))
        or _read_int(Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
        "pids_max": _read_text(Path("/sys/fs/cgroup/pids.max")) or _read_text(Path("/sys/fs/cgroup/pids/pids.max")),
        "pids_current": _read_int(Path("/sys/fs/cgroup/pids.current"))
        or _read_int(Path("/sys/fs/cgroup/pids/pids.current")),
    }


def _data_root() -> Path:
    return Path(os.getenv("DATA_DIR") or DATA_DIR)


def _logs_root(data_root: Path) -> Path:
    return Path(os.getenv("LOG_DIR") or data_root.parent / "logs")


def _default_roots(data_root: Path, logs_root: Path) -> list[dict[str, Any]]:
    cache_root = Path(os.getenv("XDG_CACHE_HOME") or data_root / "cache")
    return [
        {"id": "data", "label": "App data", "path": data_root, "required": True, "role": "database, memory, files, tasks, training, and app state"},
        {"id": "logs", "label": "App logs", "path": logs_root, "required": False, "role": "application logs and audit evidence"},
        {"id": "cache", "label": "Runtime cache", "path": cache_root, "required": False, "role": "runtime, embedding, browser, and helper caches"},
        {"id": "training", "label": "Training Lab", "path": data_root / "training", "required": False, "role": "datasets, fine-tune jobs, adapters, and small-model artifacts"},
        {"id": "models", "label": "Local models", "path": data_root / "models", "required": False, "role": "local model artifacts created or imported through Cleverly"},
        {"id": "huggingface", "label": "Hugging Face cache", "path": Path(os.getenv("HF_HOME") or "/app/.cache/huggingface"), "required": False, "role": "pre-seeded model and tokenizer cache"},
        {"id": "ollama", "label": "Ollama store", "path": Path(os.getenv("OLLAMA_MODELS") or data_root / "ollama"), "required": False, "role": "bundled Ollama model store or host-data mirror"},
        {"id": "code-workspaces", "label": "Code Workspace", "path": Path(os.getenv("CODE_WORKSPACE_DIR") or data_root / "code-workspaces"), "required": False, "role": "imports, snapshots, worker queue, and outputs"},
        {"id": "tmp", "label": "Temporary workspace", "path": Path(os.getenv("TMPDIR") or "/tmp"), "required": True, "role": "temporary files for uploads, exports, tests, and background jobs"},
        {"id": "npm-cache", "label": "npm cache", "path": Path(os.getenv("NPM_CONFIG_CACHE") or "/app/.npm"), "required": False, "role": "optional local npm/npx cache for MCP helpers"},
        {"id": "local-bin", "label": "Local package root", "path": Path(os.getenv("LOCALAPPDATA") or "/app/.local"), "required": False, "role": "local packages and CLI helper installs"},
    ]


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _disk_row(
    item: dict[str, Any],
    *,
    disk_usage: Callable[[str], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    path = Path(item.get("path") or "")
    required = item.get("required") is True
    exists = path.exists()
    usage_path = _nearest_existing(path)
    total = used = free = None
    detail = ""
    try:
        usage = disk_usage(str(usage_path))
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        free_percent = round((free / total) * 100, 1) if total else None
        low_space = free_percent is not None and (free_percent < LOW_SPACE_PERCENT or free < LOW_SPACE_BYTES)
        state = "warn" if low_space else "ok"
        detail = f"{item.get('role') or 'local runtime root'}; free={_bytes(free)} of {_bytes(total)}"
    except (OSError, ValueError):
        free_percent = None
        low_space = False
        state = "warn"
        detail = f"{item.get('role') or 'local runtime root'}; disk usage unavailable"
    if not exists:
        state = "error" if required else "loading"
        detail = f"{item.get('role') or 'local runtime root'}; path is not present in this runtime"
    return {
        "id": _trim(item.get("id"), 80),
        "state": state,
        "badge": _trim(item.get("badge") or item.get("id") or "disk", 24),
        "label": _trim(item.get("label") or item.get("id") or "Runtime root", 120),
        "title": _trim(item.get("label") or item.get("id") or "Runtime root", 160),
        "detail": detail,
        "path": str(path),
        "usage_path": str(usage_path),
        "exists": exists,
        "required": required,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "free_percent": free_percent,
        "low_space": low_space,
        "executes": False,
        "action": "open-local-data-map",
        "actionLabel": "Data",
    }


def _bytes(value: int | None) -> str:
    if value is None or value < 0:
        return "unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def _memory_rows(memory: dict[str, Any], cgroup: dict[str, Any]) -> list[dict[str, Any]]:
    total = memory.get("total_bytes")
    available = memory.get("available_bytes")
    if isinstance(total, int) and total > 0 and isinstance(available, int):
        available_percent = round((available / total) * 100, 1)
        memory_state = "warn" if available_percent < LOW_MEMORY_PERCENT else "ok"
        memory_detail = f"{_bytes(available)} available of {_bytes(total)} host-visible memory"
    else:
        available_percent = None
        memory_state = "loading"
        memory_detail = "Host-visible memory counters are unavailable in this runtime"
    limit = cgroup.get("memory_limit_bytes")
    current = cgroup.get("memory_current_bytes")
    if isinstance(limit, int) and limit > 0 and isinstance(current, int):
        cgroup_free = max(0, limit - current)
        cgroup_percent = round((cgroup_free / limit) * 100, 1)
        cgroup_state = "warn" if cgroup_percent < LOW_MEMORY_PERCENT else "ok"
        cgroup_detail = f"{_bytes(cgroup_free)} cgroup memory headroom of {_bytes(limit)}"
    else:
        cgroup_state = "loading"
        cgroup_detail = "No finite cgroup memory limit is visible"
    pids_max = _trim(cgroup.get("pids_max") or "unknown", 80)
    pids_current = cgroup.get("pids_current")
    pid_state = "ok"
    pid_detail = f"current={pids_current if pids_current is not None else 'unknown'}; limit={pids_max}"
    if pids_max not in {"", "max", "unknown"} and isinstance(pids_current, int):
        try:
            if int(pids_max) - pids_current < 32:
                pid_state = "warn"
        except ValueError:
            pass
    return [
        {
            "id": "host-memory",
            "state": memory_state,
            "badge": "ram",
            "title": "Host-visible memory",
            "detail": memory_detail,
            "available_percent": available_percent,
            "executes": False,
            "action": "open-system-health",
            "actionLabel": "Status",
        },
        {
            "id": "container-memory",
            "state": cgroup_state,
            "badge": "cg",
            "title": "Container memory limit",
            "detail": cgroup_detail,
            "executes": False,
            "action": "open-system-health",
            "actionLabel": "Status",
        },
        {
            "id": "process-limit",
            "state": pid_state,
            "badge": "pid",
            "title": "Process limit",
            "detail": pid_detail,
            "executes": False,
            "action": "open-system-health",
            "actionLabel": "Status",
        },
    ]


def _api_action(method: str, path: str, title: str, *, writes: bool = False, requires_approval: bool = False, uses_network: bool = False) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": requires_approval,
        "uses_network": uses_network,
    }


def run_operator_runtime_plan(
    owner: str = "local",
    *,
    data_root: str | Path | None = None,
    logs_root: str | Path | None = None,
    roots: list[dict[str, Any]] | None = None,
    memory_info: dict[str, Any] | None = None,
    cgroup_info: dict[str, Any] | None = None,
    docker_like: bool | None = None,
    disk_usage: Callable[[str], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    """Return read-only resource guidance for local heavy jobs."""
    owner = owner or "local"
    data = Path(data_root) if data_root is not None else _data_root()
    logs = Path(logs_root) if logs_root is not None else _logs_root(data)
    root_items = roots if roots is not None else _default_roots(data, logs)
    disk_rows = [_disk_row(item, disk_usage=disk_usage) for item in root_items]
    memory = memory_info if isinstance(memory_info, dict) else _memory_info()
    cgroup = cgroup_info if isinstance(cgroup_info, dict) else _cgroup_info()
    resource_rows = _memory_rows(memory, cgroup)
    in_docker = _docker_like() if docker_like is None else bool(docker_like)
    low_space = [row for row in disk_rows if row.get("low_space")]
    missing_required = [row for row in disk_rows if row.get("required") and not row.get("exists")]
    memory_warnings = [row for row in resource_rows if row["state"] == "warn"]
    runtime_state = "error" if missing_required else ("warn" if low_space or memory_warnings else "ok")

    machine_rows = [
        {
            "id": "runtime",
            "state": "ok" if in_docker else "warn",
            "badge": "dock",
            "title": "Runtime boundary",
            "detail": "Docker/container runtime detected" if in_docker else "Native runtime or container marker unavailable",
            "executes": False,
            "action": "open-offline",
            "actionLabel": "Offline",
        },
        {
            "id": "offline",
            "state": "ok" if offline_mode() else "warn",
            "badge": "net",
            "title": "Offline mode",
            "detail": "CLEVERLY_OFFLINE is active" if offline_mode() else "Network mode is enabled or not declared offline",
            "executes": False,
            "action": "open-offline",
            "actionLabel": "Policy",
        },
        {
            "id": "python",
            "state": "ok",
            "badge": "py",
            "title": "Python runtime",
            "detail": f"{platform.python_implementation()} {platform.python_version()} on {platform.system() or sys.platform}",
            "executes": False,
            "action": "open-system-health",
            "actionLabel": "Status",
        },
        {
            "id": "limits",
            "state": "ok",
            "badge": "lim",
            "title": "Configured container limits",
            "detail": f"tmpfs={os.getenv('CLEVERLY_TMPFS_SIZE') or 'default'}; pids={os.getenv('CLEVERLY_PIDS_LIMIT') or cgroup.get('pids_max') or 'default'}",
            "executes": False,
            "action": "open-system-health",
            "actionLabel": "Status",
        },
    ]

    job_rows = [
        {
            "id": "training",
            "state": "warn" if any(row["id"] in {"training", "models", "huggingface"} and row["state"] in {"warn", "error"} for row in disk_rows) else "ok",
            "badge": "train",
            "title": "Training and fine-tuning readiness",
            "detail": "Review training/model/cache free space before starting LoRA, dataset import, or local model creation jobs.",
            "executes": False,
            "requires_approval": True,
            "action": "open-training-preflight",
            "actionLabel": "Training",
        },
        {
            "id": "code",
            "state": "warn" if any(row["id"] in {"code-workspaces", "tmp"} and row["state"] in {"warn", "error"} for row in disk_rows) else "ok",
            "badge": "code",
            "title": "Code Workspace job readiness",
            "detail": "Check workspace and temp headroom before running tests, builds, snapshots, or agent edit loops.",
            "executes": False,
            "requires_approval": True,
            "action": "open-code-preflight",
            "actionLabel": "Code",
        },
        {
            "id": "backup",
            "state": "warn" if any(row["id"] in {"data", "logs"} and row["state"] in {"warn", "error"} for row in disk_rows) else "ok",
            "badge": "bak",
            "title": "Backup/export readiness",
            "detail": "Confirm data/log storage before encrypted exports, full snapshots, restore drills, or large media imports.",
            "executes": False,
            "requires_approval": True,
            "action": "open-backup-preflight",
            "actionLabel": "Backup",
        },
        {
            "id": "models",
            "state": "warn" if any(row["id"] in {"models", "huggingface", "ollama"} and row["state"] in {"warn", "error"} for row in disk_rows) else "ok",
            "badge": "model",
            "title": "Local model cache readiness",
            "detail": "Model downloads/imports remain explicit; this only reports local cache roots and headroom.",
            "executes": False,
            "requires_approval": True,
            "action": "open-model-preflight",
            "actionLabel": "Models",
        },
    ]

    guard_rows = [
        {
            "state": "ok",
            "badge": "read",
            "title": "Read-only resource probes",
            "detail": "This plan uses Python filesystem and OS counters only; it does not run host commands or inspect file contents.",
        },
        {
            "state": "ok",
            "badge": "ask",
            "title": "Heavy jobs stay approval-gated",
            "detail": "Training, code execution, backup, model import/download, service repair, and cleanup remain separate user-approved actions.",
        },
        {
            "state": "ok",
            "badge": "net",
            "title": "No network probes",
            "detail": "The plan does not call remote services, pull images, download models, or contact package registries.",
        },
    ]
    api_actions = [
        _api_action("GET", "/api/operator/runtime-plan", "Read runtime resource plan"),
        _api_action("GET", "/api/runtime", "Read basic runtime status"),
        _api_action("GET", "/api/offline-control/status", "Read offline/storage status"),
        _api_action("GET", "/api/operator/services", "Read local service probes"),
        _api_action("POST", "/api/tasks/{task_id}/run", "Run scheduled task after approval", writes=True, requires_approval=True),
        _api_action("POST", "/api/training/finetune/jobs", "Start fine-tune job after approval", writes=True, requires_approval=True),
        _api_action("POST", "/api/code-workspaces/{workspace_id}/run", "Run workspace command after approval", writes=True, requires_approval=True),
        _api_action("POST", "/api/offline-control/models/import", "Import local model after review", writes=True, requires_approval=True),
    ]
    evidence_rows = [
        {"label": "Data root", "path": str(data), "detail": "Primary app state and local operator data"},
        {"label": "Logs root", "path": str(logs), "detail": "Application and audit logs"},
        {"label": "Runtime", "path": "/api/runtime", "detail": "Docker/native and model endpoint boundary"},
        {"label": "Offline status", "path": "/api/offline-control/status", "detail": "Storage mode, network posture, and readiness"},
    ]
    return {
        "mode": "read-only-runtime-resource-plan",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner": owner,
        "summary": {
            "state": runtime_state,
            "docker_like": in_docker,
            "offline": offline_mode(),
            "root_count": len(disk_rows),
            "existing_root_count": sum(1 for row in disk_rows if row["exists"]),
            "missing_required_count": len(missing_required),
            "low_space_root_count": len(low_space),
            "memory_warning_count": len(memory_warnings),
            "starts_jobs": False,
            "runs_shell": False,
            "reads_file_contents": False,
            "writes_files": False,
            "deletes_files": False,
            "downloads_models": False,
            "pulls_images": False,
            "uses_network": False,
            "requires_heavy_job_approval": True,
        },
        "machine_rows": machine_rows,
        "resource_rows": resource_rows,
        "disk_rows": disk_rows,
        "job_rows": job_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "runtime": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "docker_like": in_docker,
            "offline": offline_mode(),
            "tmpfs_size": os.getenv("CLEVERLY_TMPFS_SIZE") or "",
            "pids_limit": os.getenv("CLEVERLY_PIDS_LIMIT") or "",
        },
        "approval": {
            "required": True,
            "gate": "Heavy Job Approval",
            "policy": (
                "This endpoint only reads local runtime resource counters and path metadata. It does not run shell "
                "commands, read file contents, write files, delete files, start jobs, download models, pull images, "
                "restart services, or use network access."
            ),
        },
        "paths": {
            "data_root": str(data),
            "logs_root": str(logs),
            "activity": "data/operator_activity.json",
            "training": "data/training",
            "models": "data/models",
            "code_workspaces": "data/code-workspaces",
        },
    }
