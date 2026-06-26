"""Read-only command-layer readiness proof for the Cleverly operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS, resolve_operator_route_matrix


ENTRY_POINTS = [
    {
        "id": "command-layer-dashboard",
        "entry": "dashboard",
        "badge": "dash",
        "title": "Command Center dashboard",
        "detail": "Dashboard buttons open command surfaces and plans without bypassing the local command catalog.",
        "action": "refresh-command-center",
    },
    {
        "id": "command-layer-text",
        "entry": "text",
        "badge": "text",
        "title": "Text command input",
        "detail": "Typed requests preflight through /api/operator/route before the browser runs a local command.",
        "action": "summarize-today",
    },
    {
        "id": "command-layer-palette",
        "entry": "palette",
        "badge": "pal",
        "title": "Global command palette",
        "detail": "Palette search uses the persisted backend catalog for route previews and command trust state.",
        "action": "open-command-palette",
    },
    {
        "id": "command-layer-voice",
        "entry": "voice",
        "badge": "voice",
        "title": "Voice command route",
        "detail": "Voice transcripts route through the same command catalog after explicit browser microphone approval.",
        "action": "open-voice-preflight",
    },
    {
        "id": "command-layer-workflow",
        "entry": "workflow",
        "badge": "flow",
        "title": "Agent workflow handoff",
        "detail": "Workflow phrases are resolved by the backend route matrix before any approval-gated loop can start.",
        "action": "open-loops",
    },
]


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


def _trust_mode(command: dict[str, Any], policy: dict[str, str]) -> str:
    if command.get("alwaysAsk") or command.get("always_ask"):
        return "ask"
    trust = str(command.get("trust") or "local").lower()
    return policy.get(trust if trust in TRUST_LEVELS else "local", "ask")


def _command_catalog_rows(commands: list[dict[str, Any]], policy: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_id = _trim(command.get("id"), 160)
        if not command_id:
            continue
        trust = str(command.get("trust") or "local").lower()
        if trust not in TRUST_LEVELS:
            trust = "local"
        mode = _trust_mode(command, policy)
        rows.append({
            "id": command_id,
            "state": "warn" if mode == "ask" else "ok",
            "badge": trust,
            "title": _trim(command.get("title") or command_id, 240),
            "detail": f"{_trim(command.get('category') or 'Operator', 120)}; {mode} mode; trust={trust}",
            "action": command_id,
            "actionLabel": "Open" if mode == "auto" else "Review",
            "trust": trust,
            "trust_mode": mode,
            "workflow": command.get("workflow") is True,
            "requires_approval": mode == "ask",
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "writes_activity": False,
            "uses_network": False,
        })
    rows.sort(key=lambda row: (row["state"] != "warn", row["title"].lower()))
    return rows[:24]


def _entry_rows(commands: list[dict[str, Any]], workflows: list[dict[str, Any]], configured: dict[str, Any]) -> list[dict[str, Any]]:
    command_ids = {
        _trim(command.get("id"), 160)
        for command in commands
        if isinstance(command, dict) and _trim(command.get("id"), 160)
    }
    route_ready = bool(commands) and bool(configured.get("commands"))
    workflow_ready = bool(workflows) and bool(configured.get("workflows"))
    rows: list[dict[str, Any]] = []
    for entry in ENTRY_POINTS:
        action = entry["action"]
        action_ready = action in command_ids or action == "refresh-command-center"
        if entry["entry"] == "workflow":
            ready = route_ready and workflow_ready
        else:
            ready = route_ready and action_ready
        rows.append({
            **entry,
            "state": "ok" if ready else "warn",
            "ready": ready,
            "command_id": action,
            "action_ready": action_ready,
            "command_layer_api": "/api/operator/command-layer-plan",
            "route_api": "/api/operator/route",
            "routes_api": "/api/operator/routes",
            "commands_api": "/api/operator/commands",
            "workflows_api": "/api/operator/workflows",
            "policy_api": "/api/operator/policy",
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "writes_activity": False,
            "uses_network": False,
        })
    return rows


def _route_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in matrix.get("rows") if isinstance(matrix.get("rows"), list) else []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "id": _trim(row.get("id") or row.get("expected_route_id"), 160),
            "state": row.get("state") or ("ok" if row.get("route_ready") else "warn"),
            "badge": _trim(row.get("area") or "route", 40),
            "title": _trim(row.get("title") or row.get("phrase") or "Workflow route", 240),
            "detail": (
                f"{_trim(row.get('phrase'), 300)}; "
                f"selected={_trim(row.get('selected_id'), 160) or 'none'}; "
                f"expected={_trim(row.get('expected_route_id'), 160) or 'none'}; "
                f"approval={_trim(row.get('approval_mode'), 80) or 'not required'}"
            ),
            "action": _trim(row.get("expected_route_id") or row.get("command_id"), 160) or "open-command-palette",
            "actionLabel": "Open" if row.get("route_ready") else "Review",
            "phrase": _trim(row.get("phrase"), 300),
            "command_id": _trim(row.get("command_id"), 160),
            "approval_id": _trim(row.get("approval_id"), 160),
            "expected_route_id": _trim(row.get("expected_route_id"), 160),
            "selected_id": _trim(row.get("selected_id"), 160),
            "route_ready": row.get("route_ready") is True,
            "command_ready": row.get("command_ready") is True,
            "approval_ready": row.get("approval_ready") is True,
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "writes_activity": False,
            "uses_network": False,
        })
    return rows[:24]


def _api_action(
    path: str,
    title: str,
    *,
    method: str = "GET",
    writes: bool = False,
    routes_commands: bool = False,
    executes_commands: bool = False,
) -> dict[str, Any]:
    return {
        "path": path,
        "method": method,
        "title": title,
        "state": "warn" if writes or routes_commands or executes_commands else "ok",
        "writes": writes,
        "routes_commands": routes_commands,
        "executes_commands": executes_commands,
        "starts_workflows": False,
        "runs_shell": False,
        "uses_network": False,
        "requires_approval": writes or routes_commands or executes_commands,
    }


def _guard_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "command-layer-read-only",
            "state": "ok",
            "badge": "audit",
            "title": "Read-only command-layer audit",
            "detail": "This endpoint resolves catalog and workflow readiness only; it never runs a command.",
        },
        {
            "id": "command-layer-approval-boundary",
            "state": "ok",
            "badge": "ask",
            "title": "Approval state is visible before execution",
            "detail": "Trust modes come from the same local policy used by dashboard, palette, voice, and workflow requests.",
        },
        {
            "id": "command-layer-local-first",
            "state": "ok",
            "badge": "local",
            "title": "Local-first command catalog",
            "detail": "Commands, workflows, and policy are read from local persisted stores scoped to the current owner.",
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
    executes_commands: bool = False,
    approves_commands: bool = False,
    starts_workflows: bool = False,
    writes_activity: bool = False,
    changes_policy: bool = False,
    publishes_catalog: bool = False,
    reads_transcript: bool = False,
    runs_shell: bool = False,
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
        "starts_workflows": False,
        "writes_activity": False,
        "changes_policy": False,
        "publishes_catalog": False,
        "reads_transcript": False,
        "runs_shell": False,
        "uses_network": False,
        "gated_operation": {
            "routes_commands": routes_commands,
            "executes_commands": executes_commands,
            "approves_commands": approves_commands,
            "starts_workflows": starts_workflows,
            "writes_activity": writes_activity,
            "changes_policy": changes_policy,
            "publishes_catalog": publishes_catalog,
            "reads_transcript": reads_transcript,
            "runs_shell": runs_shell,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    *,
    command_count: int,
    workflow_count: int,
    route_ready_count: int,
    route_match_count: int,
    ask_count: int,
    network_count: int,
    danger_count: int,
    configured: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog_ready = command_count > 0 and bool(configured.get("commands"))
    workflow_ready = workflow_count > 0 and route_ready_count >= route_match_count and route_match_count > 0
    policy_ready = bool(configured.get("policy"))
    network_risk_count = network_count + danger_count
    return [
        _handoff_row(
            "command-layer-text-route-handoff",
            "ok" if catalog_ready else "warn",
            "text",
            "Text route handoff",
            "Typed commands resolve through backend route preflight before the browser falls back to local matching.",
            "summarize-today",
            "Route",
            target_api="/api/operator/route",
            routes_commands=True,
        ),
        _handoff_row(
            "command-layer-palette-catalog-handoff",
            "ok" if catalog_ready else "warn",
            "pal",
            "Palette catalog handoff",
            "The command palette publishes and reads the sanitized local command catalog used for route previews.",
            "open-command-palette",
            "Palette",
            target_api="/api/operator/commands",
            publishes_catalog=True,
        ),
        _handoff_row(
            "command-layer-voice-route-handoff",
            "ok" if catalog_ready else "warn",
            "voice",
            "Voice route handoff",
            "Voice transcripts use the same local route matrix after browser microphone permission is granted.",
            "open-voice-preflight",
            "Voice",
            target_api="/api/operator/voice-plan",
            routes_commands=True,
            reads_transcript=True,
        ),
        _handoff_row(
            "command-layer-policy-handoff",
            "ok" if policy_ready else "warn",
            "ask",
            "Trust policy handoff",
            "Trust modes decide whether local, approval, network, and danger routes ask before execution.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/policy",
            changes_policy=True,
        ),
        _handoff_row(
            "command-layer-approval-queue-handoff",
            "warn" if ask_count else "ok",
            "gate",
            "Approval queue handoff",
            f"{ask_count} ask-first command route(s) must pass through approval evidence before execution.",
            "open-trust-controls",
            "Approvals",
            target_api="/api/operator/approval-plan",
            approval_command_id="request-approval-decision",
            approves_commands=True,
        ),
        _handoff_row(
            "command-layer-workflow-start-handoff",
            "ok" if workflow_ready else "warn",
            "flow",
            "Workflow start handoff",
            "Agent workflow phrases resolve through the backend matrix before any loop or workflow can start.",
            "open-loops",
            "Loops",
            target_api="/api/operator/workflows",
            requires_approval=True,
            approval_command_id="request-workflow-start",
            routes_commands=True,
            starts_workflows=True,
        ),
        _handoff_row(
            "command-layer-activity-ledger-handoff",
            "ok",
            "log",
            "Activity ledger handoff",
            "Approved command execution writes source, route proof, trust mode, status, retry, and recovery metadata locally.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            writes_activity=True,
        ),
        _handoff_row(
            "command-layer-network-policy-handoff",
            "warn" if network_risk_count else "ok",
            "net",
            "Network and danger route handoff",
            f"{network_risk_count} network or danger route(s) must stay behind offline policy and explicit approval.",
            "open-offline",
            "Policy",
            target_api="/api/operator/safety-plan",
            approval_command_id="request-network-break-glass",
            uses_network=True,
        ),
    ]


def _alert_rows(
    *,
    configured: dict[str, Any],
    entry_rows: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if not configured.get("commands"):
        alerts.append({
            "id": "command-layer-catalog-missing",
            "state": "error",
            "badge": "cat",
            "title": "Command catalog missing",
            "detail": "Publish the browser command catalog before backend route health can be proven.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_approval": False,
        })
    if not configured.get("workflows"):
        alerts.append({
            "id": "command-layer-workflows-missing",
            "state": "warn",
            "badge": "flow",
            "title": "Workflow route catalog missing",
            "detail": "Workflow phrase proof needs the local workflow catalog to verify target requests.",
            "action": "open-loops",
            "actionLabel": "Loops",
            "requires_approval": False,
        })
    for row in entry_rows:
        if row.get("ready") is not True:
            alerts.append({
                "id": f"command-layer-entry-{row['entry']}",
                "state": "warn",
                "badge": row.get("badge") or "entry",
                "title": f"Command entry point not proven: {row.get('title')}",
                "detail": row.get("detail") or "Command-layer entry point needs route proof.",
                "action": row.get("action") or "open-command-palette",
                "actionLabel": "Review",
                "requires_approval": False,
            })
    unresolved = [row for row in route_rows if row.get("route_ready") is not True]
    if unresolved:
        alerts.append({
            "id": "command-layer-routes-unresolved",
            "state": "error",
            "badge": "route",
            "title": "Workflow routes unresolved",
            "detail": f"{len(unresolved)} workflow phrase(s) do not resolve to their expected command route.",
            "action": "open-capability-map",
            "actionLabel": "Review",
            "requires_approval": False,
        })
    danger = [row for row in catalog_rows if row.get("trust") == "danger"]
    if danger:
        alerts.append({
            "id": "command-layer-danger-routes",
            "state": "warn",
            "badge": "risk",
            "title": "High-risk command routes present",
            "detail": f"{len(danger)} high-risk command route(s) are present and must stay ask-gated.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    return alerts[:16]


def run_operator_command_layer_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    loops: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    configured: dict[str, Any] | None = None,
    paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return read-only command catalog, workflow route, and trust gate proof."""
    command_rows_source = [row for row in (commands or []) if isinstance(row, dict)]
    workflow_rows_source = [row for row in (workflows or []) if isinstance(row, dict)]
    loop_rows_source = [row for row in (loops or []) if isinstance(row, dict)]
    normalized_policy = _normalize_policy(policy)
    source_configured = {
        "commands": bool((configured or {}).get("commands", bool(command_rows_source))),
        "workflows": bool((configured or {}).get("workflows", bool(workflow_rows_source))),
        "policy": bool((configured or {}).get("policy", bool(policy))),
    }
    matrix = resolve_operator_route_matrix(command_rows_source, workflow_rows_source, normalized_policy)
    route_rows = _route_rows(matrix)
    catalog_rows = _command_catalog_rows(command_rows_source, normalized_policy)
    entry_rows = _entry_rows(command_rows_source, workflow_rows_source, source_configured)
    guard_rows = _guard_rows()
    alert_rows = _alert_rows(
        configured=source_configured,
        entry_rows=entry_rows,
        route_rows=route_rows,
        catalog_rows=catalog_rows,
    )
    ask_count = sum(1 for command in command_rows_source if _trust_mode(command, normalized_policy) == "ask")
    network_count = sum(1 for command in command_rows_source if str(command.get("trust") or "local").lower() == "network")
    danger_count = sum(1 for command in command_rows_source if str(command.get("trust") or "local").lower() == "danger")
    route_summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    route_match_count = int(route_summary.get("total") or len(route_rows))
    route_ready_count = int(route_summary.get("ready") or 0)
    handoff_rows = _handoff_rows(
        command_count=len(command_rows_source),
        workflow_count=len(workflow_rows_source),
        route_ready_count=route_ready_count,
        route_match_count=route_match_count,
        ask_count=ask_count,
        network_count=network_count,
        danger_count=danger_count,
        configured=source_configured,
    )
    all_paths = {
        "commands": "data/operator_commands.json",
        "workflows": "data/operator_workflows.json",
        "policy": "data/operator_policy.json",
        **(paths or {}),
    }
    return {
        "mode": "read-only-command-layer-plan",
        "owner": owner,
        "generated_at": _utc_now(),
        "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
        "configured": source_configured,
        "summary": {
            "command_count": len(command_rows_source),
            "catalog_row_count": len(catalog_rows),
            "workflow_count": len(workflow_rows_source),
            "loop_count": len(loop_rows_source),
            "route_match_count": route_match_count,
            "route_match_ready_count": route_ready_count,
            "route_unresolved_count": int(route_summary.get("unresolved") or 0),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": sum(1 for row in entry_rows if row.get("ready") is True),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "ask_first_count": ask_count,
            "network_route_count": network_count,
            "danger_route_count": danger_count,
            "command_layer_alert_count": len(alert_rows),
            "critical_command_layer_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "writes_activity": False,
            "changes_policy": False,
            "runs_shell": False,
            "uses_network": False,
        },
        "catalog_rows": catalog_rows,
        "route_rows": route_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
        "guard_rows": guard_rows,
        "api_actions": [
            _api_action("/api/operator/command-layer-plan", "Read command-layer readiness proof"),
            _api_action("/api/operator/commands", "Read or publish sanitized command catalog", method="POST", writes=True),
            _api_action("/api/operator/workflows", "Read or publish workflow route catalog", method="POST", writes=True),
            _api_action("/api/operator/route", "Resolve one command route", method="POST", routes_commands=True),
            _api_action("/api/operator/routes", "Resolve workflow route matrix"),
            _api_action("/api/operator/policy", "Read or update command trust policy", method="POST", writes=True),
            _api_action("/api/operator/activity", "Write activity after explicit command execution", method="POST", writes=True),
        ],
        "paths": all_paths,
        "approval": {
            "required": False,
            "policy": (
                "This endpoint only audits command catalog, workflow route matrix, entry points, and trust gates. "
                "It does not route a live request, execute commands, approve actions, start workflows, write activity, "
                "change policy, run shell commands, or use network access."
            ),
            "disallowed_actions": [
                "route a live command request",
                "execute commands",
                "approve commands",
                "start workflows",
                "start jobs",
                "write activity",
                "change trust policy",
                "run shell commands",
                "use network access",
            ],
        },
    }
