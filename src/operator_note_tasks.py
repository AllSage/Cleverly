"""Read-only note-to-task draft planning for the operator API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_NOTE_TEXT = 2200
MAX_CANDIDATES = 8


def _trim(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _parse_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _note_dict(note: Any) -> dict[str, Any]:
    if isinstance(note, dict):
        raw = note
        items = _parse_items(raw.get("items"))
        return {
            "id": _trim(raw.get("id"), 120),
            "owner": _trim(raw.get("owner"), 120),
            "title": _trim(raw.get("title") or raw.get("name") or raw.get("summary"), 240),
            "content": _trim(raw.get("content") or raw.get("text"), MAX_NOTE_TEXT),
            "items": items,
            "note_type": _trim(raw.get("note_type") or raw.get("type"), 80),
            "archived": raw.get("archived") is True,
            "deleted": raw.get("deleted") is True,
            "created_at": _iso(raw.get("created_at") or raw.get("timestamp")),
            "updated_at": _iso(raw.get("updated_at") or raw.get("modified_at")),
        }
    items = _parse_items(getattr(note, "items", None))
    return {
        "id": _trim(getattr(note, "id", ""), 120),
        "owner": _trim(getattr(note, "owner", ""), 120),
        "title": _trim(getattr(note, "title", ""), 240),
        "content": _trim(getattr(note, "content", ""), MAX_NOTE_TEXT),
        "items": items,
        "note_type": _trim(getattr(note, "note_type", ""), 80),
        "archived": getattr(note, "archived", False) is True,
        "deleted": False,
        "created_at": _iso(getattr(note, "created_at", "")),
        "updated_at": _iso(getattr(note, "updated_at", "")),
    }


def note_task_text(note: dict[str, Any]) -> str:
    lines: list[str] = []
    if note.get("title"):
        lines.append(str(note["title"]))
    if note.get("content"):
        lines.append(str(note["content"]))
    for item in _parse_items(note.get("items")):
        text = item if isinstance(item, str) else item.get("text") if isinstance(item, dict) else ""
        if text:
            suffix = " [done]" if isinstance(item, dict) and item.get("done") else ""
            lines.append(f"- {text}{suffix}")
    return _trim("\n".join(lines), MAX_NOTE_TEXT)


def _note_title(note: dict[str, Any]) -> str:
    return _trim(note.get("title") or note.get("content") or note.get("id") or "local note", 80)


def _scheduled_tomorrow_morning(now: datetime | None = None) -> str:
    base = now or datetime.now().astimezone()
    scheduled = (base + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return scheduled.isoformat()


def task_draft_from_note(note: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    text = note_task_text(note or {})
    title = _note_title(note or {})
    prompt = (
        [
            "Review this local note and turn it into concrete next actions.",
            "Identify the next step, any blocked items, and whether a recurring task should be created.",
            "",
            text,
        ]
        if note and text
        else [
            "Review the local note I paste or select before saving this task.",
            "Turn it into concrete next actions, blocked items, and any recurring task recommendation.",
        ]
    )
    return {
        "name": f"Follow up: {title}"[:80] if note and text else "Follow up: local note",
        "task_type": "llm",
        "trigger_type": "schedule",
        "schedule": "once",
        "scheduled_date": _scheduled_tomorrow_morning(now),
        "output_target": "session",
        "notifications_enabled": True,
        "prompt": "\n".join(prompt),
    }


def _sort_key(note: dict[str, Any]) -> str:
    return str(note.get("updated_at") or note.get("created_at") or "")


def _load_recent_notes(owner: str, limit: int = MAX_CANDIDATES) -> list[dict[str, Any]]:
    from core.database import Note, SessionLocal

    db = SessionLocal()
    try:
        query = db.query(Note)
        if owner and owner != "local":
            query = query.filter(Note.owner == owner)
        else:
            query = query.filter((Note.owner == None) | (Note.owner == "") | (Note.owner == "local"))  # noqa: E711
        query = query.filter(Note.archived == False)  # noqa: E712
        notes = query.order_by(Note.pinned.desc(), Note.sort_order.asc(), Note.updated_at.desc()).limit(limit).all()
        return [_note_dict(note) for note in notes]
    finally:
        db.close()


def _entry_rows(candidate_count: int, selected: bool) -> list[dict[str, Any]]:
    source_detail = (
        f"Note To Task Draft opens with {candidate_count} local candidate note(s); saving still happens from Tasks review."
        if candidate_count
        else "Note To Task Draft opens first and asks for a local note before any task can be saved."
    )
    selected_detail = (
        "Workflow handoff can pass a selected note into a draft payload, but it cannot save, schedule, or run a task from this endpoint."
        if selected
        else "Workflow handoff stays in manual draft mode until a local note source is selected."
    )
    return [
        {
            "id": "note-task-dashboard-entry",
            "entry": "dashboard",
            "state": "ok" if candidate_count else "warn",
            "badge": "dash",
            "title": "Command Center dashboard",
            "detail": source_detail,
            "command_id": "draft-task-from-note",
            "action": "draft-task-from-note",
            "actionLabel": "Draft",
            "requires_review": True,
            "executes": False,
            "creates_task": False,
        },
        {
            "id": "note-task-text-entry",
            "entry": "text",
            "state": "ok",
            "badge": "text",
            "title": "Typed operator command",
            "detail": "The phrase 'Create a task from this note' resolves to a draft payload and Tasks review before any save.",
            "command_id": "draft-task-from-note",
            "action": "draft-task-from-note",
            "actionLabel": "Draft",
            "requires_review": True,
            "executes": False,
            "creates_task": False,
        },
        {
            "id": "note-task-palette-entry",
            "entry": "palette",
            "state": "ok",
            "badge": "cmd",
            "title": "Global command palette",
            "detail": "The palette exposes Create Task From Note as a local draft route, not an auto-save route.",
            "command_id": "draft-task-from-note",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_review": True,
            "executes": False,
            "creates_task": False,
        },
        {
            "id": "note-task-voice-entry",
            "entry": "voice",
            "state": "ok",
            "badge": "voice",
            "title": "Voice command mode",
            "detail": "Voice routing can open the same draft flow without storing full note text or draft prompts in activity.",
            "command_id": "draft-task-from-note",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
            "requires_review": True,
            "executes": False,
            "creates_task": False,
        },
        {
            "id": "note-task-workflow-entry",
            "entry": "workflow",
            "state": "ok" if selected else "warn",
            "badge": "flow",
            "title": "Automation workflow handoff",
            "detail": selected_detail,
            "command_id": "draft-task-from-note",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
            "requires_review": True,
            "executes": False,
            "creates_task": False,
        },
    ]


def _alert_rows(candidate_count: int, selected: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if candidate_count == 0:
        rows.append({
            "id": "note-task-no-note-candidates",
            "state": "warn",
            "badge": "note",
            "title": "No local notes available for task draft",
            "detail": "Create or select a local note before saving a task from this draft flow.",
            "action": "open-notes",
            "actionLabel": "Notes",
            "requires_approval": False,
            "executes": False,
            "creates_task": False,
            "uses_network": False,
        })
    elif not selected:
        rows.append({
            "id": "note-task-no-selected-note",
            "state": "warn",
            "badge": "draft",
            "title": "No note selected for task draft",
            "detail": "A manual draft can open, but saving a useful task needs a selected local note source.",
            "action": "draft-task-from-note",
            "actionLabel": "Draft",
            "requires_approval": False,
            "executes": False,
            "creates_task": False,
            "uses_network": False,
        })
    rows.append({
        "id": "note-task-save-review-required",
        "state": "warn",
        "badge": "ask",
        "title": "Task save requires review",
        "detail": "The draft payload is not saved, scheduled, run, or notified until the owner reviews it in Tasks.",
        "action": "open-tasks",
        "actionLabel": "Tasks",
        "requires_approval": True,
        "executes": False,
        "creates_task": False,
        "uses_network": False,
    })
    return rows


def run_operator_note_task_draft(
    owner: str,
    *,
    notes: list[Any] | None = None,
    note_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return read-only task draft evidence from recent local notes."""
    raw_notes = notes if notes is not None else _load_recent_notes(owner)
    candidates = [
        _note_dict(note)
        for note in raw_notes
        if not _note_dict(note).get("archived") and not _note_dict(note).get("deleted")
    ][:MAX_CANDIDATES]
    selected = None
    if note_id:
        selected = next((note for note in candidates if note.get("id") == note_id), None)
    if selected is None:
        selected = next((note for note in candidates if note_task_text(note)), None) or (candidates[0] if candidates else None)
    entry_rows = _entry_rows(len(candidates), selected is not None)
    alert_rows = _alert_rows(len(candidates), selected is not None)
    rows = []
    for note in candidates:
        text = note_task_text(note)
        draft = task_draft_from_note(note, now=now)
        rows.append({
            "id": note.get("id") or "",
            "state": "ok" if text else "warn",
            "title": _note_title(note),
            "detail": text or "No note body visible; draft opens with a manual prompt",
            "note": note,
            "draft": draft,
            "selected": selected is not None and note.get("id") == selected.get("id"),
        })
    selected_draft = task_draft_from_note(selected, now=now)
    evidence_rows = [
        {
            "id": "note-source",
            "state": "ok" if candidates else "warn",
            "badge": "note",
            "title": "Local note source",
            "detail": f"{len(candidates)} candidate note{'s' if len(candidates) != 1 else ''}; selected: {_note_title(selected) if selected else 'none'}",
            "action": "open-notes",
            "actionLabel": "Notes",
        },
        {
            "id": "task-draft-review",
            "state": "ok" if selected else "warn",
            "badge": "draft",
            "title": "Tasks review gate",
            "detail": "The generated payload opens in Tasks for owner review; it is not saved, scheduled, run, or notified by this plan.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
        },
        {
            "id": "activity-ledger",
            "state": "ok",
            "badge": "log",
            "title": "Note task activity ledger",
            "detail": "Opening a draft from Command Center or the command fallback is mirrored to data/operator_activity.json with note and draft metadata only; full note text and draft prompts are not stored.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
    ]
    api_actions = [
        {
            "id": "note-task-plan",
            "method": "GET",
            "path": "/api/operator/note-task-draft",
            "risk": "read-only-draft-evidence",
            "executes": False,
            "writes": False,
            "requires_approval": False,
        },
        {
            "id": "notes-read",
            "method": "GET",
            "path": "/api/notes",
            "risk": "local-note-read",
            "executes": False,
            "writes": False,
            "requires_approval": False,
        },
        {
            "id": "activity-metadata",
            "method": "POST",
            "path": "/api/operator/activity",
            "risk": "local-metadata-ledger",
            "executes": False,
            "writes": True,
            "requires_approval": False,
        },
    ]
    return {
        "mode": "read-only-note-task-draft",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "owner": owner,
        "summary": {
            "state": "ok" if selected else "warn",
            "notes": len(candidates),
            "selected": bool(selected),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "note_task_alert_count": len(alert_rows),
            "critical_note_task_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "creates_task": False,
            "activity_metadata_only": True,
            "writes_activity": False,
            "next_action": "Review the draft in Tasks before saving automation." if selected else "Create or select a note before saving a task.",
        },
        "selected_note": selected or {},
        "draft": selected_draft,
        "candidates": rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "evidence_rows": evidence_rows,
        "api_actions": api_actions,
        "approval": {
            "required_to_save": True,
            "policy": "This endpoint only drafts a task payload. It does not create, schedule, run, notify, or modify notes/tasks.",
        },
        "paths": {
            "notes": "data/app.db:notes",
            "tasks": "data/app.db:scheduled_tasks",
            "activity": "data/operator_activity.json",
        },
    }
