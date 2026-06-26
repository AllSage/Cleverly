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


def _experience_alert_rows(
    target_rows: list[dict[str, Any]],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    critical_targets = {
        "container-health",
        "code-tests",
        "watch-build",
        "backup-verify",
    }
    for row in target_rows:
        if row.get("state") == "ok":
            continue
        target_id = _trim(row.get("id"), 160) or "target"
        missing_command = row.get("command_ready") is not True
        route_mismatch = row.get("route_ready") is not True
        approval_gap = row.get("approval_id") and row.get("approval_ready") is not True
        if missing_command:
            reason = f"command {row.get('command_id') or 'missing'} is not in the command catalog"
        elif route_mismatch:
            reason = (
                f"selected route {row.get('selected_id') or 'missing'} does not match "
                f"{row.get('expected_route_id') or 'expected route'}"
            )
        elif approval_gap:
            reason = f"approval command {row.get('approval_id')} is not ask-gated"
        else:
            reason = "target phrase needs route review"
        rows.append(
            {
                "id": f"experience-target-{target_id}",
                "state": "error" if target_id in critical_targets or missing_command else "warn",
                "badge": row.get("badge") or row.get("area") or "target",
                "title": f"Target phrase not ready: {row.get('title') or target_id}",
                "detail": f"{row.get('phrase') or row.get('title')}: {reason}.",
                "action": "open-capability-map",
                "actionLabel": "Map",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    if not configured.get("commands"):
        rows.append(
            {
                "id": "experience-command-catalog-missing",
                "state": "error",
                "badge": "cmd",
                "title": "Target command catalog missing",
                "detail": "Target-experience proof needs the operator command catalog before voice/text phrases can be proven.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    if not configured.get("workflows"):
        rows.append(
            {
                "id": "experience-workflow-catalog-missing",
                "state": "warn",
                "badge": "flow",
                "title": "Target workflow catalog missing",
                "detail": "Persisted workflow phrase evidence is missing; built-in target phrases are still used for read-only proof.",
                "action": "open-automation-map",
                "actionLabel": "Automation",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    if not configured.get("policy"):
        rows.append(
            {
                "id": "experience-policy-evidence-missing",
                "state": "warn",
                "badge": "ask",
                "title": "Target trust policy evidence missing",
                "detail": "Approval posture is using defaults because persisted trust-policy evidence is not available.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    return rows[:16]


def _entry_rows(
    *,
    command_count: int,
    workflow_count: int,
    target_count: int,
    route_ready: int,
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    command_ready = command_count > 0 and configured.get("commands")
    target_ready = target_count > 0 and route_ready == target_count
    workflow_ready = workflow_count > 0 or target_ready
    return [
        {
            "id": "dashboard",
            "state": "ok" if target_ready else "warn",
            "badge": "dash",
            "title": "Command Center dashboard",
            "detail": f"{route_ready}/{target_count} target actions have backend route proof for dashboard cards and panels.",
            "channel": "dashboard",
            "ready": target_ready,
            "executes": False,
            "requires_approval": False,
            "action": "refresh-command-center",
            "actionLabel": "Dashboard",
        },
        {
            "id": "text-command",
            "state": "ok" if command_ready and target_ready else "warn",
            "badge": "text",
            "title": "Text command input",
            "detail": f"{command_count} persisted command(s); typed phrases preflight through /api/operator/route before local execution.",
            "channel": "text",
            "ready": command_ready and target_ready,
            "executes": False,
            "requires_approval": False,
            "action": "open-command-palette",
            "actionLabel": "Text",
        },
        {
            "id": "command-palette",
            "state": "ok" if command_ready else "warn",
            "badge": "pal",
            "title": "Global command palette",
            "detail": "Palette search uses the persisted command catalog and backend route preview for typed natural-language commands.",
            "channel": "palette",
            "ready": command_ready,
            "executes": False,
            "requires_approval": False,
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            "id": "voice-command",
            "state": "ok" if command_ready and target_ready else "warn",
            "badge": "voice",
            "title": "Voice command route",
            "detail": "Recognized speech routes through the same text command layer after browser microphone permission.",
            "channel": "voice",
            "ready": command_ready and target_ready,
            "executes": False,
            "requires_approval": True,
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            "id": "agent-workflows",
            "state": "ok" if workflow_ready else "warn",
            "badge": "flow",
            "title": "Agent workflow target phrases",
            "detail": f"{workflow_count} persisted workflow phrase(s); built-in targets provide fallback route proof for the core operator experience.",
            "channel": "workflow",
            "ready": workflow_ready,
            "executes": False,
            "requires_approval": True,
            "action": "open-automation-map",
            "actionLabel": "Workflows",
        },
    ]


def _route_match_rows(target_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in target_rows:
        matches = row.get("matches") if isinstance(row.get("matches"), list) else []
        best = matches[0] if matches and isinstance(matches[0], dict) else {}
        score = int(best.get("score") or 0)
        selected_id = _trim(row.get("selected_id"), 160)
        expected_id = _trim(row.get("expected_route_id"), 160)
        approval_id = _trim(row.get("approval_id"), 160)
        approval_detail = f"; approval={approval_id}:{row.get('approval_mode') or 'missing'}" if approval_id else ""
        rows.append({
            "id": f"route-match-{row.get('id') or expected_id or len(rows)}",
            "state": "ok" if row.get("route_ready") else ("warn" if selected_id else "error"),
            "badge": row.get("badge") or row.get("area") or "route",
            "title": f"Route match: {row.get('title') or row.get('phrase') or expected_id}",
            "detail": (
                f"phrase={row.get('phrase')}; selected={selected_id or 'missing'}; expected={expected_id}; "
                f"score={score}; trust={row.get('trust') or 'local'}:{row.get('trust_mode') or 'auto'}"
                f"{approval_detail}"
            ),
            "phrase": row.get("phrase") or "",
            "selected_id": selected_id,
            "expected_route_id": expected_id,
            "score": score,
            "trust": row.get("trust") or "local",
            "trust_mode": row.get("trust_mode") or "auto",
            "approval_id": approval_id,
            "approval_mode": row.get("approval_mode") or "",
            "route_ready": row.get("route_ready") is True,
            "command_ready": row.get("command_ready") is True,
            "approval_ready": row.get("approval_ready") is True,
            "executes": False,
            "routes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "action": expected_id or "open-capability-map",
            "actionLabel": "Open" if row.get("route_ready") else "Review",
        })
    return rows


def _handoff_row(
    row: dict[str, Any],
    *,
    starts_workflows: bool = False,
    starts_jobs: bool = False,
    starts_training: bool = False,
    runs_search: bool = False,
    runs_shell: bool = False,
    runs_docker: bool = False,
    writes_files: bool = False,
    writes_activity: bool = False,
    creates_tasks: bool = False,
    creates_backup: bool = False,
    restores_data: bool = False,
    restarts_services: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    approval_id = _trim(row.get("approval_id"), 160)
    return {
        "id": f"experience-{row.get('id')}-handoff",
        "state": "ok" if row.get("state") == "ok" else row.get("state", "warn"),
        "badge": row.get("badge") or row.get("area") or "target",
        "title": f"{row.get('title') or row.get('id')} handoff",
        "detail": (
            f"{row.get('phrase') or row.get('title')} routes to {row.get('endpoint')}; "
            f"selected={row.get('selected_id') or 'missing'}; expected={row.get('expected_route_id') or 'missing'}"
        ),
        "action": row.get("expected_route_id") or row.get("action") or "open-capability-map",
        "actionLabel": "Open" if row.get("state") == "ok" else "Review",
        "target_api": row.get("endpoint") or "/api/operator/experience-plan",
        "target_phrase": row.get("phrase") or "",
        "command_id": row.get("command_id") or "",
        "approval_command_id": approval_id,
        "requires_approval": bool(approval_id) or bool(row.get("requires_approval")),
        "executes": False,
        "routes_commands": False,
        "executes_commands": False,
        "starts_workflows": False,
        "starts_jobs": False,
        "starts_training": False,
        "runs_search": False,
        "runs_shell": False,
        "runs_docker": False,
        "writes_files": False,
        "writes_activity": False,
        "creates_tasks": False,
        "creates_backup": False,
        "restores_data": False,
        "exports_data": False,
        "deletes_records": False,
        "restarts_services": False,
        "uses_network": False,
        "gated_operation": {
            "routes_commands": True,
            "starts_workflows": starts_workflows,
            "starts_jobs": starts_jobs,
            "starts_training": starts_training,
            "runs_search": runs_search,
            "runs_shell": runs_shell,
            "runs_docker": runs_docker,
            "writes_files": writes_files,
            "writes_activity": writes_activity,
            "creates_tasks": creates_tasks,
            "creates_backup": creates_backup,
            "restores_data": restores_data,
            "restarts_services": restarts_services,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(target_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_id = {row.get("id"): row for row in target_rows}
    rows: list[dict[str, Any]] = []
    if rows_by_id.get("summarize-today"):
        rows.append(_handoff_row(rows_by_id["summarize-today"], writes_activity=True))
    if rows_by_id.get("container-health"):
        rows.append(_handoff_row(rows_by_id["container-health"], runs_docker=True, restarts_services=True, writes_activity=True))
    if rows_by_id.get("code-tests"):
        rows.append(_handoff_row(rows_by_id["code-tests"], runs_shell=True, writes_files=True, writes_activity=True))
    if rows_by_id.get("train-small-model"):
        rows.append(_handoff_row(rows_by_id["train-small-model"], starts_jobs=True, starts_training=True, writes_files=True, writes_activity=True))
    if rows_by_id.get("watch-build"):
        rows.append(_handoff_row(rows_by_id["watch-build"], starts_workflows=True, runs_shell=True, writes_activity=True))
    if rows_by_id.get("note-task"):
        rows.append(_handoff_row(rows_by_id["note-task"], creates_tasks=True, writes_activity=True))
    if rows_by_id.get("local-doc-search"):
        rows.append(_handoff_row(rows_by_id["local-doc-search"], runs_search=True))
    if rows_by_id.get("change-brief"):
        rows.append(_handoff_row(rows_by_id["change-brief"], writes_activity=True))
    if rows_by_id.get("backup-verify"):
        rows.append(_handoff_row(rows_by_id["backup-verify"], creates_backup=True, restores_data=True, writes_activity=True))
    return rows


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
    configured_summary = {
        "commands": bool(configured.get("commands", bool(command_by_id))),
        "workflows": bool(configured.get("workflows", bool(workflows))),
        "policy": bool(configured.get("policy", policy is not None)),
    }
    alert_rows = _experience_alert_rows(target_rows, configured_summary)
    route_match_rows = _route_match_rows(target_rows)
    handoff_rows = _handoff_rows(target_rows)
    entry_rows = _entry_rows(
        command_count=len(command_by_id),
        workflow_count=len(workflows or []),
        target_count=len(target_rows),
        route_ready=route_ready,
        configured=configured_summary,
    )
    entry_ready = sum(1 for row in entry_rows if row["ready"])
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
            "route_match_count": len(route_match_rows),
            "route_match_ready_count": sum(1 for row in route_match_rows if row.get("route_ready")),
            "approval_target_count": len(approval_targets),
            "approval_ready_count": approval_ready,
            "experience_alert_count": len(alert_rows),
            "critical_experience_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "entry_path_count": len(entry_rows),
            "entry_path_ready_count": entry_ready,
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": entry_ready,
            "dashboard_entry_ready": any(row["id"] == "dashboard" and row["ready"] for row in entry_rows),
            "text_entry_ready": any(row["id"] == "text-command" and row["ready"] for row in entry_rows),
            "palette_entry_ready": any(row["id"] == "command-palette" and row["ready"] for row in entry_rows),
            "voice_entry_ready": any(row["id"] == "voice-command" and row["ready"] for row in entry_rows),
            "workflow_entry_ready": any(row["id"] == "agent-workflows" and row["ready"] for row in entry_rows),
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
        "route_match_rows": route_match_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "target_workflows": target_workflows,
        "configured": configured_summary,
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
