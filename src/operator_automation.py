"""Read-only automation operations evidence for Cleverly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS, resolve_operator_route_matrix


FAILURE_TERMS = ("fail", "failed", "error", "exception", "blocked", "unhealthy")
PENDING_TERMS = ("pending", "approval", "running", "queued", "waiting", "watching")


ENTRY_POINTS = [
    ("dashboard", "dash", "Automation dashboard", "open-automation-map"),
    ("text", "text", "Typed automation request", "open-automation-preflight"),
    ("palette", "pal", "Palette automation route", "open-command-palette"),
    ("voice", "voice", "Voice automation route", "open-voice-preflight"),
    ("workflow", "flow", "Workflow automation handoff", "open-automation-map"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _as_records(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in records or [] if isinstance(item, dict)]


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, str]:
    normalized = dict(DEFAULT_TRUST_POLICY)
    if isinstance(policy, dict):
        for level in TRUST_LEVELS:
            mode = str(policy.get(level) or normalized[level]).lower()
            normalized[level] = mode if mode in {"auto", "ask"} else normalized[level]
    return normalized


def _trust_level(command: dict[str, Any]) -> str:
    trust = str(command.get("trust") or "local").lower()
    return trust if trust in TRUST_LEVELS else "local"


def _trust_mode(command: dict[str, Any], policy: dict[str, str]) -> str:
    if command.get("alwaysAsk") or command.get("always_ask"):
        return "ask"
    return policy.get(_trust_level(command), "ask")


def _command_id(command: dict[str, Any]) -> str:
    return _trim(command.get("id") or command.get("command_id"), 160)


def _workflow_ids(workflows: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for workflow in workflows:
        for key in ("id", "commandId", "command_id", "approvalId", "approval_id", "expectedRouteId", "expected_route_id"):
            value = _trim(workflow.get(key), 160)
            if value:
                ids.add(value)
    return ids


def _automation_commands(commands: list[dict[str, Any]], workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workflow_ids = _workflow_ids(workflows)
    rows = []
    for command in commands:
        command_id = _command_id(command)
        text = " ".join(
            _trim(value, 240).lower()
            for value in (command_id, command.get("title"), command.get("category"), command.get("subtitle"))
            if value
        )
        if command.get("workflow") or command_id in workflow_ids or any(term in text for term in ("automation", "workflow", "loop", "build watch", "watch build", "task")):
            rows.append(command)
    return rows


def _activity_state(record: dict[str, Any]) -> str:
    text = " ".join(
        _trim(value, 240).lower()
        for value in (record.get("status"), record.get("state"), record.get("title"), record.get("detail"), record.get("result"), record.get("error"))
        if value
    )
    if any(term in text for term in FAILURE_TERMS):
        return "error"
    if any(term in text for term in PENDING_TERMS):
        return "warn"
    return "ok"


def _activity_log_ready(record: dict[str, Any]) -> bool:
    return bool(
        _trim(record.get("detail"))
        or _trim(record.get("result"))
        or _trim(record.get("error"))
        or _trim(record.get("stdout"))
        or _trim(record.get("stderr"))
        or _trim(record.get("status"))
        or _trim(record.get("state"))
        or isinstance(record.get("events"), list) and bool(record.get("events"))
    )


def _activity_needs_recovery(record: dict[str, Any]) -> bool:
    text = " ".join(
        _trim(value, 240).lower()
        for value in (
            record.get("command_id"),
            record.get("title"),
            record.get("status"),
            record.get("state"),
            record.get("detail"),
            record.get("result"),
            record.get("error"),
        )
        if value
    )
    return _activity_state(record) == "error" or any(
        term in text
        for term in ("backup", "build", "container", "docker", "file", "repair", "restore", "retry", "rollback", "shell", "task")
    )


def _activity_rollback_ready(record: dict[str, Any]) -> bool:
    return bool(
        _trim(record.get("rollback_hint"))
        or _trim(record.get("recovery_hint"))
        or not _activity_needs_recovery(record)
    )


def _automation_activity(activity: list[dict[str, Any]], command_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for record in activity:
        command_id = _trim(record.get("command_id") or record.get("commandId"), 160)
        text = " ".join(
            _trim(value, 240).lower()
            for value in (command_id, record.get("title"), record.get("category"), record.get("source"), record.get("detail"))
            if value
        )
        if command_id in command_ids or any(term in text for term in ("automation", "agent loop", "workflow", "build until green", "watch build", "task from note", "backup")):
            rows.append(record)
    return rows[:24]


def _route_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in matrix.get("rows") if isinstance(matrix.get("rows"), list) else []:
        rows.append({
            "id": _trim(row.get("id") or row.get("expected_route_id"), 160),
            "state": row.get("state") or ("ok" if row.get("route_ready") else "warn"),
            "badge": _trim(row.get("area") or "route", 40),
            "title": _trim(row.get("title") or row.get("phrase") or "Workflow route", 240),
            "detail": f"{_trim(row.get('phrase'), 300)}; selected={_trim(row.get('selected_id'), 160) or 'none'}; expected={_trim(row.get('expected_route_id'), 160) or 'none'}",
            "action": _trim(row.get("expected_route_id") or row.get("command_id"), 160) or "open-command-palette",
            "actionLabel": "Open" if row.get("route_ready") else "Review",
            "route_ready": row.get("route_ready") is True,
            "approval_ready": row.get("approval_ready") is True,
            "requires_approval": bool(row.get("approval_id")),
            "starts_workflows": False,
            "executes_commands": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return rows[:16]


def _automation_rows(
    *,
    commands: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    loops: list[dict[str, Any]],
    activity: list[dict[str, Any]],
    policy: dict[str, str],
    route_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    automation_commands = _automation_commands(commands, workflows)
    ask_commands = [command for command in automation_commands if _trust_mode(command, policy) == "ask"]
    pending = [record for record in activity if _activity_state(record) == "warn"]
    failed = [record for record in activity if _activity_state(record) == "error"]
    unresolved = [row for row in route_rows if row.get("route_ready") is not True]
    return [
        {
            "id": "automation-command-catalog",
            "state": "ok" if automation_commands else "warn",
            "badge": "cmd",
            "title": "Automation command catalog",
            "detail": f"{len(automation_commands)} automation/workflow command(s); {len(ask_commands)} ask before execution.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            "id": "workflow-route-matrix",
            "state": "ok" if workflows and not unresolved else ("warn" if workflows else "loading"),
            "badge": "route",
            "title": "Workflow route matrix",
            "detail": f"{len(route_rows) - len(unresolved)}/{len(route_rows)} workflow route(s) resolve to the expected command.",
            "action": "open-capability-map",
            "actionLabel": "Routes",
        },
        {
            "id": "agent-loop-catalog",
            "state": "ok" if loops else "warn",
            "badge": "loop",
            "title": "Agent Loop catalog",
            "detail": f"{len(loops)} local loop template(s) visible for repeatable work.",
            "action": "open-loops",
            "actionLabel": "Loops",
        },
        {
            "id": "scheduled-task-boundary",
            "state": "ok",
            "badge": "task",
            "title": "Scheduled task boundary",
            "detail": "Scheduled task creation, edits, runs, pauses, and notifications remain in Tasks controls.",
            "action": "open-tasks",
            "actionLabel": "Tasks",
        },
        {
            "id": "build-watch-boundary",
            "state": "ok" if any("build" in _trim(command.get("id"), 160) or "build" in _trim(command.get("title"), 240).lower() for command in automation_commands) else "warn",
            "badge": "build",
            "title": "Build-watch handoff",
            "detail": "Build-watch requests review workspace, command candidates, loop limits, and approval before any build loop starts.",
            "action": "open-build-watch-plan",
            "actionLabel": "Build",
        },
        {
            "id": "activity-ledger",
            "state": "error" if failed else ("warn" if pending else "ok"),
            "badge": "log",
            "title": "Automation activity ledger",
            "detail": f"{len(pending)} pending/running/queued record(s); {len(failed)} failed/blocked record(s).",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            "id": "recovery-handoff",
            "state": "warn" if failed else "ok",
            "badge": "recover",
            "title": "Recovery and retry handoff",
            "detail": "Retries, rollbacks, repair, cleanup, and restore work route through Activity and Recovery Map before changing local state.",
            "action": "open-recovery-map",
            "actionLabel": "Recovery",
        },
        {
            "id": "offline-webhook-boundary",
            "state": "ok",
            "badge": "local",
            "title": "Local-first automation boundary",
            "detail": "Network, webhook, shell, filesystem, and credential-capable automation must remain approval-gated by trust policy.",
            "action": "open-offline",
            "actionLabel": "Policy",
        },
    ]


def _entry_rows(commands: list[dict[str, Any]], workflows: list[dict[str, Any]], configured: dict[str, Any]) -> list[dict[str, Any]]:
    command_ids = {_command_id(command) for command in commands}
    ready = bool(commands) and bool(configured.get("commands"))
    workflow_ready = bool(workflows) and bool(configured.get("workflows"))
    rows = []
    for entry, badge, title, action in ENTRY_POINTS:
        is_ready = (workflow_ready if entry == "workflow" else ready) and (action in command_ids or action in {"open-automation-map", "open-automation-preflight"})
        rows.append({
            "id": f"automation-{entry}-route",
            "entry": entry,
            "state": "ok" if is_ready else "warn",
            "badge": badge,
            "title": title,
            "detail": "Automation requests open preflight, map, trust, activity, or loop review before any task, loop, shell command, or workflow can run.",
            "action": action,
            "actionLabel": "Open" if is_ready else "Review",
            "ready": is_ready,
            "command_id": action,
            "automation_api": "/api/operator/automation-plan",
            "autonomy_api": "/api/operator/autonomy-plan",
            "approval_api": "/api/operator/approval-plan",
            "loops_api": "/api/operator/loops-plan",
            "tasks_api": "/api/operator/tasks-plan",
            "activity_api": "/api/operator/activity",
            "requires_approval": True,
            "starts_automation": False,
            "starts_loops": False,
            "runs_tasks": False,
            "routes_commands": False,
            "executes_commands": False,
            "approves_commands": False,
            "writes_activity": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return rows


def _activity_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records[:10]:
        state = _activity_state(record)
        record_id = _trim(record.get("id"), 160)
        command_id = _trim(record.get("command_id"), 160)
        retryable = bool(command_id and command_id != "chat-command")
        log_ready = _activity_log_ready(record)
        needs_recovery = _activity_needs_recovery(record)
        rollback_ready = _activity_rollback_ready(record)
        rows.append({
            "id": record_id,
            "state": state,
            "badge": _trim(record.get("status") or record.get("state") or "activity", 40),
            "title": _trim(record.get("title") or record.get("command_id") or "Automation activity", 240),
            "detail": "; ".join([
                _trim(record.get("detail") or record.get("result") or record.get("error") or "Recorded automation activity", 300),
                "copy log ready" if log_ready else "log evidence missing",
                "retry gated" if retryable else "no retry route",
                "rollback/recovery ready" if rollback_ready else "rollback guidance missing",
            ]),
            "action": record_id and f"activity-detail:{record_id}" or "open-activity-preflight",
            "actionLabel": "Details" if record_id else "Activity",
            "activity_id": record_id,
            "command_id": command_id,
            "detail_action": record_id and f"activity-detail:{record_id}" or "open-activity-preflight",
            "copy_log_action": record_id and f"copy-activity-log:{record_id}" or "copy-latest-activity-log",
            "retry_action": record_id and retryable and f"retry-activity:{record_id}" or "",
            "recovery_action": "open-recovery-map" if needs_recovery else "open-activity-preflight",
            "log_ready": log_ready,
            "rollback_ready": rollback_ready,
            "needs_recovery": needs_recovery,
            "retryable": retryable,
            "requires_approval": True,
            "retries_commands": False,
            "writes_activity": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return rows


def _alert_rows(
    *,
    configured: dict[str, Any],
    automation_rows: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alerts = []
    if not configured.get("commands"):
        alerts.append(("error", "cmd", "Automation command catalog missing", "Publish command catalog evidence before approving automation.", "open-command-palette"))
    if not configured.get("workflows"):
        alerts.append(("warn", "flow", "Workflow catalog missing", "Publish Agent Loop and workflow route evidence before starting local automation.", "open-automation-map"))
    unresolved = [row for row in route_rows if row.get("route_ready") is not True]
    if unresolved:
        alerts.append(("error", "route", "Automation workflow routes unresolved", f"{len(unresolved)} workflow route(s) do not resolve to expected commands.", "open-capability-map"))
    failed = [row for row in activity_rows if row.get("state") == "error"]
    if failed:
        alerts.append(("error", "fail", "Automation failures need recovery review", f"{len(failed)} automation activity record(s) failed or were blocked.", "open-recovery-map"))
    for row in automation_rows:
        if row.get("state") in {"error", "warn"}:
            alerts.append((row.get("state"), row.get("badge"), row.get("title"), row.get("detail"), row.get("action")))
    return [
        {
            "id": f"automation-alert-{index}",
            "state": state,
            "badge": badge or "auto",
            "title": title,
            "detail": detail,
            "action": action or "open-automation-map",
            "actionLabel": "Review",
            "requires_approval": state == "error",
        }
        for index, (state, badge, title, detail, action) in enumerate(alerts[:14], 1)
    ]


def _api_action(path: str, title: str, *, writes: bool = False, starts: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "method": "GET" if not writes and not starts else "POST",
        "title": title,
        "state": "warn" if writes or starts else "ok",
        "writes": writes,
        "starts_automation": starts,
        "starts_loops": starts,
        "runs_tasks": starts,
        "routes_commands": False,
        "executes_commands": False,
        "runs_shell": False,
        "uses_network": False,
        "requires_approval": writes or starts,
    }


def run_operator_automation_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    loops: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    activity: list[dict[str, Any]] | None = None,
    configured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return read-only automation readiness, route, activity, and recovery proof."""
    clean_commands = _as_records(commands)
    clean_workflows = _as_records(workflows)
    clean_loops = _as_records(loops)
    clean_activity = _as_records(activity)
    trust_policy = _normalize_policy(policy)
    configured_summary = {
        "commands": bool((configured or {}).get("commands", bool(clean_commands))),
        "workflows": bool((configured or {}).get("workflows", bool(clean_workflows or clean_loops))),
        "policy": bool((configured or {}).get("policy", bool(policy))),
    }
    automation_commands = _automation_commands(clean_commands, clean_workflows)
    automation_ids = {_command_id(command) for command in automation_commands}
    automation_activity = _automation_activity(clean_activity, automation_ids)
    matrix = resolve_operator_route_matrix(clean_commands, clean_workflows, trust_policy)
    route_rows = _route_rows(matrix)
    automation_rows = _automation_rows(
        commands=clean_commands,
        workflows=clean_workflows,
        loops=clean_loops,
        activity=automation_activity,
        policy=trust_policy,
        route_rows=route_rows,
    )
    entry_rows = _entry_rows(clean_commands, clean_workflows, configured_summary)
    activity_rows = _activity_rows(automation_activity)
    alert_rows = _alert_rows(
        configured=configured_summary,
        automation_rows=automation_rows,
        route_rows=route_rows,
        activity_rows=activity_rows,
    )
    ask_commands = [command for command in automation_commands if _trust_mode(command, trust_policy) == "ask"]
    pending = [row for row in activity_rows if row.get("state") == "warn"]
    failed = [row for row in activity_rows if row.get("state") == "error"]
    route_summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    return {
        "mode": "read-only-automation-operations-plan",
        "owner": owner,
        "generated_at": _utc_now(),
        "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
        "configured": configured_summary,
        "summary": {
            "automation_command_count": len(automation_commands),
            "ask_automation_count": len(ask_commands),
            "workflow_count": len(clean_workflows),
            "loop_count": len(clean_loops),
            "route_match_count": int(route_summary.get("total") or len(route_rows)),
            "route_match_ready_count": int(route_summary.get("ready") or 0),
            "pending_count": len(pending),
            "failure_count": len(failed),
            "activity_count": len(activity_rows),
            "automation_row_count": len(automation_rows),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": sum(1 for row in entry_rows if row.get("ready") is True),
            "automation_alert_count": len(alert_rows),
            "critical_automation_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
            "starts_automation": False,
            "starts_loops": False,
            "runs_tasks": False,
            "routes_commands": False,
            "executes_commands": False,
            "approves_commands": False,
            "writes_activity": False,
            "runs_shell": False,
            "uses_network": False,
        },
        "automation_rows": automation_rows,
        "route_rows": route_rows,
        "entry_rows": entry_rows,
        "activity_rows": activity_rows,
        "alert_rows": alert_rows,
        "api_actions": [
            _api_action("/api/operator/automation-plan", "Read automation operations proof"),
            _api_action("/api/operator/autonomy-plan", "Read autonomy and trust evidence"),
            _api_action("/api/operator/approval-plan", "Read approval queue evidence"),
            _api_action("/api/operator/loops-plan", "Read Agent Loop evidence"),
            _api_action("/api/operator/tasks-plan", "Read scheduled task evidence"),
            _api_action("/api/operator/build-watch-plan", "Read build-watch loop preflight"),
            _api_action("/api/operator/recovery-plan", "Read retry and recovery handoff"),
            _api_action("/api/tasks", "Create or update scheduled tasks", writes=True),
            _api_action("/api/tasks/runs", "Start or rerun scheduled automation", starts=True),
        ],
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "activity": "data/operator_activity.json",
            "tasks": "data/tasks.json",
            "task_runs": "data/task_runs.json",
        },
        "approval": {
            "required": False,
            "policy": (
                "This endpoint only audits local automation readiness, route proof, activity, and recovery handoffs. "
                "It does not start automation, start loops, run tasks, route live commands, execute commands, approve actions, "
                "write activity, run shell commands, write files, call webhooks, or use network access."
            ),
            "disallowed_actions": [
                "start automation",
                "start loops",
                "run tasks",
                "route live commands",
                "execute commands",
                "approve actions",
                "write activity",
                "run shell commands",
                "write files",
                "call webhooks",
                "use network access",
            ],
        },
    }
