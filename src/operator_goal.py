"""Read-only goal-readiness proof for the Cleverly operating console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS


GOAL_PRINCIPLES = [
    {
        "id": "local-first",
        "title": "Local-first",
        "badge": "local",
        "action_id": "open-local-data-map",
        "action_label": "Data",
        "required_policies": ["network"],
        "endpoints": ["/api/offline-control/status", "/api/operator/runtime-plan", "/api/operator/toolchain-plan"],
        "paths": ["data/settings.json", "data/operator_policy.json", "cleverly-data:/app/data"],
        "proof": "Data, models, memory, tasks, files, and logs stay local unless network-capable features are explicitly enabled.",
    },
    {
        "id": "operator-style-ux",
        "title": "Operator-style UX",
        "badge": "ux",
        "action_id": "open-console-readiness-audit",
        "action_label": "Audit",
        "endpoints": ["/api/operator/console-plan", "/api/operator/experience-plan"],
        "paths": ["static/js/commandCenter.js", "data/operator_commands.json"],
        "proof": "Major features are reachable through the dashboard, command palette, text command, voice path, or agent workflow.",
    },
    {
        "id": "permissioned-autonomy",
        "title": "Permissioned autonomy",
        "badge": "gate",
        "action_id": "open-autonomy-map",
        "action_label": "Autonomy",
        "required_policies": ["approval", "network", "danger"],
        "endpoints": ["/api/operator/autonomy-plan", "/api/operator/safety-plan", "/api/operator/policy"],
        "paths": ["data/operator_policy.json", "data/operator_activity.json"],
        "proof": "Cleverly can suggest, ask, execute, or auto-execute only through visible trust levels and approval gates.",
    },
    {
        "id": "unified-memory",
        "title": "Unified memory",
        "badge": "memory",
        "action_id": "open-memory-profile",
        "action_label": "Memory",
        "endpoints": ["/api/memory", "/api/operator/memory-plan", "/api/operator/profile"],
        "paths": ["data/memory.json", "data/memory_doc.md", "data/operator_profile.json"],
        "proof": "Preferences, projects, decisions, recurring tasks, model choices, and workflows remain usable local context.",
    },
    {
        "id": "practical-control",
        "title": "Practical control",
        "badge": "control",
        "action_id": "open-operator-runbook",
        "action_label": "Runbook",
        "endpoints": ["/api/operator/toolchain-plan", "/api/operator/models", "/api/training/status", "/api/code-workspaces"],
        "paths": ["data/models/", "data/training/", "data/code_workspace/", "docker-compose.yml"],
        "proof": "Models, fine-tuning, code workspaces, documents, notes, schedules, backups, and containers are mapped from one console.",
    },
    {
        "id": "clear-visibility",
        "title": "Clear visibility",
        "badge": "audit",
        "action_id": "open-activity-preflight",
        "action_label": "Activity",
        "endpoints": ["/api/operator/activity", "/api/operator/activity-plan"],
        "paths": ["data/operator_activity.json", "logs/"],
        "proof": "Automated work should appear in the local activity timeline with status, result, logs, retry, and recovery evidence.",
    },
    {
        "id": "safety-by-default",
        "title": "Safety by default",
        "badge": "safe",
        "action_id": "open-trust-controls",
        "action_label": "Trust",
        "required_policies": ["approval", "network", "danger"],
        "endpoints": ["/api/operator/safety-plan", "/api/operator/file-ops-plan", "/api/operator/code-test-plan"],
        "paths": ["data/auth.json", "data/sessions.json", "data/operator_policy.json"],
        "proof": "Destructive, network, credential, filesystem, and shell actions require obvious approval unless explicitly trusted.",
    },
]


DEFINITION_ROWS = [
    {
        "id": "situational-awareness",
        "title": "Situational awareness",
        "badge": "dash",
        "action_id": "open-console-readiness-audit",
        "action_label": "Audit",
        "endpoints": ["/api/operator/console-plan", "/api/operator/runtime-plan", "/api/operator/model-ops-plan"],
        "paths": ["data/operator_activity.json", "data/settings.json", "logs/"],
        "proof": "The main screen shows system status, models, security state, jobs, memory, tasks, calendar, code, training, and alerts.",
    },
    {
        "id": "command-layer",
        "title": "Command layer routes requests",
        "badge": "route",
        "action_id": "open-capability-map",
        "action_label": "Map",
        "endpoints": ["/api/operator/routes", "/api/operator/experience-plan", "/api/operator/commands"],
        "paths": ["data/operator_commands.json", "data/operator_workflows.json"],
        "proof": "Natural phrases route to the right tool, preflight, plan, or approval request before execution.",
    },
    {
        "id": "visible-permissioned-automation",
        "title": "Automation is visible and permissioned",
        "badge": "auto",
        "action_id": "open-autonomy-map",
        "action_label": "Autonomy",
        "required_policies": ["approval", "danger"],
        "endpoints": ["/api/operator/autonomy-plan", "/api/operator/activity-plan", "/api/operator/build-watch-plan"],
        "paths": ["data/operator_activity.json", "data/operator_workflows.json", "data/task_runs.json"],
        "proof": "Long-running work, retries, repair plans, training jobs, and build loops are logged locally and trust-gated.",
    },
    {
        "id": "docker-runtime-reliability",
        "title": "Local-first features work in Docker runtime",
        "badge": "run",
        "action_id": "open-local-services-map",
        "action_label": "Services",
        "endpoints": ["/health", "/api/operator/services", "/api/operator/runtime-plan", "/api/operator/toolchain-plan"],
        "paths": ["cleverly-data:/app/data", "cleverly-logs:/app/logs", "cleverly-ollama:/root/.ollama", "docker-compose.yml"],
        "proof": "Docker health, support services, data volumes, caches, model stores, and repair gates are inspectable without running repairs.",
    },
]


EVIDENCE_ROWS = [
    {
        "id": "goal-readiness-proof",
        "title": "Backend goal readiness plan",
        "badge": "goal",
        "action_id": "open-cleverly-goal-prompt",
        "action_label": "Goal",
        "endpoints": ["/api/operator/goal-plan"],
        "paths": ["src/operator_goal.py", "static/js/commandCenter.js"],
        "proof": "The goal prompt is backed by a read-only API that lists principles, done criteria, evidence rows, API gates, and data paths.",
    },
    {
        "id": "target-experience-proof",
        "title": "Target experience proof",
        "badge": "target",
        "action_id": "open-capability-map",
        "action_label": "Map",
        "endpoints": ["/api/operator/experience-plan", "/api/operator/routes"],
        "paths": ["data/operator_commands.json", "data/operator_workflows.json"],
        "proof": "Named target phrases such as summarize today, run tests, search documents, and watch builds are audited against routes.",
    },
    {
        "id": "console-proof",
        "title": "Command Center readiness proof",
        "badge": "dash",
        "action_id": "open-console-readiness-audit",
        "action_label": "Audit",
        "endpoints": ["/api/operator/console-plan"],
        "paths": ["static/js/commandCenter.js"],
        "proof": "The dashboard sections required by the operating-console objective are checked as backend rows.",
    },
    {
        "id": "toolchain-proof",
        "title": "Local toolchain integration proof",
        "badge": "tools",
        "action_id": "open-operator-runbook",
        "action_label": "Runbook",
        "endpoints": ["/api/operator/toolchain-plan"],
        "paths": ["docker-compose.yml", "data/settings.json"],
        "proof": "Offline Control, Ollama, ChromaDB/RAG, SearXNG, Training Lab, Code Workspace, Voice, Tasks, Calendar, Memory, and services are inventoried.",
    },
    {
        "id": "safety-proof",
        "title": "Safety-boundary proof",
        "badge": "safe",
        "action_id": "open-trust-controls",
        "action_label": "Trust",
        "required_policies": ["approval", "network", "danger"],
        "endpoints": ["/api/operator/safety-plan"],
        "paths": ["data/auth.json", "data/operator_policy.json"],
        "proof": "Destructive, network, credential, filesystem, and shell boundaries are visible before sensitive work can run.",
    },
    {
        "id": "memory-proof",
        "title": "Unified-memory proof",
        "badge": "mem",
        "action_id": "open-memory-profile",
        "action_label": "Memory",
        "endpoints": ["/api/operator/memory-plan", "/api/memory", "/api/operator/profile"],
        "paths": ["data/memory.json", "data/operator_profile.json"],
        "proof": "Preferences, projects, decisions, recurring tasks, and model choices are mapped to local memory/profile evidence.",
    },
    {
        "id": "activity-proof",
        "title": "Activity timeline proof",
        "badge": "log",
        "action_id": "open-activity-preflight",
        "action_label": "Activity",
        "endpoints": ["/api/operator/activity-plan", "/api/operator/activity"],
        "paths": ["data/operator_activity.json"],
        "proof": "Executed and retryable operator work has a durable local ledger path with status, result, trust, and recovery metadata.",
    },
    {
        "id": "runtime-proof",
        "title": "Runtime and service proof",
        "badge": "run",
        "action_id": "open-local-services-map",
        "action_label": "Services",
        "endpoints": ["/health", "/api/operator/runtime-plan", "/api/operator/services"],
        "paths": ["cleverly-data:/app/data", "cleverly-logs:/app/logs", "docker-compose.yml"],
        "proof": "Runtime health, sealed volumes, local data roots, support-service status, and repair gates are exposed without starting jobs.",
    },
]


DATA_PATHS = [
    "data/app.db",
    "data/auth.json",
    "data/sessions.json",
    "data/settings.json",
    "data/operator_commands.json",
    "data/operator_workflows.json",
    "data/operator_policy.json",
    "data/operator_activity.json",
    "data/operator_profile.json",
    "data/memory.json",
    "data/memory_doc.md",
    "data/tasks.json",
    "data/task_runs.json",
    "data/calendar.json",
    "data/notes.json",
    "data/personal_docs/",
    "data/uploads/",
    "data/research/",
    "data/gallery/",
    "data/code_workspace/",
    "data/training/",
    "data/training_jobs.json",
    "data/models/",
    "data/backups/",
    "data/tts_cache/",
    "logs/",
    "cleverly-data:/app/data",
    "cleverly-logs:/app/logs",
    "cleverly-cache:/root/.cache",
    "cleverly-ollama:/root/.ollama",
    "cleverly-chromadb-data:/data",
    "cleverly-searxng-data:/etc/searxng",
    "cleverly-searxng-cache:/var/cache/searxng",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _command_map(commands: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {
        _trim(command.get("id"), 160): command
        for command in commands or []
        if isinstance(command, dict) and _trim(command.get("id"), 160)
    }


def _policy(policy: dict[str, Any] | None) -> dict[str, str]:
    normalized = dict(DEFAULT_TRUST_POLICY)
    if isinstance(policy, dict):
        for level in TRUST_LEVELS:
            mode = str(policy.get(level) or normalized[level]).lower()
            normalized[level] = mode if mode in {"auto", "ask"} else normalized[level]
    return normalized


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


def _goal_row(item: dict[str, Any], commands: dict[str, dict[str, Any]], policy: dict[str, str]) -> dict[str, Any]:
    action_id = item["action_id"]
    action_ready = action_id in commands
    required_policies = [level for level in item.get("required_policies", []) if level in TRUST_LEVELS]
    policy_ready = all(policy.get(level) == "ask" for level in required_policies)
    state = "ok" if action_ready and policy_ready else "warn"
    detail_parts = [
        item["proof"],
        f"action={action_id if action_ready else f'{action_id}:missing'}",
    ]
    if required_policies:
        detail_parts.append(
            "policy="
            + ", ".join(f"{level}:{policy.get(level, 'missing')}" for level in required_policies)
        )
    return {
        "id": item["id"],
        "state": state,
        "badge": item["badge"],
        "title": item["title"],
        "detail": "; ".join(detail_parts),
        "proof": item["proof"],
        "action": action_id,
        "actionLabel": item.get("action_label") or "Open",
        "action_id": action_id,
        "action_ready": action_ready,
        "required_policies": required_policies,
        "policy_ready": policy_ready,
        "endpoints": list(item["endpoints"]),
        "paths": list(item["paths"]),
        "executes": False,
        "routes_commands": False,
        "starts_workflows": False,
        "starts_jobs": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
        "approves_actions": False,
    }


def run_operator_goal_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return read-only proof that the implementation is moving toward the full operating-console goal."""
    owner = owner or "local"
    configured = configured if isinstance(configured, dict) else {}
    commands_by_id = _command_map(commands)
    normalized_policy = _policy(policy)
    principle_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in GOAL_PRINCIPLES]
    definition_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in DEFINITION_ROWS]
    evidence_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in EVIDENCE_ROWS]
    requirement_rows = [*principle_rows, *definition_rows, *evidence_rows]
    ready_rows = [row for row in requirement_rows if row["state"] == "ok"]
    endpoint_paths = sorted(
        {
            path
            for item in [*GOAL_PRINCIPLES, *DEFINITION_ROWS, *EVIDENCE_ROWS]
            for path in item["endpoints"]
        }
    )
    guard_rows = [
        {
            "state": "ok",
            "badge": "read",
            "title": "Read-only goal proof",
            "detail": "This endpoint lists operating-console principles, done criteria, evidence, API gates, and data paths only.",
        },
        {
            "state": "ok" if normalized_policy.get("network") == "ask" else "warn",
            "badge": "local",
            "title": "Local-first network posture",
            "detail": f"network tier is {normalized_policy.get('network', 'missing')}; this plan never opens egress.",
        },
        {
            "state": "ok" if normalized_policy.get("approval") == "ask" and normalized_policy.get("danger") == "ask" else "warn",
            "badge": "gate",
            "title": "Approval and danger gates",
            "detail": f"approval={normalized_policy.get('approval', 'missing')}; danger={normalized_policy.get('danger', 'missing')}.",
        },
        {
            "state": "ok",
            "badge": "log",
            "title": "Execution evidence belongs in Activity",
            "detail": "Approved executions should create local activity records with status, result, trust, logs, retry, and recovery metadata.",
        },
        {
            "state": "ok",
            "badge": "plan",
            "title": "Plans stay separate from actions",
            "detail": "Goal, console, safety, toolchain, runtime, training, model, file, code, and activity plans do not execute work.",
        },
    ]
    api_actions = [
        _api_action("/api/operator/goal-plan", "Read operating-console goal plan"),
        *[_api_action(path, f"Read {path} goal evidence feed") for path in endpoint_paths],
        _api_action("/api/operator/activity", "Write activity after explicit approved execution", writes=True),
    ]
    return {
        "mode": "read-only-goal-readiness-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "ok" if len(ready_rows) == len(requirement_rows) else "warn",
            "principle_count": len(principle_rows),
            "definition_count": len(definition_rows),
            "evidence_count": len(evidence_rows),
            "requirement_count": len(requirement_rows),
            "ready_count": len(ready_rows),
            "issue_count": len(requirement_rows) - len(ready_rows),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(DATA_PATHS),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "policy_count": len(normalized_policy),
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "approves_actions": False,
        },
        "principle_rows": principle_rows,
        "definition_rows": definition_rows,
        "evidence_rows": evidence_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "required_endpoints": endpoint_paths,
        "configured": {
            "commands": bool(configured.get("commands", bool(commands_by_id))),
            "workflows": bool(configured.get("workflows", bool(workflows))),
            "policy": bool(configured.get("policy", policy is not None)),
        },
        "approval": {
            "required": False,
            "gate": "Read-only operating-console goal proof",
            "policy": (
                "This endpoint only proves operating-console principles, definition-of-done rows, evidence feeds, "
                "API gates, guard rails, and data paths. It does not route commands, execute commands, approve actions, "
                "start workflows, start jobs, run shell commands, write files, restart services, train models, query web search, "
                "read credentials, export data, delete records, or use network access."
            ),
        },
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
            "activity": "data/operator_activity.json",
            "profile": "data/operator_profile.json",
            "data_paths": list(DATA_PATHS),
        },
    }
