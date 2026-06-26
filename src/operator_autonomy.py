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


def _entry_rows(*, configured: dict[str, bool], command_count: int, workflow_count: int) -> list[dict[str, Any]]:
    ready = configured.get("commands", command_count > 0) and configured.get("policy", True)
    workflow_ready = configured.get("workflows", workflow_count > 0) and workflow_count > 0
    common = {
        "command_id": "open-automation-map",
        "trust_command_id": "open-trust-controls",
        "activity_command_id": "open-activity-preflight",
        "palette_command_id": "open-command-palette",
        "route_api": "/api/operator/route",
        "routes_api": "/api/operator/routes",
        "policy_api": "/api/operator/policy",
        "workflows_api": "/api/operator/workflows",
        "activity_api": "/api/operator/activity",
        "requires_approval": True,
        "executes": False,
        "routes_commands": False,
        "executes_commands": False,
        "approves_commands": False,
        "retries_commands": False,
        "starts_workflows": False,
        "changes_policy": False,
        "deletes_activity": False,
        "runs_shell": False,
        "modifies_files": False,
        "uses_network": False,
    }
    state = "ok" if ready else "warn"
    workflow_state = "ok" if ready and workflow_ready else "warn"
    return [
        {
            **common,
            "id": "autonomy-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard autonomy route",
            "detail": f"The Automation dashboard opens policy, route, approval, workflow, and activity evidence first; {command_count} command(s) visible.",
            "action": "open-automation-map",
            "actionLabel": "Automation",
        },
        {
            **common,
            "id": "autonomy-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed autonomy request route",
            "detail": "Typed automation requests route through read-only command matching and trust policy evidence before any command can run.",
            "action": "open-automation-map",
            "actionLabel": "Automation",
        },
        {
            **common,
            "id": "autonomy-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette autonomy route",
            "detail": "The command palette can inspect route proof and ask gates without approving, retrying, starting workflows, or changing policy.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "autonomy-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice autonomy route",
            "detail": "Voice requests can open autonomy preflight and trust controls without executing commands, speaking approvals, or using network access.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "autonomy-workflow-route",
            "entry": "workflow",
            "state": workflow_state,
            "badge": "flow",
            "title": "Workflow autonomy route",
            "detail": f"Workflow handoffs review {workflow_count} persisted workflow route(s), ask gates, and activity evidence before any loop or workflow starts.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _decision_mode_rows(
    *,
    clean_commands: list[dict[str, Any]],
    ask_commands: list[dict[str, Any]],
    auto_commands: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    trust_policy: dict[str, str],
) -> list[dict[str, Any]]:
    network_danger_ask = trust_policy.get("network") == "ask" and trust_policy.get("danger") == "ask"
    return [
        {
            "id": "suggest",
            "mode": "suggest",
            "state": "ok" if clean_commands else "warn",
            "badge": "suggest",
            "title": "Suggest",
            "detail": f"{len(clean_commands)} command{'s' if len(clean_commands) != 1 else ''} can be suggested with preview, route, trust, and recovery context before execution.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "executes": False,
            "routes_commands": False,
            "approves_commands": False,
            "starts_workflows": False,
            "uses_network": False,
        },
        {
            "id": "ask",
            "mode": "ask",
            "state": "ok" if ask_commands and network_danger_ask else "warn",
            "badge": "ask",
            "title": "Ask",
            "detail": f"{len(ask_commands)} command{'s' if len(ask_commands) != 1 else ''} ask under current policy; network={trust_policy.get('network')}; danger={trust_policy.get('danger')}.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "routes_commands": False,
            "approves_commands": False,
            "starts_workflows": False,
            "uses_network": False,
        },
        {
            "id": "execute-once",
            "mode": "execute",
            "state": "ok" if decisions or pending else "loading",
            "badge": "exec",
            "title": "Execute after approval",
            "detail": f"{len(decisions)} approval decision record{'s' if len(decisions) != 1 else ''}; {len(pending)} pending/running/queued command{'s' if len(pending) != 1 else ''} visible in Activity.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "executes": False,
            "routes_commands": False,
            "approves_commands": False,
            "starts_workflows": False,
            "uses_network": False,
        },
        {
            "id": "auto-execute",
            "mode": "auto-execute",
            "state": "ok" if auto_commands and network_danger_ask and not failed else "warn",
            "badge": "auto",
            "title": "Auto-execute within trust tier",
            "detail": f"{len(auto_commands)} command{'s' if len(auto_commands) != 1 else ''} auto under policy; {len(failed)} failed record{'s' if len(failed) != 1 else ''} require review before broader autonomy.",
            "action": "open-autonomy-map",
            "actionLabel": "Autonomy",
            "executes": False,
            "routes_commands": False,
            "approves_commands": False,
            "starts_workflows": False,
            "uses_network": False,
        },
    ]


def _permission_checkpoint_rows(
    *,
    clean_commands: list[dict[str, Any]],
    ask_commands: list[dict[str, Any]],
    auto_commands: list[dict[str, Any]],
    workflow_commands: list[dict[str, Any]],
    workflow_ask: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    retryable: list[dict[str, Any]],
    trust_policy: dict[str, str],
) -> list[dict[str, Any]]:
    network_danger_ask = trust_policy.get("network") == "ask" and trust_policy.get("danger") == "ask"
    local_auto_only = bool(auto_commands) and all(_trust_level(command) == "local" for command in auto_commands)
    common = {
        "executes": False,
        "routes_commands": False,
        "executes_commands": False,
        "approves_commands": False,
        "retries_commands": False,
        "starts_workflows": False,
        "changes_policy": False,
        "deletes_activity": False,
        "runs_shell": False,
        "modifies_files": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "checkpoint-suggest-route-preview",
            "checkpoint": "suggest",
            "state": "ok" if clean_commands else "warn",
            "badge": "suggest",
            "title": "Suggest checkpoint",
            "detail": f"{len(clean_commands)} command(s) can be previewed with route, trust tier, and recovery context before any execution path.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_approval": False,
        },
        {
            **common,
            "id": "checkpoint-ask-evidence-review",
            "checkpoint": "ask",
            "state": "ok" if ask_commands and network_danger_ask else "warn",
            "badge": "ask",
            "title": "Ask checkpoint",
            "detail": f"{len(ask_commands)} ask-gated command(s); network={trust_policy.get('network')}; danger={trust_policy.get('danger')}.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        },
        {
            **common,
            "id": "checkpoint-execute-ledger",
            "checkpoint": "execute",
            "state": "ok" if decisions or pending else "loading",
            "badge": "exec",
            "title": "Execute checkpoint",
            "detail": f"{len(decisions)} decision record(s), {len(pending)} pending record(s), and {len(retryable)} retryable command record(s) are visible in Activity.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "requires_approval": True,
        },
        {
            **common,
            "id": "checkpoint-auto-local-scope",
            "checkpoint": "auto-execute",
            "state": "ok" if local_auto_only and network_danger_ask and not failed else "warn",
            "badge": "auto",
            "title": "Auto-execute scope checkpoint",
            "detail": f"{len(auto_commands)} auto command(s); auto scope should remain local-only while network and high-risk tiers stay ask-gated.",
            "action": "open-autonomy-map",
            "actionLabel": "Autonomy",
            "requires_approval": False,
        },
        {
            **common,
            "id": "checkpoint-workflow-handoff",
            "checkpoint": "workflow",
            "state": "ok" if workflow_commands and len(workflow_ask) == len(workflow_commands) else ("loading" if not workflow_commands else "warn"),
            "badge": "flow",
            "title": "Workflow handoff checkpoint",
            "detail": f"{len(workflow_ask)}/{len(workflow_commands)} workflow command(s) ask before loop or automation handoff.",
            "action": "open-automation-map",
            "actionLabel": "Automation",
            "requires_approval": bool(workflow_commands),
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
    approval_command_id: str = "",
    requires_approval: bool = False,
    routes_commands: bool = False,
    approves_commands: bool = False,
    retries_commands: bool = False,
    starts_workflows: bool = False,
    changes_policy: bool = False,
    deletes_activity: bool = False,
    writes_activity: bool = False,
    runs_shell: bool = False,
    modifies_files: bool = False,
    uses_network: bool = False,
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
        "routes_commands": False,
        "executes_commands": False,
        "approves_commands": False,
        "retries_commands": False,
        "starts_workflows": False,
        "changes_policy": False,
        "deletes_activity": False,
        "writes_activity": False,
        "runs_shell": False,
        "modifies_files": False,
        "uses_network": False,
        "gated_operation": {
            "routes_commands": routes_commands,
            "approves_commands": approves_commands,
            "retries_commands": retries_commands,
            "starts_workflows": starts_workflows,
            "changes_policy": changes_policy,
            "deletes_activity": deletes_activity,
            "writes_activity": writes_activity,
            "runs_shell": runs_shell,
            "modifies_files": modifies_files,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    *,
    clean_commands: list[dict[str, Any]],
    ask_commands: list[dict[str, Any]],
    workflow_commands: list[dict[str, Any]],
    workflow_ask: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    retryable: list[dict[str, Any]],
    trust_policy: dict[str, str],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    network_ask = trust_policy.get("network") == "ask"
    danger_ask = trust_policy.get("danger") == "ask"
    policy_ready = bool(configured.get("policy", True))
    workflow_ready = bool(workflow_commands) and len(workflow_ask) == len(workflow_commands)
    activity_ready = bool(pending or decisions or failed or retryable)
    return [
        _handoff_row(
            "autonomy-route-preview-handoff",
            "ok" if clean_commands else "warn",
            "route",
            "Route preview handoff",
            "Suggested commands can move through backend route proof before the browser executes any command.",
            "open-command-palette",
            "Palette",
            target_api="/api/operator/route",
            routes_commands=True,
        ),
        _handoff_row(
            "autonomy-ask-approval-handoff",
            "ok" if ask_commands and network_ask and danger_ask else "warn",
            "ask",
            "Ask and approval handoff",
            f"{len(ask_commands)} ask-gated command(s) can hand off to approval evidence before execution.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/approval-plan",
            approval_command_id="request-approval-decision",
            requires_approval=True,
            approves_commands=True,
        ),
        _handoff_row(
            "autonomy-workflow-start-handoff",
            "ok" if workflow_ready else "warn",
            "flow",
            "Workflow start handoff",
            f"{len(workflow_ask)}/{len(workflow_commands)} workflow command(s) are ask-gated before loop or automation start.",
            "open-automation-map",
            "Automation",
            target_api="/api/operator/loops-plan",
            approval_command_id="request-workflow-start",
            requires_approval=True,
            routes_commands=True,
            starts_workflows=True,
        ),
        _handoff_row(
            "autonomy-activity-ledger-handoff",
            "ok" if activity_ready else "loading",
            "log",
            "Activity ledger handoff",
            "Approved or attempted autonomous work can be reviewed with status, route proof, trust mode, logs, retry, and recovery metadata.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity-plan",
            writes_activity=True,
        ),
        _handoff_row(
            "autonomy-retry-recovery-handoff",
            "warn" if failed else ("ok" if retryable else "loading"),
            "retry",
            "Retry and recovery handoff",
            f"{len(retryable)} retryable record(s) and {len(failed)} failure(s) can hand off to recovery before replay.",
            "open-activity-preflight",
            "Recovery",
            target_api="/api/operator/recovery-plan",
            approval_command_id="request-recovery-action",
            requires_approval=True,
            retries_commands=True,
        ),
        _handoff_row(
            "autonomy-trust-policy-handoff",
            "ok" if policy_ready and network_ask and danger_ask else "warn",
            "cfg",
            "Trust policy handoff",
            "Policy changes remain separate from this audit and route through Trust Controls before changing local autonomy behavior.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/policy",
            approval_command_id="request-policy-change",
            requires_approval=True,
            changes_policy=True,
        ),
        _handoff_row(
            "autonomy-network-offline-handoff",
            "ok" if network_ask else "warn",
            "net",
            "Network and offline handoff",
            f"Network tier is {trust_policy.get('network')}; network-capable commands stay behind Offline Control and approval policy.",
            "open-offline",
            "Offline",
            target_api="/api/operator/safety-plan",
            approval_command_id="request-network-break-glass",
            requires_approval=True,
            uses_network=True,
        ),
        _handoff_row(
            "autonomy-safety-boundary-handoff",
            "ok" if danger_ask else "error",
            "risk",
            "Safety boundary handoff",
            f"High-risk tier is {trust_policy.get('danger')}; destructive, shell, filesystem, and cleanup actions require safety review.",
            "open-trust-controls",
            "Safety",
            target_api="/api/operator/safety-plan",
            approval_command_id="request-approval-decision",
            requires_approval=True,
            deletes_activity=True,
            runs_shell=True,
            modifies_files=True,
        ),
    ]


def _automation_alert_rows(
    *,
    clean_commands: list[dict[str, Any]],
    clean_workflows: list[dict[str, Any]],
    workflow_commands: list[dict[str, Any]],
    workflow_ask: list[dict[str, Any]],
    trust_policy: dict[str, str],
    pending: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    retryable: list[dict[str, Any]],
    unresolved: int,
    matrix_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not clean_commands:
        rows.append(
            {
                "id": "command-catalog-missing",
                "state": "error",
                "badge": "cmd",
                "title": "Command catalog missing",
                "detail": "No persisted operator commands are visible for palette, voice, dashboard, or workflow routing.",
                "action": "open-command-palette",
                "actionLabel": "Palette",
                "requires_approval": False,
            }
        )
    if not clean_workflows:
        rows.append(
            {
                "id": "workflow-catalog-missing",
                "state": "warn",
                "badge": "flow",
                "title": "Workflow catalog not published",
                "detail": "No persisted workflow target phrases are visible in data/operator_workflows.json.",
                "action": "open-automation-map",
                "actionLabel": "Map",
                "requires_approval": False,
            }
        )
    if unresolved:
        rows.append(
            {
                "id": "workflow-route-unresolved",
                "state": "error",
                "badge": "route",
                "title": "Workflow route proof needs review",
                "detail": f"{unresolved} workflow target(s) do not resolve to the expected command.",
                "action": "open-capability-map",
                "actionLabel": "Proof",
                "requires_approval": False,
            }
        )
    if workflow_commands and not workflow_ask:
        rows.append(
            {
                "id": "workflow-ask-gate-missing",
                "state": "warn",
                "badge": "ask",
                "title": "Workflow ask gate missing",
                "detail": "Workflow commands can route without an ask gate under the current trust policy.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": True,
            }
        )
    if trust_policy["network"] != "ask":
        rows.append(
            {
                "id": "network-auto-enabled",
                "state": "warn",
                "badge": "net",
                "title": "Network tier is not ask-gated",
                "detail": f"Network command policy is {trust_policy['network']}; local-first operation expects ask.",
                "action": "open-offline",
                "actionLabel": "Offline",
                "requires_approval": True,
            }
        )
    if trust_policy["danger"] != "ask":
        rows.append(
            {
                "id": "danger-auto-enabled",
                "state": "error",
                "badge": "risk",
                "title": "High-risk tier is not ask-gated",
                "detail": f"High-risk command policy is {trust_policy['danger']}; destructive actions should ask by default.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": True,
            }
        )
    if pending:
        rows.append(
            {
                "id": "pending-automation-approvals",
                "state": "warn",
                "badge": "hold",
                "title": "Pending automation approvals",
                "detail": f"{len(pending)} command(s) are waiting, running, queued, or pending approval.",
                "action": "open-activity-preflight",
                "actionLabel": "Activity",
                "requires_approval": False,
            }
        )
    if failed:
        rows.append(
            {
                "id": "failed-automation-commands",
                "state": "error",
                "badge": "fail",
                "title": "Automation command failures",
                "detail": f"{len(failed)} failed command record(s) need recovery review before retry.",
                "action": "open-activity-preflight",
                "actionLabel": "Inspect",
                "requires_approval": False,
            }
        )
    if retryable:
        rows.append(
            {
                "id": "retryable-automation-commands",
                "state": "warn",
                "badge": "retry",
                "title": "Retryable command records",
                "detail": f"{len(retryable)} routed command record(s) have retry evidence; retry still follows the current trust policy.",
                "action": "open-activity-preflight",
                "actionLabel": "Retry",
                "requires_approval": True,
            }
        )
    approval_gated = int(matrix_summary.get("approval_gated") or 0)
    approval_ready = int(matrix_summary.get("approval_ready") or 0)
    if approval_gated and approval_ready < approval_gated:
        rows.append(
            {
                "id": "approval-route-incomplete",
                "state": "warn",
                "badge": "ask",
                "title": "Approval route incomplete",
                "detail": f"{approval_ready}/{approval_gated} approval-gated workflow target(s) are route-ready.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": True,
            }
        )
    return rows[:12]


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
    decision_mode_rows = _decision_mode_rows(
        clean_commands=clean_commands,
        ask_commands=ask_commands,
        auto_commands=auto_commands,
        pending=pending,
        decisions=decisions,
        failed=failed,
        trust_policy=trust_policy,
    )
    permission_checkpoint_rows = _permission_checkpoint_rows(
        clean_commands=clean_commands,
        ask_commands=ask_commands,
        auto_commands=auto_commands,
        workflow_commands=workflow_commands,
        workflow_ask=workflow_ask,
        pending=pending,
        decisions=decisions,
        failed=failed,
        retryable=retryable,
        trust_policy=trust_policy,
    )

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
    alert_rows = _automation_alert_rows(
        clean_commands=clean_commands,
        clean_workflows=clean_workflows,
        workflow_commands=workflow_commands,
        workflow_ask=workflow_ask,
        trust_policy=trust_policy,
        pending=pending,
        failed=failed,
        retryable=retryable,
        unresolved=unresolved,
        matrix_summary=matrix_summary,
    )
    entry_rows = _entry_rows(
        configured=configured,
        command_count=len(clean_commands),
        workflow_count=len(clean_workflows),
    )
    handoff_rows = _handoff_rows(
        clean_commands=clean_commands,
        ask_commands=ask_commands,
        workflow_commands=workflow_commands,
        workflow_ask=workflow_ask,
        pending=pending,
        decisions=decisions,
        failed=failed,
        retryable=retryable,
        trust_policy=trust_policy,
        configured=configured,
    )
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
            "decision_mode_count": len(decision_mode_rows),
            "decision_mode_ready_count": len([row for row in decision_mode_rows if row.get("state") == "ok"]),
            "permission_checkpoint_count": len(permission_checkpoint_rows),
            "permission_checkpoint_ready_count": len([row for row in permission_checkpoint_rows if row.get("state") == "ok"]),
            "automation_alert_count": len(alert_rows),
            "critical_automation_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "routes_commands": False,
            "executes_commands": False,
            "approves_commands": False,
            "retries_commands": False,
            "starts_workflows": False,
            "changes_policy": False,
            "deletes_activity": False,
            "writes_activity": False,
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
        "decision_mode_rows": decision_mode_rows,
        "permission_checkpoint_rows": permission_checkpoint_rows,
        "permission_rows": permission_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
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
