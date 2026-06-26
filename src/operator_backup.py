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


def _entry_rows(missing_count: int, audit_count: int) -> list[dict[str, Any]]:
    coverage_detail = (
        "Backup Verification Plan can show complete snapshot path coverage before an owner-approved export."
        if not missing_count
        else f"Backup Verification Plan opens first and flags {missing_count} missing snapshot path(s) before export."
    )
    evidence_detail = (
        "Workflow handoff can include recent backup audit evidence, restore-drill checks, and snapshot verification criteria."
        if audit_count
        else "Workflow handoff stays in review mode until export, restore drill, and snapshot verification evidence are recorded."
    )
    return [
        {
            "id": "backup-dashboard-entry",
            "entry": "dashboard",
            "state": "ok" if not missing_count else "warn",
            "badge": "dash",
            "title": "Command Center dashboard",
            "detail": coverage_detail,
            "command_id": "prepare-backup",
            "approval_command_id": "request-backup-export",
            "action": "prepare-backup",
            "actionLabel": "Plan",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "backup-text-entry",
            "entry": "text",
            "state": "ok",
            "badge": "text",
            "title": "Typed operator command",
            "detail": "The phrase 'Prepare a backup and verify it' opens this read-only verification plan before any export or restore action.",
            "command_id": "prepare-backup",
            "approval_command_id": "request-backup-export",
            "action": "prepare-backup",
            "actionLabel": "Plan",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "backup-palette-entry",
            "entry": "palette",
            "state": "ok",
            "badge": "cmd",
            "title": "Global command palette",
            "detail": "The palette exposes Prepare Backup as an approval-gated safety route and separates it from the export request.",
            "command_id": "prepare-backup",
            "approval_command_id": "request-backup-export",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "backup-voice-entry",
            "entry": "voice",
            "state": "ok",
            "badge": "voice",
            "title": "Voice command mode",
            "detail": "Voice routing can land on the same Backup Verification Plan without reading passwords, exporting files, or restoring data.",
            "command_id": "prepare-backup",
            "approval_command_id": "request-backup-export",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "backup-workflow-entry",
            "entry": "workflow",
            "state": "ok" if audit_count and not missing_count else "warn",
            "badge": "flow",
            "title": "Automation workflow handoff",
            "detail": evidence_detail,
            "command_id": "prepare-backup",
            "approval_command_id": "request-backup-export",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
            "requires_approval": True,
            "executes": False,
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
    approval_command_id: str = "request-backup-export",
    requires_approval: bool = True,
    creates_backup: bool = False,
    verifies_backup: bool = False,
    restores_data: bool = False,
    reads_backup: bool = False,
    reads_password: bool = False,
    writes_files: bool = False,
    moves_files: bool = False,
    deletes_files: bool = False,
    uploads_backup: bool = False,
    writes_activity: bool = False,
    runs_shell: bool = False,
    uses_network: bool = False,
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
        "approval_command_id": approval_command_id,
        "requires_approval": requires_approval,
        "executes": False,
        "creates_backup": False,
        "verifies_backup": False,
        "restores_data": False,
        "reads_backup": False,
        "reads_password": False,
        "writes_files": False,
        "moves_files": False,
        "deletes_files": False,
        "uploads_backup": False,
        "writes_activity": False,
        "runs_shell": False,
        "uses_network": False,
        "gated_operation": {
            "creates_backup": creates_backup,
            "verifies_backup": verifies_backup,
            "restores_data": restores_data,
            "reads_backup": reads_backup,
            "reads_password": reads_password,
            "writes_files": writes_files,
            "moves_files": moves_files,
            "deletes_files": deletes_files,
            "uploads_backup": uploads_backup,
            "writes_activity": writes_activity,
            "runs_shell": runs_shell,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(missing_count: int, audit_count: int) -> list[dict[str, Any]]:
    coverage_state = "ok" if not missing_count else "warn"
    audit_state = "ok" if audit_count else "loading"
    return [
        _handoff_row(
            "backup-scope-selection-handoff",
            coverage_state,
            "scope",
            "Backup scope selection handoff",
            "Review encrypted export coverage and full snapshot paths before any backup operation is requested.",
            "open-local-data-map",
            "Data",
            target_api="/api/operator/backup-plan",
            requires_approval=False,
        ),
        _handoff_row(
            "backup-encrypted-export-handoff",
            "warn",
            "ask",
            "Encrypted export handoff",
            "Offline Control owns password entry and encrypted app export creation after explicit user approval.",
            "request-backup-export",
            "Ask",
            target_api="/api/backup/encrypted/export",
            creates_backup=True,
            reads_password=True,
            writes_files=True,
        ),
        _handoff_row(
            "backup-full-snapshot-handoff",
            coverage_state,
            "snap",
            "Full snapshot handoff",
            "Host snapshot creation covers runtime data, uploads, gallery, workspaces, training artifacts, models, and logs.",
            "open-backup-preflight",
            "Snapshot",
            target_api="scripts/cleverly-backup snapshot --pretty",
            creates_backup=True,
            writes_files=True,
            runs_shell=True,
        ),
        _handoff_row(
            "backup-snapshot-verify-handoff",
            "warn",
            "verify",
            "Snapshot verification handoff",
            "Verification reads a user-selected archive and records `scripts/cleverly-backup verify` output before trust.",
            "open-backup-preflight",
            "Verify",
            target_api="scripts/cleverly-backup verify PATH --pretty",
            verifies_backup=True,
            reads_backup=True,
            runs_shell=True,
        ),
        _handoff_row(
            "backup-restore-drill-handoff",
            audit_state,
            "test",
            "Restore drill handoff",
            "Dry-run restore checks the encrypted file and password without importing or overwriting live data.",
            "open-backups",
            "Test",
            target_api="/api/backup/encrypted/import?dry_run=true",
            restores_data=True,
            reads_backup=True,
            reads_password=True,
        ),
        _handoff_row(
            "backup-password-custody-handoff",
            "warn",
            "key",
            "Password custody handoff",
            "Record where the backup password is kept without storing the password value in Cleverly logs or activity.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
            reads_password=True,
            writes_activity=True,
        ),
        _handoff_row(
            "backup-storage-location-handoff",
            "ok",
            "store",
            "Storage location handoff",
            "Record the local/offline destination for encrypted exports and full snapshots; network transfer stays out of this plan.",
            "open-local-data-map",
            "Data",
            target_api="/api/operator/data-plan",
            requires_approval=False,
            moves_files=True,
        ),
        _handoff_row(
            "backup-activity-ledger-handoff",
            audit_state,
            "log",
            "Activity ledger handoff",
            "Export filename, snapshot path, verify output, restore-drill summary, and storage notes stay in local evidence.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
            writes_activity=True,
        ),
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


def _backup_alert_rows(
    missing_rows: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if missing_rows:
        rows.append(
            {
                "id": "snapshot-coverage-incomplete",
                "state": "error",
                "badge": "snap",
                "title": "Full snapshot coverage incomplete",
                "detail": f"{len(missing_rows)} expected runtime path(s) are missing or unreadable in this environment.",
                "action": "open-local-data-map",
                "actionLabel": "Data",
                "requires_approval": False,
            }
        )
    if not audit_rows:
        rows.append(
            {
                "id": "backup-audit-missing",
                "state": "warn",
                "badge": "audit",
                "title": "Backup audit evidence missing",
                "detail": "No recent backup/export/restore audit event is visible; record export and restore-drill evidence before risky work.",
                "action": "open-activity-preflight",
                "actionLabel": "Activity",
                "requires_approval": False,
            }
        )
    rows.extend(
        [
            {
                "id": "encrypted-export-approval-required",
                "state": "warn",
                "badge": "ask",
                "title": "Encrypted export approval required",
                "detail": "Creating an encrypted app export requires an explicit backup password and user action in Offline Control.",
                "action": "request-backup-export",
                "actionLabel": "Ask",
                "requires_approval": True,
            },
            {
                "id": "restore-drill-approval-required",
                "state": "warn",
                "badge": "test",
                "title": "Restore drill approval required",
                "detail": "Test Restore should run in dry-run mode with a user-selected backup file and no live-data import.",
                "action": "open-backups",
                "actionLabel": "Test",
                "requires_approval": True,
            },
            {
                "id": "snapshot-verify-required",
                "state": "warn",
                "badge": "verify",
                "title": "Snapshot verification required",
                "detail": "Run `scripts/cleverly-backup verify PATH --pretty` against the selected archive before trusting it.",
                "action": "open-backup-preflight",
                "actionLabel": "Verify",
                "requires_approval": True,
            },
        ]
    )
    return rows[:MAX_AUDIT_ROWS]


def _verification_packet(
    snapshot_rows: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    sequence_rows: list[dict[str, Any]],
    host_commands: list[dict[str, Any]],
    api_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_rows = [row for row in snapshot_rows if row["state"] != "ok"]
    expected_artifacts = [
        {
            "id": "encrypted-export",
            "state": "warn",
            "title": "Encrypted app export file",
            "detail": "Browser-downloaded encrypted backup plus filename and creation time.",
            "required": True,
        },
        {
            "id": "password-custody",
            "state": "warn",
            "title": "Password custody note",
            "detail": "Record where the backup password is kept; never store the password in Cleverly activity.",
            "required": True,
        },
        {
            "id": "restore-drill-summary",
            "state": "ok" if audit_rows else "loading",
            "title": "Dry-run restore summary",
            "detail": "Test Restore output proving the encrypted file decrypts without importing live data.",
            "required": True,
        },
        {
            "id": "full-snapshot-archive",
            "state": "warn" if missing_rows else "ok",
            "title": "Full data snapshot archive",
            "detail": "Archive or volume snapshot covering app DB, auth, sessions, documents, uploads, code, training, models, logs, and media.",
            "required": True,
        },
        {
            "id": "snapshot-verify-output",
            "state": "warn",
            "title": "Snapshot verification output",
            "detail": "`scripts/cleverly-backup verify PATH --pretty` output saved next to the archive or in the activity report.",
            "required": True,
        },
        {
            "id": "storage-location",
            "state": "warn",
            "title": "Storage location",
            "detail": "Offline/local destination path or media label for the encrypted export and full snapshot.",
            "required": True,
        },
    ]
    verification_checks = [
        {
            "id": "export-readable",
            "state": "warn",
            "title": "Encrypted export exists",
            "detail": "Confirm the browser download completed and the file is readable before moving it.",
            "executes": False,
        },
        {
            "id": "restore-dry-run",
            "state": "ok" if audit_rows else "loading",
            "title": "Encrypted restore drill passes",
            "detail": "Run dry-run import to decrypt and summarize sections without writing live data.",
            "executes": False,
        },
        {
            "id": "snapshot-covers-runtime",
            "state": "warn" if missing_rows else "ok",
            "title": "Full snapshot covers runtime paths",
            "detail": f"{len(snapshot_rows) - len(missing_rows)}/{len(snapshot_rows)} expected runtime path(s) visible in this environment.",
            "executes": False,
        },
        {
            "id": "snapshot-verify",
            "state": "warn",
            "title": "Snapshot archive verifies",
            "detail": "Run the verify command against the selected archive before relying on it.",
            "executes": False,
        },
        {
            "id": "evidence-recorded",
            "state": "ok" if audit_rows else "loading",
            "title": "Evidence is recorded",
            "detail": f"{len(audit_rows)} recent backup/export/restore audit event(s) visible.",
            "executes": False,
        },
    ]
    disallowed = [
        "restore into live data without a dry-run review",
        "delete existing data",
        "upload backup files",
        "store backup passwords in activity logs",
        "overwrite Docker volumes",
        "move host files without approval",
        "run network transfer commands",
    ]
    candidate_actions = [
        *[
            {
                "id": row.get("id") or "",
                "title": row.get("title") or "Backup step",
                "detail": row.get("detail") or "",
                "risk": row.get("risk") or "read-only",
                "approval_required": row.get("approval_required") is True,
                "executes": False,
            }
            for row in sequence_rows[:6]
        ],
        *[
            {
                "id": row.get("id") or row.get("label") or "",
                "title": row.get("label") or "Host backup command",
                "detail": row.get("command") or "",
                "risk": row.get("risk") or "approval-required",
                "approval_required": row.get("requires_approval") is True,
                "executes": False,
            }
            for row in host_commands[:4]
        ],
        *[
            {
                "id": row.get("id") or "",
                "title": f"{row.get('method') or 'POST'} {row.get('path') or ''}".strip(),
                "detail": f"{row.get('risk') or 'approval-required'}; password required={bool(row.get('requires_password'))}",
                "risk": row.get("risk") or "approval-required",
                "approval_required": bool(row.get("requires_password")),
                "executes": False,
            }
            for row in api_actions[:4]
        ],
    ]
    return {
        "state": "warn" if missing_rows else ("ok" if audit_rows else "loading"),
        "approval_required": True,
        "scope": "Prove both encrypted app export and full local runtime snapshot before risky work.",
        "expected_artifacts": expected_artifacts,
        "verification_checks": verification_checks,
        "candidate_actions": candidate_actions[:12],
        "disallowed_actions": disallowed,
        "pass_criteria": [
            "encrypted export file exists",
            "restore drill completes in dry-run mode",
            "full snapshot archive exists",
            "snapshot verify command succeeds",
            "storage location and password custody are recorded",
            "no live data is overwritten during verification",
        ],
        "missing_snapshot_items": [
            {"id": row["id"], "title": row["title"], "path": row["path"], "detail": row["detail"]}
            for row in missing_rows[:12]
        ],
        "executes": False,
        "writes": False,
        "restores_data": False,
        "runs_shell": False,
        "uses_network": False,
    }


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
    entry_rows = _entry_rows(len(missing_rows), len(audit_rows))
    handoff_rows = _handoff_rows(len(missing_rows), len(audit_rows))
    evidence_rows = _evidence_rows(audit_rows, len(missing_rows))
    host_commands = [
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
    ]
    api_actions = [
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
    ]
    verification_packet = _verification_packet(snapshot_rows, audit_rows, sequence_rows, host_commands, api_actions)
    alert_rows = _backup_alert_rows(missing_rows, audit_rows)
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
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "backup_alert_count": len(alert_rows),
            "critical_backup_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "creates_backup": False,
            "restores_data": False,
            "runs_shell": False,
            "uses_network": False,
            "requires_export_approval": True,
            "next_action": "Run encrypted export and restore drill from Offline Control, then verify a full snapshot tarball." if not missing_rows else "Review missing data paths before claiming full snapshot coverage.",
        },
        "protected_rows": protected_rows,
        "snapshot_rows": snapshot_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "sequence_rows": sequence_rows,
        "evidence_rows": evidence_rows,
        "alert_rows": alert_rows,
        "verification_packet": verification_packet,
        "host_commands": host_commands,
        "api_actions": api_actions,
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
