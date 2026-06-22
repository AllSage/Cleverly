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
    return {
        "mode": "read-only-note-task-draft",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "owner": owner,
        "summary": {
            "state": "ok" if selected else "warn",
            "notes": len(candidates),
            "selected": bool(selected),
            "creates_task": False,
            "next_action": "Review the draft in Tasks before saving automation." if selected else "Create or select a note before saving a task.",
        },
        "selected_note": selected or {},
        "draft": selected_draft,
        "candidates": rows,
        "approval": {
            "required_to_save": True,
            "policy": "This endpoint only drafts a task payload. It does not create, schedule, run, notify, or modify notes/tasks.",
        },
        "paths": {
            "notes": "data/app.db:notes",
            "tasks": "data/app.db:scheduled_tasks",
        },
    }
