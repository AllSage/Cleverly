"""Read-only calendar and scheduling readiness plan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.settings import load_features, load_settings, offline_mode


MAX_ROWS = 12


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _parse_time(value: Any) -> datetime | None:
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
    dt = _parse_time(value)
    if dt is None:
        return _trim(value, 120)
    return dt.isoformat().replace("+00:00", "Z")


def _time_sort_value(value: Any) -> float:
    dt = _parse_time(value)
    return dt.timestamp() if dt is not None else 253402300799.0


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


def _owner_matches(row: dict[str, Any], owner: str) -> bool:
    row_owner = row.get("owner")
    calendar_owner = row.get("calendar_owner")
    if owner == "local":
        return row_owner in (None, "", "local") and calendar_owner in (None, "", "local")
    return row_owner in (None, "", owner) and calendar_owner in (None, "", owner)


def _event_row(event: Any) -> dict[str, Any]:
    calendar = _value(event, "calendar")
    calendar_name = _value(calendar, "name") if calendar is not None else ""
    calendar_owner = _value(calendar, "owner") if calendar is not None else ""
    calendar_label = _value(event, "calendar_name")
    if not calendar_label and isinstance(calendar, str):
        calendar_label = calendar
    if not calendar_label:
        calendar_label = calendar_name
    start = _iso(_value(event, "dtstart", "start", "starts_at"))
    end = _iso(_value(event, "dtend", "end", "ends_at"))
    reminder_minutes = _value(event, "reminder_minutes", "reminder", "alarm_minutes")
    try:
        reminder_value = int(reminder_minutes) if reminder_minutes not in (None, "") else 0
    except (TypeError, ValueError):
        reminder_value = 0
    status = _trim(_value(event, "status") or "confirmed", 80)
    all_day = bool(_value(event, "all_day"))
    return {
        "id": _trim(_value(event, "uid", "id"), 160),
        "state": "loading" if status == "cancelled" else ("warn" if reminder_value > 0 else "ok"),
        "badge": "cal",
        "title": _trim(_value(event, "summary", "title", "name") or "Calendar event", 240),
        "detail": f"{start or 'unscheduled'} to {end or 'open'}; reminder {reminder_value or 'none'}",
        "start": start,
        "end": end,
        "all_day": all_day,
        "location": _trim(_value(event, "location") or "", 160),
        "calendar": _trim(calendar_label or "", 160),
        "calendar_id": _trim(_value(event, "calendar_href", "calendar_id") or "", 160),
        "owner": _value(event, "owner"),
        "calendar_owner": calendar_owner,
        "status": status,
        "importance": _trim(_value(event, "importance") or "normal", 80),
        "type": _trim(_value(event, "event_type", "type") or "", 80),
        "reminder_minutes": reminder_value,
        "has_reminder": reminder_value > 0,
        "recurring": bool(_trim(_value(event, "rrule") or "", 400)),
    }


def _load_event_rows(owner: str, events: list[Any] | None, current: datetime) -> list[dict[str, Any]]:
    if isinstance(events, list):
        source = events
    else:
        source = []
        try:
            from core.database import CalendarCal, CalendarEvent, SessionLocal

            start = (current - timedelta(days=1)).replace(tzinfo=None)
            end = (current + timedelta(days=30)).replace(tzinfo=None)
            db = SessionLocal()
            try:
                query = db.query(CalendarEvent).join(CalendarCal).filter(
                    CalendarEvent.status != "cancelled",
                    CalendarEvent.dtend >= start,
                    CalendarEvent.dtstart <= end,
                )
                if owner != "local":
                    query = query.filter(CalendarCal.owner == owner)
                else:
                    query = query.filter((CalendarCal.owner == None) | (CalendarCal.owner == "") | (CalendarCal.owner == "local"))  # noqa: E711
                source = query.order_by(CalendarEvent.dtstart.asc()).limit(120).all()
            finally:
                db.close()
        except Exception:
            source = []
    rows = [_event_row(event) for event in source]
    rows = [row for row in rows if _owner_matches(row, owner)]
    rows.sort(key=lambda row: _time_sort_value(row.get("start")))
    return rows[:120]


def _feature_enabled(features: dict[str, Any], key: str, default: bool = True) -> bool:
    if key in features:
        return bool(features.get(key))
    return default


def _calendar_sync_configured(settings: dict[str, Any]) -> bool:
    caldav = settings.get("caldav")
    if isinstance(caldav, dict) and _trim(caldav.get("url"), 200):
        return True
    for key in ("calendar_sync_enabled", "caldav_enabled", "google_calendar_enabled", "email_auto_calendar"):
        if bool(settings.get(key)):
            return True
    return False


def _api_action(
    path: str,
    title: str,
    *,
    method: str = "GET",
    writes: bool = False,
    deletes: bool = False,
    uploads: bool = False,
    exports: bool = False,
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
        "uploads": uploads,
        "exports": exports,
        "executes": False,
        "sends_notification": sends_notification,
        "uses_network": uses_network,
        "requires_approval": requires_approval,
    }


def _calendar_alert_rows(
    *,
    rows: list[dict[str, Any]],
    settings: dict[str, Any],
    features: dict[str, Any],
    offline: bool,
    current: datetime,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    today = current.date()
    today_rows = [
        row for row in rows
        if (dt := _parse_time(row.get("start"))) is not None and dt.date() == today
    ]
    upcoming_rows = [
        row for row in rows
        if (dt := _parse_time(row.get("start"))) is not None and current <= dt <= current + timedelta(days=7)
    ]
    reminder_rows = [row for row in rows if row.get("has_reminder")]
    recurring_rows = [row for row in rows if row.get("recurring")]
    if not rows:
        alerts.append({
            "id": "calendar-empty",
            "state": "warn",
            "badge": "cal",
            "title": "No local calendar events visible",
            "detail": "Calendar storage is available, but no event rows are visible in the local scheduling window.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": False,
            "uses_network": False,
        })
    if today_rows:
        alerts.append({
            "id": "calendar-today",
            "state": "warn",
            "badge": "today",
            "title": "Calendar events due today",
            "detail": f"{len(today_rows)} event{'s' if len(today_rows) != 1 else ''} scheduled today; edits stay in Calendar.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": False,
            "uses_network": False,
        })
    if upcoming_rows:
        alerts.append({
            "id": "calendar-upcoming",
            "state": "warn",
            "badge": "week",
            "title": "Upcoming calendar window active",
            "detail": f"{len(upcoming_rows)} event{'s' if len(upcoming_rows) != 1 else ''} visible in the next seven days.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": False,
            "uses_network": False,
        })
    if reminder_rows:
        alerts.append({
            "id": "calendar-reminders",
            "state": "warn",
            "badge": "bell",
            "title": "Calendar reminders need review",
            "detail": f"{len(reminder_rows)} event reminder{'s' if len(reminder_rows) != 1 else ''} visible; dispatch remains permissioned.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": False,
            "uses_network": False,
        })
    if recurring_rows:
        alerts.append({
            "id": "calendar-recurring",
            "state": "warn",
            "badge": "rrule",
            "title": "Recurring calendar rules present",
            "detail": f"{len(recurring_rows)} recurring event rule{'s' if len(recurring_rows) != 1 else ''} should be reviewed before edits/imports.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": False,
            "uses_network": False,
        })
    sync_configured = _calendar_sync_configured(settings)
    network_allowed = _feature_enabled(features, "network_integrations", True)
    calendar_feature = _feature_enabled(features, "calendar", True)
    if not calendar_feature:
        alerts.append({
            "id": "calendar-feature-disabled",
            "state": "warn",
            "badge": "flag",
            "title": "Calendar feature disabled",
            "detail": "Calendar routes are configured but the Calendar feature flag is disabled.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if sync_configured and (offline or not network_allowed):
        alerts.append({
            "id": "calendar-sync-blocked",
            "state": "warn",
            "badge": "net",
            "title": "Calendar sync blocked by local policy",
            "detail": "CalDAV/email calendar sync appears configured, but offline or network policy blocks network access.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if sync_configured and not offline and network_allowed:
        alerts.append({
            "id": "calendar-sync-approval",
            "state": "warn",
            "badge": "sync",
            "title": "Calendar sync requires approval",
            "detail": "Remote calendar sync is configured; the operator plan will not contact or pull from it automatically.",
            "action": "open-calendar",
            "actionLabel": "Calendar",
            "requires_approval": True,
            "uses_network": True,
        })
    alerts.append({
        "id": "calendar-write-delete-gates",
        "state": "warn",
        "badge": "ask",
        "title": "Calendar write/delete gates require review",
        "detail": "Creating, updating, deleting, importing, exporting, syncing, and firing notifications remain explicit actions.",
        "action": "open-trust-controls",
        "actionLabel": "Trust",
        "requires_approval": True,
        "uses_network": False,
    })
    return alerts[:16]


def _entry_rows(*, calendar_enabled: bool) -> list[dict[str, Any]]:
    state = "ok" if calendar_enabled else "warn"
    common = {
        "command_id": "open-work-preflight",
        "start_command_id": "open-calendar",
        "approval_api": "/api/calendar/events",
        "sync_api": "/api/calendar/sync",
        "requires_approval": True,
        "executes": False,
        "creates_events": False,
        "updates_events": False,
        "deletes_events": False,
        "imports_calendars": False,
        "exports_calendars": False,
        "syncs_calendars": False,
        "sends_notifications": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "calendar-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard calendar preflight",
            "detail": "The Work panel opens read-only calendar posture before any event write, import, export, sync, or notification.",
            "action": "open-work-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "calendar-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed calendar request route",
            "detail": "Typed calendar requests route to Work Operations Preflight before opening Calendar or write-capable calendar APIs.",
            "action": "open-work-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "calendar-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette calendar route",
            "detail": "The command palette separates calendar review from event create, edit, delete, import, export, and sync APIs.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "calendar-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice calendar route",
            "detail": "Voice transcripts use the same local command route and open calendar preflight before any calendar write or sync.",
            "action": "start-voice-command",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "calendar-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow calendar handoff",
            "detail": "Workflow handoff can review local calendar windows and sync policy, but writes and remote sync stay explicit.",
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
        "creates_events": False,
        "updates_events": False,
        "deletes_events": False,
        "imports_calendars": False,
        "exports_calendars": False,
        "syncs_calendars": False,
        "tests_remote_connection": False,
        "sends_notifications": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    calendar_enabled: bool,
    event_count: int,
    today_count: int,
    upcoming_count: int,
    reminder_count: int,
    recurring_count: int,
    sync_configured: bool,
    offline: bool,
    network_allowed: bool,
) -> list[dict[str, Any]]:
    create_state = "ok" if calendar_enabled else "warn"
    delete_state = "warn" if event_count else "loading"
    import_state = "warn"
    sync_state = "warn" if sync_configured else "ok"
    reminder_state = "warn" if reminder_count or today_count or upcoming_count else "ok"
    recurring_state = "warn" if recurring_count else "ok"
    activity_state = "warn" if today_count or sync_configured else "ok"
    sync_detail = (
        f"sync_configured={sync_configured}; offline={offline}; network_integrations_enabled={network_allowed}; "
        "remote sync/test is approval-gated and never runs from this plan."
    )
    return [
        _handoff_row(
            "calendar-create-update-handoff",
            create_state,
            "edit",
            "Create and update handoff",
            f"{event_count} local event(s) are visible; event creation, quick parse, and updates stay in Calendar review.",
            "open-calendar",
            "Calendar",
            target_api="/api/calendar/events",
        ),
        _handoff_row(
            "calendar-delete-lifecycle-handoff",
            delete_state,
            "del",
            "Delete and lifecycle handoff",
            "Calendar event and calendar deletion remove local schedule evidence and require explicit review.",
            "open-work-preflight",
            "Preflight",
            target_api="/api/calendar/events/{uid}",
        ),
        _handoff_row(
            "calendar-import-export-handoff",
            import_state,
            "ics",
            "Import and export handoff",
            "ICS import/export moves calendar data across file boundaries and stays in owner-selected Calendar actions.",
            "open-calendar",
            "Calendar",
            target_api="/api/calendar/import",
        ),
        _handoff_row(
            "calendar-sync-egress-handoff",
            sync_state,
            "sync",
            "Remote sync and connection-test handoff",
            sync_detail,
            "open-offline",
            "Policy",
            target_api="/api/calendar/sync",
        ),
        _handoff_row(
            "calendar-reminder-notification-handoff",
            reminder_state,
            "bell",
            "Reminder and notification handoff",
            f"{reminder_count} reminder event(s), {today_count} event(s) today, and {upcoming_count} upcoming event(s) require visible dispatch posture.",
            "open-calendar",
            "Calendar",
            target_api="/api/calendar/events",
        ),
        _handoff_row(
            "calendar-recurring-rule-handoff",
            recurring_state,
            "rrule",
            "Recurring rule handoff",
            f"{recurring_count} recurring rule(s) are visible; recurring edits/imports should be reviewed before changing repeated schedule state.",
            "open-calendar",
            "Calendar",
            target_api="/api/calendar/events/{uid}",
        ),
        _handoff_row(
            "calendar-activity-recovery-handoff",
            activity_state,
            "log",
            "Activity and recovery handoff",
            "Calendar writes, imports, exports, sync attempts, and reminder dispatch should leave activity evidence and rollback context.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
        ),
    ]


def run_operator_calendar_plan(
    owner: str = "local",
    *,
    events: list[Any] | None = None,
    settings: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    offline: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return read-only local Calendar, reminder, and sync evidence."""
    owner = owner or "local"
    current = _parse_time(now) or datetime.now(timezone.utc)
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
    event_rows = _load_event_rows(owner, events, current)
    today = current.date()
    today_rows = [
        row for row in event_rows
        if (dt := _parse_time(row.get("start"))) is not None and dt.date() == today
    ]
    upcoming_rows = [
        row for row in event_rows
        if (dt := _parse_time(row.get("start"))) is not None and current <= dt <= current + timedelta(days=7)
    ]
    all_day_rows = [row for row in event_rows if row.get("all_day")]
    reminder_rows = [row for row in event_rows if row.get("has_reminder")]
    recurring_rows = [row for row in event_rows if row.get("recurring")]
    sync_configured = _calendar_sync_configured(settings)
    network_allowed = _feature_enabled(features, "network_integrations", True)
    calendar_enabled = _feature_enabled(features, "calendar", True)
    alert_rows = _calendar_alert_rows(
        rows=event_rows,
        settings=settings,
        features=features,
        offline=offline_state,
        current=current,
    )
    entry_rows = _entry_rows(calendar_enabled=calendar_enabled)
    handoff_rows = _handoff_rows(
        calendar_enabled=calendar_enabled,
        event_count=len(event_rows),
        today_count=len(today_rows),
        upcoming_count=len(upcoming_rows),
        reminder_count=len(reminder_rows),
        recurring_count=len(recurring_rows),
        sync_configured=sync_configured,
        offline=offline_state,
        network_allowed=network_allowed,
    )
    api_actions = [
        _api_action("/api/operator/calendar-plan", "Read calendar operations plan"),
        _api_action("/api/calendar/events", "Read calendar events"),
        _api_action("/api/calendar/calendars", "Read calendar list"),
        _api_action("/api/calendar/config", "Read calendar sync config"),
        _api_action("/api/calendar/events", "Create calendar event", method="POST", writes=True, requires_approval=True),
        _api_action("/api/calendar/events/{uid}", "Update calendar event", method="PUT", writes=True, requires_approval=True),
        _api_action("/api/calendar/events/{uid}", "Delete calendar event", method="DELETE", writes=True, deletes=True, requires_approval=True),
        _api_action("/api/calendar/calendars", "Create calendar", method="POST", writes=True, requires_approval=True),
        _api_action("/api/calendar/calendars/{cal_id}", "Update calendar", method="PUT", writes=True, requires_approval=True),
        _api_action("/api/calendar/calendars/{cal_id}", "Delete calendar", method="DELETE", writes=True, deletes=True, requires_approval=True),
        _api_action("/api/calendar/import", "Import .ics calendar file", method="POST", writes=True, uploads=True, requires_approval=True),
        _api_action("/api/calendar/export/{cal_id}", "Export .ics calendar file", exports=True, requires_approval=True),
        _api_action("/api/calendar/sync", "Sync remote calendar", method="POST", writes=True, uses_network=True, requires_approval=True),
        _api_action("/api/calendar/test", "Test remote calendar connection", method="POST", uses_network=True, requires_approval=True),
        _api_action("/api/calendar/quick-parse", "Parse event draft", method="POST", requires_approval=True),
    ]
    return {
        "mode": "read-only-calendar-operations-plan",
        "generated_at": _iso(current),
        "owner": owner,
        "summary": {
            "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
            "event_count": len(event_rows),
            "today_event_count": len(today_rows),
            "upcoming_event_count": len(upcoming_rows),
            "all_day_event_count": len(all_day_rows),
            "reminder_event_count": len(reminder_rows),
            "recurring_event_count": len(recurring_rows),
            "calendar_sync_configured": sync_configured,
            "network_integrations_enabled": network_allowed,
            "offline": offline_state,
            "calendar_alert_count": len(alert_rows),
            "critical_calendar_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "creates_events": False,
            "updates_events": False,
            "deletes_events": False,
            "imports_calendars": False,
            "exports_calendars": False,
            "syncs_calendars": False,
            "sends_notifications": False,
            "uses_network": False,
        },
        "event_rows": event_rows[:MAX_ROWS],
        "today_rows": today_rows[:MAX_ROWS],
        "upcoming_rows": upcoming_rows[:MAX_ROWS],
        "reminder_rows": reminder_rows[:MAX_ROWS],
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Calendar readiness only",
            "policy": (
                "This endpoint only inspects local calendar event metadata, reminder posture, sync configuration, "
                "API gates, and data paths. It does not create calendar events, update events, delete events, "
                "import calendars, export calendars, sync calendars, test remote connections, send notifications, "
                "run shell commands, or use network access."
            ),
        },
        "paths": {
            "calendars": "data/app.db:calendars",
            "events": "data/app.db:calendar_events",
            "activity": "data/operator_activity.json",
            "settings": "data/settings.json",
        },
    }
