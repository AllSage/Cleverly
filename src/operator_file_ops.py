"""Read-only file operation evidence for the Cleverly operator console."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, PERSONAL_DIR, UPLOAD_DIR

MAX_ROWS = 24
MAX_CHILDREN = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _data_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else Path(os.getenv("DATA_DIR") or DATA_DIR)


def _logs_root(data_root: Path, root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else Path(os.getenv("LOG_DIR") or data_root.parent / "logs")


def _rel_label(path: Path, data_root: Path, logs_root: Path) -> str:
    try:
        if path == logs_root or logs_root in path.parents:
            rel = path.relative_to(logs_root)
            return "logs" if str(rel) == "." else f"logs/{rel.as_posix()}"
        if path == data_root or data_root in path.parents:
            rel = path.relative_to(data_root)
            return "data" if str(rel) == "." else f"data/{rel.as_posix()}"
    except ValueError:
        pass
    return str(path)


def _direct_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "kind": "missing",
            "direct_files": 0,
            "direct_dirs": 0,
            "direct_bytes": 0,
            "truncated": False,
            "error": "",
        }
    if path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return {
            "exists": True,
            "kind": "file",
            "direct_files": 1,
            "direct_dirs": 0,
            "direct_bytes": size,
            "truncated": False,
            "error": "",
        }
    if not path.is_dir():
        return {
            "exists": True,
            "kind": "other",
            "direct_files": 0,
            "direct_dirs": 0,
            "direct_bytes": 0,
            "truncated": False,
            "error": "",
        }
    files = dirs = bytes_total = 0
    truncated = False
    try:
        for idx, child in enumerate(path.iterdir()):
            if idx >= MAX_CHILDREN:
                truncated = True
                break
            try:
                if child.is_dir():
                    dirs += 1
                elif child.is_file():
                    files += 1
                    bytes_total += child.stat().st_size
            except OSError:
                continue
        return {
            "exists": True,
            "kind": "directory",
            "direct_files": files,
            "direct_dirs": dirs,
            "direct_bytes": bytes_total,
            "truncated": truncated,
            "error": "",
        }
    except OSError as exc:
        return {
            "exists": True,
            "kind": "directory",
            "direct_files": files,
            "direct_dirs": dirs,
            "direct_bytes": bytes_total,
            "truncated": truncated,
            "error": _trim(exc, 180),
        }


def _root_row(
    row_id: str,
    path: Path,
    data_root: Path,
    logs_root: Path,
    description: str,
    *,
    sensitive: bool = False,
    writable: bool = True,
    backup_required: bool = True,
) -> dict[str, Any]:
    summary = _direct_summary(path)
    state = "ok" if summary["exists"] and not summary["error"] else "warn"
    detail = (
        f"{description}; {summary['kind']}; "
        f"{summary['direct_files']} direct file(s), {summary['direct_dirs']} direct dir(s), {summary['direct_bytes']} direct byte(s)"
    )
    if summary["truncated"]:
        detail += f"; direct listing truncated at {MAX_CHILDREN}"
    if summary["error"]:
        detail += f"; inspect error: {summary['error']}"
    if sensitive:
        detail += "; sensitive"
    return {
        "id": row_id,
        "state": state,
        "badge": "key" if sensitive else ("log" if row_id == "logs" else "file"),
        "title": _rel_label(path, data_root, logs_root),
        "detail": detail,
        "path": str(path),
        "exists": bool(summary["exists"]),
        "kind": summary["kind"],
        "direct_files": _safe_int(summary["direct_files"]),
        "direct_dirs": _safe_int(summary["direct_dirs"]),
        "direct_bytes": _safe_int(summary["direct_bytes"]),
        "error": summary["error"],
        "sensitive": sensitive,
        "writable": writable,
        "backup_required": backup_required,
        "action": "open-backup-preflight" if backup_required else "open-local-data-map",
        "actionLabel": "Backup" if backup_required else "Map",
    }


def _default_roots(data_root: Path, logs_root: Path) -> list[tuple[str, Path, str, bool, bool, bool]]:
    return [
        ("data-root", data_root, "Main app runtime data root", False, True, True),
        ("logs", logs_root, "Application logs", False, True, True),
        ("app-db", data_root / "app.db", "SQLite app database", True, True, True),
        ("auth", data_root / "auth.json", "Users, password hashes, privileges, and auth settings", True, True, True),
        ("settings", data_root / "settings.json", "Application settings", True, True, True),
        ("features", data_root / "features.json", "Feature flags", False, True, True),
        ("prefs", data_root / "user_prefs.json", "Per-user preferences and operator profile", False, True, True),
        ("sessions", data_root / "sessions.json", "Session metadata cache", False, True, True),
        ("operator-activity", data_root / "operator_activity.json", "Operator command activity ledger", False, True, True),
        ("uploads", Path(os.getenv("UPLOAD_DIR") or UPLOAD_DIR), "Uploaded working files", False, True, True),
        ("personal-docs", Path(os.getenv("PERSONAL_DIR") or PERSONAL_DIR), "Personal document library and local index metadata", False, True, True),
        ("personal-doc-index", data_root / "personal_docs" / "index", "Personal document keyword/vector side index", False, True, True),
        ("personal-uploads", data_root / "personal_uploads", "Per-owner direct RAG uploads", False, True, True),
        ("gallery", data_root / "gallery", "Generated and saved gallery media", False, True, True),
        ("gallery-uploads", data_root / "gallery_uploads", "Uploaded gallery media", False, True, True),
        ("generated-images", data_root / "generated_images", "Generated image artifacts", False, True, True),
        ("research", data_root / "deep_research", "Deep Research outputs and reports", False, True, True),
        ("search", data_root / "search", "Search cache and analytics", False, True, True),
        ("chroma", data_root / "chroma", "Local/native Chroma vector index", False, True, True),
        ("code-workspaces", data_root / "code-workspaces", "Code Workspace imports, snapshots, worker queue, and outputs", False, True, True),
        ("training", data_root / "training", "Training Lab datasets, jobs, adapters, and base models", False, True, True),
        ("models", data_root / "models", "Local model artifacts", False, True, True),
        ("huggingface", data_root / "huggingface", "Hugging Face cache/model files", False, True, True),
        ("ollama", data_root / "ollama", "Host-data Ollama model mirror when enabled", False, True, True),
        ("vault", data_root / "vault.json", "Vault session/config", True, True, True),
        ("app-key", data_root / ".app_key", "Local encryption key material", True, True, True),
        ("ssh", data_root / "ssh", "Cookbook SSH identity mirror when HostData is enabled", True, True, True),
        ("cache", data_root / "cache", "General cache and FastEmbed cache root", False, True, False),
        ("npm-cache", data_root / "npm-cache", "npm/npx cache", False, True, False),
        ("local-packages", data_root / "local", "Local package installs used by Cookbook", False, True, False),
    ]


def _api_action(
    method: str,
    path: str,
    title: str,
    *,
    writes: bool = False,
    requires_approval: bool = False,
    destructive: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": requires_approval,
        "destructive": destructive,
        "uses_network": uses_network,
    }


def _file_alert_rows(
    root_rows: list[dict[str, Any]],
    sensitive_rows: list[dict[str, Any]],
    missing_required: list[dict[str, Any]],
    operation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in missing_required[:6]:
        rows.append(
            {
                "id": f"missing-{row['id']}",
                "state": "error",
                "badge": "missing",
                "title": f"Missing required root: {row['title']}",
                "detail": f"Backup-required local root is not visible; verify the path before file operations: {row['path']}",
                "action": "open-local-data-map",
                "actionLabel": "Map",
                "approval_required": True,
                "destructive": False,
            }
        )
    for row in [item for item in root_rows if item.get("error")][:4]:
        rows.append(
            {
                "id": f"scan-error-{row['id']}",
                "state": "warn",
                "badge": "scan",
                "title": f"Scan warning: {row['title']}",
                "detail": f"Shallow metadata scan could not inspect this root completely: {row['error']}",
                "action": "open-local-data-map",
                "actionLabel": "Map",
                "approval_required": False,
                "destructive": False,
            }
        )
    for row in sensitive_rows[:6]:
        rows.append(
            {
                "id": f"sensitive-{row['id']}",
                "state": "warn",
                "badge": "key",
                "title": f"Sensitive root mapped: {row['title']}",
                "detail": "Review trust boundaries before export, copy, sync, delete, restore, or backup sharing; metadata only.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "approval_required": True,
                "destructive": False,
            }
        )
    for row in operation_rows:
        title = str(row.get("title") or "")
        if title not in {"Write/copy/move gate", "Delete/restore gate"}:
            continue
        rows.append(
            {
                "id": title.lower().replace("/", "-").replace(" ", "-"),
                "state": row.get("state") or "warn",
                "badge": row.get("badge") or "gate",
                "title": title,
                "detail": row.get("detail") or "File operation requires explicit approval.",
                "action": row.get("action") or "open-trust-controls",
                "actionLabel": row.get("actionLabel") or row.get("action_label") or "Review",
                "approval_required": True,
                "destructive": title == "Delete/restore gate",
            }
        )
    return rows[:MAX_ROWS]


def _entry_rows(*, root_rows: list[dict[str, Any]], alert_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready = bool(root_rows)
    state = "ok" if ready else "warn"
    common = {
        "command_id": "open-local-data-map",
        "trust_command_id": "open-trust-controls",
        "backup_command_id": "open-backup-preflight",
        "offline_command_id": "open-offline",
        "activity_command_id": "open-activity-preflight",
        "palette_command_id": "open-command-palette",
        "file_ops_api": "/api/operator/file-ops-plan",
        "backup_api": "/api/operator/backup-plan",
        "personal_api": "/api/personal",
        "personal_upload_api": "/api/personal/upload",
        "code_workspaces_api": "/api/code-workspaces",
        "requires_approval": True,
        "ready": ready,
        "executes": False,
        "reads_file_contents": False,
        "writes_files": False,
        "copies_files": False,
        "moves_files": False,
        "deletes_files": False,
        "uploads_files": False,
        "imports_files": False,
        "indexes_files": False,
        "exports_files": False,
        "restores_files": False,
        "runs_shell": False,
        "uses_network": False,
    }
    alert_detail = f"{len(alert_rows)} file safety alert(s) visible before any file operation."
    return [
        {
            **common,
            "id": "file-ops-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard file operation route",
            "detail": f"The Local Data Map opens file-root inventory, write gates, delete gates, and backup posture first; {alert_detail}",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            **common,
            "id": "file-ops-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed file request route",
            "detail": "Typed file requests route to metadata-only map evidence and approval boundaries without opening, copying, moving, or deleting files.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            **common,
            "id": "file-ops-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette file route",
            "detail": "The command palette exposes file-map, backup, trust, and activity handoffs while keeping write/import/delete APIs separate and approval-gated.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "file-ops-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice file request route",
            "detail": "Voice mode can open file preflight and backup posture without reading file contents, uploading files, or running shell commands.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "file-ops-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow file handoff",
            "detail": "Automation handoffs can review file scope, activity evidence, and backup requirements, but file changes remain explicit and approval-gated.",
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
        "executes": False,
        "reads_file_contents": False,
        "writes_files": False,
        "copies_files": False,
        "moves_files": False,
        "deletes_files": False,
        "uploads_files": False,
        "imports_files": False,
        "indexes_files": False,
        "exports_files": False,
        "restores_files": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    existing_count: int,
    root_count: int,
    missing_required_count: int,
    sensitive_count: int,
) -> list[dict[str, Any]]:
    scope_state = "ok" if existing_count else "warn"
    delete_state = "error" if missing_required_count else "warn"
    return [
        _handoff_row(
            "file-read-scope-handoff",
            scope_state,
            "read",
            "Read scope handoff",
            f"{existing_count}/{root_count} configured roots are visible; content reads still require an owning feature or explicit selected file.",
            "open-local-data-map",
            "Scope",
            target_api="/api/operator/file-ops-plan",
            requires_approval=True,
        ),
        _handoff_row(
            "file-write-import-handoff",
            "warn",
            "write",
            "Write and import handoff",
            "Uploads, imports, workspace writes, gallery saves, and document changes stay in their owning surfaces with explicit approval.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/file-ops-plan",
            approval_api="/api/upload",
            requires_approval=True,
        ),
        _handoff_row(
            "file-delete-restore-handoff",
            delete_state,
            "del",
            "Delete and restore handoff",
            f"{missing_required_count} backup-required root(s) are missing; destructive delete, restore, and replace actions need backup/recovery evidence first.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_api="/api/backup/encrypted/import",
            requires_approval=True,
        ),
        _handoff_row(
            "file-backup-snapshot-handoff",
            "warn" if missing_required_count else "ok",
            "bak",
            "Backup and snapshot handoff",
            "Encrypted app exports, code workspace snapshots, and restore drills should precede risky file work.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_api="/api/backup/encrypted/export",
            requires_approval=True,
        ),
        _handoff_row(
            "file-index-library-handoff",
            "ok" if existing_count else "warn",
            "idx",
            "Index and library handoff",
            "Personal docs, Library/RAG, and document search indexing stay separate from raw file writes and require owner-selected sources.",
            "open-library-preflight",
            "Library",
            target_api="/api/operator/document-search-plan",
            approval_api="/api/personal/upload",
            requires_approval=True,
        ),
        _handoff_row(
            "file-activity-recovery-handoff",
            "warn" if sensitive_count else "ok",
            "log",
            "Activity and recovery handoff",
            f"{sensitive_count} sensitive root(s) are mapped; approved file actions should record status, result, logs, retry, and rollback evidence without leaking contents.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity-plan",
            approval_api="/api/operator/recovery-plan",
            requires_approval=bool(sensitive_count),
        ),
    ]


def run_operator_file_ops_plan(
    owner: str = "local",
    *,
    data_root: str | Path | None = None,
    logs_root: str | Path | None = None,
    roots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return read-only file-root and file-operation safety evidence."""
    owner = owner or "local"
    data_path = _data_root(data_root)
    logs_path = _logs_root(data_path, logs_root)
    root_defs: list[tuple[str, Path, str, bool, bool, bool]]
    if roots is not None:
        root_defs = [
            (
                _trim(item.get("id") or item.get("title") or "root", 120),
                Path(item.get("path") or data_path),
                _trim(item.get("description") or item.get("detail") or "Local file root", 240),
                bool(item.get("sensitive")),
                item.get("writable") is not False,
                item.get("backup_required") is not False,
            )
            for item in roots
            if isinstance(item, dict)
        ]
    else:
        root_defs = _default_roots(data_path, logs_path)
    root_rows = [
        _root_row(row_id, path, data_path, logs_path, description, sensitive=sensitive, writable=writable, backup_required=backup_required)
        for row_id, path, description, sensitive, writable, backup_required in root_defs[:MAX_ROWS]
    ]
    existing = [row for row in root_rows if row["exists"]]
    sensitive_rows = [row for row in root_rows if row["sensitive"]]
    missing_required = [row for row in root_rows if row["backup_required"] and not row["exists"]]
    direct_files = sum(row["direct_files"] for row in root_rows)
    direct_dirs = sum(row["direct_dirs"] for row in root_rows)
    direct_bytes = sum(row["direct_bytes"] for row in root_rows)
    operation_rows = [
        {
            "state": "ok" if existing else "warn",
            "badge": "scan",
            "title": "File root inventory",
            "detail": f"{len(existing)}/{len(root_rows)} configured local roots are visible; metadata only, no file contents read",
            "action": "open-local-data-map",
            "actionLabel": "Map",
        },
        {
            "state": "ok",
            "badge": "read",
            "title": "Read/open boundary",
            "detail": "Read operations should stay inside app-owned data, documents, code workspaces, uploads, or explicitly selected files.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "state": "warn",
            "badge": "write",
            "title": "Write/copy/move gate",
            "detail": "Writing, copying, moving, importing, or uploading files requires explicit user action in the owning feature surface.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            "state": "warn",
            "badge": "del",
            "title": "Delete/restore gate",
            "detail": "Deleting files, removing directories, restoring backups, or replacing data is destructive and requires review plus backup evidence.",
            "action": "open-backup-preflight",
            "actionLabel": "Backup",
        },
        {
            "state": "warn" if sensitive_rows else "ok",
            "badge": "key",
            "title": "Sensitive material",
            "detail": f"{len(sensitive_rows)} sensitive local root(s) mapped: auth, vault, app key, SSH identity, or database files",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "state": "ok",
            "badge": "net",
            "title": "No network or shell",
            "detail": "This plan does not upload, sync, run shell commands, install packages, or use network access.",
            "action": "open-offline",
            "actionLabel": "Offline",
        },
    ]
    alert_rows = _file_alert_rows(root_rows, sensitive_rows, missing_required, operation_rows)
    entry_rows = _entry_rows(root_rows=root_rows, alert_rows=alert_rows)
    handoff_rows = _handoff_rows(
        existing_count=len(existing),
        root_count=len(root_rows),
        missing_required_count=len(missing_required),
        sensitive_count=len(sensitive_rows),
    )
    guard_rows = [
        {
            "state": "ok",
            "title": "Metadata-only scan",
            "detail": "The plan checks path existence, shallow direct child counts, and direct child bytes only.",
        },
        {
            "state": "ok",
            "title": "Feature-owned writes",
            "detail": "Documents, Gallery, Code Workspace, Training, Backup, and Personal Docs keep their own approval flows for writes.",
        },
        {
            "state": "ok",
            "title": "Backup before destructive action",
            "detail": "Destructive file operations should be preceded by encrypted export, snapshot, or workspace snapshot evidence.",
        },
        {
            "state": "ok",
            "title": "No host traversal",
            "detail": "File operations should stay within configured local roots unless the user explicitly selects an external file or directory.",
        },
    ]
    api_actions = [
        _api_action("GET", "/api/operator/file-ops-plan", "Read file operation plan"),
        _api_action("GET", "/api/operator/backup-plan", "Read backup coverage before file changes"),
        _api_action("GET", "/api/operator/document-search-plan", "Read local document index coverage"),
        _api_action("GET", "/api/personal", "List personal documents"),
        _api_action("POST", "/api/personal/upload", "Upload files to personal documents", writes=True, requires_approval=True),
        _api_action("DELETE", "/api/personal/file", "Delete personal upload/index entry", writes=True, requires_approval=True, destructive=True),
        _api_action("GET", "/api/code-workspaces", "List sealed code workspaces"),
        _api_action("POST", "/api/code-workspaces/import", "Import a workspace archive", writes=True, requires_approval=True),
        _api_action("POST", "/api/code-workspaces/{workspace_id}/snapshot", "Create workspace snapshot", writes=True, requires_approval=True),
        _api_action("POST", "/api/code-workspaces/{workspace_id}/write", "Write workspace file", writes=True, requires_approval=True),
        _api_action("POST", "/api/backup/encrypted/export", "Create encrypted app export", writes=True, requires_approval=True),
        _api_action("POST", "/api/backup/encrypted/import", "Restore or dry-run encrypted backup", writes=True, requires_approval=True, destructive=True),
        _api_action("POST", "/api/upload", "Upload chat/document attachment", writes=True, requires_approval=True),
        _api_action("DELETE", "/api/documents/{document_id}", "Delete saved document", writes=True, requires_approval=True, destructive=True),
    ]
    evidence_rows = [
        {"label": "Data root", "path": str(data_path), "detail": f"{len(existing)} visible mapped roots"},
        {"label": "Logs root", "path": str(logs_path), "detail": "application logs"},
        {"label": "Personal docs", "path": str(Path(os.getenv("PERSONAL_DIR") or PERSONAL_DIR)), "detail": "local document library"},
        {"label": "Uploads", "path": str(Path(os.getenv("UPLOAD_DIR") or UPLOAD_DIR)), "detail": "chat/document uploads"},
        {"label": "Code workspaces", "path": str(data_path / "code-workspaces"), "detail": "repo imports, snapshots, outputs"},
        {"label": "Backup coverage", "path": "scripts/cleverly-backup", "detail": "full data snapshot and verify workflow"},
    ]
    return {
        "mode": "read-only-file-ops-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "warn" if missing_required else "ok",
            "root_count": len(root_rows),
            "existing_root_count": len(existing),
            "missing_required_count": len(missing_required),
            "sensitive_root_count": len(sensitive_rows),
            "file_alert_count": len(alert_rows),
            "critical_file_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "direct_file_count": direct_files,
            "direct_dir_count": direct_dirs,
            "direct_bytes": direct_bytes,
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("ready")]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "executes": False,
            "reads_file_contents": False,
            "writes_files": False,
            "copies_files": False,
            "moves_files": False,
            "deletes_files": False,
            "uploads_files": False,
            "imports_files": False,
            "indexes_files": False,
            "exports_files": False,
            "restores_files": False,
            "runs_shell": False,
            "uses_network": False,
            "requires_write_approval": True,
            "requires_delete_approval": True,
        },
        "root_rows": root_rows,
        "sensitive_rows": sensitive_rows[:MAX_ROWS],
        "operation_rows": operation_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "approval": {
            "required": True,
            "policy": (
                "This endpoint only reads shallow file metadata. It does not read file contents, write files, copy files, "
                "move files, delete files, upload files, import files, index files, export files, restore files, run shell "
                "commands, or use network access."
            ),
        },
        "paths": {
            "data_root": str(data_path),
            "logs_root": str(logs_path),
            "personal_docs": str(Path(os.getenv("PERSONAL_DIR") or PERSONAL_DIR)),
            "uploads": str(Path(os.getenv("UPLOAD_DIR") or UPLOAD_DIR)),
            "code_workspaces": str(data_path / "code-workspaces"),
            "training": str(data_path / "training"),
            "models": str(data_path / "models"),
            "backup_script": "scripts/cleverly-backup",
        },
    }
