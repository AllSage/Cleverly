"""Read-only scheduled task and task-run readiness plan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.settings import load_features, load_settings, offline_mode


MAX_ROWS = 12
SHELL_ACTIONS = {"run_local", "run_script", "ssh_command"}


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


def _owner_matches(source: Any, owner: str) -> bool:
    source_owner = _value(source, "owner")
    if owner == "local":
        return source_owner in (None, "", "local")
    return source_owner in (None, "", owner)


def _is_failure(status: Any) -> bool:
    return any(token in str(status or "").lower() for token in ("fail", "error", "blocked"))


def _is_running(status: Any) -> bool:
    return any(token in str(status or "").lower() for token in ("running", "queued", "pending"))


def _is_inactive(status: Any) -> bool:
    return any(token in str(status or "").lower() for token in ("paused", "archived", "disabled", "deleted", "cancelled", "completed"))


def _task_row(task: Any) -> dict[str, Any]:
    trigger = _trim(_value(task, "trigger_type") or "schedule", 80)
    schedule = _trim(_value(task, "schedule") or trigger, 120)
    action = _trim(_value(task, "action") or "", 120)
    output_target = _trim(_value(task, "output_target") or "session", 120)
    notifications_enabled = bool(_value(task, "notifications_enabled", "notifications") if _value(task, "notifications_enabled", "notifications") is not None else False)
    next_run = _iso(_value(task, "next_run", "scheduled_date", "due_at"))
    status = _trim(_value(task, "status") or "active", 80)
    return {
        "id": _trim(_value(task, "id"), 160),
        "state": "loading" if _is_inactive(status) else ("warn" if next_run else "ok"),
        "badge": "task",
        "title": _trim(_value(task, "name", "title", "prompt", "action") or "Scheduled task", 240),
        "detail": f"{status}; {trigger}; {schedule}; next {next_run or 'none'}",
        "status": status,
        "type": _trim(_value(task, "task_type", "type") or "llm", 80),
        "action": action,
        "schedule": schedule,
        "trigger_type": trigger,
        "trigger_event": _trim(_value(task, "trigger_event") or "", 120),
        "next_run": next_run,
        "last_run": _iso(_value(task, "last_run")),
        "run_count": int(_value(task, "run_count") or 0),
        "model": _trim(_value(task, "model") or "", 160),
        "endpoint_url": _trim(_value(task, "endpoint_url") or "", 240),
        "output_target": output_target,
        "notifications_enabled": notifications_enabled,
        "webhook_task": trigger == "webhook",
        "event_task": trigger == "event",
        "shell_action": action in SHELL_ACTIONS,
        "owner": _value(task, "owner"),
        "created_at": _iso(_value(task, "created_at")),
        "updated_at": _iso(_value(task, "updated_at")),
    }


def _run_row(run: Any, task: Any = None) -> dict[str, Any]:
    status = _trim(_value(run, "status", "state") or "running", 80)
    task_name = _value(run, "task_name", "name", "title") or _value(task, "name", "title")
    task_action = _value(run, "action") or _value(task, "action")
    return {
        "id": _trim(_value(run, "id"), 160),
        "state": "error" if _is_failure(status) else ("warn" if _is_running(status) else "ok"),
        "badge": "run",
        "title": _trim(task_name or _value(run, "task_id") or "Task run", 240),
        "detail": _trim(_value(run, "error") or _value(run, "result") or status, 500),
        "task_id": _trim(_value(run, "task_id"), 160),
        "task_name": _trim(task_name or "", 240),
        "status": status,
        "started_at": _iso(_value(run, "started_at", "created_at")),
        "finished_at": _iso(_value(run, "finished_at", "updated_at")),
        "result": _trim(_value(run, "result") or "", 500),
        "error": _trim(_value(run, "error") or "", 500),
        "model": _trim(_value(run, "model") or _value(task, "model") or "", 160),
        "action": _trim(task_action or "", 120),
        "output_target": _trim(_value(run, "output_target") or _value(task, "output_target") or "session", 120),
        "owner": _value(run, "owner") or _value(task, "owner"),
    }


def _load_task_rows(owner: str, tasks: list[Any] | None) -> list[dict[str, Any]]:
    if isinstance(tasks, list):
        source = tasks
    else:
        source = []
        try:
            from core.database import ScheduledTask, SessionLocal

            db = SessionLocal()
            try:
                query = db.query(ScheduledTask)
                if owner != "local":
                    query = query.filter(ScheduledTask.owner == owner)
                else:
                    query = query.filter((ScheduledTask.owner == None) | (ScheduledTask.owner == "") | (ScheduledTask.owner == "local"))  # noqa: E711
                source = query.order_by(ScheduledTask.created_at.desc()).limit(160).all()
            finally:
                db.close()
        except Exception:
            source = []
    rows = [_task_row(task) for task in source if _owner_matches(task, owner)]
    rows.sort(key=lambda row: (_is_inactive(row.get("status")), _time_sort_value(row.get("next_run"))))
    return rows[:160]


def _load_run_rows(owner: str, runs: list[Any] | None) -> list[dict[str, Any]]:
    if isinstance(runs, list):
        rows = [_run_row(run) for run in runs if _owner_matches(run, owner)]
    else:
        rows = []
        try:
            from core.database import ScheduledTask, SessionLocal, TaskRun

            db = SessionLocal()
            try:
                query = db.query(TaskRun, ScheduledTask).join(ScheduledTask, TaskRun.task_id == ScheduledTask.id)
                if owner != "local":
                    query = query.filter(ScheduledTask.owner == owner)
                else:
                    query = query.filter((ScheduledTask.owner == None) | (ScheduledTask.owner == "") | (ScheduledTask.owner == "local"))  # noqa: E711
                rows = [_run_row(run, task) for run, task in query.order_by(TaskRun.started_at.desc()).limit(120).all()]
            finally:
                db.close()
        except Exception:
            rows = []
    rows.sort(key=lambda row: _time_sort_value(row.get("started_at")), reverse=True)
    return rows[:120]


def _feature_enabled(features: dict[str, Any], key: str, default: bool = True) -> bool:
    if key in features:
        return bool(features.get(key))
    return default


def _tasks_enabled(settings: dict[str, Any], features: dict[str, Any]) -> bool:
    if "tasks_enabled" in settings:
        return bool(settings.get("tasks_enabled"))
    return _feature_enabled(features, "tasks", True)


def _api_action(
    path: str,
    title: str,
    *,
    method: str = "GET",
    writes: bool = False,
    deletes: bool = False,
    starts_job: bool = False,
    stops_job: bool = False,
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
        "stops_job": stops_job,
        "sends_notification": sends_notification,
        "uses_network": uses_network,
        "requires_approval": requires_approval,
    }


def _tasks_alert_rows(
    *,
    task_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
    settings: dict[str, Any],
    features: dict[str, Any],
    offline: bool,
    current: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active_tasks = [row for row in task_rows if not _is_inactive(row.get("status"))]
    overdue_tasks = [
        row for row in active_tasks
        if (dt := _parse_time(row.get("next_run"))) is not None and dt < current
    ]
    due_today = [
        row for row in active_tasks
        if (dt := _parse_time(row.get("next_run"))) is not None and dt.date() == current.date()
    ]
    failed_runs = [row for row in run_rows if _is_failure(row.get("status"))]
    active_runs = [row for row in run_rows if _is_running(row.get("status"))]
    webhook_tasks = [row for row in active_tasks if row.get("webhook_task")]
    notification_tasks = [row for row in active_tasks if row.get("notifications_enabled") or row.get("output_target") in {"notification", "email"}]
    email_output_tasks = [row for row in active_tasks if row.get("output_target") == "email"]
    shell_tasks = [row for row in active_tasks if row.get("shell_action")]
    webhooks_enabled = _feature_enabled(features, "webhooks", True)
    network_allowed = _feature_enabled(features, "network_integrations", True)
    email_enabled = _feature_enabled(features, "email", True)
    if not _tasks_enabled(settings, features):
        rows.append({
            "id": "tasks-feature-disabled",
            "state": "warn",
            "badge": "flag",
            "title": "Tasks feature disabled",
            "detail": "Scheduled task controls are present but task execution is disabled by local settings or feature policy.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
            "requires_approval": False,
            "uses_network": False,
        })
    if not task_rows:
        rows.append({
            "id": "tasks-empty",
            "state": "warn",
            "badge": "task",
            "title": "No scheduled tasks visible",
            "detail": "Task storage is available, but no local scheduled task rows are visible for automation posture.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
            "requires_approval": False,
            "uses_network": False,
        })
    if overdue_tasks:
        rows.append({
            "id": "tasks-overdue",
            "state": "error",
            "badge": "late",
            "title": "Scheduled tasks overdue",
            "detail": f"{len(overdue_tasks)} active task{'s' if len(overdue_tasks) != 1 else ''} have a next run before now.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
            "requires_approval": True,
            "uses_network": False,
        })
    if due_today:
        rows.append({
            "id": "tasks-due-today",
            "state": "warn",
            "badge": "today",
            "title": "Tasks due today",
            "detail": f"{len(due_today)} active task{'s' if len(due_today) != 1 else ''} are scheduled for today.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
            "requires_approval": False,
            "uses_network": False,
        })
    if failed_runs:
        rows.append({
            "id": "tasks-failed-runs",
            "state": "error",
            "badge": "fail",
            "title": "Task runs failed",
            "detail": f"{len(failed_runs)} recent task run{'s' if len(failed_runs) != 1 else ''} need recovery review.",
            "action": "open-operations-queue",
            "actionLabel": "Queue",
            "requires_approval": True,
            "uses_network": False,
        })
    if active_runs:
        rows.append({
            "id": "tasks-active-runs",
            "state": "warn",
            "badge": "run",
            "title": "Task runs active",
            "detail": f"{len(active_runs)} task run{'s' if len(active_runs) != 1 else ''} are running, queued, or pending.",
            "action": "open-operations-queue",
            "actionLabel": "Queue",
            "requires_approval": False,
            "uses_network": False,
        })
    if webhook_tasks and (offline or not webhooks_enabled or not network_allowed):
        rows.append({
            "id": "tasks-webhook-blocked",
            "state": "warn",
            "badge": "hook",
            "title": "Webhook task triggers blocked by policy",
            "detail": "Webhook-triggered tasks exist, but offline/webhook/network policy blocks external trigger delivery.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    elif webhook_tasks:
        rows.append({
            "id": "tasks-webhook-approval",
            "state": "warn",
            "badge": "hook",
            "title": "Webhook task triggers require review",
            "detail": f"{len(webhook_tasks)} active webhook task{'s' if len(webhook_tasks) != 1 else ''} can be triggered externally.",
            "action": "open-automation-map",
            "actionLabel": "Automation",
            "requires_approval": True,
            "uses_network": True,
        })
    if email_output_tasks and (offline or not email_enabled or not network_allowed):
        rows.append({
            "id": "tasks-email-output-blocked",
            "state": "warn",
            "badge": "mail",
            "title": "Email task output blocked",
            "detail": "One or more tasks target email output, but offline/email/network policy blocks delivery.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if notification_tasks:
        rows.append({
            "id": "tasks-notification-review",
            "state": "warn",
            "badge": "notify",
            "title": "Task notification delivery needs review",
            "detail": f"{len(notification_tasks)} task{'s' if len(notification_tasks) != 1 else ''} can deliver notifications or external output.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
            "requires_approval": True,
            "uses_network": False,
        })
    if shell_tasks:
        rows.append({
            "id": "tasks-shell-actions",
            "state": "error",
            "badge": "shell",
            "title": "Shell-capable task actions require admin review",
            "detail": "run_local, run_script, and ssh_command task actions stay behind admin and trust gates.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
            "uses_network": False,
        })
    rows.append({
        "id": "tasks-write-run-gates",
        "state": "warn",
        "badge": "ask",
        "title": "Task write/run gates require review",
        "detail": "Creating, updating, pausing, resuming, deleting, reverting, running, stopping, cache clearing, and webhook regeneration stay explicit.",
        "action": "open-trust-controls",
        "actionLabel": "Trust",
        "requires_approval": True,
        "uses_network": False,
    })
    return rows[:18]


def _entry_rows(*, tasks_enabled: bool) -> list[dict[str, Any]]:
    state = "ok" if tasks_enabled else "warn"
    common = {
        "command_id": "open-work-preflight",
        "start_command_id": "open-tasks",
        "approval_api": "/api/tasks",
        "run_api": "/api/tasks/{task_id}/run",
        "requires_approval": True,
        "executes": False,
        "creates_tasks": False,
        "updates_tasks": False,
        "deletes_tasks": False,
        "runs_tasks": False,
        "stops_tasks": False,
        "changes_webhooks": False,
        "sends_notifications": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "tasks-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard task preflight",
            "detail": "The Work panel opens read-only task automation posture before any task write or run.",
            "action": "open-work-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "tasks-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed task request route",
            "detail": "Typed task requests route to Work Operations Preflight before opening Tasks or write-capable task APIs.",
            "action": "open-work-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "tasks-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette task route",
            "detail": "The command palette separates task review from create, update, delete, run, stop, and webhook APIs.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "tasks-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice task route",
            "detail": "Voice transcripts use the same local command route and open task preflight before any scheduled work starts.",
            "action": "start-voice-command",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "tasks-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow task handoff",
            "detail": "Workflow handoff can review scheduled tasks and recent runs, but execution stays behind explicit task controls.",
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
        "creates_tasks": False,
        "updates_tasks": False,
        "deletes_tasks": False,
        "runs_tasks": False,
        "stops_tasks": False,
        "changes_webhooks": False,
        "clears_cache": False,
        "sends_notifications": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    tasks_enabled: bool,
    active_task_count: int,
    paused_task_count: int,
    overdue_count: int,
    failed_run_count: int,
    active_run_count: int,
    webhook_count: int,
    notification_count: int,
    email_output_count: int,
    shell_count: int,
    offline: bool,
    webhooks_enabled: bool,
    network_integrations_enabled: bool,
) -> list[dict[str, Any]]:
    create_state = "ok" if tasks_enabled else "warn"
    lifecycle_state = "warn" if paused_task_count or overdue_count else ("loading" if not active_task_count else "ok")
    run_state = "error" if failed_run_count else ("warn" if active_run_count or overdue_count else ("loading" if not active_task_count else "ok"))
    webhook_state = "warn" if webhook_count else "ok"
    notification_state = "warn" if notification_count or email_output_count else "ok"
    shell_state = "error" if shell_count else "ok"
    recovery_state = "warn" if failed_run_count or overdue_count else "ok"
    webhook_detail = (
        f"{webhook_count} webhook-triggered task(s); offline={offline}, webhooks_enabled={webhooks_enabled}, network_integrations_enabled={network_integrations_enabled}."
    )
    return [
        _handoff_row(
            "tasks-create-update-handoff",
            create_state,
            "edit",
            "Create and update handoff",
            f"{active_task_count} active task(s) are visible; creation, parsing, and updates stay in Tasks review before persistence.",
            "open-tasks",
            "Tasks",
            target_api="/api/tasks",
        ),
        _handoff_row(
            "tasks-lifecycle-delete-handoff",
            lifecycle_state,
            "life",
            "Pause, resume, revert, and delete handoff",
            f"{paused_task_count} paused and {overdue_count} overdue task(s) require explicit lifecycle review before state changes or deletion.",
            "open-work-preflight",
            "Preflight",
            target_api="/api/tasks/{task_id}",
        ),
        _handoff_row(
            "tasks-run-stop-retry-handoff",
            run_state,
            "run",
            "Run, stop, and retry handoff",
            f"{active_run_count} active and {failed_run_count} failed run(s) route through task/run approval before execution or retry.",
            "open-operations-queue",
            "Queue",
            target_api="/api/tasks/{task_id}/run",
        ),
        _handoff_row(
            "tasks-webhook-handoff",
            webhook_state,
            "hook",
            "Webhook trigger handoff",
            webhook_detail,
            "open-automation-map",
            "Automation",
            target_api="/api/tasks/{task_id}/webhook/{token}",
        ),
        _handoff_row(
            "tasks-notification-output-handoff",
            notification_state,
            "notify",
            "Notification and email output handoff",
            f"{notification_count} notification/output task(s), including {email_output_count} email output task(s), require delivery review before sending.",
            "open-tasks",
            "Tasks",
            target_api="/api/tasks/notifications",
        ),
        _handoff_row(
            "tasks-shell-admin-handoff",
            shell_state,
            "shell",
            "Shell and admin action handoff",
            f"{shell_count} shell-capable task(s) are mapped; run_local, run_script, and ssh_command stay behind trust/admin gates.",
            "open-trust-controls",
            "Trust",
            target_api="/api/tasks/{task_id}/run",
        ),
        _handoff_row(
            "tasks-activity-recovery-handoff",
            recovery_state,
            "log",
            "Activity and recovery handoff",
            "Task writes, runs, stops, failed runs, and retries should leave activity records with logs and recovery context.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
        ),
    ]


def run_operator_tasks_plan(
    owner: str = "local",
    *,
    tasks: list[Any] | None = None,
    runs: list[Any] | None = None,
    settings: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    offline: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return read-only scheduled task, trigger, and run evidence."""
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
    task_rows = _load_task_rows(owner, tasks)
    run_rows = _load_run_rows(owner, runs)
    active_tasks = [row for row in task_rows if not _is_inactive(row.get("status"))]
    paused_tasks = [row for row in task_rows if str(row.get("status") or "").lower() == "paused"]
    schedule_tasks = [row for row in active_tasks if row.get("trigger_type") == "schedule"]
    event_tasks = [row for row in active_tasks if row.get("event_task")]
    webhook_tasks = [row for row in active_tasks if row.get("webhook_task")]
    overdue_tasks = [
        row for row in active_tasks
        if (dt := _parse_time(row.get("next_run"))) is not None and dt < current
    ]
    due_today = [
        row for row in active_tasks
        if (dt := _parse_time(row.get("next_run"))) is not None and dt.date() == current.date()
    ]
    failed_runs = [row for row in run_rows if _is_failure(row.get("status"))]
    active_runs = [row for row in run_rows if _is_running(row.get("status"))]
    notification_tasks = [row for row in active_tasks if row.get("notifications_enabled") or row.get("output_target") in {"notification", "email"}]
    email_output_tasks = [row for row in active_tasks if row.get("output_target") == "email"]
    shell_tasks = [row for row in active_tasks if row.get("shell_action")]
    alert_rows = _tasks_alert_rows(
        task_rows=task_rows,
        run_rows=run_rows,
        settings=settings,
        features=features,
        offline=offline_state,
        current=current,
    )
    tasks_enabled = _tasks_enabled(settings, features)
    entry_rows = _entry_rows(tasks_enabled=tasks_enabled)
    webhooks_enabled = _feature_enabled(features, "webhooks", True)
    network_integrations_enabled = _feature_enabled(features, "network_integrations", True)
    handoff_rows = _handoff_rows(
        tasks_enabled=tasks_enabled,
        active_task_count=len(active_tasks),
        paused_task_count=len(paused_tasks),
        overdue_count=len(overdue_tasks),
        failed_run_count=len(failed_runs),
        active_run_count=len(active_runs),
        webhook_count=len(webhook_tasks),
        notification_count=len(notification_tasks),
        email_output_count=len(email_output_tasks),
        shell_count=len(shell_tasks),
        offline=offline_state,
        webhooks_enabled=webhooks_enabled,
        network_integrations_enabled=network_integrations_enabled,
    )
    api_actions = [
        _api_action("/api/operator/tasks-plan", "Read scheduled task operations plan"),
        _api_action("/api/tasks?include_last_run=true", "Read scheduled tasks"),
        _api_action("/api/tasks/runs/recent", "Read recent task runs"),
        _api_action("/api/tasks/notifications", "Read and clear task notifications", writes=True, sends_notification=True, requires_approval=True),
        _api_action("/api/tasks/meta/output-targets", "Read task output targets"),
        _api_action("/api/tasks/meta/actions", "Read built-in task actions"),
        _api_action("/api/tasks/meta/events", "Read event triggers"),
        _api_action("/api/tasks/parse", "Draft task from text", method="POST", requires_approval=True),
        _api_action("/api/tasks", "Create scheduled task", method="POST", writes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}", "Update scheduled task", method="PUT", writes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}", "Delete scheduled task", method="DELETE", writes=True, deletes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/pause", "Pause scheduled task", method="POST", writes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/resume", "Resume scheduled task", method="POST", writes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/revert", "Revert built-in scheduled task", method="POST", writes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/run", "Run scheduled task now", method="POST", writes=True, starts_job=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/stop", "Stop running scheduled task", method="POST", writes=True, stops_job=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/clear-cache", "Clear derived task cache", method="POST", writes=True, deletes=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/webhook/{token}", "Trigger task through webhook", method="POST", writes=True, starts_job=True, uses_network=True, requires_approval=True),
        _api_action("/api/tasks/{task_id}/webhook-regenerate", "Regenerate webhook token", method="POST", writes=True, requires_approval=True),
    ]
    return {
        "mode": "read-only-task-automation-plan",
        "generated_at": _iso(current),
        "owner": owner,
        "summary": {
            "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
            "task_count": len(task_rows),
            "active_task_count": len(active_tasks),
            "paused_task_count": len(paused_tasks),
            "schedule_task_count": len(schedule_tasks),
            "event_task_count": len(event_tasks),
            "webhook_task_count": len(webhook_tasks),
            "overdue_task_count": len(overdue_tasks),
            "due_today_count": len(due_today),
            "run_count": len(run_rows),
            "active_run_count": len(active_runs),
            "failed_run_count": len(failed_runs),
            "notification_task_count": len(notification_tasks),
            "email_output_task_count": len(email_output_tasks),
            "shell_action_task_count": len(shell_tasks),
            "tasks_enabled": tasks_enabled,
            "webhooks_enabled": webhooks_enabled,
            "network_integrations_enabled": network_integrations_enabled,
            "offline": offline_state,
            "task_alert_count": len(alert_rows),
            "critical_task_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "creates_tasks": False,
            "updates_tasks": False,
            "deletes_tasks": False,
            "runs_tasks": False,
            "stops_tasks": False,
            "changes_webhooks": False,
            "clears_cache": False,
            "sends_notifications": False,
            "uses_network": False,
        },
        "task_rows": task_rows[:MAX_ROWS],
        "run_rows": run_rows[:MAX_ROWS],
        "overdue_rows": overdue_tasks[:MAX_ROWS],
        "due_today_rows": due_today[:MAX_ROWS],
        "failed_run_rows": failed_runs[:MAX_ROWS],
        "active_run_rows": active_runs[:MAX_ROWS],
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Task automation readiness only",
            "policy": (
                "This endpoint only inspects local scheduled task metadata, trigger posture, recent task-run metadata, "
                "notification/webhook posture, API gates, and data paths. It does not create tasks, update tasks, "
                "pause or resume tasks, delete tasks, revert built-ins, run tasks, stop tasks, clear caches, regenerate "
                "webhook tokens, trigger webhooks, send notifications, run shell commands, or use network access."
            ),
        },
        "paths": {
            "tasks": "data/app.db:scheduled_tasks",
            "runs": "data/app.db:task_runs",
            "activity": "data/operator_activity.json",
            "settings": "data/settings.json",
        },
    }
