"""Unified read-only work operations evidence for the operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


MAX_ROWS = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _items(value: Any, *names: str) -> list[dict[str, Any]]:
    source = value
    if isinstance(source, dict):
        for name in names:
            candidate = source.get(name)
            if isinstance(candidate, list):
                source = candidate
                break
        else:
            return []
    if not isinstance(source, list):
        return []
    return [item for item in source if isinstance(item, dict)]


def _summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(plan, dict) and isinstance(plan.get("summary"), dict):
        return plan["summary"]
    return {}


def _state(*rows: dict[str, Any], fallback: str = "ok") -> str:
    states = [str(row.get("state") or "").lower() for row in rows if isinstance(row, dict)]
    if "error" in states:
        return "error"
    if "warn" in states:
        return "warn"
    return fallback


def _num(summary: dict[str, Any], *names: str) -> int:
    for name in names:
        try:
            return int(summary.get(name) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _api_action(
    method: str,
    path: str,
    title: str,
    *,
    writes: bool = False,
    deletes: bool = False,
    starts_job: bool = False,
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
        "executes": starts_job,
        "starts_job": starts_job,
        "sends_notification": sends_notification,
        "uses_network": uses_network,
        "requires_approval": requires_approval,
    }


def _entry_rows(state: str) -> list[dict[str, Any]]:
    common = {
        "work_ops_api": "/api/operator/work-ops-plan",
        "briefing_api": "/api/operator/briefing",
        "workday_api": "/api/operator/workday-plan",
        "tasks_api": "/api/operator/tasks-plan",
        "notes_api": "/api/operator/notes-plan",
        "calendar_api": "/api/operator/calendar-plan",
        "command_id": "summarize-today",
        "work_command_id": "open-work-preflight",
        "task_command_id": "open-tasks",
        "note_command_id": "open-notes",
        "calendar_command_id": "open-calendar",
        "draft_command_id": "draft-task-from-note",
        "requires_approval": True,
        "executes": False,
        "creates_tasks": False,
        "updates_tasks": False,
        "runs_tasks": False,
        "creates_notes": False,
        "updates_notes": False,
        "fires_reminders": False,
        "creates_calendar_events": False,
        "updates_calendar_events": False,
        "syncs_calendars": False,
        "starts_automation": False,
        "writes_activity": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "work-ops-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard work operations route",
            "detail": "The Command Center opens one read-only work operations view across briefing, tasks, notes, calendar, and scheduling gates.",
            "action": "open-work-preflight",
            "actionLabel": "Work",
        },
        {
            **common,
            "id": "work-ops-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed summarize-today route",
            "detail": "Typed requests like summarize today route to local work evidence before any task, note, calendar, reminder, or automation write.",
            "action": "summarize-today",
            "actionLabel": "Brief",
        },
        {
            **common,
            "id": "work-ops-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "pal",
            "title": "Palette work operations route",
            "detail": "Palette commands expose Tasks, Notes, Calendar, Work Preflight, and Task From Note through one permissioned work layer.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "work-ops-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice work operations route",
            "detail": "Voice transcripts can request briefing, note-to-task drafting, task review, and calendar review without bypassing approval gates.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "work-ops-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow work operations handoff",
            "detail": "Agent workflows can inspect work operations evidence, but creating tasks, syncing calendars, and firing reminders stay explicit.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _section_row(
    *,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
) -> dict[str, Any]:
    return {
        "state": state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
    }


def _alert_rows(
    *,
    briefing_plan: dict[str, Any],
    workday_plan: dict[str, Any],
    tasks_plan: dict[str, Any],
    notes_plan: dict[str, Any],
    calendar_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sources = [
        ("briefing", briefing_plan, "briefing_alert_count", "open-work-preflight"),
        ("workday", workday_plan, "alert_count", "open-work-preflight"),
        ("tasks", tasks_plan, "task_alert_count", "open-tasks"),
        ("notes", notes_plan, "notes_alert_count", "open-notes"),
        ("calendar", calendar_plan, "calendar_alert_count", "open-calendar"),
    ]
    for source, plan, fallback_count_key, default_action in sources:
        for row in _items(plan, "alert_rows")[:MAX_ROWS]:
            rows.append({
                "id": f"work-ops-{source}-{row.get('id') or len(rows) + 1}",
                "state": row.get("state") or "warn",
                "badge": row.get("badge") or source[:4],
                "title": row.get("title") or f"{source.title()} alert",
                "detail": row.get("detail") or f"{source.title()} alert from local work operations plan.",
                "action": row.get("action") or default_action,
                "actionLabel": row.get("actionLabel") or row.get("action_label") or "Review",
                "source": source,
                "requires_approval": row.get("requires_approval") is True or row.get("requiresApproval") is True,
                "uses_network": row.get("uses_network") is True or row.get("usesNetwork") is True,
            })
    if not rows:
        combined = [
            _num(_summary(plan), fallback_count_key)
            for _, plan, fallback_count_key, _ in sources
        ]
        if any(combined):
            rows.append({
                "id": "work-ops-alert-summary",
                "state": "warn",
                "badge": "sum",
                "title": "Work operations alerts summarized",
                "detail": "One or more source plans reported alerts but did not expose individual rows.",
                "action": "open-work-preflight",
                "actionLabel": "Work",
                "source": "summary",
                "requires_approval": False,
                "uses_network": False,
            })
    return rows[:MAX_ROWS]


def _handoff_rows(
    *,
    briefing_plan: dict[str, Any],
    tasks_plan: dict[str, Any],
    notes_plan: dict[str, Any],
    calendar_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    briefing_summary = _summary(briefing_plan)
    tasks_summary = _summary(tasks_plan)
    notes_summary = _summary(notes_plan)
    calendar_summary = _summary(calendar_plan)
    task_candidates = _num(notes_summary, "task_candidate_count")
    failed_runs = _num(tasks_summary, "failed_run_count")
    due_today = _num(tasks_summary, "due_today_count") or _num(briefing_summary, "due_today_count")
    today_events = _num(calendar_summary, "today_event_count") or _num(briefing_summary, "today_event_count")
    sync_configured = bool(calendar_summary.get("calendar_sync_configured"))
    common = {
        "executes": False,
        "creates_tasks": False,
        "updates_tasks": False,
        "runs_tasks": False,
        "creates_notes": False,
        "updates_notes": False,
        "fires_reminders": False,
        "creates_calendar_events": False,
        "updates_calendar_events": False,
        "syncs_calendars": False,
        "starts_automation": False,
        "writes_activity": False,
        "sends_notifications": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "handoff-summarize-today",
            "state": "ok" if briefing_plan.get("mode") else "warn",
            "badge": "brief",
            "title": "Summarize today handoff",
            "detail": f"{due_today} task(s) due and {today_events} calendar event(s) feed the read-only briefing before work starts.",
            "action": "summarize-today",
            "actionLabel": "Brief",
            "requires_approval": False,
        },
        {
            **common,
            "id": "handoff-task-review",
            "state": "warn" if failed_runs else ("ok" if tasks_plan.get("mode") else "loading"),
            "badge": "task",
            "title": "Task review handoff",
            "detail": f"{due_today} task(s) due today; {failed_runs} failed run(s) need Activity or Tasks review before retry.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
            "requires_approval": False,
        },
        {
            **common,
            "id": "handoff-note-task-draft",
            "state": "ok" if task_candidates else "loading",
            "badge": "note",
            "title": "Note-to-task draft handoff",
            "detail": f"{task_candidates} note candidate(s) can open a draft; saving or scheduling remains an explicit Tasks action.",
            "action": "draft-task-from-note",
            "actionLabel": "Draft",
            "requires_approval": bool(task_candidates),
        },
        {
            **common,
            "id": "handoff-calendar-review",
            "state": "warn" if today_events else ("ok" if calendar_plan.get("mode") else "loading"),
            "badge": "cal",
            "title": "Calendar review handoff",
            "detail": f"{today_events} event(s) today; creating, editing, or deleting events stays in Calendar.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": False,
        },
        {
            **common,
            "id": "handoff-calendar-sync-gate",
            "state": "warn" if sync_configured else "ok",
            "badge": "sync",
            "title": "Calendar sync gate",
            "detail": "Remote calendar sync is configured and remains network approval-gated." if sync_configured else "No remote calendar sync is configured in the current work evidence.",
            "action": "open-calendar",
            "actionLabel": "Sync",
            "requires_approval": sync_configured,
            "syncs_calendars": False,
            "uses_network": False,
            "network_after_approval": sync_configured,
        },
    ]


def run_operator_work_ops_plan(
    owner: str = "local",
    *,
    briefing_plan: dict[str, Any] | None = None,
    workday_plan: dict[str, Any] | None = None,
    tasks_plan: dict[str, Any] | None = None,
    notes_plan: dict[str, Any] | None = None,
    calendar_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one read-only work operations view across briefing, tasks, notes, and calendar."""
    owner = owner or "local"
    briefing_plan = briefing_plan if isinstance(briefing_plan, dict) else {}
    workday_plan = workday_plan if isinstance(workday_plan, dict) else {}
    tasks_plan = tasks_plan if isinstance(tasks_plan, dict) else {}
    notes_plan = notes_plan if isinstance(notes_plan, dict) else {}
    calendar_plan = calendar_plan if isinstance(calendar_plan, dict) else {}

    briefing_summary = _summary(briefing_plan)
    workday_summary = _summary(workday_plan)
    tasks_summary = _summary(tasks_plan)
    notes_summary = _summary(notes_plan)
    calendar_summary = _summary(calendar_plan)

    source_rows = [
        _section_row(
            state=_trim(briefing_summary.get("state") or "ok", 20),
            badge="brief",
            title="Today briefing",
            detail=(
                f"{_num(briefing_summary, 'due_today_count')} task(s) due; "
                f"{_num(briefing_summary, 'today_event_count')} event(s) today; "
                f"{_num(briefing_summary, 'briefing_alert_count')} briefing alert(s)"
            ),
            action="summarize-today",
            action_label="Brief",
        ),
        _section_row(
            state=_trim(workday_summary.get("state") or "ok", 20),
            badge="work",
            title="Workday evidence",
            detail=(
                f"{_num(workday_summary, 'active_task_count')} active task(s); "
                f"{_num(workday_summary, 'today_event_count')} event(s) today; "
                f"{_num(workday_summary, 'note_task_candidate_count')} note task candidate(s)"
            ),
            action="open-work-preflight",
            action_label="Work",
        ),
        _section_row(
            state=_trim(tasks_summary.get("state") or "ok", 20),
            badge="task",
            title="Task automation",
            detail=(
                f"{_num(tasks_summary, 'active_task_count')} active; "
                f"{_num(tasks_summary, 'due_today_count')} due today; "
                f"{_num(tasks_summary, 'failed_run_count')} failed run(s); "
                f"{_num(tasks_summary, 'task_alert_count')} alert(s)"
            ),
            action="open-tasks",
            action_label="Tasks",
        ),
        _section_row(
            state=_trim(notes_summary.get("state") or "ok", 20),
            badge="note",
            title="Notes and task drafts",
            detail=(
                f"{_num(notes_summary, 'active_note_count')} active note(s); "
                f"{_num(notes_summary, 'task_candidate_count')} task candidate(s); "
                f"{_num(notes_summary, 'due_note_count')} due note(s)"
            ),
            action="open-notes",
            action_label="Notes",
        ),
        _section_row(
            state=_trim(calendar_summary.get("state") or "ok", 20),
            badge="cal",
            title="Calendar window",
            detail=(
                f"{_num(calendar_summary, 'event_count')} event(s); "
                f"{_num(calendar_summary, 'today_event_count')} today; "
                f"{_num(calendar_summary, 'calendar_alert_count')} alert(s); "
                f"sync configured={bool(calendar_summary.get('calendar_sync_configured'))}"
            ),
            action="open-calendar",
            action_label="Calendar",
        ),
    ]
    alert_rows = _alert_rows(
        briefing_plan=briefing_plan,
        workday_plan=workday_plan,
        tasks_plan=tasks_plan,
        notes_plan=notes_plan,
        calendar_plan=calendar_plan,
    )
    state = _state(*source_rows, *alert_rows)
    entry_rows = _entry_rows(state)
    handoff_rows = _handoff_rows(
        briefing_plan=briefing_plan,
        tasks_plan=tasks_plan,
        notes_plan=notes_plan,
        calendar_plan=calendar_plan,
    )
    guard_rows = [
        _section_row(
            state="ok",
            badge="read",
            title="Read-only aggregate",
            detail="This plan reads existing local work evidence only and does not mutate tasks, notes, calendars, reminders, activity, or workflows.",
            action="open-work-preflight",
            action_label="Work",
        ),
        _section_row(
            state="ok",
            badge="ask",
            title="Write approval boundary",
            detail="Creating tasks, updating tasks, running tasks, creating notes, editing notes, firing reminders, and changing calendars require explicit review.",
            action="open-trust-controls",
            action_label="Trust",
        ),
        _section_row(
            state="ok",
            badge="net",
            title="Network boundary",
            detail="Calendar sync, webhook triggers, external notifications, and remote integrations remain outside this local plan and require network approval.",
            action="open-offline",
            action_label="Policy",
        ),
        _section_row(
            state="ok",
            badge="log",
            title="Activity visibility",
            detail="Follow-up actions route through the command catalog and activity timeline so retry, rollback, and logs stay visible.",
            action="open-activity-preflight",
            action_label="Activity",
        ),
    ]
    api_actions = [
        _api_action("GET", "/api/operator/work-ops-plan", "Read unified work operations plan"),
        _api_action("GET", "/api/operator/briefing", "Read today briefing snapshot"),
        _api_action("GET", "/api/operator/workday-plan", "Read workday evidence"),
        _api_action("GET", "/api/operator/tasks-plan", "Read task automation evidence"),
        _api_action("GET", "/api/operator/notes-plan", "Read notes and reminder evidence"),
        _api_action("GET", "/api/operator/calendar-plan", "Read calendar window evidence"),
        _api_action("GET", "/api/operator/note-task-draft", "Draft task payload from note metadata"),
        _api_action("POST", "/api/tasks", "Create scheduled task after review", writes=True, requires_approval=True),
        _api_action("POST", "/api/tasks/{task_id}/run", "Run scheduled task after approval", writes=True, starts_job=True, requires_approval=True),
        _api_action("POST", "/api/notes", "Create note after review", writes=True, requires_approval=True),
        _api_action("POST", "/api/notes/fire-reminder", "Fire note reminder after approval", writes=True, sends_notification=True, requires_approval=True),
        _api_action("POST", "/api/calendar/events", "Create calendar event after review", writes=True, requires_approval=True),
        _api_action("POST", "/api/calendar/sync", "Sync remote calendar after explicit network approval", writes=True, uses_network=True, requires_approval=True),
    ]
    source_error_count = sum(1 for plan in (briefing_plan, workday_plan, tasks_plan, notes_plan, calendar_plan) if plan.get("ok") is False)
    route_ready = sum(1 for row in entry_rows if row.get("state") == "ok")
    return {
        "mode": "read-only-work-operations-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "task_count": _num(tasks_summary, "task_count") or _num(workday_summary, "task_count"),
            "active_task_count": _num(tasks_summary, "active_task_count") or _num(workday_summary, "active_task_count"),
            "due_today_count": _num(tasks_summary, "due_today_count") or _num(briefing_summary, "due_today_count"),
            "failed_run_count": _num(tasks_summary, "failed_run_count") or _num(workday_summary, "failed_run_count"),
            "today_event_count": _num(calendar_summary, "today_event_count") or _num(briefing_summary, "today_event_count"),
            "calendar_event_count": _num(calendar_summary, "event_count") or _num(workday_summary, "calendar_event_count"),
            "note_count": _num(notes_summary, "note_count") or _num(workday_summary, "note_count"),
            "task_candidate_count": _num(notes_summary, "task_candidate_count") or _num(workday_summary, "note_task_candidate_count"),
            "briefing_alert_count": _num(briefing_summary, "briefing_alert_count"),
            "workday_alert_count": _num(workday_summary, "alert_count"),
            "task_alert_count": _num(tasks_summary, "task_alert_count"),
            "notes_alert_count": _num(notes_summary, "notes_alert_count"),
            "calendar_alert_count": _num(calendar_summary, "calendar_alert_count"),
            "work_ops_alert_count": len(alert_rows),
            "critical_work_ops_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
            "source_error_count": source_error_count,
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": route_ready,
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "summarize_today_ready": bool(briefing_plan.get("mode") or workday_plan.get("mode")),
            "note_task_draft_ready": bool(_num(notes_summary, "task_candidate_count") or _num(workday_summary, "note_task_candidate_count")),
            "calendar_review_ready": bool(calendar_plan.get("mode")),
            "task_review_ready": bool(tasks_plan.get("mode")),
            "creates_tasks": False,
            "updates_tasks": False,
            "runs_tasks": False,
            "creates_notes": False,
            "updates_notes": False,
            "fires_reminders": False,
            "creates_calendar_events": False,
            "updates_calendar_events": False,
            "syncs_calendars": False,
            "runs_automation": False,
            "writes_activity": False,
            "sends_notifications": False,
            "runs_shell": False,
            "uses_network": False,
            "requires_write_approval": True,
        },
        "work_ops_rows": source_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "source_plans": {
            "briefing": briefing_plan.get("mode") or "",
            "workday": workday_plan.get("mode") or "",
            "tasks": tasks_plan.get("mode") or "",
            "notes": notes_plan.get("mode") or "",
            "calendar": calendar_plan.get("mode") or "",
        },
        "paths": {
            "tasks": "data/app.db:scheduled_tasks",
            "task_runs": "data/app.db:task_runs",
            "notes": "data/app.db:notes",
            "calendar": "data/app.db:calendars,calendar_events",
            "activity": "data/operator_activity.json",
            "settings": "data/settings.json",
        },
        "approval": {
            "required": False,
            "gate": "Work operations readiness only",
            "policy": (
                "This endpoint aggregates existing local briefing, workday, task, notes, and calendar plans. "
                "It does not create tasks, update tasks, run tasks, create notes, update notes, fire reminders, "
                "create calendar events, update calendar events, sync calendars, start automation, write activity, "
                "send notifications, run shell commands, or use network access."
            ),
        },
    }
