"""Read-only notes, checklist, and reminder readiness plan."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from src.settings import load_features, load_settings, offline_mode


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _parse_time(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _note_items(value: Any) -> list[dict[str, Any]]:
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    rows = []
    for item in raw[:100]:
        if isinstance(item, dict):
            rows.append(item)
        elif isinstance(item, str):
            rows.append({"text": item, "done": False})
    return rows


def _note_field(note: Any, key: str, default: Any = None) -> Any:
    if isinstance(note, dict):
        return note.get(key, default)
    return getattr(note, key, default)


def _note_owner_matches(note: Any, owner: str) -> bool:
    note_owner = _note_field(note, "owner", "")
    if owner == "local":
        return note_owner in (None, "", "local")
    return note_owner == owner


def _note_to_row(note: Any) -> dict[str, Any]:
    items = _note_items(_note_field(note, "items"))
    incomplete = [item for item in items if not bool(item.get("done") or item.get("checked"))]
    title = _trim(_note_field(note, "title") or _note_field(note, "name") or "Untitled note", 240)
    content = _trim(_note_field(note, "content") or _note_field(note, "text") or "", 500)
    updated_at = _note_field(note, "updated_at") or _note_field(note, "created_at") or ""
    due_date = _note_field(note, "due_date") or ""
    archived = bool(_note_field(note, "archived", False))
    return {
        "id": _trim(_note_field(note, "id"), 160),
        "state": "loading" if archived else ("warn" if due_date else "ok"),
        "badge": "note",
        "title": title,
        "detail": f"{len(items)} checklist item{'s' if len(items) != 1 else ''}; {len(incomplete)} open; due {due_date or 'none'}",
        "content_chars": len(content),
        "item_count": len(items),
        "open_item_count": len(incomplete),
        "pinned": bool(_note_field(note, "pinned", False)),
        "archived": archived,
        "due_date": _trim(due_date, 80),
        "updated_at": _trim(updated_at, 80),
        "source": _trim(_note_field(note, "source") or "", 80),
        "task_candidate": bool(incomplete) or bool(re.search(r"\b(todo|task|follow up|remind|deadline|next action)\b", f"{title} {content}", re.I)),
    }


def _load_note_rows(owner: str, notes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if isinstance(notes, list):
        source = notes
    else:
        source = []
        try:
            from core.database import Note, SessionLocal

            db = SessionLocal()
            try:
                query = db.query(Note)
                if owner != "local":
                    query = query.filter(Note.owner == owner)
                else:
                    query = query.filter((Note.owner == None) | (Note.owner == "") | (Note.owner == "local"))  # noqa: E711
                source = query.all()
            finally:
                db.close()
        except Exception:
            source = []
    rows = [_note_to_row(note) for note in source if _note_owner_matches(note, owner)]
    rows.sort(key=lambda row: _parse_time(row.get("updated_at")), reverse=True)
    return rows[:80]


def _api_action(
    path: str,
    title: str,
    *,
    method: str = "GET",
    writes: bool = False,
    deletes: bool = False,
    sends_notification: bool = False,
    uses_network: bool = False,
    requires_approval: bool = False,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "deletes": deletes,
        "executes": False,
        "sends_notification": sends_notification,
        "uses_network": uses_network,
        "requires_approval": requires_approval,
    }


def _entry_rows(*, notes_enabled: bool) -> list[dict[str, Any]]:
    state = "ok" if notes_enabled else "warn"
    common = {
        "command_id": "open-work-preflight",
        "start_command_id": "open-notes",
        "draft_command_id": "draft-task-from-note",
        "approval_api": "/api/notes",
        "reminder_api": "/api/notes/fire-reminder",
        "requires_approval": True,
        "executes": False,
        "creates_notes": False,
        "updates_notes": False,
        "archives_notes": False,
        "deletes_notes": False,
        "toggles_checklist_items": False,
        "fires_reminders": False,
        "creates_tasks": False,
        "sends_notifications": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "notes-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard notes preflight",
            "detail": "The Work panel opens read-only note, checklist, and reminder posture before any note write or reminder dispatch.",
            "action": "open-work-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "notes-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed notes request route",
            "detail": "Typed note requests route to Work Operations Preflight before opening Notes or write-capable note APIs.",
            "action": "open-work-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "notes-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette notes route",
            "detail": "The command palette separates notes review from create, edit, archive, delete, reorder, checklist, and reminder APIs.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "notes-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice notes route",
            "detail": "Voice mode can open the notes preflight or draft route without writing notes, firing reminders, or sending notifications.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "notes-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow notes handoff",
            "detail": "Automation handoffs can review note candidates and route to a task draft, but note writes and reminders stay approval-gated.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _notes_alert_rows(
    *,
    rows: list[dict[str, Any]],
    settings: dict[str, Any],
    features: dict[str, Any],
    offline: bool,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    active = [row for row in rows if not row.get("archived")]
    due = [row for row in active if row.get("due_date")]
    task_candidates = [row for row in active if row.get("task_candidate")]
    if not active:
        alerts.append({
            "id": "notes-empty",
            "state": "warn",
            "badge": "note",
            "title": "No active notes visible",
            "detail": "Notes are available but no active note rows are visible for handoff or reminders.",
            "action": "open-notes",
            "actionLabel": "Notes",
            "requires_approval": False,
            "uses_network": False,
        })
    if due:
        alerts.append({
            "id": "notes-reminders-due",
            "state": "warn",
            "badge": "due",
            "title": "Note reminders need review",
            "detail": f"{len(due)} note reminder{'s' if len(due) != 1 else ''} visible; browser/email/ntfy dispatch stays in Notes.",
            "action": "open-notes",
            "actionLabel": "Notes",
            "requires_approval": False,
            "uses_network": False,
        })
    if task_candidates:
        alerts.append({
            "id": "notes-task-candidates",
            "state": "warn",
            "badge": "task",
            "title": "Notes ready for task draft",
            "detail": f"{len(task_candidates)} note{'s' if len(task_candidates) != 1 else ''} contain checklist or action language.",
            "action": "draft-task-from-note",
            "actionLabel": "Draft",
            "requires_approval": True,
            "uses_network": False,
        })
    reminder_channel = _trim(settings.get("reminder_channel") or "browser", 40)
    reminder_blocked = offline
    if reminder_channel == "email":
        reminder_blocked = reminder_blocked or features.get("email") is False
    if reminder_channel == "ntfy":
        reminder_blocked = reminder_blocked or features.get("network_notifications") is False
    if reminder_channel in {"email", "ntfy"} and reminder_blocked:
        alerts.append({
            "id": "notes-network-reminder-blocked",
            "state": "warn",
            "badge": "net",
            "title": "Network reminder channel blocked",
            "detail": f"Reminder channel is {reminder_channel}, but offline or feature policy blocks network notification delivery.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    alerts.append({
        "id": "notes-write-delete-gates",
        "state": "warn",
        "badge": "ask",
        "title": "Note write/delete gates require review",
        "detail": "Creating, editing, archiving, pinning, reordering, firing reminders, and deleting notes remain explicit actions.",
        "action": "open-trust-controls",
        "actionLabel": "Trust",
        "requires_approval": True,
        "uses_network": False,
    })
    return alerts[:16]


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
    approval_command_id: str = "open-work-preflight",
    requires_approval: bool = True,
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
        "creates_notes": False,
        "updates_notes": False,
        "archives_notes": False,
        "deletes_notes": False,
        "toggles_checklist_items": False,
        "fires_reminders": False,
        "creates_tasks": False,
        "exports_notes": False,
        "indexes_notes": False,
        "sends_notifications": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    notes_enabled: bool,
    active_count: int,
    archived_count: int,
    checklist_count: int,
    open_item_count: int,
    due_count: int,
    task_candidate_count: int,
    reminder_channel: str,
    offline: bool,
) -> list[dict[str, Any]]:
    write_state = "ok" if notes_enabled else "warn"
    lifecycle_state = "warn" if active_count or archived_count else "loading"
    checklist_state = "warn" if checklist_count or open_item_count else "ok"
    reminder_state = "warn" if due_count else "ok"
    task_state = "warn" if task_candidate_count else ("loading" if not active_count else "ok")
    search_state = "ok" if active_count else "loading"
    activity_state = "warn" if due_count or task_candidate_count else "ok"
    return [
        _handoff_row(
            "notes-create-update-handoff",
            write_state,
            "edit",
            "Create and update handoff",
            f"{active_count} active note(s) are visible; note creation, edits, pinning, and reorder actions stay in Notes review.",
            "open-notes",
            "Notes",
            target_api="/api/notes",
        ),
        _handoff_row(
            "notes-archive-delete-handoff",
            lifecycle_state,
            "del",
            "Archive and delete handoff",
            f"{archived_count} archived note(s) and {active_count} active note(s) require explicit review before archival, deletion, or recovery-impacting cleanup.",
            "open-work-preflight",
            "Preflight",
            target_api="/api/notes/{note_id}",
        ),
        _handoff_row(
            "notes-checklist-handoff",
            checklist_state,
            "check",
            "Checklist handoff",
            f"{checklist_count} checklist note(s) with {open_item_count} open item(s) can be reviewed before item toggles or task conversion.",
            "open-notes",
            "Notes",
            target_api="/api/notes/{note_id}/items/{index}/toggle",
        ),
        _handoff_row(
            "notes-reminder-notification-handoff",
            reminder_state,
            "bell",
            "Reminder and notification handoff",
            f"{due_count} due note reminder(s); reminder_channel={reminder_channel}; offline={offline}. Dispatch remains explicit.",
            "open-notes",
            "Notes",
            target_api="/api/notes/fire-reminder",
        ),
        _handoff_row(
            "notes-task-draft-handoff",
            task_state,
            "task",
            "Note-to-task draft handoff",
            f"{task_candidate_count} note(s) look task-ready; drafts can be generated without saving, scheduling, or running tasks.",
            "draft-task-from-note",
            "Draft",
            target_api="/api/operator/note-task-draft",
        ),
        _handoff_row(
            "notes-search-export-handoff",
            search_state,
            "find",
            "Search and export boundary handoff",
            "Local note metadata can support search, draft, and copy/export review without uploading or indexing note contents from this endpoint.",
            "open-local-data-map",
            "Data",
            target_api="/api/operator/notes-plan",
        ),
        _handoff_row(
            "notes-activity-recovery-handoff",
            activity_state,
            "log",
            "Activity and recovery handoff",
            "Note writes, deletes, reminders, and task drafts should leave activity evidence and recovery context before owner-visible state changes.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
        ),
    ]


def run_operator_notes_plan(
    owner: str = "local",
    *,
    notes: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Return read-only local Notes, checklist, and reminder evidence."""
    owner = owner or "local"
    try:
        loaded_settings = load_settings()
    except Exception:
        loaded_settings = {}
    try:
        loaded_features = load_features()
    except Exception:
        loaded_features = {}
    settings = {**loaded_settings, **(settings or {})}
    features = {**loaded_features, **(features or {})}
    offline_state = offline_mode() if offline is None else bool(offline)
    notes_enabled = features.get("notes") is not False
    note_rows = _load_note_rows(owner, notes)
    active_rows = [row for row in note_rows if not row.get("archived")]
    archived_rows = [row for row in note_rows if row.get("archived")]
    checklist_rows = [row for row in active_rows if row.get("item_count")]
    due_rows = [row for row in active_rows if row.get("due_date")]
    task_candidate_rows = [row for row in active_rows if row.get("task_candidate")]
    alert_rows = _notes_alert_rows(rows=note_rows, settings=settings, features=features, offline=offline_state)
    entry_rows = _entry_rows(notes_enabled=notes_enabled)
    reminder_channel = _trim(settings.get("reminder_channel") or "browser", 40)
    open_checklist_count = sum(int(row.get("open_item_count") or 0) for row in checklist_rows)
    handoff_rows = _handoff_rows(
        notes_enabled=notes_enabled,
        active_count=len(active_rows),
        archived_count=len(archived_rows),
        checklist_count=len(checklist_rows),
        open_item_count=open_checklist_count,
        due_count=len(due_rows),
        task_candidate_count=len(task_candidate_rows),
        reminder_channel=reminder_channel,
        offline=offline_state,
    )
    api_actions = [
        _api_action("/api/operator/notes-plan", "Read notes operations plan"),
        _api_action("/api/notes", "Read notes"),
        _api_action("/api/notes", "Create note", method="POST", writes=True, requires_approval=True),
        _api_action("/api/notes/{note_id}", "Update note", method="PUT", writes=True, requires_approval=True),
        _api_action("/api/notes/{note_id}", "Delete note", method="DELETE", writes=True, deletes=True, requires_approval=True),
        _api_action("/api/notes/{note_id}/pin", "Toggle note pin", method="POST", writes=True, requires_approval=True),
        _api_action("/api/notes/{note_id}/archive", "Archive note", method="POST", writes=True, requires_approval=True),
        _api_action("/api/notes/{note_id}/items/{index}/toggle", "Toggle checklist item", method="POST", writes=True, requires_approval=True),
        _api_action("/api/notes/reorder", "Reorder notes", method="POST", writes=True, requires_approval=True),
        _api_action("/api/notes/fire-reminder", "Fire note reminder", method="POST", writes=True, sends_notification=True, uses_network=not offline_state, requires_approval=True),
        _api_action("/api/operator/note-task-draft", "Draft task from note", writes=False, requires_approval=False),
    ]
    return {
        "mode": "read-only-notes-operations-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
            "note_count": len(note_rows),
            "active_note_count": len(active_rows),
            "archived_note_count": len(archived_rows),
            "pinned_note_count": len([row for row in active_rows if row.get("pinned")]),
            "checklist_note_count": len(checklist_rows),
            "open_checklist_item_count": open_checklist_count,
            "due_note_count": len(due_rows),
            "task_candidate_count": len(task_candidate_rows),
            "reminder_channel": reminder_channel,
            "notes_alert_count": len(alert_rows),
            "critical_notes_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "creates_notes": False,
            "updates_notes": False,
            "archives_notes": False,
            "deletes_notes": False,
            "fires_reminders": False,
            "creates_tasks": False,
            "uses_network": False,
        },
        "note_rows": note_rows[:12],
        "checklist_rows": checklist_rows[:8],
        "due_rows": due_rows[:8],
        "task_candidate_rows": task_candidate_rows[:8],
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Notes readiness only",
            "policy": (
                "This endpoint only inspects local note metadata, checklist counts, reminder posture, "
                "task-draft candidates, and API gates. It does not create notes, update notes, archive "
                "notes, delete notes, toggle checklist items, fire reminders, create tasks, run shell "
                "commands, or use network access."
            ),
        },
        "paths": {
            "notes": "data/app.db:notes",
            "activity": "data/operator_activity.json",
            "settings": "data/settings.json",
        },
    }
