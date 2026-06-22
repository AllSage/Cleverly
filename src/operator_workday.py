"""Read-only workday and scheduling evidence for the operator console."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

MAX_ROWS = 8


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Any) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return _trim(value, 120)
    return dt.isoformat().replace("+00:00", "Z")


def _is_inactive(status: Any) -> bool:
    text = str(status or "").lower()
    return any(token in text for token in ("paused", "archived", "disabled", "deleted", "cancelled", "completed"))


def _is_failure(status: Any) -> bool:
    text = str(status or "").lower()
    return any(token in text for token in ("fail", "error", "blocked"))


def _is_running(status: Any) -> bool:
    text = str(status or "").lower()
    return any(token in text for token in ("running", "queued", "pending"))


def _value(source: Any, *names: str) -> Any:
    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source.get(name)
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _task_row(task: Any) -> dict[str, Any]:
    due_at = _value(task, "next_run", "scheduled_date", "due_at", "due")
    return {
        "id": _trim(_value(task, "id"), 120),
        "title": _trim(_value(task, "name", "title", "action", "prompt") or "Task", 160),
        "status": _trim(_value(task, "status") or "active", 80),
        "type": _trim(_value(task, "task_type", "type") or "llm", 80),
        "action": _trim(_value(task, "action") or "", 120),
        "schedule": _trim(_value(task, "schedule", "trigger_type") or "", 120),
        "due_at": _iso(due_at),
        "next_run": _iso(_value(task, "next_run")),
        "last_run": _iso(_value(task, "last_run")),
        "model": _trim(_value(task, "model") or "", 160),
    }


def _run_row(run: Any) -> dict[str, Any]:
    return {
        "id": _trim(_value(run, "id"), 120),
        "task_id": _trim(_value(run, "task_id"), 120),
        "title": _trim(_value(run, "task_name", "name", "title", "task_id") or "Task run", 160),
        "status": _trim(_value(run, "status", "state") or "running", 80),
        "started_at": _iso(_value(run, "started_at", "created_at")),
        "finished_at": _iso(_value(run, "finished_at", "updated_at")),
        "result": _trim(_value(run, "result") or "", 240),
        "error": _trim(_value(run, "error") or "", 240),
        "model": _trim(_value(run, "model") or "", 160),
    }


def _event_row(event: Any) -> dict[str, Any]:
    return {
        "id": _trim(_value(event, "uid", "id"), 160),
        "title": _trim(_value(event, "summary", "title", "name") or "Event", 160),
        "start": _iso(_value(event, "dtstart", "start")),
        "end": _iso(_value(event, "dtend", "end")),
        "all_day": bool(_value(event, "all_day")),
        "location": _trim(_value(event, "location") or "", 160),
        "importance": _trim(_value(event, "importance") or "normal", 80),
        "type": _trim(_value(event, "event_type", "type") or "", 80),
    }


def _note_row(note: Any) -> dict[str, Any]:
    content = _trim(_value(note, "content", "text") or "", 320)
    return {
        "id": _trim(_value(note, "id"), 160),
        "title": _trim(_value(note, "title", "name") or content or "Note", 160),
        "label": _trim(_value(note, "label") or "", 120),
        "pinned": bool(_value(note, "pinned")),
        "due_date": _trim(_value(note, "due_date", "due") or "", 120),
        "updated_at": _iso(_value(note, "updated_at", "modified_at", "created_at")),
        "has_text": bool(content or _value(note, "items")),
    }


def _owner_filter(query: Any, model: Any, owner: str) -> Any:
    if owner and owner != "local":
        return query.filter(model.owner == owner)
    return query


def _load_tasks(owner: str) -> list[Any]:
    from core.database import ScheduledTask, SessionLocal

    db = SessionLocal()
    try:
        query = _owner_filter(db.query(ScheduledTask), ScheduledTask, owner)
        return query.order_by(ScheduledTask.created_at.desc()).limit(80).all()
    finally:
        db.close()


def _load_runs(owner: str) -> list[Any]:
    from core.database import ScheduledTask, SessionLocal, TaskRun

    db = SessionLocal()
    try:
        query = db.query(TaskRun, ScheduledTask).join(ScheduledTask, TaskRun.task_id == ScheduledTask.id)
        query = _owner_filter(query, ScheduledTask, owner)
        rows = query.order_by(TaskRun.started_at.desc()).limit(80).all()
        out: list[dict[str, Any]] = []
        for run, task in rows:
            row = _run_row(run)
            row["title"] = _trim(getattr(task, "name", "") or row["title"], 160)
            out.append(row)
        return out
    finally:
        db.close()


def _load_events(owner: str, current: datetime) -> list[Any]:
    from core.database import CalendarCal, CalendarEvent, SessionLocal

    start = (current - timedelta(days=1)).replace(tzinfo=None)
    end = (current + timedelta(days=7)).replace(tzinfo=None)
    db = SessionLocal()
    try:
        query = db.query(CalendarEvent).join(CalendarCal).filter(
            CalendarEvent.status != "cancelled",
            CalendarEvent.dtend >= start,
            CalendarEvent.dtstart <= end,
        )
        if owner and owner != "local":
            query = query.filter(CalendarCal.owner == owner)
        return query.order_by(CalendarEvent.dtstart.asc()).limit(80).all()
    finally:
        db.close()


def _load_notes(owner: str) -> list[Any]:
    from core.database import Note, SessionLocal

    db = SessionLocal()
    try:
        query = db.query(Note).filter(Note.archived == False)  # noqa: E712
        query = _owner_filter(query, Note, owner)
        return query.order_by(Note.pinned.desc(), Note.updated_at.desc()).limit(80).all()
    finally:
        db.close()


def _section(provided: list[Any] | None, loader: Callable[[], list[Any]], mapper: Callable[[Any], dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if provided is not None:
        return [mapper(item) for item in provided], ""
    try:
        return [mapper(item) for item in loader()], ""
    except Exception as exc:
        return [], str(exc)[:500]


def _api_action(
    method: str,
    path: str,
    title: str,
    *,
    writes: bool = False,
    requires_approval: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": requires_approval,
        "uses_network": uses_network,
    }


def run_operator_workday_plan(
    owner: str = "local",
    *,
    tasks: list[Any] | None = None,
    runs: list[Any] | None = None,
    events: list[Any] | None = None,
    notes: list[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return read-only task, calendar, note, and briefing evidence."""
    owner = owner or "local"
    current = _now(now)
    today = current.date()
    task_rows, task_error = _section(tasks, lambda: _load_tasks(owner), _task_row)
    run_rows, run_error = _section(runs, lambda: _load_runs(owner), _run_row)
    event_rows, event_error = _section(events, lambda: _load_events(owner, current), _event_row)
    note_rows, note_error = _section(notes, lambda: _load_notes(owner), _note_row)

    active_tasks = [row for row in task_rows if not _is_inactive(row["status"])]
    due_today = [
        row for row in active_tasks
        if (dt := _parse_dt(row.get("due_at") or row.get("next_run"))) is not None and dt.date() == today
    ]
    overdue = [
        row for row in active_tasks
        if (dt := _parse_dt(row.get("due_at") or row.get("next_run"))) is not None and dt < current
    ]
    failed_runs = [row for row in run_rows if _is_failure(row.get("status"))]
    active_runs = [row for row in run_rows if _is_running(row.get("status"))]
    today_events = [
        row for row in event_rows
        if (dt := _parse_dt(row.get("start"))) is not None and dt.date() == today
    ]
    note_candidates = [row for row in note_rows if row.get("has_text")]
    source_errors = {
        name: error
        for name, error in {
            "tasks": task_error,
            "runs": run_error,
            "calendar": event_error,
            "notes": note_error,
        }.items()
        if error
    }

    work_rows = [
        {
            "state": "error" if overdue else ("warn" if due_today else "ok"),
            "badge": "task",
            "title": "Task schedule evidence",
            "detail": f"{len(active_tasks)} active; {len(due_today)} due today; {len(overdue)} overdue",
            "action": "open-tasks",
            "actionLabel": "Tasks",
        },
        {
            "state": "error" if failed_runs else ("warn" if active_runs else "ok"),
            "badge": "runs",
            "title": "Task run ledger",
            "detail": f"{len(active_runs)} active or queued; {len(failed_runs)} failed in recent runs",
            "action": "open-operations-queue" if failed_runs or active_runs else "open-tasks",
            "actionLabel": "Review" if failed_runs or active_runs else "Tasks",
        },
        {
            "state": "warn" if today_events else "ok",
            "badge": "cal",
            "title": "Calendar today",
            "detail": f"{len(today_events)} today; {len(event_rows)} visible in the local work window",
            "action": "open-calendar",
            "actionLabel": "Calendar",
        },
        {
            "state": "ok" if note_candidates else "warn",
            "badge": "note",
            "title": "Note-to-task readiness",
            "detail": f"{len(note_candidates)} note candidates with text; drafts still require review before save",
            "action": "draft-task-from-note" if note_candidates else "open-notes",
            "actionLabel": "Draft" if note_candidates else "Notes",
        },
        {
            "state": "ok",
            "badge": "gate",
            "title": "Workday write boundary",
            "detail": "This plan reads local work data only; task creation, calendar edits, sync, and runs stay approval-gated.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
    ]
    for name, error in source_errors.items():
        work_rows.append({
            "state": "warn",
            "badge": "src",
            "title": f"{name.title()} source",
            "detail": error,
            "action": "open-system-health",
            "actionLabel": "Health",
        })

    guard_rows = [
        {
            "state": "ok",
            "title": "Read-only daily briefing",
            "detail": "Summarize-today evidence is assembled from local tasks, runs, calendar events, notes, and operator ledgers.",
        },
        {
            "state": "ok",
            "title": "Task creation approval",
            "detail": "Draft payloads can be reviewed in Tasks, but this endpoint never saves, schedules, runs, or edits tasks.",
        },
        {
            "state": "ok",
            "title": "Calendar safety",
            "detail": "Calendar event creation, edits, deletion, import/export, and CalDAV sync are outside this plan.",
        },
        {
            "state": "ok",
            "title": "No side effects",
            "detail": "No notes are edited, notifications sent, automation loops started, shell commands run, or network calls made.",
        },
    ]
    api_actions = [
        _api_action("GET", "/api/operator/workday-plan", "Read workday/scheduling plan"),
        _api_action("GET", "/api/operator/briefing", "Read today briefing snapshot"),
        _api_action("GET", "/api/tasks?include_last_run=true", "Read scheduled tasks"),
        _api_action("GET", "/api/tasks/runs/recent", "Read recent task runs"),
        _api_action("GET", "/api/calendar/events", "Read local calendar events"),
        _api_action("GET", "/api/notes", "Read local notes"),
        _api_action("POST", "/api/tasks", "Create a task after review", writes=True, requires_approval=True),
        _api_action("POST", "/api/tasks/{task_id}/run", "Run a task after approval", writes=True, requires_approval=True),
        _api_action("POST", "/api/calendar/events", "Create a calendar event after review", writes=True, requires_approval=True),
        _api_action("POST", "/api/calendar/sync", "Calendar network sync after explicit enablement", writes=True, requires_approval=True, uses_network=True),
    ]
    evidence_rows = [
        {"label": "Tasks", "path": "data/app.db:scheduled_tasks", "detail": f"{len(task_rows)} rows visible"},
        {"label": "Task runs", "path": "data/app.db:task_runs", "detail": f"{len(run_rows)} recent rows visible"},
        {"label": "Calendar", "path": "data/app.db:calendars,calendar_events", "detail": f"{len(event_rows)} event rows visible"},
        {"label": "Notes", "path": "data/app.db:notes", "detail": f"{len(note_rows)} note rows visible"},
        {"label": "Activity", "path": "data/operator_activity.json", "detail": "Command status, logs, retry, and recovery evidence"},
        {"label": "Workflow catalog", "path": "data/operator_workflows.json", "detail": "Published local command/workflow readiness"},
    ]

    return {
        "mode": "read-only-workday-plan",
        "generated_at": _iso(current),
        "owner": owner,
        "summary": {
            "state": "warn" if source_errors or overdue or failed_runs else "ok",
            "task_count": len(task_rows),
            "active_task_count": len(active_tasks),
            "due_today_count": len(due_today),
            "overdue_count": len(overdue),
            "run_count": len(run_rows),
            "active_run_count": len(active_runs),
            "failed_run_count": len(failed_runs),
            "calendar_event_count": len(event_rows),
            "today_event_count": len(today_events),
            "note_count": len(note_rows),
            "note_task_candidate_count": len(note_candidates),
            "source_error_count": len(source_errors),
            "briefing_ready": not source_errors,
            "creates_tasks": False,
            "updates_tasks": False,
            "runs_tasks": False,
            "creates_calendar_events": False,
            "syncs_calendar": False,
            "edits_notes": False,
            "sends_notifications": False,
            "runs_automation": False,
            "runs_shell": False,
            "uses_network": False,
            "requires_write_approval": True,
        },
        "work_rows": work_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "task_rows": task_rows[:MAX_ROWS],
        "run_rows": run_rows[:MAX_ROWS],
        "calendar_rows": event_rows[:MAX_ROWS],
        "note_rows": note_rows[:MAX_ROWS],
        "source_errors": source_errors,
        "commands": {
            "briefing": "summarize-today",
            "note_task": "draft-task-from-note",
            "tasks": "open-tasks",
            "calendar": "open-calendar",
        },
        "approval": {
            "required": True,
            "policy": (
                "This endpoint only reads local workday data. It does not create tasks, update tasks, run tasks, "
                "create calendar events, sync calendars, edit notes, send notifications, start automation, run shell "
                "commands, or use network access."
            ),
        },
        "paths": {
            "tasks": "data/app.db:scheduled_tasks",
            "task_runs": "data/app.db:task_runs",
            "calendar": "data/app.db:calendars,calendar_events",
            "notes": "data/app.db:notes",
            "activity": "data/operator_activity.json",
            "workflows": "data/operator_workflows.json",
        },
    }
