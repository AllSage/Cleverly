"""Read-only change brief evidence for the Cleverly operator console."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

MAX_ROWS = 10
MAX_COMMANDS = 24
MAX_DETAIL = 500


def _trim(value: Any, limit: int = MAX_DETAIL) -> str:
    return str(value or "").strip()[:limit]


def _iso(value: datetime) -> str:
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _window(since: str = "yesterday", now: datetime | None = None) -> dict[str, Any]:
    end = _coerce_now(now)
    mode = str(since or "yesterday").strip().lower()
    if mode in {"24h", "last-24-hours", "last 24 hours"}:
        start = end - timedelta(hours=24)
        label = "last 24 hours"
    else:
        start = (end - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        label = "since yesterday"
    return {
        "label": label,
        "start": _iso(start),
        "end": _iso(end),
        "start_ts": start.timestamp(),
        "end_ts": end.timestamp(),
    }


def _timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000 if raw > 1_000_000_000_000 else raw
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _owner_matches(record: dict[str, Any], owner: str) -> bool:
    record_owner = str(record.get("owner") or "local")
    if owner and owner != "local":
        return record_owner == owner
    return record_owner in {"", "local"}


def _state_from_status(value: Any, default: str = "ok") -> str:
    text = str(value or "").lower()
    if any(word in text for word in ("fail", "error", "aborted")):
        return "error"
    if any(word in text for word in ("blocked", "pending", "running", "queued", "warn")):
        return "warn"
    return default


def _load_activity(owner: str) -> tuple[list[dict[str, Any]], str]:
    path = Path(DATA_DIR) / "operator_activity.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], ""
    except (OSError, json.JSONDecodeError) as exc:
        return [], _trim(exc, 300)
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        return [], ""
    rows = [record for record in records if isinstance(record, dict) and _owner_matches(record, owner)]
    rows.sort(key=lambda item: _timestamp(item.get("updated_at") or item.get("created_at")), reverse=True)
    return rows, ""


def _load_workspaces(owner: str) -> tuple[list[dict[str, Any]], str]:
    try:
        from src import code_workspace

        rows = code_workspace.list_workspaces(owner=owner)
        return [row for row in rows if isinstance(row, dict)], ""
    except Exception as exc:
        return [], _trim(exc, 300)


def _workspace_row(workspace: dict[str, Any], start_ts: float) -> dict[str, Any]:
    updated_ts = _timestamp(workspace.get("updated_at") or workspace.get("created_at"))
    changed = updated_ts >= start_ts if updated_ts else False
    name = _trim(workspace.get("name") or workspace.get("title") or workspace.get("id") or "Code workspace", 160)
    workspace_id = _trim(workspace.get("id"), 160)
    path = _trim(workspace.get("path") or workspace.get("root"), 300)
    updated_at = workspace.get("updated_at") or workspace.get("created_at") or ""
    return {
        "id": workspace_id,
        "state": "ok" if changed else "loading",
        "badge": "changed" if changed else "workspace",
        "title": name,
        "detail": f"{path or 'sealed code workspace'}; updated {_trim(updated_at, 80) or 'unknown'}",
        "changed_since": changed,
        "updated_at": updated_at,
        "path": path,
        "api": {
            "status": f"/api/code-workspaces/{workspace_id}/status" if workspace_id else "",
            "diff": f"/api/code-workspaces/{workspace_id}/diff" if workspace_id else "",
        },
    }


def _activity_row(record: dict[str, Any]) -> dict[str, Any]:
    status = record.get("status") or record.get("state") or ""
    title = _trim(record.get("title") or record.get("command_id") or record.get("id") or "Operator activity", 180)
    detail = _trim(record.get("detail") or record.get("category") or record.get("source") or "local operator record", 300)
    changed_at = record.get("updated_at") or record.get("created_at") or record.get("timestamp") or ""
    return {
        "id": _trim(record.get("id"), 160),
        "state": _state_from_status(status),
        "badge": _trim(status or record.get("trust") or "activity", 80),
        "title": title,
        "detail": f"{detail}; updated {_trim(changed_at, 80) or 'unknown'}",
        "changed_at": changed_at,
        "command_id": _trim(record.get("command_id"), 160),
    }


def _evidence_commands(workspace_rows: list[dict[str, Any]], start_iso: str) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for row in workspace_rows[:MAX_ROWS]:
        workspace_id = row.get("id") or ""
        if not workspace_id:
            continue
        base = {
            "workspace_id": workspace_id,
            "workspace": row.get("title") or workspace_id,
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        }
        commands.extend([
            {
                **base,
                "id": f"{workspace_id}:status",
                "label": "Git status",
                "command": "git status --short",
                "api": row.get("api", {}).get("status", ""),
            },
            {
                **base,
                "id": f"{workspace_id}:diff-stat",
                "label": "Diff stat",
                "command": "git diff --stat",
                "api": row.get("api", {}).get("diff", ""),
            },
            {
                **base,
                "id": f"{workspace_id}:recent-log",
                "label": "Recent commits",
                "command": f'git log --since="{start_iso}" --oneline --decorate --max-count=20',
                "api": "",
            },
        ])
        if len(commands) >= MAX_COMMANDS:
            break
    return commands[:MAX_COMMANDS]


def run_operator_change_brief(
    owner: str = "local",
    *,
    since: str = "yesterday",
    workspaces: list[Any] | None = None,
    activity: list[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a local, read-only evidence packet for "what changed" requests."""
    owner = owner or "local"
    window = _window(since, now)
    raw_workspaces, workspace_error = (workspaces, "") if workspaces is not None else _load_workspaces(owner)
    raw_activity, activity_error = (activity, "") if activity is not None else _load_activity(owner)
    normalized_workspaces = [row for row in raw_workspaces or [] if isinstance(row, dict) and _owner_matches(row, owner)]
    normalized_activity = [row for row in raw_activity or [] if isinstance(row, dict) and _owner_matches(row, owner)]
    workspace_rows = [_workspace_row(row, window["start_ts"]) for row in normalized_workspaces]
    activity_rows = [
        _activity_row(row)
        for row in normalized_activity
        if _timestamp(row.get("updated_at") or row.get("created_at") or row.get("timestamp")) >= window["start_ts"]
    ][:MAX_ROWS]
    workspace_rows.sort(key=lambda row: _timestamp(row.get("updated_at")), reverse=True)
    changed_workspaces = [row for row in workspace_rows if row.get("changed_since")]
    evidence = _evidence_commands(workspace_rows, window["start"])
    total_changes = len(changed_workspaces) + len(activity_rows)
    state = "ok" if total_changes else ("warn" if workspace_rows or normalized_activity else "loading")
    return {
        "mode": "read-only-change-brief",
        "generated_at": _iso(_coerce_now(now)),
        "owner": owner,
        "window": {
            "label": window["label"],
            "start": window["start"],
            "end": window["end"],
        },
        "summary": {
            "state": state,
            "workspace_count": len(workspace_rows),
            "changed_workspace_count": len(changed_workspaces),
            "activity_count": len(activity_rows),
            "activity_total": len(normalized_activity),
            "total_change_count": total_changes,
            "creates_changes": False,
            "runs_shell": False,
            "next_action": "Open Code Workspace for exact git status/diff before acting." if workspace_rows else "Create or import a Code Workspace to inspect repo changes.",
        },
        "workspace_rows": workspace_rows[:MAX_ROWS],
        "changed_workspace_rows": changed_workspaces[:MAX_ROWS],
        "activity_rows": activity_rows,
        "evidence_commands": evidence,
        "errors": {
            "workspaces": workspace_error,
            "activity": activity_error,
        },
        "approval": {
            "required": False,
            "policy": "This endpoint only reads local metadata and activity records. It does not run shell commands, execute git, modify files, start services, or use the network.",
        },
        "paths": {
            "activity": "data/operator_activity.json",
            "workspaces": "data/code-workspaces/workspaces.json",
            "workspace_root": "data/code-workspaces",
        },
    }
