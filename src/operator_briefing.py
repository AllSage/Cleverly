"""Read-only local briefing snapshot for the Cleverly operator console."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.database import (
    CalendarCal,
    CalendarEvent,
    Memory,
    Note,
    ScheduledTask,
    SessionLocal,
    TaskRun,
)
from src.constants import DATA_DIR
from src.operator_checks import run_operator_service_snapshot
from src.operator_models import run_operator_model_snapshot


MAX_ROWS = 8


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _trim(value: Any, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def _section(loader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"ok": True, **loader()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def _owner_filter(query, model, owner: str):
    if owner and owner != "local":
        return query.filter(model.owner == owner)
    return query


def _task_row(task: ScheduledTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": _trim(task.name or task.action or "Task", 160),
        "status": _trim(task.status or "active", 80),
        "type": _trim(task.task_type or "llm", 80),
        "action": _trim(task.action or "", 120),
        "schedule": _trim(task.schedule or task.trigger_type or "", 120),
        "next_run": _iso(task.next_run),
        "last_run": _iso(task.last_run),
        "run_count": int(task.run_count or 0),
        "model": _trim(task.model or "", 160),
    }


def _tasks(owner: str) -> dict[str, Any]:
    now = _utc_now().replace(tzinfo=None)
    end = now + timedelta(days=1)
    db = SessionLocal()
    try:
        query = _owner_filter(db.query(ScheduledTask), ScheduledTask, owner)
        rows = query.order_by(ScheduledTask.next_run.asc().nullslast(), ScheduledTask.created_at.desc()).all()
        active = [row for row in rows if str(row.status or "").lower() == "active"]
        due_today = [
            row for row in active
            if row.next_run is not None and now <= row.next_run <= end
        ]
        paused = [row for row in rows if str(row.status or "").lower() == "paused"]
        return {
            "total": len(rows),
            "active": len(active),
            "paused": len(paused),
            "due_today": len(due_today),
            "items": [_task_row(row) for row in (due_today or active or rows)[:MAX_ROWS]],
        }
    finally:
        db.close()


def _run_row(run: TaskRun, task: ScheduledTask | None = None) -> dict[str, Any]:
    return {
        "id": run.id,
        "task_id": run.task_id,
        "title": _trim(getattr(task, "name", "") or run.task_id, 160),
        "status": _trim(run.status or "running", 80),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "result": _trim(run.result or "", 240),
        "error": _trim(run.error or "", 240),
        "model": _trim(run.model or "", 160),
    }


def _runs(owner: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(TaskRun, ScheduledTask).join(ScheduledTask, TaskRun.task_id == ScheduledTask.id)
        query = _owner_filter(query, ScheduledTask, owner)
        rows = query.order_by(TaskRun.started_at.desc()).limit(MAX_ROWS).all()
        items = [_run_row(run, task) for run, task in rows]
        return {
            "total": len(items),
            "failed": sum(1 for item in items if any(key in item["status"].lower() for key in ("fail", "error"))),
            "running": sum(1 for item in items if "running" in item["status"].lower()),
            "items": items,
        }
    finally:
        db.close()


def _event_row(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.uid,
        "title": _trim(event.summary or "Event", 160),
        "start": _iso(event.dtstart),
        "end": _iso(event.dtend),
        "all_day": bool(event.all_day),
        "location": _trim(event.location or "", 160),
        "importance": _trim(event.importance or "normal", 80),
        "type": _trim(event.event_type or "", 80),
    }


def _events(owner: str) -> dict[str, Any]:
    now = _utc_now().replace(tzinfo=None)
    end = now + timedelta(days=7)
    db = SessionLocal()
    try:
        query = db.query(CalendarEvent).join(CalendarCal).filter(
            CalendarEvent.status != "cancelled",
            CalendarEvent.dtend >= now,
            CalendarEvent.dtstart <= end,
        )
        if owner and owner != "local":
            query = query.filter(CalendarCal.owner == owner)
        rows = query.order_by(CalendarEvent.dtstart.asc()).limit(MAX_ROWS).all()
        today = now.date()
        return {
            "total": len(rows),
            "today": sum(1 for row in rows if row.dtstart and row.dtstart.date() == today),
            "items": [_event_row(row) for row in rows],
        }
    finally:
        db.close()


def _note_row(note: Note) -> dict[str, Any]:
    return {
        "id": note.id,
        "title": _trim(note.title or note.content or "Note", 160),
        "label": _trim(note.label or "", 120),
        "pinned": bool(note.pinned),
        "due_date": _trim(note.due_date or "", 120),
        "source": _trim(note.source or "user", 80),
        "updated_at": _iso(note.updated_at),
    }


def _notes(owner: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(Note).filter(Note.archived == False)  # noqa: E712
        query = _owner_filter(query, Note, owner)
        rows = query.order_by(Note.pinned.desc(), Note.updated_at.desc()).limit(MAX_ROWS).all()
        return {
            "total": query.count(),
            "pinned": sum(1 for row in rows if row.pinned),
            "items": [_note_row(row) for row in rows],
        }
    finally:
        db.close()


def _memory_row(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "title": _trim(memory.text, 180),
        "category": _trim(memory.category or "fact", 100),
        "source": _trim(memory.source or "user", 100),
        "timestamp": memory.timestamp or 0,
    }


def _memories(owner: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(Memory)
        query = _owner_filter(query, Memory, owner)
        rows = query.order_by(Memory.timestamp.desc()).limit(MAX_ROWS).all()
        return {
            "total": query.count(),
            "items": [_memory_row(row) for row in rows],
        }
    finally:
        db.close()


def _json_store(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _operator_activity(owner: str) -> dict[str, Any]:
    data = _json_store(Path(DATA_DIR) / "operator_activity.json")
    rows = [
        item for item in data.get("records", [])
        if isinstance(item, dict) and str(item.get("owner") or "local") == owner
    ]
    rows.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return {
        "total": len(rows),
        "failed": sum(1 for row in rows if str(row.get("state") or row.get("status") or "").lower() in {"error", "failed"}),
        "items": rows[:MAX_ROWS],
        "path": "data/operator_activity.json",
    }


def _operator_workflows(owner: str) -> dict[str, Any]:
    data = _json_store(Path(DATA_DIR) / "operator_workflows.json")
    record = {}
    owners = data.get("owners")
    if isinstance(owners, dict):
        record = owners.get(owner) or {}
    return {
        "configured": bool(record),
        "loop_count": int(record.get("loop_count") or 0),
        "workflow_count": int(record.get("workflow_count") or 0),
        "ready_count": int(record.get("ready_count") or 0),
        "approval_gated_count": int(record.get("approval_gated_count") or 0),
        "updated_at": _trim(record.get("updated_at") or "", 80),
        "path": "data/operator_workflows.json",
    }


def _service_summary() -> dict[str, Any]:
    snapshot = run_operator_service_snapshot()
    summary = snapshot.get("summary") or {}
    return {
        "summary": summary,
        "ready": int(summary.get("error") or 0) == 0,
        "services": [
            {
                "id": item.get("id"),
                "state": item.get("state"),
                "label": item.get("label"),
                "detail": item.get("detail"),
            }
            for item in (snapshot.get("services") or [])[:MAX_ROWS]
            if isinstance(item, dict)
        ],
    }


def _model_summary() -> dict[str, Any]:
    snapshot = run_operator_model_snapshot()
    return {
        "primary": snapshot.get("primary") or {},
        "readiness": snapshot.get("readiness") or {},
        "endpoints": (snapshot.get("endpoints") or {}).get("counts") or {},
        "training": {
            "datasets": (snapshot.get("training") or {}).get("dataset_count", 0),
            "artifacts": (snapshot.get("training") or {}).get("artifact_count", 0),
        },
        "finetune": {
            "jobs": (snapshot.get("finetune") or {}).get("job_counts") or {},
            "trainable": (snapshot.get("finetune") or {}).get("trainable_count", 0),
        },
    }


def _headline_rows(sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tasks = sections.get("tasks", {})
    events = sections.get("calendar", {})
    runs = sections.get("runs", {})
    activity = sections.get("activity", {})
    workflows = sections.get("workflows", {})
    models = sections.get("models", {})
    services = sections.get("services", {})

    rows.append({
        "state": "ok" if tasks.get("ok") else "warn",
        "title": "Tasks",
        "detail": f"{tasks.get('active', 0)} active; {tasks.get('due_today', 0)} due today",
    })
    rows.append({
        "state": "ok" if events.get("ok") else "warn",
        "title": "Calendar",
        "detail": f"{events.get('today', 0)} today; {events.get('total', 0)} in the 7-day window",
    })
    rows.append({
        "state": "error" if runs.get("failed") else ("ok" if runs.get("ok") else "warn"),
        "title": "Recent task runs",
        "detail": f"{runs.get('running', 0)} running; {runs.get('failed', 0)} failed",
    })
    rows.append({
        "state": "error" if activity.get("failed") else ("ok" if activity.get("ok") else "warn"),
        "title": "Operator activity",
        "detail": f"{activity.get('total', 0)} local records; {activity.get('failed', 0)} failed",
    })
    rows.append({
        "state": "ok" if workflows.get("ready_count") == workflows.get("workflow_count") and workflows.get("workflow_count") else "warn",
        "title": "Automation routes",
        "detail": f"{workflows.get('ready_count', 0)}/{workflows.get('workflow_count', 0)} ready; {workflows.get('approval_gated_count', 0)} approval-gated",
    })
    readiness = models.get("readiness") if isinstance(models.get("readiness"), dict) else {}
    rows.append({
        "state": readiness.get("state") or "warn",
        "title": "Models and training",
        "detail": readiness.get("summary") or "model snapshot unavailable",
    })
    service_summary = services.get("summary") if isinstance(services.get("summary"), dict) else {}
    rows.append({
        "state": "error" if service_summary.get("error") else ("warn" if service_summary.get("warn") else "ok"),
        "title": "Local services",
        "detail": f"{service_summary.get('ok', 0)} ok; {service_summary.get('warn', 0)} warn; {service_summary.get('error', 0)} error",
    })
    return rows


def run_operator_briefing_snapshot(owner: str = "local") -> dict[str, Any]:
    """Return a local-first briefing snapshot for the current operator."""
    owner = owner or "local"
    sections = {
        "tasks": _section(lambda: _tasks(owner)),
        "runs": _section(lambda: _runs(owner)),
        "calendar": _section(lambda: _events(owner)),
        "notes": _section(lambda: _notes(owner)),
        "memory": _section(lambda: _memories(owner)),
        "activity": _section(lambda: _operator_activity(owner)),
        "workflows": _section(lambda: _operator_workflows(owner)),
        "models": _section(_model_summary),
        "services": _section(_service_summary),
    }
    return {
        "generated_at": _iso(_utc_now()),
        "mode": "read-only-local",
        "owner": owner,
        "headline_rows": _headline_rows(sections),
        "sections": sections,
        "paths": {
            "activity": "data/operator_activity.json",
            "workflows": "data/operator_workflows.json",
            "tasks": "data/app.db:scheduled_tasks",
            "task_runs": "data/app.db:task_runs",
            "calendar": "data/app.db:calendar_events",
            "notes": "data/app.db:notes",
            "memory": "data/app.db:memories",
        },
    }
