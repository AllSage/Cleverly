"""Read-only safety-boundary proof for high-risk operator actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS


SAFETY_BOUNDARIES = [
    {
        "id": "destructive",
        "title": "Destructive and recovery actions",
        "badge": "risk",
        "required_ask_ids": ["request-container-fix", "request-backup-export"],
        "support_action_ids": ["prepare-backup", "open-local-services-map", "open-trust-controls"],
        "endpoints": ["/api/operator/repair-plan", "/api/operator/backup-plan", "/api/operator/activity-plan"],
        "paths": ["data/backups/", "data/operator_activity.json", "logs/"],
        "proof": "Restarts, deletes, restores, exports, cleanup, and repair passes must stay behind explicit approval routes.",
        "network_capable": False,
    },
    {
        "id": "network",
        "title": "Network and external egress",
        "badge": "net",
        "required_policy": "network",
        "required_ask_ids": [],
        "support_action_ids": ["open-offline", "open-research-preflight", "open-model-routing-map"],
        "endpoints": ["/api/offline-control/status", "/api/search/config", "/api/search/providers", "/api/operator/toolchain-plan"],
        "paths": ["data/settings.json", "data/operator_policy.json", "cleverly-searxng-data:/etc/searxng"],
        "proof": "Research, web search, model downloads, webhook delivery, calendar sync, and external endpoints require explicit network policy.",
        "network_capable": True,
    },
    {
        "id": "credential",
        "title": "Credentials and secrets",
        "badge": "cred",
        "required_policy": "danger",
        "required_ask_ids": [],
        "support_action_ids": ["open-offline", "open-trust-controls"],
        "endpoints": ["/api/auth/status", "/api/auth/settings", "/api/operator/file-ops-plan"],
        "paths": ["data/auth.json", "data/sessions.json", "data/settings.json", ".ssh/"],
        "proof": "Auth files, sessions, API keys, tokens, SSH material, vault config, and settings remain admin-only and ask-gated.",
        "network_capable": False,
    },
    {
        "id": "filesystem",
        "title": "Filesystem writes and sensitive paths",
        "badge": "file",
        "required_ask_ids": ["request-backup-export"],
        "support_action_ids": ["open-code-workspace-map", "open-library-preflight", "prepare-backup"],
        "endpoints": ["/api/operator/file-ops-plan", "/api/operator/code-test-plan", "/api/operator/document-search-plan", "/api/operator/backup-plan"],
        "paths": ["data/code-workspaces/", "data/personal_docs/", "data/uploads/", "data/backups/"],
        "proof": "Writes, moves, deletes, imports, exports, restores, snapshots, commits, and sensitive roots require review before action.",
        "network_capable": False,
    },
    {
        "id": "shell",
        "title": "Shell, Docker, tests, and loops",
        "badge": "shell",
        "required_ask_ids": ["run-tests", "request-build-watch-loop", "request-container-fix"],
        "support_action_ids": ["open-code-workspace-map", "open-local-services-map", "open-automation-map"],
        "endpoints": ["/api/operator/code-test-plan", "/api/operator/build-watch-plan", "/api/operator/repair-plan", "/api/operator/runtime-plan"],
        "paths": ["data/code-workspaces/", "cleverly-code-worker", "docker-compose.yml", "logs/"],
        "proof": "Shell commands, Docker repair, tests, builds, dependency installs, and repeated loops must start from read-only plans.",
        "network_capable": False,
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _policy(policy: dict[str, Any] | None) -> dict[str, str]:
    normalized = dict(DEFAULT_TRUST_POLICY)
    if isinstance(policy, dict):
        for level in TRUST_LEVELS:
            mode = str(policy.get(level) or normalized[level]).lower()
            normalized[level] = mode if mode in {"auto", "ask"} else normalized[level]
    return normalized


def _command_map(commands: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {
        _trim(command.get("id"), 160): command
        for command in commands or []
        if isinstance(command, dict) and _trim(command.get("id"), 160)
    }


def _trust_level(command: dict[str, Any] | None) -> str:
    trust = str((command or {}).get("trust") or "local").lower()
    return trust if trust in TRUST_LEVELS else "local"


def _trust_mode(command: dict[str, Any] | None, policy: dict[str, str]) -> str:
    if not command:
        return "missing"
    if command.get("alwaysAsk") or command.get("always_ask"):
        return "ask"
    return policy.get(_trust_level(command), "ask")


def _action_state(action_ids: list[str], commands: dict[str, dict[str, Any]], policy: dict[str, str]) -> tuple[bool, int, int, list[str]]:
    if not action_ids:
        return True, 0, 0, []
    missing: list[str] = []
    ask_ready = 0
    for action_id in action_ids:
        command = commands.get(action_id)
        if not command:
            missing.append(action_id)
            continue
        if _trust_mode(command, policy) == "ask":
            ask_ready += 1
    return not missing and ask_ready == len(action_ids), ask_ready, len(action_ids), missing


def _risk_row(boundary: dict[str, Any], commands: dict[str, dict[str, Any]], policy: dict[str, str]) -> dict[str, Any]:
    action_ready, ask_ready, ask_total, missing = _action_state(boundary["required_ask_ids"], commands, policy)
    required_policy = boundary.get("required_policy")
    policy_ready = not required_policy or policy.get(required_policy) == "ask"
    state = "ok" if action_ready and policy_ready else "warn"
    detail_parts = [
        boundary["proof"],
        f"{ask_ready}/{ask_total} required action gates ask-first" if ask_total else "",
        f"{required_policy} policy={policy.get(required_policy, 'missing')}" if required_policy else "",
        f"missing={', '.join(missing)}" if missing else "",
    ]
    required_actions = [
        {
            "id": action_id,
            "ready": action_id in commands and _trust_mode(commands.get(action_id), policy) == "ask",
            "trust": _trust_level(commands.get(action_id)),
            "mode": _trust_mode(commands.get(action_id), policy),
        }
        for action_id in boundary["required_ask_ids"]
    ]
    support_actions = [
        {
            "id": action_id,
            "ready": action_id in commands,
            "trust": _trust_level(commands.get(action_id)),
            "mode": _trust_mode(commands.get(action_id), policy),
        }
        for action_id in boundary["support_action_ids"]
    ]
    return {
        "id": boundary["id"],
        "state": state,
        "badge": boundary["badge"],
        "title": boundary["title"],
        "detail": "; ".join(part for part in detail_parts if part),
        "proof": boundary["proof"],
        "required_policy": required_policy or "",
        "policy_ready": policy_ready,
        "ask_ready_count": ask_ready,
        "ask_total": ask_total,
        "missing_action_ids": missing,
        "required_actions": required_actions,
        "support_actions": support_actions,
        "endpoints": list(boundary["endpoints"]),
        "paths": list(boundary["paths"]),
        "network_capable": bool(boundary["network_capable"]),
        "executes": False,
        "routes_commands": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
        "requires_approval": bool(ask_total or required_policy),
        "action": "open-trust-controls",
        "actionLabel": "Trust",
    }


def _api_action(path: str, title: str, *, uses_network: bool = False, writes: bool = False) -> dict[str, Any]:
    return {
        "method": "GET" if not writes else "POST",
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": writes or uses_network,
        "uses_network": uses_network,
    }


def _entry_rows(*, ready_count: int, risk_count: int, configured: dict[str, bool]) -> list[dict[str, Any]]:
    ready = ready_count == risk_count and configured.get("commands") and configured.get("policy")
    workflow_ready = ready and configured.get("workflows")
    common = {
        "command_id": "open-trust-controls",
        "safety_command_id": "open-autonomy-map",
        "offline_command_id": "open-offline",
        "activity_command_id": "open-activity-preflight",
        "data_command_id": "open-local-data-map",
        "code_command_id": "open-code-preflight",
        "services_command_id": "open-local-services-map",
        "backup_command_id": "open-backup-preflight",
        "automation_command_id": "open-automation-map",
        "safety_api": "/api/operator/safety-plan",
        "policy_api": "/api/operator/policy",
        "activity_api": "/api/operator/activity",
        "file_ops_api": "/api/operator/file-ops-plan",
        "runtime_api": "/api/operator/runtime-plan",
        "repair_api": "/api/operator/repair-plan",
        "backup_api": "/api/operator/backup-plan",
        "requires_approval": True,
        "executes": False,
        "routes_commands": False,
        "executes_commands": False,
        "approves_actions": False,
        "starts_workflows": False,
        "starts_jobs": False,
        "runs_shell": False,
        "runs_docker": False,
        "writes_files": False,
        "reads_credentials": False,
        "exports_data": False,
        "deletes_records": False,
        "uses_network": False,
    }
    state = "ok" if ready else "warn"
    workflow_state = "ok" if workflow_ready else "warn"
    return [
        {
            **common,
            "id": "safety-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard safety route",
            "detail": f"The dashboard opens safety-boundary proof for {ready_count}/{risk_count} risk class(es) before any high-risk action.",
            "action": "open-autonomy-map",
            "actionLabel": "Safety",
        },
        {
            **common,
            "id": "safety-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed safety request route",
            "detail": "Typed destructive, network, credential, filesystem, shell, and repair requests route to safety proof and trust review first.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            **common,
            "id": "safety-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette safety route",
            "detail": "The command palette can inspect safety boundaries, route proof, and approval gates without executing or approving actions.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "safety-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice safety route",
            "detail": "Voice requests can open safety and trust preflight without approving commands, reading credentials, running shell, or using network access.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "safety-workflow-route",
            "entry": "workflow",
            "state": workflow_state,
            "badge": "flow",
            "title": "Workflow safety route",
            "detail": "Workflow handoffs review automation, backup, activity, and trust evidence before loops, jobs, repairs, or high-risk commands can start.",
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
    approval_command_id: str = "open-trust-controls",
    approval_api: str = "/api/operator/policy",
    requires_approval: bool = True,
    routes_commands: bool = False,
    executes_commands: bool = False,
    approves_actions: bool = False,
    starts_workflows: bool = False,
    starts_jobs: bool = False,
    runs_shell: bool = False,
    runs_docker: bool = False,
    writes_files: bool = False,
    reads_credentials: bool = False,
    exports_data: bool = False,
    deletes_records: bool = False,
    restarts_services: bool = False,
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
        "approval_api": approval_api,
        "requires_approval": requires_approval,
        "executes": False,
        "routes_commands": False,
        "executes_commands": False,
        "approves_actions": False,
        "starts_workflows": False,
        "starts_jobs": False,
        "runs_shell": False,
        "runs_docker": False,
        "writes_files": False,
        "reads_credentials": False,
        "exports_data": False,
        "deletes_records": False,
        "restarts_services": False,
        "uses_network": uses_network,
        "network_after_approval": network_after_approval,
        "gated_operation": {
            "routes_commands": routes_commands,
            "executes_commands": executes_commands,
            "approves_actions": approves_actions,
            "starts_workflows": starts_workflows,
            "starts_jobs": starts_jobs,
            "runs_shell": runs_shell,
            "runs_docker": runs_docker,
            "writes_files": writes_files,
            "reads_credentials": reads_credentials,
            "exports_data": exports_data,
            "deletes_records": deletes_records,
            "restarts_services": restarts_services,
            "uses_network": uses_network or network_after_approval,
        },
    }


def _handoff_rows(risk_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risks = {str(row.get("id")): row for row in risk_rows}
    destructive_state = str(risks.get("destructive", {}).get("state") or "warn")
    network_state = str(risks.get("network", {}).get("state") or "warn")
    credential_state = str(risks.get("credential", {}).get("state") or "warn")
    filesystem_state = str(risks.get("filesystem", {}).get("state") or "warn")
    shell_state = str(risks.get("shell", {}).get("state") or "warn")
    backup_state = "ok" if destructive_state == "ok" and filesystem_state == "ok" else "warn"
    return [
        _handoff_row(
            "safety-destructive-recovery-handoff",
            destructive_state,
            "risk",
            "Destructive/recovery approval handoff",
            "Restart, restore, delete, cleanup, export, and repair actions must leave this read-only proof through an explicit approval route.",
            "request-container-fix",
            "Repair",
            target_api="/api/operator/repair-plan",
            approval_command_id="request-container-fix",
            restarts_services=True,
            deletes_records=True,
            exports_data=True,
        ),
        _handoff_row(
            "safety-network-egress-handoff",
            network_state,
            "net",
            "Network egress handoff",
            "Research, web search, model downloads, webhooks, calendar sync, and external endpoints require offline/network policy review.",
            "open-offline",
            "Offline",
            target_api="/api/offline-control/status",
            approval_command_id="open-offline",
            network_after_approval=True,
        ),
        _handoff_row(
            "safety-credential-secret-handoff",
            credential_state,
            "cred",
            "Credential and secret handoff",
            "Auth files, sessions, API keys, tokens, SSH material, vault config, and settings stay behind credential posture review.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/credentials-plan",
            reads_credentials=True,
            writes_files=True,
        ),
        _handoff_row(
            "safety-filesystem-boundary-handoff",
            filesystem_state,
            "file",
            "Filesystem boundary handoff",
            "Writes, moves, deletes, imports, exports, restores, snapshots, commits, and sensitive roots require File Ops and backup review.",
            "open-local-data-map",
            "Files",
            target_api="/api/operator/file-ops-plan",
            approval_command_id="open-local-data-map",
            writes_files=True,
            deletes_records=True,
            exports_data=True,
        ),
        _handoff_row(
            "safety-shell-docker-handoff",
            shell_state,
            "shell",
            "Shell, Docker, tests, and loop handoff",
            "Shell commands, Docker repair, tests, builds, dependency installs, and repeated loops must start from read-only plans.",
            "open-code-workspace-map",
            "Code",
            target_api="/api/operator/code-test-plan",
            approval_command_id="run-tests",
            executes_commands=True,
            starts_workflows=True,
            starts_jobs=True,
            runs_shell=True,
            runs_docker=True,
        ),
        _handoff_row(
            "safety-backup-recovery-handoff",
            backup_state,
            "back",
            "Backup and rollback handoff",
            "Risky local actions should prove backup, restore, rollback, retry, and recovery routes before execution approval.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_command_id="prepare-backup",
            writes_files=True,
            exports_data=True,
        ),
        _handoff_row(
            "safety-activity-ledger-handoff",
            "ok",
            "log",
            "Activity ledger handoff",
            "Approved high-risk actions should write trust, status, result, log, retry, and recovery references to the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            approval_command_id="open-activity-preflight",
            requires_approval=False,
        ),
    ]


def _safety_alert_rows(
    risk_rows: list[dict[str, Any]],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in risk_rows:
        if row.get("state") == "ok":
            continue
        missing = row.get("missing_action_ids") or []
        required_policy = row.get("required_policy") or ""
        detail_parts = [
            row.get("proof") or row.get("detail") or "Safety boundary needs review.",
            f"missing required action(s): {', '.join(str(item) for item in missing)}" if missing else "",
            f"{required_policy} policy is not ask-gated" if required_policy and not row.get("policy_ready") else "",
        ]
        risk_id = str(row.get("id") or "safety")
        rows.append(
            {
                "id": f"safety-boundary-{risk_id}",
                "state": "error" if risk_id in {"destructive", "credential", "shell"} else "warn",
                "badge": row.get("badge") or "risk",
                "title": f"Safety boundary not ask-gated: {row.get('title') or risk_id}",
                "detail": "; ".join(part for part in detail_parts if part),
                "action": row.get("action") or "open-trust-controls",
                "actionLabel": row.get("actionLabel") or "Trust",
                "requires_approval": True,
                "uses_network": bool(row.get("network_capable")),
            }
        )
    if not configured.get("commands"):
        rows.append(
            {
                "id": "safety-command-catalog-missing",
                "state": "error",
                "badge": "cmd",
                "title": "Command safety catalog missing",
                "detail": "Safety proof needs the operator command catalog to verify required ask-gated routes.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    if not configured.get("policy"):
        rows.append(
            {
                "id": "safety-policy-missing",
                "state": "warn",
                "badge": "trust",
                "title": "Trust policy evidence missing",
                "detail": "Safety proof is using default trust policy because no persisted operator policy evidence was provided.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": False,
            }
        )
    if not configured.get("workflows"):
        rows.append(
            {
                "id": "safety-workflow-catalog-missing",
                "state": "warn",
                "badge": "flow",
                "title": "Workflow safety catalog missing",
                "detail": "Workflow exposure evidence is absent; review Agent Loops before approving autonomous workflow starts.",
                "action": "open-automation-map",
                "actionLabel": "Automation",
                "requires_approval": False,
            }
        )
    return rows[:10]


def run_operator_safety_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return read-only proof for the objective's high-risk safety classes."""
    owner = owner or "local"
    configured = configured if isinstance(configured, dict) else {}
    normalized_policy = _policy(policy)
    commands_by_id = _command_map(commands)
    risk_rows = [_risk_row(boundary, commands_by_id, normalized_policy) for boundary in SAFETY_BOUNDARIES]
    ready_rows = [row for row in risk_rows if row["state"] == "ok"]
    ask_total = sum(int(row["ask_total"]) for row in risk_rows)
    ask_ready = sum(int(row["ask_ready_count"]) for row in risk_rows)
    network_capable = [row for row in risk_rows if row["network_capable"]]
    endpoint_paths = sorted({path for boundary in SAFETY_BOUNDARIES for path in boundary["endpoints"]})
    data_paths = sorted({path for boundary in SAFETY_BOUNDARIES for path in boundary["paths"]})
    configured_summary = {
        "commands": bool(configured.get("commands", bool(commands_by_id))),
        "workflows": bool(configured.get("workflows", bool(workflows))),
        "policy": bool(configured.get("policy", policy is not None)),
    }
    guard_rows = [
        {
            "state": "ok",
            "badge": "read",
            "title": "Read-only safety proof",
            "detail": "This endpoint audits safety classes, trust policy, command gates, API feeds, and data paths only.",
        },
        {
            "state": "ok" if normalized_policy.get("network") == "ask" else "warn",
            "badge": "net",
            "title": "Network break-glass gate",
            "detail": f"network tier is {normalized_policy.get('network', 'missing')}; network-capable work remains separate from this plan.",
        },
        {
            "state": "ok" if normalized_policy.get("danger") == "ask" else "warn",
            "badge": "risk",
            "title": "High-risk action gate",
            "detail": f"danger tier is {normalized_policy.get('danger', 'missing')}; destructive/credential work should ask first.",
        },
        {
            "state": "ok",
            "badge": "log",
            "title": "Activity ledger boundary",
            "detail": "Approved executions should create activity records with trust tags, status, result, logs, retry, and recovery notes.",
        },
    ]
    api_actions = [
        _api_action("/api/operator/safety-plan", "Read safety-boundary plan"),
        *[_api_action(path, f"Read {path} safety feed") for path in endpoint_paths],
        _api_action("/api/operator/activity", "Write activity after explicit approved execution", writes=True),
    ]
    alert_rows = _safety_alert_rows(risk_rows, configured_summary)
    entry_rows = _entry_rows(
        ready_count=len(ready_rows),
        risk_count=len(risk_rows),
        configured=configured_summary,
    )
    handoff_rows = _handoff_rows(risk_rows)
    return {
        "mode": "read-only-safety-boundary-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "ok" if len(ready_rows) == len(risk_rows) else "warn",
            "risk_count": len(risk_rows),
            "ready_count": len(ready_rows),
            "issue_count": len(risk_rows) - len(ready_rows),
            "ask_ready_count": ask_ready,
            "ask_total": ask_total,
            "network_capable_count": len(network_capable),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(data_paths),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "safety_alert_count": len(alert_rows),
            "critical_safety_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "approves_actions": False,
            "requires_approval_for_sensitive_targets": True,
        },
        "risk_rows": risk_rows,
        "guard_rows": guard_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "required_risks": [boundary["id"] for boundary in SAFETY_BOUNDARIES],
        "configured": configured_summary,
        "approval": {
            "required": False,
            "gate": "Read-only safety-boundary proof",
            "policy": (
                "This endpoint only proves safety classes, command approval posture, trust policy, API gates, and data paths. "
                "It does not route commands, execute commands, approve actions, start workflows, start jobs, run shell commands, "
                "write files, restart services, train models, query web search, read credentials, export data, delete records, or use network access."
            ),
        },
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
            "activity": "data/operator_activity.json",
            "data_paths": data_paths,
        },
    }
