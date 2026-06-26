"""Read-only Agent Loop and workflow readiness evidence for Cleverly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS, resolve_operator_route_matrix


FAILURE_RE = ("fail", "failed", "error", "exception", "blocked", "unhealthy")
PENDING_RE = ("pending", "approval", "running", "queued", "waiting", "watching")


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


def _command_title(command: dict[str, Any]) -> str:
    return _trim(command.get("title") or _command_id(command) or "Command", 180)


def _workflow_command_id(workflow: dict[str, Any]) -> str:
    return _trim(workflow.get("commandId") or workflow.get("command_id") or workflow.get("id"), 160)


def _workflow_expected_id(workflow: dict[str, Any]) -> str:
    return _trim(
        workflow.get("expectedRouteId")
        or workflow.get("expected_route_id")
        or workflow.get("commandId")
        or workflow.get("command_id")
        or workflow.get("id"),
        160,
    )


def _workflow_approval_id(workflow: dict[str, Any]) -> str:
    return _trim(workflow.get("approvalId") or workflow.get("approval_id"), 160)


def _state_from_activity(record: dict[str, Any]) -> str:
    text = " ".join(
        _trim(value, 240).lower()
        for value in (
            record.get("status"),
            record.get("state"),
            record.get("detail"),
            record.get("result"),
            record.get("title"),
        )
        if value
    )
    if any(term in text for term in FAILURE_RE):
        return "error"
    if any(term in text for term in PENDING_RE):
        return "warn"
    return "ok"


def _is_loop_activity(record: dict[str, Any], loop_ids: set[str], workflow_ids: set[str]) -> bool:
    command_id = _trim(record.get("command_id") or record.get("commandId"), 160)
    if command_id and command_id in workflow_ids:
        return True
    text = " ".join(
        _trim(value, 300).lower()
        for value in (
            command_id,
            record.get("title"),
            record.get("detail"),
            record.get("category"),
            record.get("source"),
        )
        if value
    )
    if "agent loop" in text or "workflow" in text or "build until green" in text or "watch build" in text:
        return True
    return any(loop_id and loop_id.lower() in text for loop_id in loop_ids)


def _api_action(
    action_id: str,
    method: str,
    path: str,
    *,
    risk: str,
    requires_approval: bool,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "method": method,
        "path": path,
        "risk": risk,
        "executes": False,
        "requires_approval": requires_approval,
    }


def _entry_rows(*, loops_ready: bool, workflows_ready: bool, commands_ready: bool) -> list[dict[str, Any]]:
    state = "ok" if loops_ready and workflows_ready and commands_ready else "warn"
    common = {
        "command_id": "open-automation-preflight",
        "start_command_id": "open-loops",
        "map_command_id": "open-automation-map",
        "approval_command_id": "open-trust-controls",
        "activity_command_id": "open-activity-preflight",
        "approval_api": "/api/operator/workflows",
        "activity_api": "/api/operator/activity",
        "route_api": "/api/operator/routes",
        "requires_approval": True,
        "executes": False,
        "starts_loops": False,
        "routes_commands": False,
        "executes_commands": False,
        "approves_commands": False,
        "starts_jobs": False,
        "changes_policy": False,
        "writes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "loops-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard loop preflight",
            "detail": "The Automation panel opens read-only Agent Loop posture before any loop start, workflow route, job, or command execution.",
            "action": "open-automation-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "loops-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed loop request route",
            "detail": "Typed requests such as watch this repo until the build passes route to loop preflight before any loop can start.",
            "action": "open-automation-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "loops-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette loop route",
            "detail": "The command palette separates loop review from workflow start, shell-capable jobs, and command execution.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "loops-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice loop route",
            "detail": "Voice mode can open loop preflight without starting loops, approving actions, or running jobs.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "loops-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow loop handoff",
            "detail": "Automation handoffs can show loop templates, route proof, approval gates, and activity before the owner starts a loop.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _loop_alert_rows(
    *,
    clean_loops: list[dict[str, Any]],
    clean_workflows: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
    loop_commands: list[dict[str, Any]],
    workflow_commands: list[dict[str, Any]],
    approval_gated: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    trust_policy: dict[str, str],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not clean_loops:
        rows.append({
            "id": "loop-catalog-missing",
            "state": "warn",
            "badge": "loop",
            "title": "Agent Loop catalog not published",
            "detail": "No persisted Agent Loop templates are visible in data/operator_workflows.json.",
            "action": "open-loops",
            "actionLabel": "Loops",
            "requires_approval": False,
        })
    if not clean_workflows:
        rows.append({
            "id": "workflow-routes-missing",
            "state": "warn",
            "badge": "route",
            "title": "Loop workflow routes missing",
            "detail": "No persisted workflow route phrases are available for loop commands.",
            "action": "open-automation-map",
            "actionLabel": "Map",
            "requires_approval": False,
        })
    unresolved = [row for row in route_rows if not row.get("route_ready")]
    if unresolved:
        rows.append({
            "id": "loop-route-unresolved",
            "state": "error",
            "badge": "proof",
            "title": "Loop route proof needs review",
            "detail": f"{len(unresolved)} workflow route(s) do not resolve to the expected loop command.",
            "action": "open-capability-map",
            "actionLabel": "Proof",
            "requires_approval": False,
        })
    if clean_loops and not loop_commands:
        rows.append({
            "id": "loop-command-link-missing",
            "state": "warn",
            "badge": "cmd",
            "title": "Loop command links missing",
            "detail": "Persisted loop templates do not reference command actions that are visible in the command catalog.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_approval": False,
        })
    if workflow_commands and not approval_gated:
        rows.append({
            "id": "loop-approval-gate-missing",
            "state": "warn",
            "badge": "ask",
            "title": "Loop start approval gate missing",
            "detail": "Loop-capable workflow commands can route without an ask gate under the current policy.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if trust_policy["danger"] != "ask":
        rows.append({
            "id": "danger-auto-enabled",
            "state": "error",
            "badge": "risk",
            "title": "High-risk loop actions are not ask-gated",
            "detail": f"High-risk command policy is {trust_policy['danger']}; loop starts and repair actions should ask by default.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if trust_policy["network"] != "ask":
        rows.append({
            "id": "network-auto-enabled",
            "state": "warn",
            "badge": "net",
            "title": "Network loop actions are not ask-gated",
            "detail": f"Network command policy is {trust_policy['network']}; local-first loops should ask before network use.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "requires_approval": True,
        })
    if pending:
        rows.append({
            "id": "pending-loop-activity",
            "state": "warn",
            "badge": "hold",
            "title": "Loop activity pending",
            "detail": f"{len(pending)} loop/workflow record(s) are waiting, running, queued, or pending approval.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "requires_approval": False,
        })
    if failed:
        rows.append({
            "id": "failed-loop-activity",
            "state": "error",
            "badge": "fail",
            "title": "Loop activity failures",
            "detail": f"{len(failed)} loop/workflow failure record(s) need review before retry.",
            "action": "open-activity-preflight",
            "actionLabel": "Inspect",
            "requires_approval": False,
        })
    if not configured.get("workflows", False):
        rows.append({
            "id": "loop-store-not-configured",
            "state": "warn",
            "badge": "store",
            "title": "Workflow catalog store is empty",
            "detail": "The browser has not yet published loop and workflow evidence to the backend owner store.",
            "action": "open-automation-map",
            "actionLabel": "Publish",
            "requires_approval": False,
        })
    return rows[:12]


def run_operator_loops_plan(
    owner: str = "local",
    *,
    loops: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    commands: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    activity: list[dict[str, Any]] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return a read-only proof of Agent Loop routing, gates, and activity."""
    owner = owner or "local"
    clean_loops = _as_records(loops)
    clean_workflows = _as_records(workflows)
    clean_commands = [item for item in commands or [] if isinstance(item, dict) and _command_id(item)]
    clean_activity = _as_records(activity)
    trust_policy = _normalize_policy(policy)
    configured = configured if isinstance(configured, dict) else {}
    command_by_id = {_command_id(command): command for command in clean_commands}

    route_matrix = resolve_operator_route_matrix(clean_commands, clean_workflows, trust_policy)
    route_rows = _as_records(route_matrix.get("rows") if isinstance(route_matrix, dict) else [])
    matrix_summary = route_matrix.get("summary") if isinstance(route_matrix.get("summary"), dict) else {}

    loop_action_ids = {
        _trim(action_id, 160)
        for loop in clean_loops
        for action_id in (loop.get("actionIds") or loop.get("action_ids") or [])
        if _trim(action_id, 160)
    }
    workflow_ids = {
        value
        for workflow in clean_workflows
        for value in (_workflow_command_id(workflow), _workflow_expected_id(workflow), _workflow_approval_id(workflow))
        if value
    }
    loop_commands = [command_by_id[action_id] for action_id in loop_action_ids if action_id in command_by_id]
    workflow_commands = [
        command for command in clean_commands
        if command.get("workflow") or _command_id(command) in workflow_ids
    ]
    approval_gated = [
        command for command in workflow_commands
        if _trust_mode(command, trust_policy) == "ask"
    ]
    loop_ids = {_trim(loop.get("id"), 160) for loop in clean_loops if _trim(loop.get("id"), 160)}
    loop_activity = [
        record for record in clean_activity
        if _is_loop_activity(record, loop_ids, workflow_ids | loop_action_ids)
    ]
    pending = [record for record in loop_activity if _state_from_activity(record) == "warn"]
    failed = [record for record in loop_activity if _state_from_activity(record) == "error"]

    loop_rows = [
        {
            "id": _trim(loop.get("id"), 160),
            "state": "ok" if loop.get("actionIds") or loop.get("action_ids") else "warn",
            "badge": _trim(loop.get("category") or "loop", 24),
            "title": _trim(loop.get("title") or loop.get("id") or "Agent Loop", 180),
            "detail": _trim(loop.get("goal") or loop.get("summary") or loop.get("check") or "Local repeatable operator loop", 500),
            "mode": _trim(loop.get("mode") or "Manual", 80),
            "step_count": len(loop.get("steps") or []),
            "action_count": len(loop.get("actionIds") or loop.get("action_ids") or []),
            "action": "open-loops",
            "actionLabel": "Loops",
            "executes": False,
            "requires_approval": True,
        }
        for loop in clean_loops[:12]
    ]

    workflow_rows = [
        {
            "id": _trim(workflow.get("id") or _workflow_expected_id(workflow), 160),
            "state": next((row.get("state") for row in route_rows if row.get("id") == _trim(workflow.get("id") or _workflow_expected_id(workflow), 160)), workflow.get("state") or "warn"),
            "badge": _trim(workflow.get("area") or "flow", 24),
            "title": _trim(workflow.get("phrase") or workflow.get("title") or workflow.get("id") or "Workflow route", 220),
            "detail": _trim(workflow.get("detail") or workflow.get("plan") or workflow.get("proof") or "Persisted workflow route evidence", 600),
            "command_id": _workflow_command_id(workflow),
            "expected_route_id": _workflow_expected_id(workflow),
            "approval_id": _workflow_approval_id(workflow),
            "route_ready": bool(next((row.get("route_ready") for row in route_rows if row.get("id") == _trim(workflow.get("id") or _workflow_expected_id(workflow), 160)), workflow.get("routeReady"))),
            "approval_mode": _trim(workflow.get("approvalMode") or workflow.get("approval_mode") or "", 80),
            "action": _workflow_expected_id(workflow) or "open-command-palette",
            "actionLabel": "Route",
            "executes": False,
            "requires_approval": bool(_workflow_approval_id(workflow)),
        }
        for workflow in clean_workflows[:12]
    ]

    activity_rows = [
        {
            "id": _trim(record.get("id"), 160),
            "state": _state_from_activity(record),
            "badge": _trim(record.get("status") or record.get("state") or "log", 24),
            "title": _trim(record.get("title") or record.get("command_id") or "Loop activity", 180),
            "detail": _trim(record.get("detail") or record.get("result") or "Recorded in local operator activity", 500),
            "action": _trim(record.get("command_id"), 160) or "open-activity-preflight",
            "actionLabel": "Activity",
            "executes": False,
            "requires_approval": False,
        }
        for record in loop_activity[:8]
    ]

    permission_rows = [
        {
            "id": "loop-start-approval",
            "state": "ok" if approval_gated else ("loading" if not workflow_commands else "warn"),
            "badge": "ask",
            "title": "Loop start approval",
            "detail": f"{len(approval_gated)}/{len(workflow_commands)} loop-capable command{'s' if len(workflow_commands) != 1 else ''} ask before route",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "route-proof",
            "state": "ok" if int(matrix_summary.get("unresolved") or 0) == 0 and clean_workflows else ("warn" if clean_workflows else "loading"),
            "badge": "proof",
            "title": "Loop route proof",
            "detail": f"{int(matrix_summary.get('route_ready') or 0)}/{int(matrix_summary.get('total') or 0)} workflow route{'s' if int(matrix_summary.get('total') or 0) != 1 else ''} resolve to expected commands",
            "action": "open-capability-map",
            "actionLabel": "Proof",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "network-gate",
            "state": "ok" if trust_policy["network"] == "ask" else "warn",
            "badge": "net",
            "title": "Network loop gate",
            "detail": f"Network trust tier is {trust_policy['network']}; local-first loop work expects ask.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "executes": False,
            "requires_approval": trust_policy["network"] == "ask",
        },
        {
            "id": "danger-gate",
            "state": "ok" if trust_policy["danger"] == "ask" else "warn",
            "badge": "risk",
            "title": "High-risk loop gate",
            "detail": f"High-risk trust tier is {trust_policy['danger']}; repair/destructive loop actions should ask.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": trust_policy["danger"] == "ask",
        },
    ]

    alert_rows = _loop_alert_rows(
        clean_loops=clean_loops,
        clean_workflows=clean_workflows,
        route_rows=route_rows,
        loop_commands=loop_commands,
        workflow_commands=workflow_commands,
        approval_gated=approval_gated,
        pending=pending,
        failed=failed,
        trust_policy=trust_policy,
        configured=configured,
    )
    configured_summary = {
        "commands": bool(configured.get("commands", bool(clean_commands))),
        "workflows": bool(configured.get("workflows", bool(clean_workflows or clean_loops))),
        "policy": bool(configured.get("policy", policy is not None)),
    }
    entry_rows = _entry_rows(
        loops_ready=bool(clean_loops),
        workflows_ready=bool(clean_workflows) and int(matrix_summary.get("unresolved") or 0) == 0,
        commands_ready=bool(loop_commands or workflow_commands) and configured_summary["commands"],
    )
    state = "error" if failed or int(matrix_summary.get("unresolved") or 0) else ("warn" if alert_rows else "ok")

    api_actions = [
        _api_action("loops-plan", "GET", "/api/operator/loops-plan", risk="read-only", requires_approval=False),
        _api_action("operator-workflows", "GET", "/api/operator/workflows", risk="read-only", requires_approval=False),
        _api_action("operator-workflows-update", "POST", "/api/operator/workflows", risk="local-catalog-write", requires_approval=True),
        _api_action("operator-autonomy-plan", "GET", "/api/operator/autonomy-plan", risk="read-only", requires_approval=False),
        _api_action("operator-build-watch-plan", "GET", "/api/operator/build-watch-plan", risk="read-only", requires_approval=False),
        _api_action("operator-activity", "GET", "/api/operator/activity", risk="read-only", requires_approval=False),
        _api_action("operator-routes", "GET", "/api/operator/routes", risk="read-only-route-proof", requires_approval=False),
    ]

    return {
        "mode": "read-only-agent-loops-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "loop_count": len(clean_loops),
            "workflow_count": len(clean_workflows),
            "workflow_command_count": len(workflow_commands),
            "loop_action_count": len(loop_action_ids),
            "loop_command_count": len(loop_commands),
            "approval_gated_count": len(approval_gated),
            "route_ready_count": int(matrix_summary.get("route_ready") or 0),
            "route_total_count": int(matrix_summary.get("total") or 0),
            "unresolved_route_count": int(matrix_summary.get("unresolved") or 0),
            "pending_count": len(pending),
            "failure_count": len(failed),
            "loop_alert_count": len(alert_rows),
            "critical_loop_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "starts_loops": False,
            "routes_commands": False,
            "executes_commands": False,
            "approves_commands": False,
            "starts_jobs": False,
            "changes_policy": False,
            "writes_files": False,
            "runs_shell": False,
            "uses_network": False,
            "next_action": "Open Automation Map to review loop templates, route proof, approval gates, and activity before starting any loop.",
        },
        "policy": trust_policy,
        "configured": configured_summary,
        "loop_rows": loop_rows,
        "workflow_rows": workflow_rows,
        "permission_rows": permission_rows,
        "activity_rows": activity_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "api_actions": api_actions,
        "route_matrix": route_matrix,
        "approval": {
            "required": False,
            "gate": "Agent Loop audit only",
            "policy": "This endpoint only audits Agent Loop templates, workflow routes, approval gates, and activity evidence. It does not start loops, route commands, execute commands, approve actions, start jobs, change trust policy, run shell commands, write files, or use network access.",
            "disallowed_by_default": [
                "start loop",
                "route command",
                "execute command",
                "approve action",
                "start job",
                "change trust policy",
                "run shell",
                "write files",
                "use network",
            ],
        },
        "paths": {
            "workflows": "data/operator_workflows.json",
            "commands": "data/operator_commands.json",
            "policy": "data/operator_policy.json",
            "activity": "data/operator_activity.json",
        },
    }
