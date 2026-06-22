"""Read-only backup verification planning for the Cleverly operator console."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

MAX_AUDIT_ROWS = 12

ENCRYPTED_EXPORT_SECTIONS = [
    ("memories", "Memory records"),
    ("presets", "Preset library"),
    ("skills", "Reusable skills"),
    ("settings", "Settings"),
    ("features", "Feature flags"),
    ("preferences", "User preferences"),
]

FULL_SNAPSHOT_ITEMS = [
    ("app-db", "data/app.db", "SQLite database: notes, tasks, task runs, calendar, documents, and app tables"),
    ("auth", "data/auth.json", "Users, password hashes, privileges, and auth settings"),
    ("sessions", "data/sessions.json", "Session metadata cache"),
    ("operator-ledger", "data/operator_activity.json", "Command Center activity, logs, retries, and recovery evidence"),
    ("personal-docs", "data/personal_docs", "Local documents and document indexes"),
    ("uploads", "data/uploads", "Uploaded working files"),
    ("gallery", "data/gallery", "Generated and uploaded media"),
    ("research", "data/deep_research", "Deep Research outputs and reports"),
    ("code", "data/code-workspaces", "Code Workspace imports, snapshots, worker queue, and exports"),
    ("training", "data/training", "Training Lab datasets, jobs, adapters, and fine-tune files"),
    ("models", "data/models", "Local model artifacts"),
    ("logs", "logs", "Application logs"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _data_root() -> Path:
    return Path(os.getenv("DATA_DIR") or DATA_DIR)


def _logs_root(data_root: Path) -> Path:
    return Path(os.getenv("LOG_DIR") or data_root.parent / "logs")


def _resolve_app_path(label_path: str, data_root: Path, logs_root: Path) -> Path:
    if label_path == "logs":
        return logs_root
    if label_path.startswith("data/"):
        return data_root / label_path.removeprefix("data/")
    return data_root / label_path


def _path_state(path: Path) -> str:
    try:
        if path.exists():
            return "ok"
    except OSError:
        return "warn"
    return "warn"


def _path_detail(path: Path, description: str) -> str:
    try:
        if path.is_file():
            return f"{description}; file present"
        if path.is_dir():
            return f"{description}; directory present"
        return f"{description}; not found in current runtime"
    except OSError as exc:
        return f"{description}; could not inspect path: {_trim(exc, 160)}"


def _audit_timestamp(record: dict[str, Any]) -> float:
    value = record.get("timestamp") or record.get("created_at") or record.get("updated_at")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _load_backup_audit(owner: str, data_root: Path) -> list[dict[str, Any]]:
    path = data_root / "audit" / "local-audit.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines[-200:]):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").lower()
        detail = json.dumps(item.get("detail") or {}, default=str).lower()
        user = str(item.get("user") or "local")
        if owner and owner != "local" and user not in {owner, ""}:
            continue
        if "backup" in action or "restore" in action or ("export" in action and "backup" in detail):
            rows.append(item)
        if len(rows) >= MAX_AUDIT_ROWS:
            break
    rows.sort(key=_audit_timestamp, reverse=True)
    return rows


def _audit_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in records[:MAX_AUDIT_ROWS]:
        action = _trim(item.get("action") or "backup event", 120)
        timestamp = _trim(item.get("timestamp") or item.get("created_at") or "", 120)
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        filename = detail.get("filename") or detail.get("path") or detail.get("format") or ""
        rows.append({
            "id": _trim(item.get("id") or action, 160),
            "state": "ok",
            "badge": "audit",
            "title": action,
            "detail": f"{filename or 'local audit record'} at {timestamp or 'unknown time'}",
            "timestamp": timestamp,
        })
    return rows


def _protected_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "state": "ok",
            "badge": key[:4],
            "title": label,
            "detail": "Included in password-encrypted app export payload",
            "action": "open-backups",
            "actionLabel": "Export",
        }
        for key, label in ENCRYPTED_EXPORT_SECTIONS
    ]


def _snapshot_rows(data_root: Path, logs_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_id, label_path, description in FULL_SNAPSHOT_ITEMS:
        path = _resolve_app_path(label_path, data_root, logs_root)
        rows.append({
            "id": row_id,
            "state": _path_state(path),
            "badge": "snap",
            "title": label_path,
            "detail": _path_detail(path, description),
            "path": str(path),
            "required_for_full_backup": True,
            "action": "open-local-data-map",
            "actionLabel": "Data",
        })
    return rows


def _sequence_rows(audit_count: int, missing_count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": "scope",
            "state": "ok",
            "badge": "1",
            "title": "Choose backup scope",
            "detail": "Use encrypted export for portable app settings/memory/skills and full snapshots for runtime files, database, media, workspaces, and models.",
            "risk": "read-only",
            "approval_required": False,
            "executes": False,
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            "id": "encrypted-export",
            "state": "warn",
            "badge": "2",
            "title": "Create encrypted app export",
            "detail": "Requires a backup password and explicit user action in Offline Control Backups.",
            "risk": "approval-required",
            "approval_required": True,
            "executes": False,
            "api": "/api/backup/encrypted/export",
            "action": "request-backup-export",
            "actionLabel": "Ask",
        },
        {
            "id": "full-snapshot",
            "state": "warn" if missing_count else "ok",
            "badge": "3",
            "title": "Create full data snapshot",
            "detail": "Use scripts/cleverly-backup snapshot or an approved Docker-volume backup outside this read-only endpoint.",
            "risk": "approval-required",
            "approval_required": True,
            "executes": False,
            "command": "python scripts/cleverly-backup snapshot --pretty",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            "id": "verify-tarball",
            "state": "ok",
            "badge": "4",
            "title": "Verify full snapshot tarball",
            "detail": "Run tarball verification without extracting or overwriting live data.",
            "risk": "read-only-after-user-selects-file",
            "approval_required": True,
            "executes": False,
            "command": "python scripts/cleverly-backup verify PATH --pretty",
            "action": "open-backups",
            "actionLabel": "Verify",
        },
        {
            "id": "restore-drill",
            "state": "ok",
            "badge": "5",
            "title": "Run encrypted restore drill",
            "detail": "Use dry-run restore to decrypt and summarize a backup without importing data.",
            "risk": "approval-required",
            "approval_required": True,
            "executes": False,
            "api": "/api/backup/encrypted/import dry_run=true",
            "action": "open-backups",
            "actionLabel": "Test",
        },
        {
            "id": "record-evidence",
            "state": "ok" if audit_count else "loading",
            "badge": "6",
            "title": "Record evidence",
            "detail": "Keep export filename, snapshot path, verify output, restore-drill summary, storage location, and password custody note.",
            "risk": "read-only",
            "approval_required": False,
            "executes": False,
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
    ]


def _evidence_rows(audit_rows: list[dict[str, Any]], missing_count: int) -> list[dict[str, Any]]:
    rows = [
        {
            "id": "audit",
            "state": "ok" if audit_rows else "loading",
            "badge": "audit",
            "title": "Backup audit events",
            "detail": f"{len(audit_rows)} recent backup/export/restore event{'s' if len(audit_rows) != 1 else ''} visible",
            "action": "open-offline",
            "actionLabel": "Audit",
        },
        {
            "id": "snapshot-verify",
            "state": "warn" if missing_count else "ok",
            "badge": "snap",
            "title": "Full snapshot verification",
            "detail": "Store `scripts/cleverly-backup verify` output with the snapshot filename and storage location.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            "id": "restore-drill",
            "state": "ok",
            "badge": "test",
            "title": "Encrypted restore drill",
            "detail": "Dry-run restore confirms the password and recognized sections without importing live data.",
            "action": "open-backups",
            "actionLabel": "Test",
        },
    ]
    return rows + audit_rows[:4]


def run_operator_backup_plan(
    owner: str = "local",
    *,
    backup_audit: list[dict[str, Any]] | None = None,
    data_root: str | Path | None = None,
    logs_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return a read-only backup verification plan."""
    owner = owner or "local"
    data_path = Path(data_root) if data_root is not None else _data_root()
    logs_path = Path(logs_root) if logs_root is not None else _logs_root(data_path)
    audit_records = backup_audit if backup_audit is not None else _load_backup_audit(owner, data_path)
    protected_rows = _protected_rows()
    snapshot_rows = _snapshot_rows(data_path, logs_path)
    missing_rows = [row for row in snapshot_rows if row["state"] != "ok"]
    audit_rows = _audit_rows(audit_records)
    sequence_rows = _sequence_rows(len(audit_rows), len(missing_rows))
    evidence_rows = _evidence_rows(audit_rows, len(missing_rows))
    state = "warn" if missing_rows else ("ok" if audit_rows else "loading")
    return {
        "mode": "read-only-backup-verify-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "encrypted_export_sections": len(protected_rows),
            "full_snapshot_items": len(snapshot_rows),
            "missing_snapshot_items": len(missing_rows),
            "audit_count": len(audit_rows),
            "creates_backup": False,
            "restores_data": False,
            "runs_shell": False,
            "requires_export_approval": True,
            "next_action": "Run encrypted export and restore drill from Offline Control, then verify a full snapshot tarball." if not missing_rows else "Review missing data paths before claiming full snapshot coverage.",
        },
        "protected_rows": protected_rows,
        "snapshot_rows": snapshot_rows,
        "sequence_rows": sequence_rows,
        "evidence_rows": evidence_rows,
        "host_commands": [
            {
                "id": "snapshot",
                "label": "Create full data snapshot",
                "risk": "approval-required",
                "command": "python scripts/cleverly-backup snapshot --pretty",
                "executes": False,
                "requires_approval": True,
            },
            {
                "id": "verify",
                "label": "Verify full data snapshot",
                "risk": "read-only-after-user-selects-file",
                "command": "python scripts/cleverly-backup verify PATH --pretty",
                "executes": False,
                "requires_approval": True,
            },
        ],
        "api_actions": [
            {
                "id": "encrypted-export",
                "method": "POST",
                "path": "/api/backup/encrypted/export",
                "risk": "approval-required",
                "executes": False,
                "requires_password": True,
            },
            {
                "id": "test-restore",
                "method": "POST",
                "path": "/api/backup/encrypted/import",
                "risk": "dry-run-read-only-after-user-file",
                "executes": False,
                "dry_run": True,
                "requires_password": True,
            },
        ],
        "approval": {
            "required": True,
            "gate": "Request Backup Export",
            "policy": "This endpoint only prepares backup evidence. It does not export, import, restore, delete, upload, move data, create host snapshots, run shell commands, or read backup passwords.",
            "disallowed_by_default": [
                "restore",
                "delete data",
                "upload backup",
                "move host files",
                "read backup password",
            ],
        },
        "paths": {
            "data": str(data_path),
            "logs": str(logs_path),
            "audit": str(data_path / "audit" / "local-audit.jsonl"),
            "backup_script": "scripts/cleverly-backup",
            "encrypted_export": "/api/backup/encrypted/export",
            "encrypted_restore_drill": "/api/backup/encrypted/import?dry_run=true",
        },
    }
