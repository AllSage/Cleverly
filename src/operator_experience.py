"""Read-only target-experience proof for the Cleverly operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS, resolve_operator_route


TARGET_EXPERIENCES = [
    {
        "id": "summarize-today",
        "phrase": "Cleverly, summarize today.",
        "title": "Today briefing",
        "area": "Briefing",
        "command_id": "summarize-today",
        "expected_route_id": "summarize-today",
        "endpoint": "/api/operator/briefing",
        "proof": "Read-only local snapshot of tasks, calendar, memory, models, services, and activity.",
    },
    {
        "id": "container-health",
        "phrase": "Check the containers and fix anything unhealthy.",
        "title": "Container health and repair request",
        "area": "Services",
        "command_id": "open-container-repair-plan",
        "approval_id": "request-container-fix",
        "expected_route_id": "request-container-fix",
        "endpoint": "/api/operator/repair-plan",
        "proof": "Status is read-only; repair requests remain approval-gated.",
    },
    {
        "id": "code-tests",
        "phrase": "Open my code workspace and run the tests.",
        "title": "Code workspace test plan",
        "area": "Code",
        "command_id": "run-tests",
        "expected_route_id": "run-tests",
        "endpoint": "/api/operator/code-test-plan",
        "proof": "Plan-first route before workspace commands or file changes.",
    },
    {
        "id": "train-small-model",
        "phrase": "Train a small model on this dataset.",
        "title": "Training run plan",
        "area": "Training",
        "command_id": "open-training-run-plan",
        "expected_route_id": "open-training-run-plan",
        "endpoint": "/api/operator/training-plan",
        "proof": "Dataset, artifact, dependency, and job evidence before training starts.",
    },
    {
        "id": "watch-build",
        "phrase": "Watch this repo until the build passes.",
        "title": "Build-watch loop",
        "area": "Automation",
        "command_id": "watch-build-until-green",
        "approval_id": "request-build-watch-loop",
        "expected_route_id": "request-build-watch-loop",
        "endpoint": "/api/operator/build-watch-plan",
        "proof": "Loop start is approval-gated and bounded by local workflow controls.",
    },
    {
        "id": "note-task",
        "phrase": "Create a task from this note.",
        "title": "Note-to-task draft",
        "area": "Tasks",
        "command_id": "draft-task-from-note",
        "expected_route_id": "draft-task-from-note",
        "endpoint": "/api/operator/note-task-draft",
        "proof": "Draft-only route; saving or scheduling remains a separate reviewed action.",
    },
    {
        "id": "local-doc-search",
        "phrase": "Search my local documents for this.",
        "title": "Local document search",
        "area": "Documents",
        "command_id": "search-local-documents",
        "expected_route_id": "search-local-documents",
        "endpoint": "/api/operator/document-search-plan",
        "proof": "Local Library/RAG preflight before retrieval.",
    },
    {
        "id": "change-brief",
        "phrase": "Explain what changed since yesterday.",
        "title": "Change brief",
        "area": "Activity",
        "command_id": "explain-changes-since-yesterday",
        "expected_route_id": "explain-changes-since-yesterday",
        "endpoint": "/api/operator/change-brief",
        "proof": "Read-only local workspace and activity evidence.",
    },
    {
        "id": "backup-verify",
        "phrase": "Prepare a backup and verify it.",
        "title": "Backup verification plan",
        "area": "Safety",
        "command_id": "prepare-backup",
        "approval_id": "request-backup-export",
        "expected_route_id": "prepare-backup",
        "endpoint": "/api/operator/backup-plan",
        "proof": "Backup planning is read-only; export/restore actions require explicit approval.",
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


def _target_workflows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in TARGET_EXPERIENCES:
        rows.append({
            "id": target["id"],
            "phrase": target["phrase"],
            "title": target["title"],
            "area": target["area"],
            "commandId": target["command_id"],
            "approvalId": target.get("approval_id", ""),
            "expectedRouteId": target["expected_route_id"],
            "proof": target["proof"],
            "state": "ok",
        })
    return rows


def _target_row(
    target: dict[str, Any],
    *,
    commands: dict[str, dict[str, Any]],
    policy: dict[str, str],
    workflows: list[dict[str, Any]],
) -> dict[str, Any]:
    command = commands.get(target["command_id"])
    approval = commands.get(target.get("approval_id", ""))
    result = resolve_operator_route(target["phrase"], list(commands.values()), workflows, policy, limit=3)
    selected = result.get("selected") if isinstance(result.get("selected"), dict) else {}
    selected_id = _trim(selected.get("id"), 160)
    expected_route_id = target["expected_route_id"]
    command_ready = command is not None
    approval_ready = not target.get("approval_id") or (_trust_mode(approval, policy) == "ask")
    route_ready = selected_id == expected_route_id
    state = "ok" if command_ready and approval_ready and route_ready else ("warn" if command_ready and selected_id else "error")
    if target.get("approval_id") and not approval:
        approval_ready = False
        state = "warn" if command_ready else "error"
    detail = (
        f"{target['proof']} route={selected_id or 'missing'}; expected={expected_route_id}; "
        f"command={target['command_id'] if command_ready else 'missing'}"
    )
    if target.get("approval_id"):
        detail += f"; approval={target['approval_id']}:{_trust_mode(approval, policy)}"
    return {
        "id": target["id"],
        "state": state,
        "badge": _trim(target["area"], 24),
        "phrase": target["phrase"],
        "title": target["title"],
        "area": target["area"],
        "detail": detail,
        "proof": target["proof"],
        "command_id": target["command_id"],
        "approval_id": target.get("approval_id", ""),
        "expected_route_id": expected_route_id,
        "selected_id": selected_id,
        "endpoint": target["endpoint"],
        "command_ready": command_ready,
        "route_ready": route_ready,
        "approval_ready": approval_ready,
        "trust": _trust_level(command),
        "trust_mode": _trust_mode(command, policy),
        "approval_mode": _trust_mode(approval, policy) if target.get("approval_id") else "",
        "executes": False,
        "requires_approval": bool(target.get("approval_id")),
        "matches": result.get("matches") or [],
        "action": expected_route_id,
        "actionLabel": "Open" if state == "ok" else "Review",
    }


def _api_action(path: str, title: str, *, writes: bool = False, requires_approval: bool = False) -> dict[str, Any]:
    return {
        "method": "GET" if not writes else "POST",
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": requires_approval,
        "uses_network": False,
    }


def run_operator_experience_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return backend proof for the goal's core user-facing operator phrases."""
    owner = owner or "local"
    configured = configured if isinstance(configured, dict) else {}
    normalized_policy = _policy(policy)
    command_by_id = _command_map(commands)
    target_workflows = _target_workflows()
    merged_workflows = target_workflows + [
        item for item in workflows or []
        if isinstance(item, dict) and _trim(item.get("phrase"), 300)
    ]
    target_rows = [
        _target_row(target, commands=command_by_id, policy=normalized_policy, workflows=merged_workflows)
        for target in TARGET_EXPERIENCES
    ]
    route_ready = sum(1 for row in target_rows if row["route_ready"])
    command_ready = sum(1 for row in target_rows if row["command_ready"])
    approval_targets = [row for row in target_rows if row["approval_id"]]
    approval_ready = sum(1 for row in approval_targets if row["approval_ready"])
    issue_rows = [row for row in target_rows if row["state"] != "ok"]
    entry_rows = [
        {
            "id": "dashboard",
            "state": "ok",
            "badge": "dash",
            "title": "Command Center dashboard",
            "detail": "Target experiences are exposed as dashboard actions and proof rows.",
            "executes": False,
        },
        {
            "id": "text-command",
            "state": "ok" if command_by_id else "warn",
            "badge": "text",
            "title": "Text command routing",
            "detail": f"{len(command_by_id)} persisted commands available to match natural-language phrases.",
            "executes": False,
        },
        {
            "id": "command-palette",
            "state": "ok" if configured.get("commands", bool(command_by_id)) else "warn",
            "badge": "pal",
            "title": "Command palette catalog",
            "detail": "Persisted command catalog backs palette search, dashboard actions, and route previews.",
            "executes": False,
        },
        {
            "id": "voice-command",
            "state": "ok",
            "badge": "vox",
            "title": "Voice command path",
            "detail": "Recognized speech routes through the same command catalog after browser permission.",
            "executes": False,
        },
        {
            "id": "workflow-targets",
            "state": "ok" if route_ready == len(target_rows) else "warn",
            "badge": "flow",
            "title": "Agent workflow target phrases",
            "detail": f"{route_ready}/{len(target_rows)} target phrases route to expected commands.",
            "executes": False,
        },
    ]
    guard_rows = [
        {
            "state": "ok",
            "badge": "read",
            "title": "Read-only route proof",
            "detail": "This plan resolves commands and approval gates only; it does not execute the selected route.",
        },
        {
            "state": "ok",
            "badge": "ask",
            "title": "Approval routes stay explicit",
            "detail": "Container repair, build-watch loop start, and backup export targets require ask-first approval commands.",
        },
        {
            "state": "ok",
            "badge": "net",
            "title": "Local-first route inventory",
            "detail": "No network calls, shell commands, file changes, task runs, training starts, or service restarts are performed.",
        },
    ]
    api_actions = [
        _api_action("/api/operator/experience-plan", "Read target-experience plan"),
        _api_action("/api/operator/route", "Resolve one command route"),
        _api_action("/api/operator/routes", "Read persisted route matrix"),
        _api_action("/api/operator/commands", "Read command catalog"),
        _api_action("/api/operator/workflows", "Read workflow catalog"),
        *[_api_action(target["endpoint"], f"Read {target['title']} evidence") for target in TARGET_EXPERIENCES],
        _api_action("/api/operator/activity", "Write executed command activity after user action", writes=True, requires_approval=False),
    ]
    state = "ok" if not issue_rows else ("warn" if command_ready else "error")
    return {
        "mode": "read-only-target-experience-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "target_count": len(target_rows),
            "ready_count": len(target_rows) - len(issue_rows),
            "issue_count": len(issue_rows),
            "command_ready_count": command_ready,
            "route_ready_count": route_ready,
            "approval_target_count": len(approval_targets),
            "approval_ready_count": approval_ready,
            "command_count": len(command_by_id),
            "workflow_count": len(workflows or []),
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "requires_approval_for_sensitive_targets": True,
        },
        "target_rows": target_rows,
        "entry_rows": entry_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "target_workflows": target_workflows,
        "configured": {
            "commands": bool(configured.get("commands", bool(command_by_id))),
            "workflows": bool(configured.get("workflows", bool(workflows))),
            "policy": bool(configured.get("policy", policy is not None)),
        },
        "approval": {
            "required": False,
            "gate": "Route proof only",
            "policy": (
                "This endpoint only proves target phrase routing, command catalog coverage, and approval posture. "
                "It does not route commands, execute commands, start workflows, start jobs, run shell commands, "
                "write files, restart services, approve actions, or use network access."
            ),
        },
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
            "activity": "data/operator_activity.json",
        },
    }
