"""Read-only local data boundary plan for the Cleverly operator console."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, PERSONAL_DIR, UPLOAD_DIR
from src.settings import offline_mode


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _data_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else Path(os.getenv("DATA_DIR") or DATA_DIR)


def _logs_root(data_root: Path, root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else Path(os.getenv("LOG_DIR") or data_root.parent / "logs")


def _path_row(
    row_id: str,
    title: str,
    path: Path,
    detail: str,
    *,
    required: bool = False,
    sensitive: bool = False,
    backup_required: bool = True,
    category: str = "data",
) -> dict[str, Any]:
    try:
        exists = path.exists()
    except OSError:
        exists = False
    state = "ok" if exists else ("error" if required else "loading")
    return {
        "id": row_id,
        "state": state,
        "badge": "key" if sensitive else category[:5],
        "title": title,
        "detail": f"{detail}; path={path}; {'visible' if exists else 'not visible'}; contents not read",
        "path": str(path),
        "exists": exists,
        "required": required,
        "sensitive": sensitive,
        "backup_required": backup_required,
        "category": category,
        "executes": False,
        "reads_file_contents": False,
        "reads_secret_values": False,
        "writes_files": False,
        "deletes_files": False,
        "uses_network": False,
        "action": "open-backup-preflight" if backup_required else "open-local-data-map",
        "actionLabel": "Backup" if backup_required else "Data",
    }


def _scope_rows(data_root: Path, logs_root: Path) -> list[dict[str, Any]]:
    upload_root = Path(os.getenv("UPLOAD_DIR") or UPLOAD_DIR)
    personal_root = Path(os.getenv("PERSONAL_DIR") or PERSONAL_DIR)
    return [
        _path_row("data-root", "Primary app data root", data_root, "SQLite, settings, memory, tasks, files, training, research, and app state", required=True),
        _path_row("logs-root", "Application logs root", logs_root, "Application logs, job output, and audit evidence", required=True, category="logs"),
        _path_row("app-db", "SQLite app database", data_root / "app.db", "Main app database", sensitive=True),
        _path_row("auth-store", "Auth store", data_root / "auth.json", "Users, password hashes, privileges, and auth settings", sensitive=True),
        _path_row("settings-store", "Settings store", data_root / "settings.json", "Local app settings and model/provider choices", sensitive=True),
        _path_row("feature-store", "Feature flag store", data_root / "features.json", "Local feature and network capability switches", category="gate"),
        _path_row("session-store", "Session cache", data_root / "sessions.json", "Local session metadata", sensitive=True),
        _path_row("memory-store", "Memory and profile store", data_root / "memory.json", "Saved memories and operator profile context", category="mem"),
        _path_row("tasks-calendar", "Tasks and calendar stores", data_root / "tasks", "Tasks, task runs, reminders, and calendar evidence", category="task"),
        _path_row("uploads", "Upload workspace", upload_root, "Chat, document, and working upload files", category="file"),
        _path_row("personal-docs", "Personal documents", personal_root, "Local document library and index metadata", category="docs"),
        _path_row("gallery", "Gallery media", data_root / "gallery", "Generated and saved local media", category="media"),
        _path_row("research", "Deep Research archive", data_root / "deep_research", "Saved research reports and source evidence", category="find"),
        _path_row("code-workspaces", "Code workspaces", data_root / "code-workspaces", "Imported repos, snapshots, worker queue, and outputs", category="code"),
        _path_row("training", "Training Lab data", data_root / "training", "Datasets, jobs, adapters, and training artifacts", category="train"),
        _path_row("models", "Local model artifacts", data_root / "models", "Imported or created local model files", category="model"),
        _path_row("backups", "Backup exports", data_root / "backups", "Encrypted app exports and restore-drill evidence", category="bak"),
        _path_row("cache", "Runtime cache", data_root / "cache", "Runtime, embedding, package, and helper caches", backup_required=False, category="cache"),
        _path_row("vault", "Vault config", data_root / "vault.json", "Vault session/config metadata", sensitive=True),
        _path_row("app-key", "Local encryption key", data_root / ".app_key", "Local encryption key material", sensitive=True),
        _path_row("ssh", "SSH material", data_root / "ssh", "Cookbook SSH identity mirror when host data is enabled", sensitive=True),
    ]


def _alert_rows(scope_rows: list[dict[str, Any]], *, offline: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in [item for item in scope_rows if item.get("required") and not item.get("exists")]:
        rows.append({
            "id": f"missing-data-scope-{row['id']}",
            "state": "error",
            "badge": "path",
            "title": f"Required local data scope missing: {row['title']}",
            "detail": f"{row['path']} is not visible; verify Docker volume or host-data mount before data operations.",
            "action": "open-machine-preflight",
            "actionLabel": "Runtime",
            "requires_approval": True,
            "uses_network": False,
        })
    visible_sensitive = [row for row in scope_rows if row.get("sensitive") and row.get("exists")]
    if visible_sensitive:
        rows.append({
            "id": "sensitive-local-stores-visible",
            "state": "warn",
            "badge": "key",
            "title": "Sensitive local stores visible",
            "detail": f"{len(visible_sensitive)} sensitive local path(s) are present; values remain masked and require Trust/Backup review.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
            "uses_network": False,
        })
    if not offline:
        rows.append({
            "id": "data-network-posture-enabled",
            "state": "warn",
            "badge": "net",
            "title": "Network posture enabled",
            "detail": "Network mode is enabled or undeclared; review Offline Control before syncing, research, model, or webhook work.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "requires_approval": False,
            "uses_network": False,
        })
    rows.append({
        "id": "local-data-write-gates",
        "state": "warn",
        "badge": "ask",
        "title": "Local data writes require explicit action",
        "detail": "Imports, uploads, exports, restores, file writes, deletes, credential changes, indexing, and backup creation stay outside this read-only map.",
        "action": "open-trust-controls",
        "actionLabel": "Trust",
        "requires_approval": True,
        "uses_network": False,
    })
    rows.append({
        "id": "backup-before-risky-data-work",
        "state": "warn",
        "badge": "bak",
        "title": "Back up before risky local data work",
        "detail": "Use Backup Operations before destructive file, model, training, restore, gallery, or workspace actions.",
        "action": "open-backup-preflight",
        "actionLabel": "Backup",
        "requires_approval": True,
        "uses_network": False,
    })
    return rows[:12]


def _entry_rows(*, required_ready: bool, offline: bool) -> list[dict[str, Any]]:
    state = "ok" if required_ready and offline else "warn"
    common = {
        "command_id": "open-local-data-map",
        "backup_command_id": "open-backup-preflight",
        "trust_command_id": "open-trust-controls",
        "offline_command_id": "open-offline",
        "data_api": "/api/operator/data-plan",
        "file_ops_api": "/api/operator/file-ops-plan",
        "credentials_api": "/api/operator/credentials-plan",
        "memory_api": "/api/operator/memory-plan",
        "backup_api": "/api/operator/backup-plan",
        "requires_approval": True,
        "executes": False,
        "reads_file_contents": False,
        "reads_secret_values": False,
        "writes_files": False,
        "deletes_files": False,
        "exports_data": False,
        "restores_data": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "data-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard Local Data Map route",
            "detail": "The dashboard opens local data boundary evidence before file, memory, credential, backup, model, or workspace data work.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            **common,
            "id": "data-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed local data request route",
            "detail": "Typed local data, file, backup, credential, and storage requests route to this read-only map first.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            **common,
            "id": "data-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette Local Data Map route",
            "detail": "The command palette exposes Local Data Map, Backup, Offline, Memory, and Trust review without starting work.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "data-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice local data route",
            "detail": "Voice mode can open the data boundary map without reading files, speaking secrets, or changing local state.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "data-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow local data handoff",
            "detail": "Automation handoffs can review local data scope, backup coverage, and trust gates before any workflow writes data.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _handoff_row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    *,
    target_api: str,
    approval_api: str = "",
    requires_approval: bool = False,
    network_after_approval: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state if state in {"ok", "warn", "error", "loading"} else "warn",
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "target_api": target_api,
        "approval_api": approval_api,
        "requires_approval": requires_approval,
        "network_after_approval": network_after_approval,
        "executes": False,
        "reads_file_contents": False,
        "reads_secret_values": False,
        "writes_files": False,
        "deletes_files": False,
        "exports_data": False,
        "restores_data": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    required_ready: bool,
    sensitive_count: int,
    backup_required_count: int,
    visible_backup_count: int,
    offline: bool,
) -> list[dict[str, Any]]:
    return [
        _handoff_row(
            "data-file-ops-handoff",
            "ok" if required_ready else "warn",
            "file",
            "File operation boundary handoff",
            "File operations route to metadata-only file roots, write/delete gates, and owner approval before reading, copying, moving, uploading, or deleting files.",
            "open-local-data-map",
            "Files",
            target_api="/api/operator/file-ops-plan",
            approval_api="/api/files/upload",
            requires_approval=True,
        ),
        _handoff_row(
            "data-credential-posture-handoff",
            "warn" if sensitive_count else "ok",
            "key",
            "Credential posture handoff",
            f"{sensitive_count} sensitive local scope(s) are mapped; credential values stay masked and vault/settings writes require review.",
            "open-local-data-map",
            "Credentials",
            target_api="/api/operator/credentials-plan",
            approval_api="/api/vault/unlock",
            requires_approval=bool(sensitive_count),
        ),
        _handoff_row(
            "data-memory-profile-handoff",
            "ok",
            "mem",
            "Memory and profile handoff",
            "Memory/profile evidence routes through unified memory review before recall, extraction, import, or model-assisted memory writes.",
            "open-memory-profile",
            "Memory",
            target_api="/api/operator/memory-plan",
            approval_api="/api/memory",
            requires_approval=True,
        ),
        _handoff_row(
            "data-backup-coverage-handoff",
            "ok" if backup_required_count and visible_backup_count >= backup_required_count else "warn",
            "bak",
            "Backup coverage handoff",
            f"{visible_backup_count}/{backup_required_count} backup-required local scope(s) are visible before export, restore, delete, model, training, or workspace actions.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_api="/api/offline-control/backup/export",
            requires_approval=True,
        ),
        _handoff_row(
            "data-offline-policy-handoff",
            "ok" if offline else "warn",
            "net",
            "Offline and network policy handoff",
            "Offline Control reviews network posture before sync, webhook, remote research, model endpoint, notification, or upload workflows.",
            "open-offline",
            "Offline",
            target_api="/api/offline-control/status",
            requires_approval=not offline,
            network_after_approval=not offline,
        ),
        _handoff_row(
            "data-activity-evidence-handoff",
            "ok",
            "log",
            "Activity evidence handoff",
            "Approved data actions should leave status, result, logs, retry, and rollback evidence in the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity-plan",
        ),
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


def run_operator_data_plan(
    owner: str = "local",
    *,
    data_root: str | Path | None = None,
    logs_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return a read-only Local Data Map plan without reading file contents."""
    owner = owner or "local"
    data = _data_root(data_root)
    logs = _logs_root(data, logs_root)
    offline = offline_mode()
    scope_rows = _scope_rows(data, logs)
    required_ready = all(row["exists"] for row in scope_rows if row.get("required"))
    alert_rows = _alert_rows(scope_rows, offline=offline)
    entry_rows = _entry_rows(required_ready=required_ready, offline=offline)
    sensitive_rows = [row for row in scope_rows if row.get("sensitive")]
    backup_rows = [row for row in scope_rows if row.get("backup_required")]
    handoff_rows = _handoff_rows(
        required_ready=required_ready,
        sensitive_count=len(sensitive_rows),
        backup_required_count=len(backup_rows),
        visible_backup_count=len([row for row in backup_rows if row.get("exists")]),
        offline=offline,
    )
    critical = [row for row in alert_rows if row.get("state") == "error"]
    return {
        "mode": "read-only-local-data-map-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "error" if critical else ("warn" if alert_rows else "ok"),
            "scope_count": len(scope_rows),
            "visible_scope_count": len([row for row in scope_rows if row.get("exists")]),
            "required_scope_count": len([row for row in scope_rows if row.get("required")]),
            "required_scope_ready_count": len([row for row in scope_rows if row.get("required") and row.get("exists")]),
            "sensitive_scope_count": len(sensitive_rows),
            "visible_sensitive_scope_count": len([row for row in sensitive_rows if row.get("exists")]),
            "backup_required_scope_count": len(backup_rows),
            "data_alert_count": len(alert_rows),
            "critical_data_alert_count": len(critical),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "reads_file_contents": False,
            "reads_secret_values": False,
            "writes_files": False,
            "deletes_files": False,
            "exports_data": False,
            "restores_data": False,
            "runs_shell": False,
            "uses_network": False,
        },
        "scope_rows": scope_rows,
        "sensitive_rows": sensitive_rows,
        "backup_scope_rows": backup_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": [
            _api_action("/api/operator/data-plan", "Read Local Data Map plan"),
            _api_action("/api/operator/file-ops-plan", "Read file operation boundaries"),
            _api_action("/api/operator/credentials-plan", "Read masked credential posture"),
            _api_action("/api/operator/memory-plan", "Read memory/profile coverage"),
            _api_action("/api/operator/backup-plan", "Read backup verification plan"),
            _api_action("/api/offline-control/status", "Read offline/network posture"),
            _api_action("/api/files/upload", "Upload file after explicit action", method="POST", writes=True, requires_approval=True),
            _api_action("/api/offline-control/backup/export", "Create encrypted backup after explicit action", method="POST", writes=True, requires_approval=True),
        ],
        "guard_rows": [
            {
                "state": "ok",
                "badge": "read",
                "title": "Path metadata only",
                "detail": "The data plan checks path visibility and categories only; it does not read file contents or secret values.",
            },
            {
                "state": "ok",
                "badge": "ask",
                "title": "Writes stay outside the map",
                "detail": "Uploads, imports, exports, restores, indexing, settings changes, and deletes remain explicit UI/API actions.",
            },
            {
                "state": "ok",
                "badge": "net",
                "title": "No network probes",
                "detail": "The data plan does not sync, upload, fetch, call webhooks, query providers, or use network access.",
            },
        ],
        "paths": {
            "data_root": str(data),
            "logs_root": str(logs),
            "personal_dir": str(Path(os.getenv("PERSONAL_DIR") or PERSONAL_DIR)),
            "upload_dir": str(Path(os.getenv("UPLOAD_DIR") or UPLOAD_DIR)),
        },
        "approval": {
            "required": True,
            "gate": "Local Data Review",
            "policy": (
                "This endpoint only reads path metadata and local boundary configuration. It does not read file "
                "contents, return secret values, write files, delete files, export data, restore data, run shell "
                "commands, or use network access."
            ),
        },
    }
