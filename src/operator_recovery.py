"""Read-only recovery, rollback, and retry evidence for Cleverly."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _load_records(path: str | Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        source = data["records"]
    elif isinstance(data, list):
        source = data
    else:
        source = []
    return [item for item in source if isinstance(item, dict)]


def _record_text(record: dict[str, Any]) -> str:
    values = [
        record.get("id"),
        record.get("command_id"),
        record.get("title"),
        record.get("category"),
        record.get("status"),
        record.get("detail"),
        record.get("result"),
        record.get("error"),
        record.get("recovery_hint"),
        record.get("rollback_hint"),
    ]
    return " ".join(_trim(value, 240).lower() for value in values if value)


def _is_retryable(record: dict[str, Any]) -> bool:
    command_id = _trim(record.get("command_id"), 200)
    return bool(command_id and command_id != "chat-command")


def _is_failure(record: dict[str, Any]) -> bool:
    status = _trim(record.get("status") or record.get("state"), 120).lower()
    text = _record_text(record)
    return any(term in status or term in text for term in ("fail", "failed", "error", "exception", "blocked"))


def _needs_recovery(record: dict[str, Any]) -> bool:
    text = _record_text(record)
    return _is_failure(record) or any(
        term in text
        for term in (
            "backup",
            "build",
            "code",
            "container",
            "delete",
            "docker",
            "file",
            "fix",
            "model",
            "repair",
            "restore",
            "retry",
            "shell",
            "snapshot",
            "train",
            "workspace",
        )
    )


def _rollback_ready(record: dict[str, Any]) -> bool:
    return bool(_trim(record.get("rollback_hint")) or _trim(record.get("recovery_hint")) or not _needs_recovery(record))


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _recovery_rows(records: list[dict[str, Any]], data_root: Path, logs_root: Path) -> list[dict[str, Any]]:
    retryable = [row for row in records if _is_retryable(row)]
    failures = [row for row in records if _is_failure(row)]
    recovery_needed = [row for row in records if _needs_recovery(row)]
    rollback_ready = [row for row in recovery_needed if _rollback_ready(row)]
    code_root = data_root / "code-workspaces"
    backup_root = data_root / "backups"
    return [
        {
            "id": "activity-retry-ledger",
            "state": "ok" if retryable else "warn",
            "badge": "retry",
            "title": "Activity retry ledger",
            "detail": f"{len(retryable)} retryable command record(s); retries create new activity records through the current trust policy.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "requires_approval": True,
        },
        {
            "id": "failure-review",
            "state": "error" if failures else "ok",
            "badge": "fail",
            "title": "Failure review queue",
            "detail": f"{len(failures)} failed or blocked activity record(s) require inspection before retry or cleanup.",
            "action": "open-operations-queue",
            "actionLabel": "Queue",
            "requires_approval": False,
        },
        {
            "id": "rollback-evidence",
            "state": "ok" if len(rollback_ready) >= len(recovery_needed) else ("warn" if recovery_needed else "loading"),
            "badge": "roll",
            "title": "Rollback and recovery hints",
            "detail": f"{len(rollback_ready)}/{len(recovery_needed)} recovery-sensitive record(s) include rollback or recovery guidance.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "requires_approval": False,
        },
        {
            "id": "code-snapshots",
            "state": "ok" if _path_exists(code_root) else "loading",
            "badge": "snap",
            "title": "Code Workspace snapshot boundary",
            "detail": f"{code_root} {'is visible' if _path_exists(code_root) else 'is not visible'}; snapshot/restore actions remain in Code Workspace.",
            "action": "open-code-preflight",
            "actionLabel": "Code",
            "requires_approval": True,
        },
        {
            "id": "backup-restore-drill",
            "state": "ok" if _path_exists(backup_root) else "warn",
            "badge": "bak",
            "title": "Backup and restore-drill boundary",
            "detail": f"{backup_root} {'is visible' if _path_exists(backup_root) else 'is not visible'}; exports and restore drills require explicit actions.",
            "action": "open-backup-preflight",
            "actionLabel": "Backup",
            "requires_approval": True,
        },
        {
            "id": "container-repair-boundary",
            "state": "warn" if failures else "ok",
            "badge": "fix",
            "title": "Container repair boundary",
            "detail": "Service restarts, Docker commands, image pulls, and repairs remain in the approval-gated repair plan.",
            "action": "open-container-repair-plan",
            "actionLabel": "Repair",
            "requires_approval": True,
        },
        {
            "id": "local-data-boundary",
            "state": "ok" if _path_exists(data_root) and _path_exists(logs_root) else "warn",
            "badge": "data",
            "title": "Local data rollback boundary",
            "detail": f"data={data_root}; logs={logs_root}; Docker volumes are storage isolation, not automatic rollback.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
            "requires_approval": True,
        },
        {
            "id": "trust-gates",
            "state": "ok",
            "badge": "ask",
            "title": "Recovery actions stay permissioned",
            "detail": "Retry, restore, repair, delete, export, snapshot, and cleanup actions must pass the owning tool and trust gate.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        },
    ]


def _alert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for row in rows:
        if row.get("state") not in {"error", "warn"}:
            continue
        alerts.append({
            "id": f"recovery-alert-{row['id']}",
            "state": row.get("state") or "warn",
            "badge": row.get("badge") or "recover",
            "title": row.get("title") or "Recovery alert",
            "detail": row.get("detail") or "Recovery boundary needs review.",
            "action": row.get("action") or "open-recovery-map",
            "actionLabel": row.get("actionLabel") or "Review",
            "requires_approval": row.get("requires_approval") is True,
        })
    return alerts[:12]


def _entry_rows(recovery_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready = any(row.get("state") in {"ok", "warn", "error"} for row in recovery_rows)
    state = "ok" if ready else "warn"
    common = {
        "command_id": "open-recovery-map",
        "activity_command_id": "open-activity-preflight",
        "backup_command_id": "open-backup-preflight",
        "trust_command_id": "open-trust-controls",
        "recovery_api": "/api/operator/recovery-plan",
        "requires_approval": True,
        "executes": False,
        "retries_commands": False,
        "restores_data": False,
        "repairs_services": False,
        "deletes_files": False,
        "exports_data": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "recovery-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard Recovery Map route",
            "detail": "Dashboard recovery review opens read-only retry, rollback, backup, repair, and data boundary evidence.",
            "action": "open-recovery-map",
            "actionLabel": "Recovery",
        },
        {
            **common,
            "id": "recovery-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed recovery request route",
            "detail": "Typed retry, rollback, restore, repair, or recovery requests route to Recovery Map before work starts.",
            "action": "open-recovery-map",
            "actionLabel": "Recovery",
        },
        {
            **common,
            "id": "recovery-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette Recovery Map route",
            "detail": "The command palette exposes Recovery Map without retrying, restoring, deleting, or repairing anything.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "recovery-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice recovery route",
            "detail": "Voice mode can open Recovery Map without executing recovery actions or speaking sensitive logs.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "recovery-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow recovery handoff",
            "detail": "Automation handoffs can review recovery readiness before loop retry, repair, cleanup, or restore workflows.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _api_action(path: str, title: str, *, method: str = "GET", writes: bool = False, requires_approval: bool = False) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": requires_approval,
        "uses_network": False,
    }


def run_operator_recovery_plan(
    owner: str = "local",
    *,
    records: list[dict[str, Any]] | None = None,
    activity_path: str | Path | None = None,
    data_root: str | Path | None = None,
    logs_root: str | Path | None = None,
) -> dict[str, Any]:
    owner = owner or "local"
    data = Path(data_root) if data_root is not None else Path(DATA_DIR)
    logs = Path(logs_root) if logs_root is not None else data.parent / "logs"
    activity_file = Path(activity_path) if activity_path is not None else data / "operator_activity.json"
    source_records = records if isinstance(records, list) else _load_records(activity_file)
    owner_records = [
        record for record in source_records
        if owner == "local" or str(record.get("owner") or owner) in {owner, "", "local"}
    ]
    recovery_rows = _recovery_rows(owner_records, data, logs)
    alert_rows = _alert_rows(recovery_rows)
    entry_rows = _entry_rows(recovery_rows)
    retryable = [row for row in owner_records if _is_retryable(row)]
    failures = [row for row in owner_records if _is_failure(row)]
    recovery_needed = [row for row in owner_records if _needs_recovery(row)]
    rollback_ready = [row for row in recovery_needed if _rollback_ready(row)]
    critical = [row for row in alert_rows if row.get("state") == "error"]
    return {
        "mode": "read-only-recovery-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "error" if critical else ("warn" if alert_rows else "ok"),
            "record_count": len(owner_records),
            "retryable_count": len(retryable),
            "failure_count": len(failures),
            "recovery_needed_count": len(recovery_needed),
            "rollback_ready_count": len(rollback_ready),
            "recovery_row_count": len(recovery_rows),
            "recovery_alert_count": len(alert_rows),
            "critical_recovery_alert_count": len(critical),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "retries_commands": False,
            "restores_data": False,
            "repairs_services": False,
            "deletes_files": False,
            "exports_data": False,
            "runs_shell": False,
            "uses_network": False,
        },
        "recovery_rows": recovery_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "api_actions": [
            _api_action("/api/operator/recovery-plan", "Read Recovery Map plan"),
            _api_action("/api/operator/activity-plan", "Read activity timeline evidence"),
            _api_action("/api/operator/backup-plan", "Read backup verification plan"),
            _api_action("/api/operator/repair-plan", "Read repair plan"),
            _api_action("/api/operator/data-plan", "Read local data map plan"),
            _api_action("/api/operator/activity", "Write retry activity after explicit retry", method="POST", writes=True, requires_approval=True),
        ],
        "guard_rows": [
            {
                "state": "ok",
                "badge": "read",
                "title": "Recovery review only",
                "detail": "This endpoint reports recovery evidence and action owners only.",
            },
            {
                "state": "ok",
                "badge": "ask",
                "title": "Owning tools execute recovery",
                "detail": "Retry, restore, repair, delete, export, snapshot, and cleanup actions remain in their owning gated tools.",
            },
            {
                "state": "ok",
                "badge": "net",
                "title": "No network or shell use",
                "detail": "Recovery Plan does not call networks, run shell commands, restart services, or change files.",
            },
        ],
        "paths": {
            "activity": str(activity_file),
            "data_root": str(data),
            "logs_root": str(logs),
            "code_workspaces": str(data / "code-workspaces"),
            "backups": str(data / "backups"),
        },
        "approval": {
            "required": True,
            "gate": "Recovery owner review",
            "policy": (
                "This endpoint only reads activity and path metadata for recovery planning. It does not retry commands, "
                "restore data, repair services, delete files, export data, run shell commands, or use network access."
            ),
        },
    }
