"""Read-only approval queue and permission posture plan for Cleverly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS


TRUST_LABELS = {
    "local": "Local",
    "approval": "Approval",
    "network": "Network",
    "danger": "High Risk",
}
PENDING_TERMS = ("pending", "approval", "waiting", "queued")
FAILURE_TERMS = ("fail", "failed", "error", "exception", "blocked")
APPROVED_TERMS = ("approved", "allowed", "accepted")
CANCELLED_TERMS = ("cancelled", "canceled", "denied", "rejected")


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


def _as_records(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [row for row in rows or [] if isinstance(row, dict)]


def _as_commands(commands: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        command for command in commands or []
        if isinstance(command, dict) and _trim(command.get("id"), 160)
    ]


def _trust_level(command: dict[str, Any]) -> str:
    trust = str(command.get("trust") or "local").lower()
    return trust if trust in TRUST_LEVELS else "local"


def _trust_mode(command: dict[str, Any], policy: dict[str, str]) -> str:
    if command.get("alwaysAsk") or command.get("always_ask"):
        return "ask"
    return policy.get(_trust_level(command), "ask")


def _record_text(record: dict[str, Any]) -> str:
    events = [
        str(event.get("status") or event.get("detail") or "")
        for event in record.get("events", [])
        if isinstance(event, dict)
    ]
    values = [
        record.get("id"),
        record.get("command_id"),
        record.get("title"),
        record.get("category"),
        record.get("status"),
        record.get("state"),
        record.get("detail"),
        record.get("result"),
        record.get("error"),
        *events,
    ]
    return " ".join(_trim(value, 240).lower() for value in values if value)


def _is_pending(record: dict[str, Any]) -> bool:
    text = _record_text(record)
    return any(term in text for term in PENDING_TERMS)


def _is_failure(record: dict[str, Any]) -> bool:
    text = _record_text(record)
    return any(term in text for term in FAILURE_TERMS)


def _decision_type(record: dict[str, Any]) -> str:
    text = _record_text(record)
    if any(term in text for term in APPROVED_TERMS):
        return "approved"
    if any(term in text for term in CANCELLED_TERMS):
        return "cancelled"
    return ""


def _command_title(command: dict[str, Any] | None, fallback: str = "Command") -> str:
    return _trim((command or {}).get("title") or (command or {}).get("id") or fallback, 180)


def _commands_by_id(commands: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_trim(command.get("id"), 160): command for command in commands}


def _approval_command_rows(commands: list[dict[str, Any]], policy: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for command in commands:
        command_id = _trim(command.get("id"), 160)
        trust = _trust_level(command)
        mode = _trust_mode(command, policy)
        risky = trust in {"approval", "network", "danger"} or command.get("workflow") or command.get("alwaysAsk") or command.get("always_ask")
        if not risky and mode != "ask":
            continue
        rows.append(
            {
                "id": command_id,
                "state": "ok" if mode == "ask" else ("error" if trust == "danger" else "warn"),
                "badge": "risk" if trust == "danger" else trust,
                "title": _command_title(command),
                "detail": f"trust={trust}; mode={mode}; category={_trim(command.get('category') or 'Operator', 80)}",
                "trust": trust,
                "trust_mode": mode,
                "workflow": bool(command.get("workflow")),
                "requires_approval": mode == "ask",
                "executes": False,
                "action": command_id,
                "actionLabel": "Route",
            }
        )
    rows.sort(key=lambda row: (row["state"] == "ok", row["trust"], row["title"]))
    return rows


def _workflow_gate_rows(
    workflows: list[dict[str, Any]],
    commands_by_id: dict[str, dict[str, Any]],
    policy: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for workflow in workflows:
        approval_id = _trim(workflow.get("approvalId") or workflow.get("approval_id"), 160)
        expected_id = _trim(workflow.get("expectedRouteId") or workflow.get("expected_route_id") or workflow.get("commandId") or workflow.get("command_id"), 160)
        if not approval_id and not expected_id:
            continue
        approval_command = commands_by_id.get(approval_id) if approval_id else None
        expected_command = commands_by_id.get(expected_id) if expected_id else None
        approval_mode = _trust_mode(approval_command, policy) if approval_command else ("missing" if approval_id else "")
        gate_ready = not approval_id or approval_mode == "ask"
        route_ready = not expected_id or expected_command is not None
        rows.append(
            {
                "id": _trim(workflow.get("id") or approval_id or expected_id, 160),
                "state": "ok" if gate_ready and route_ready else ("error" if not route_ready else "warn"),
                "badge": "flow",
                "title": _trim(workflow.get("phrase") or workflow.get("title") or workflow.get("plan") or expected_id or approval_id, 220),
                "detail": f"route={expected_id or 'none'}; approval={approval_id or 'none'}; mode={approval_mode or 'n/a'}",
                "expected_route_id": expected_id,
                "approval_id": approval_id,
                "approval_mode": approval_mode,
                "approval_ready": gate_ready,
                "route_ready": route_ready,
                "requires_approval": bool(approval_id),
                "executes": False,
                "action": approval_id or expected_id or "open-automation-map",
                "actionLabel": "Gate",
            }
        )
    return rows


def _approval_queue_rows(
    activity: list[dict[str, Any]],
    commands_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in activity:
        if not _is_pending(record):
            continue
        command_id = _trim(record.get("command_id"), 160)
        command = commands_by_id.get(command_id)
        rows.append(
            {
                "id": _trim(record.get("id") or command_id or "pending-approval", 160),
                "state": "warn",
                "badge": "hold",
                "title": _trim(record.get("title") or _command_title(command, "Pending approval"), 220),
                "detail": _trim(record.get("detail") or record.get("status") or "Waiting for operator decision", 500),
                "command_id": command_id,
                "trust": _trim(record.get("trust") or (command and _trust_level(command)) or "", 80),
                "trust_mode": _trim(record.get("trust_mode") or "", 80),
                "requires_approval": True,
                "executes": False,
                "action": record.get("id") and f"activity-detail:{record['id']}" or "open-activity-preflight",
                "actionLabel": "Review",
            }
        )
    return rows[:20]


def _decision_rows(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in activity:
        decision = _decision_type(record)
        if not decision:
            continue
        rows.append(
            {
                "id": _trim(record.get("id") or record.get("command_id") or f"{decision}-decision", 160),
                "state": "ok" if decision == "approved" else "warn",
                "badge": "allow" if decision == "approved" else "stop",
                "title": _trim(record.get("title") or record.get("command_id") or decision.title(), 220),
                "detail": _trim(record.get("detail") or record.get("result") or record.get("status") or f"Operator {decision}", 500),
                "decision": decision,
                "command_id": _trim(record.get("command_id"), 160),
                "executes": False,
                "action": record.get("id") and f"activity-detail:{record['id']}" or "open-activity-preflight",
                "actionLabel": "Inspect",
            }
        )
    return rows[:20]


def _failure_rows(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in activity:
        if not _is_failure(record):
            continue
        rows.append(
            {
                "id": _trim(record.get("id") or record.get("command_id") or "failed-command", 160),
                "state": "error",
                "badge": "fail",
                "title": _trim(record.get("title") or record.get("command_id") or "Failed command", 220),
                "detail": _trim(record.get("error") or record.get("detail") or record.get("status") or "Command failure needs review", 500),
                "command_id": _trim(record.get("command_id"), 160),
                "requires_approval": False,
                "executes": False,
                "action": record.get("id") and f"activity-detail:{record['id']}" or "open-activity-preflight",
                "actionLabel": "Inspect",
            }
        )
    return rows[:20]


def _policy_rows(policy: dict[str, str], commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for level in TRUST_LEVELS:
        level_commands = [command for command in commands if _trust_level(command) == level]
        mode = policy[level]
        rows.append(
            {
                "id": f"trust-{level}",
                "state": "ok" if (level == "local" and mode == "auto") or (level != "local" and mode == "ask") else ("error" if level == "danger" else "warn"),
                "badge": "risk" if level == "danger" else level,
                "title": f"{TRUST_LABELS.get(level, level.title())} trust tier",
                "detail": f"{len(level_commands)} command(s); mode={mode}; examples={', '.join(_command_title(command) for command in level_commands[:3]) or 'none'}",
                "level": level,
                "mode": mode,
                "command_count": len(level_commands),
                "requires_approval": mode == "ask",
                "executes": False,
                "action": "open-trust-controls",
                "actionLabel": "Trust",
            }
        )
    return rows


def _decision_checkpoint_rows(
    queue_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
    policy_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    weak_policy = [row for row in policy_rows if row["level"] in {"approval", "network", "danger"} and row["mode"] != "ask"]
    return [
        {
            "id": "review-evidence-before-decision",
            "state": "warn" if queue_rows else "ok",
            "badge": "review",
            "title": "Review evidence before decision",
            "detail": f"{len(queue_rows)} pending approval record(s) should be opened in Activity before allow, cancel, retry, or policy changes.",
            "action": queue_rows[0]["action"] if queue_rows else "open-activity-preflight",
            "actionLabel": "Review",
            "requires_approval": False,
            "executes": False,
            "approves_commands": False,
            "cancels_commands": False,
            "retries_commands": False,
            "changes_policy": False,
            "writes_activity": False,
            "uses_network": False,
        },
        {
            "id": "allow-cancel-stays-explicit",
            "state": "ok",
            "badge": "ask",
            "title": "Allow or cancel stays explicit",
            "detail": "Approval posture can open Trust Controls and Activity, but it never records allow/cancel decisions from this read-only plan.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
            "executes": False,
            "approves_commands": False,
            "cancels_commands": False,
            "retries_commands": False,
            "changes_policy": False,
            "writes_activity": False,
            "uses_network": False,
        },
        {
            "id": "retry-after-recovery-checkpoint",
            "state": "warn" if failure_rows else "ok",
            "badge": "retry",
            "title": "Retry after recovery checkpoint",
            "detail": f"{len(failure_rows)} failed activity record(s) should pass Activity and Recovery Map review before replay.",
            "action": failure_rows[0]["action"] if failure_rows else "open-recovery-map",
            "actionLabel": "Recovery",
            "requires_approval": bool(failure_rows),
            "executes": False,
            "approves_commands": False,
            "cancels_commands": False,
            "retries_commands": False,
            "changes_policy": False,
            "writes_activity": False,
            "uses_network": False,
        },
        {
            "id": "trust-policy-change-gate",
            "state": "error" if any(row["level"] == "danger" for row in weak_policy) else ("warn" if weak_policy else "ok"),
            "badge": "trust",
            "title": "Trust policy change gate",
            "detail": (
                f"{len(weak_policy)} approval/network/high-risk trust tier(s) are not ask-gated; policy changes stay in Trust Controls."
                if weak_policy
                else "Approval, network, and high-risk trust tiers are ask-gated."
            ),
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
            "executes": False,
            "approves_commands": False,
            "cancels_commands": False,
            "retries_commands": False,
            "changes_policy": False,
            "writes_activity": False,
            "uses_network": False,
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
    approval_command_id: str = "open-trust-controls",
    requires_approval: bool = True,
    routes_commands: bool = False,
    executes_commands: bool = False,
    approves_commands: bool = False,
    cancels_commands: bool = False,
    retries_commands: bool = False,
    changes_policy: bool = False,
    writes_activity: bool = False,
    starts_workflows: bool = False,
    runs_shell: bool = False,
    writes_files: bool = False,
    uses_network: bool = False,
    network_after_approval: bool = False,
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
        "cancels_commands": False,
        "retries_commands": False,
        "changes_policy": False,
        "writes_activity": False,
        "starts_workflows": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": uses_network,
        "network_after_approval": network_after_approval,
        "gated_operation": {
            "routes_commands": routes_commands,
            "executes_commands": executes_commands,
            "approves_commands": approves_commands,
            "cancels_commands": cancels_commands,
            "retries_commands": retries_commands,
            "changes_policy": changes_policy,
            "writes_activity": writes_activity,
            "starts_workflows": starts_workflows,
            "runs_shell": runs_shell,
            "writes_files": writes_files,
            "uses_network": uses_network or network_after_approval,
        },
    }


def _handoff_rows(
    *,
    queue_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
    workflow_rows: list[dict[str, Any]],
    policy_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    weak_policy = [row for row in policy_rows if row["level"] in {"approval", "network", "danger"} and row["mode"] != "ask"]
    network_weak = any(row["level"] == "network" for row in weak_policy)
    danger_weak = any(row["level"] == "danger" for row in weak_policy)
    workflow_issues = [row for row in workflow_rows if row.get("state") != "ok"]
    return [
        _handoff_row(
            "approval-evidence-review-handoff",
            "warn" if queue_rows else "ok",
            "review",
            "Evidence review handoff",
            f"{len(queue_rows)} pending approval record(s) should be opened in Activity before any allow, cancel, retry, or policy change.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
        ),
        _handoff_row(
            "approval-allow-cancel-handoff",
            "warn" if queue_rows else "ok",
            "ask",
            "Allow/cancel decision handoff",
            "Allow and cancel decisions stay explicit and must be recorded through the approval UI, not this read-only plan.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/activity",
            approves_commands=True,
            cancels_commands=True,
            writes_activity=True,
        ),
        _handoff_row(
            "approval-retry-recovery-handoff",
            "warn" if failure_rows else "ok",
            "retry",
            "Retry after recovery handoff",
            f"{len(failure_rows)} failed record(s) should pass Activity and Recovery review before replay.",
            "open-recovery-map",
            "Recovery",
            target_api="/api/operator/recovery-plan",
            retries_commands=True,
            executes_commands=True,
            runs_shell=True,
        ),
        _handoff_row(
            "approval-policy-change-handoff",
            "error" if danger_weak else ("warn" if weak_policy else "ok"),
            "trust",
            "Trust policy change handoff",
            f"{len(weak_policy)} approval/network/high-risk tier(s) are not ask-gated; policy writes stay in Trust Controls.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/policy",
            changes_policy=True,
            writes_files=True,
        ),
        _handoff_row(
            "approval-workflow-gate-handoff",
            "warn" if workflow_issues else "ok",
            "flow",
            "Workflow approval gate handoff",
            f"{len(workflow_issues)} workflow route(s) need approval-gate review before loops or jobs can start.",
            "open-automation-map",
            "Automation",
            target_api="/api/operator/workflows",
            starts_workflows=True,
            executes_commands=True,
        ),
        _handoff_row(
            "approval-network-risk-handoff",
            "warn" if network_weak else "ok",
            "net",
            "Network approval handoff",
            "Network-capable commands require ask-gated policy and separate offline/network review before egress.",
            "open-offline",
            "Offline",
            target_api="/api/operator/safety-plan",
            approval_command_id="open-offline",
            network_after_approval=True,
        ),
        _handoff_row(
            "approval-activity-ledger-handoff",
            "ok",
            "log",
            "Approval activity ledger handoff",
            "Approved, cancelled, retried, and policy-reviewed decisions should write result and recovery metadata to the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
            writes_activity=True,
        ),
    ]


def _api_action(method: str, path: str, title: str, *, writes: bool = False, executes: bool = False) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": executes,
        "requires_approval": writes or executes,
        "uses_network": False,
    }


def _entry_rows(*, configured: dict[str, bool]) -> list[dict[str, Any]]:
    state = "ok" if configured.get("commands") and configured.get("policy") else "warn"
    common = {
        "command_id": "open-trust-controls",
        "review_command_id": "open-activity-preflight",
        "palette_command_id": "open-command-palette",
        "workflow_command_id": "open-automation-map",
        "approval_api": "/api/operator/policy",
        "activity_api": "/api/operator/activity",
        "route_api": "/api/operator/route",
        "requires_approval": True,
        "executes": False,
        "routes_commands": False,
        "approves_commands": False,
        "cancels_commands": False,
        "retries_commands": False,
        "changes_policy": False,
        "writes_activity": False,
        "starts_workflows": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "approval-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard approval preflight",
            "detail": "The Command Center decision checkpoint opens approval posture before any allow, cancel, retry, or trust-policy change.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            **common,
            "id": "approval-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed approval request route",
            "detail": "Typed approval or autonomy requests route to Trust Controls and Activity review before any command decision is recorded.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            **common,
            "id": "approval-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette approval route",
            "detail": "The command palette exposes approval and trust controls as review routes, not direct approve or policy-write actions.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "approval-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice approval route",
            "detail": "Voice mode can open approval preflight without approving commands, retrying activity, or changing trust settings.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "approval-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow approval handoff",
            "detail": "Automation handoffs can surface pending approvals and gates, but workflow starts and decisions remain explicit.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _alert_rows(
    *,
    command_rows: list[dict[str, Any]],
    workflow_rows: list[dict[str, Any]],
    queue_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
    policy_rows: list[dict[str, Any]],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not configured.get("commands"):
        rows.append({
            "id": "approval-command-catalog-missing",
            "state": "error",
            "badge": "cmd",
            "title": "Approval command catalog missing",
            "detail": "Approval posture cannot prove ask-gated routes without data/operator_commands.json.",
            "action": "open-command-palette",
            "actionLabel": "Commands",
            "requires_approval": False,
        })
    if not configured.get("policy"):
        rows.append({
            "id": "approval-policy-missing",
            "state": "warn",
            "badge": "trust",
            "title": "Approval trust policy missing",
            "detail": "Approval posture is using default trust policy because no owner policy evidence is persisted.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": False,
        })
    weak_policy = [row for row in policy_rows if row["level"] in {"approval", "network", "danger"} and row["mode"] != "ask"]
    for row in weak_policy:
        rows.append({
            "id": f"approval-policy-{row['level']}-not-ask",
            "state": "error" if row["level"] == "danger" else "warn",
            "badge": row.get("badge") or "trust",
            "title": f"{row['title']} is not ask-gated",
            "detail": row["detail"],
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    auto_risky = [row for row in command_rows if row["trust"] in {"approval", "network", "danger"} and row["trust_mode"] != "ask"]
    if auto_risky:
        rows.append({
            "id": "approval-risky-command-auto",
            "state": "error" if any(row["trust"] == "danger" for row in auto_risky) else "warn",
            "badge": "ask",
            "title": "Risky commands can auto-route",
            "detail": f"{len(auto_risky)} approval/network/high-risk command(s) are not ask-gated under current policy.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    workflow_issues = [row for row in workflow_rows if row["state"] != "ok"]
    if workflow_issues:
        rows.append({
            "id": "approval-workflow-gate-issues",
            "state": "warn",
            "badge": "flow",
            "title": "Workflow approval gates need review",
            "detail": f"{len(workflow_issues)} workflow route(s) have missing routes or non-ask approval gates.",
            "action": "open-automation-map",
            "actionLabel": "Automation",
            "requires_approval": True,
        })
    if queue_rows:
        rows.append({
            "id": "approval-queue-pending",
            "state": "warn",
            "badge": "hold",
            "title": "Pending approval queue",
            "detail": f"{len(queue_rows)} command record(s) are waiting for operator review.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "requires_approval": False,
        })
    if failure_rows:
        rows.append({
            "id": "approval-failures-before-retry",
            "state": "error",
            "badge": "fail",
            "title": "Failures require review before approval",
            "detail": f"{len(failure_rows)} failed command record(s) should be inspected before retry or approval.",
            "action": "open-activity-preflight",
            "actionLabel": "Inspect",
            "requires_approval": False,
        })
    if not command_rows:
        rows.append({
            "id": "approval-no-ask-gated-routes",
            "state": "warn",
            "badge": "ask",
            "title": "No approval-gated command routes visible",
            "detail": "Permissioned autonomy needs visible ask-first routes for local work, network, and high-risk actions.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": False,
        })
    return rows[:14]


def run_operator_approval_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    activity: list[dict[str, Any]] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return a read-only approval queue and permission posture snapshot."""
    owner = owner or "local"
    clean_commands = _as_commands(commands)
    clean_workflows = _as_records(workflows)
    clean_activity = _as_records(activity)
    normalized_policy = _normalize_policy(policy)
    configured = configured if isinstance(configured, dict) else {}
    configured_summary = {
        "commands": bool(configured.get("commands", bool(clean_commands))),
        "workflows": bool(configured.get("workflows", bool(clean_workflows))),
        "policy": bool(configured.get("policy", policy is not None)),
    }
    by_id = _commands_by_id(clean_commands)
    command_rows = _approval_command_rows(clean_commands, normalized_policy)
    workflow_rows = _workflow_gate_rows(clean_workflows, by_id, normalized_policy)
    queue_rows = _approval_queue_rows(clean_activity, by_id)
    decision_rows = _decision_rows(clean_activity)
    failure_rows = _failure_rows(clean_activity)
    policy_rows = _policy_rows(normalized_policy, clean_commands)
    decision_checkpoint_rows = _decision_checkpoint_rows(queue_rows, failure_rows, policy_rows)
    handoff_rows = _handoff_rows(
        queue_rows=queue_rows,
        failure_rows=failure_rows,
        workflow_rows=workflow_rows,
        policy_rows=policy_rows,
    )
    entry_rows = _entry_rows(configured=configured_summary)
    alert_rows = _alert_rows(
        command_rows=command_rows,
        workflow_rows=workflow_rows,
        queue_rows=queue_rows,
        failure_rows=failure_rows,
        policy_rows=policy_rows,
        configured=configured_summary,
    )
    critical = [row for row in alert_rows if row.get("state") == "error"]
    ask_ready = len([row for row in command_rows if row["trust_mode"] == "ask"])
    workflow_ready = len([row for row in workflow_rows if row["state"] == "ok"])
    state = "error" if critical else ("warn" if alert_rows else "ok")
    return {
        "mode": "read-only-approval-queue-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "command_count": len(clean_commands),
            "approval_route_count": len(command_rows),
            "ask_gated_count": ask_ready,
            "auto_risky_count": len([row for row in command_rows if row["trust"] in {"approval", "network", "danger"} and row["trust_mode"] != "ask"]),
            "workflow_gate_count": len(workflow_rows),
            "workflow_gate_ready_count": workflow_ready,
            "pending_approval_count": len(queue_rows),
            "decision_count": len(decision_rows),
            "decision_checkpoint_count": len(decision_checkpoint_rows),
            "decision_checkpoint_ready_count": len([row for row in decision_checkpoint_rows if row["state"] == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "approved_count": len([row for row in decision_rows if row["decision"] == "approved"]),
            "cancelled_count": len([row for row in decision_rows if row["decision"] == "cancelled"]),
            "failed_activity_count": len(failure_rows),
            "approval_alert_count": len(alert_rows),
            "critical_approval_alert_count": len(critical),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "routes_commands": False,
            "executes_commands": False,
            "approves_commands": False,
            "changes_policy": False,
            "writes_activity": False,
            "starts_workflows": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "next_action": "Open Trust Controls or Activity to review pending approvals, failed records, and ask-gated routes.",
        },
        "policy": normalized_policy,
        "configured": configured_summary,
        "approval_command_rows": command_rows[:60],
        "workflow_gate_rows": workflow_rows[:40],
        "approval_queue_rows": queue_rows,
        "decision_rows": decision_rows,
        "decision_checkpoint_rows": decision_checkpoint_rows,
        "handoff_rows": handoff_rows,
        "failure_rows": failure_rows,
        "policy_rows": policy_rows,
        "entry_rows": entry_rows,
        "guard_rows": [
            {
                "id": "approval-plan-read-only",
                "state": "ok",
                "badge": "read",
                "title": "Read-only approval posture",
                "detail": "This endpoint inventories trust policy, ask-gated commands, workflow gates, and activity decisions only.",
            },
            {
                "id": "approval-plan-no-execution",
                "state": "ok",
                "badge": "gate",
                "title": "Approval decisions stay explicit",
                "detail": "Approving, cancelling, retrying, changing policy, and executing commands happen only through explicit UI/tool actions.",
            },
        ],
        "alert_rows": alert_rows,
        "api_actions": [
            _api_action("GET", "/api/operator/approval-plan", "Read approval queue plan"),
            _api_action("GET", "/api/operator/policy", "Read trust policy"),
            _api_action("POST", "/api/operator/policy", "Change trust policy", writes=True),
            _api_action("DELETE", "/api/operator/policy", "Reset trust policy", writes=True),
            _api_action("GET", "/api/operator/commands", "Read command catalog"),
            _api_action("GET", "/api/operator/workflows", "Read workflow catalog"),
            _api_action("GET", "/api/operator/activity?limit=200", "Read activity timeline"),
            _api_action("POST", "/api/operator/activity", "Record command activity", writes=True),
        ],
        "approval": {
            "required": False,
            "gate": "Approval posture audit only",
            "policy": (
                "This endpoint only reports trust policy, ask-gated commands, workflow approval gates, pending activity, "
                "and recorded decisions. It does not route commands, execute commands, approve commands, cancel commands, "
                "retry commands, change trust policy, write activity, start workflows, run shell commands, write files, or use network access."
            ),
        },
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
            "activity": "data/operator_activity.json",
        },
    }
