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
        "api_actions": api_actions,
        "required_risks": [boundary["id"] for boundary in SAFETY_BOUNDARIES],
        "configured": {
            "commands": bool(configured.get("commands", bool(commands_by_id))),
            "workflows": bool(configured.get("workflows", bool(workflows))),
            "policy": bool(configured.get("policy", policy is not None)),
        },
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
