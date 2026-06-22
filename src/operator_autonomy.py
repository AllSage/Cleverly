"""Read-only autonomy and trust-policy evidence for the Cleverly operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS, resolve_operator_route_matrix


TRUST_LABELS = {
    "local": "Local",
    "approval": "Approval",
    "network": "Network",
    "danger": "High Risk",
}
FAILURE_RE = ("fail", "failed", "error", "exception", "blocked")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


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


def _state_from_activity(record: dict[str, Any]) -> str:
    status = _trim(record.get("status") or record.get("state"), 120).lower()
    if any(term in status for term in FAILURE_RE):
        return "error"
    if any(term in status for term in ("pending", "approval", "running", "queued", "waiting")):
        return "warn"
    return "ok"


def _is_failure(record: dict[str, Any]) -> bool:
    return _state_from_activity(record) == "error"


def _is_pending(record: dict[str, Any]) -> bool:
    status = _trim(record.get("status") or record.get("state"), 120).lower()
    return any(term in status for term in ("pending", "approval", "running", "queued", "waiting"))


def _activity_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in record.get("events", []) if isinstance(item, dict)]


def _is_decision(record: dict[str, Any]) -> bool:
    text = " ".join(
        _trim(value, 240).lower()
        for value in (
            record.get("status"),
            record.get("detail"),
            record.get("result"),
            record.get("title"),
            *[event.get("detail") or event.get("status") for event in _activity_events(record)],
        )
        if value
    )
    return any(term in text for term in ("approved", "cancelled", "denied", "rejected"))


def _command_title(command: dict[str, Any]) -> str:
    return _trim(command.get("title") or command.get("id") or "Command", 160)


def _as_commands(commands: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in commands or [] if isinstance(item, dict) and _trim(item.get("id"), 160)]


def _as_records(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in records or [] if isinstance(item, dict)]


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


def run_operator_autonomy_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    activity: list[dict[str, Any]] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return a read-only proof of command autonomy, policy, and approval posture."""
    owner = owner or "local"
    clean_commands = _as_commands(commands)
    clean_workflows = _as_records(workflows)
    clean_activity = _as_records(activity)
    trust_policy = _normalize_policy(policy)
    configured = configured if isinstance(configured, dict) else {}

    tier_commands = {
        level: [command for command in clean_commands if _trust_level(command) == level]
        for level in TRUST_LEVELS
    }
    ask_tiers = [level for level in TRUST_LEVELS if trust_policy[level] == "ask"]
    auto_tiers = [level for level in TRUST_LEVELS if trust_policy[level] != "ask"]
    ask_commands = [command for command in clean_commands if _trust_mode(command, trust_policy) == "ask"]
    auto_commands = [command for command in clean_commands if _trust_mode(command, trust_policy) != "ask"]
    network_commands = tier_commands["network"]
    danger_commands = tier_commands["danger"]
    workflow_commands = [command for command in clean_commands if command.get("workflow")]
    workflow_ask = [command for command in workflow_commands if _trust_mode(command, trust_policy) == "ask"]
    route_matrix = resolve_operator_route_matrix(clean_commands, clean_workflows, trust_policy)
    matrix_summary = route_matrix.get("summary") if isinstance(route_matrix.get("summary"), dict) else {}
    pending = [record for record in clean_activity if _is_pending(record)]
    failed = [record for record in clean_activity if _is_failure(record)]
    retryable = [
        record for record in clean_activity
        if _trim(record.get("command_id"), 160) and _trim(record.get("command_id"), 160) != "chat-command"
    ]
    decisions = [record for record in clean_activity if _is_decision(record)]

    policy_rows = [
        {
            "id": f"tier-{level}",
            "state": "ok" if level == "local" and trust_policy[level] == "auto" else ("ok" if level != "local" and trust_policy[level] == "ask" else "warn"),
            "badge": "risk" if level == "danger" else level,
            "title": f"{TRUST_LABELS.get(level, level.title())} trust tier",
            "detail": f"{len(tier_commands[level])} command{'s' if len(tier_commands[level]) != 1 else ''}; mode={trust_policy[level]}; examples={', '.join(_command_title(command) for command in tier_commands[level][:3]) or 'none'}",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": trust_policy[level] == "ask",
        }
        for level in TRUST_LEVELS
    ]

    route_rows = [
        {
            "id": "command-catalog",
            "state": "ok" if clean_commands and configured.get("commands", True) else "warn",
            "badge": "cmd",
            "title": "Command catalog",
            "detail": f"{len(clean_commands)} persisted command{'s' if len(clean_commands) != 1 else ''} available for text, voice, palette, and dashboard routing",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "workflow-catalog",
            "state": "ok" if clean_workflows and configured.get("workflows", True) else "warn",
            "badge": "flow",
            "title": "Workflow target phrases",
            "detail": f"{len(clean_workflows)} workflow target phrase{'s' if len(clean_workflows) != 1 else ''}; {int(matrix_summary.get('ready') or 0)} route-ready",
            "action": "open-capability-map",
            "actionLabel": "Proof",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "route-matrix",
            "state": "ok" if int(matrix_summary.get("unresolved") or 0) == 0 and clean_workflows else ("warn" if clean_workflows else "loading"),
            "badge": "proof",
            "title": "Backend route matrix",
            "detail": f"{int(matrix_summary.get('route_ready') or 0)}/{int(matrix_summary.get('total') or 0)} workflow phrases resolve to the expected command",
            "action": "open-capability-map",
            "actionLabel": "Map",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "approval-routes",
            "state": "ok" if int(matrix_summary.get("approval_ready") or 0) >= int(matrix_summary.get("approval_gated") or 0) else "warn",
            "badge": "ask",
            "title": "Approval route readiness",
            "detail": f"{int(matrix_summary.get('approval_ready') or 0)}/{int(matrix_summary.get('approval_gated') or 0)} approval-gated workflow target{'s' if int(matrix_summary.get('approval_gated') or 0) != 1 else ''} are ready",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": True,
        },
    ]

    workflow_rows = [
        {
            "id": _trim(command.get("id"), 160),
            "state": "warn" if _trust_mode(command, trust_policy) == "ask" else "ok",
            "badge": _trim(command.get("category") or "flow", 24),
            "title": _command_title(command),
            "detail": f"{_trust_level(command)} tier; {_trust_mode(command, trust_policy)} mode",
            "action": _trim(command.get("id"), 160),
            "actionLabel": "Route",
            "executes": False,
            "requires_approval": _trust_mode(command, trust_policy) == "ask",
        }
        for command in workflow_commands[:8]
    ]

    activity_rows = [
        {
            "id": "pending-approvals",
            "state": "warn" if pending else "ok",
            "badge": "hold",
            "title": "Pending approvals",
            "detail": f"{len(pending)} command{'s' if len(pending) != 1 else ''} waiting/running/queued",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "approval-decisions",
            "state": "ok" if decisions else "loading",
            "badge": "decide",
            "title": "Approval decisions",
            "detail": f"{len(decisions)} approved/cancelled/denied decision record{'s' if len(decisions) != 1 else ''} visible",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "failed-commands",
            "state": "error" if failed else "ok",
            "badge": "fail",
            "title": "Autonomy failures",
            "detail": f"{len(failed)} failed command record{'s' if len(failed) != 1 else ''} visible",
            "action": "open-activity-preflight",
            "actionLabel": "Inspect",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "retryable-commands",
            "state": "ok" if retryable else "warn",
            "badge": "retry",
            "title": "Retry evidence",
            "detail": f"{len(retryable)} routed command{'s' if len(retryable) != 1 else ''} can be reviewed for retry through the current trust policy",
            "action": "open-activity-preflight",
            "actionLabel": "Retry",
            "executes": False,
            "requires_approval": True,
        },
    ]

    permission_rows = [
        {
            "id": "network-gate",
            "state": "ok" if trust_policy["network"] == "ask" else "warn",
            "badge": "net",
            "title": "Network command gate",
            "detail": f"{len(network_commands)} network command{'s' if len(network_commands) != 1 else ''}; policy={trust_policy['network']}",
            "action": "open-offline",
            "actionLabel": "Offline",
            "executes": False,
            "requires_approval": trust_policy["network"] == "ask",
        },
        {
            "id": "danger-gate",
            "state": "ok" if trust_policy["danger"] == "ask" else "warn",
            "badge": "risk",
            "title": "High-risk command gate",
            "detail": f"{len(danger_commands)} high-risk command{'s' if len(danger_commands) != 1 else ''}; policy={trust_policy['danger']}",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": trust_policy["danger"] == "ask",
        },
        {
            "id": "workflow-gate",
            "state": "ok" if workflow_ask else ("loading" if not workflow_commands else "warn"),
            "badge": "flow",
            "title": "Workflow approval gate",
            "detail": f"{len(workflow_ask)}/{len(workflow_commands)} workflow command{'s' if len(workflow_commands) != 1 else ''} currently ask before route",
            "action": "open-automation-map",
            "actionLabel": "Automation",
            "executes": False,
            "requires_approval": bool(workflow_ask),
        },
        {
            "id": "policy-write",
            "state": "ok",
            "badge": "cfg",
            "title": "Policy changes are separate",
            "detail": "This plan audits current policy only; changing trust modes requires the Trust Controls UI/API.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": False,
        },
    ]

    api_actions = [
        _api_action("autonomy-plan", "GET", "/api/operator/autonomy-plan", risk="read-only", requires_approval=False),
        _api_action("operator-policy", "GET", "/api/operator/policy", risk="read-only", requires_approval=False),
        _api_action("operator-policy-update", "POST", "/api/operator/policy", risk="local-policy-write", requires_approval=True),
        _api_action("operator-commands", "GET", "/api/operator/commands", risk="read-only", requires_approval=False),
        _api_action("operator-workflows", "GET", "/api/operator/workflows", risk="read-only", requires_approval=False),
        _api_action("operator-routes", "GET", "/api/operator/routes", risk="read-only-route-proof", requires_approval=False),
        _api_action("operator-route", "POST", "/api/operator/route", risk="read-only-route-proof", requires_approval=False),
        _api_action("operator-activity", "GET", "/api/operator/activity", risk="read-only", requires_approval=False),
        _api_action("activity-delete", "DELETE", "/api/operator/activity/{activity_id}", risk="local-ledger-delete", requires_approval=True),
    ]

    evidence_rows = [
        {
            "id": "policy",
            "state": "ok" if configured.get("policy", True) else "warn",
            "badge": "policy",
            "title": "Persisted trust policy",
            "detail": "data/operator_policy.json",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "id": "catalog",
            "state": "ok" if configured.get("commands", True) and clean_commands else "warn",
            "badge": "cat",
            "title": "Persisted command catalog",
            "detail": "data/operator_commands.json",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            "id": "workflows",
            "state": "ok" if configured.get("workflows", True) and clean_workflows else "warn",
            "badge": "flow",
            "title": "Persisted workflow catalog",
            "detail": "data/operator_workflows.json",
            "action": "open-automation-map",
            "actionLabel": "Automation",
        },
        {
            "id": "activity",
            "state": "ok" if clean_activity else "loading",
            "badge": "log",
            "title": "Activity ledger",
            "detail": "data/operator_activity.json",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
    ]

    unresolved = int(matrix_summary.get("unresolved") or 0)
    state = "error" if failed else ("warn" if unresolved or not clean_commands or trust_policy["network"] != "ask" or trust_policy["danger"] != "ask" else "ok")
    return {
        "mode": "read-only-autonomy-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "command_count": len(clean_commands),
            "workflow_count": len(clean_workflows),
            "workflow_command_count": len(workflow_commands),
            "ask_tier_count": len(ask_tiers),
            "auto_tier_count": len(auto_tiers),
            "ask_command_count": len(ask_commands),
            "auto_command_count": len(auto_commands),
            "network_command_count": len(network_commands),
            "danger_command_count": len(danger_commands),
            "workflow_ask_count": len(workflow_ask),
            "route_ready_count": int(matrix_summary.get("route_ready") or 0),
            "route_total_count": int(matrix_summary.get("total") or 0),
            "unresolved_route_count": unresolved,
            "approval_ready_count": int(matrix_summary.get("approval_ready") or 0),
            "approval_gated_count": int(matrix_summary.get("approval_gated") or 0),
            "pending_count": len(pending),
            "failure_count": len(failed),
            "retryable_count": len(retryable),
            "decision_count": len(decisions),
            "routes_commands": False,
            "executes_commands": False,
            "approves_commands": False,
            "retries_commands": False,
            "starts_workflows": False,
            "changes_policy": False,
            "deletes_activity": False,
            "runs_shell": False,
            "uses_network": False,
            "next_action": "Open Autonomy Map or Trust Controls to review ask/auto tiers, route proof, workflow gates, and activity decisions before running commands.",
        },
        "policy": trust_policy,
        "configured": {
            "commands": bool(configured.get("commands", bool(clean_commands))),
            "workflows": bool(configured.get("workflows", bool(clean_workflows))),
            "policy": bool(configured.get("policy", policy is not None)),
        },
        "policy_rows": policy_rows,
        "route_rows": route_rows,
        "workflow_rows": workflow_rows,
        "activity_rows": activity_rows,
        "permission_rows": permission_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "route_matrix": route_matrix,
        "approval": {
            "required": False,
            "gate": "Autonomy audit only",
            "policy": "This endpoint only audits command autonomy, trust policy, workflow routes, and activity evidence. It does not route commands, approve commands, retry commands, start workflows, change trust policy, delete activity, run shell commands, modify files, or use network access.",
            "disallowed_by_default": [
                "route command",
                "approve command",
                "retry command",
                "start workflow",
                "change trust policy",
                "delete activity",
                "run shell",
            ],
        },
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
            "activity": "data/operator_activity.json",
        },
    }
