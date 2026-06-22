"""Read-only Command Center situational-awareness proof."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


CONSOLE_SECTIONS = [
    {
        "id": "system-status",
        "title": "System status",
        "badge": "system",
        "action_id": "open-operator-runbook",
        "endpoints": ["/api/health", "/api/runtime", "/api/operator/runtime-plan"],
        "proof": "Health, runtime, Docker/native mode, disk, process, and service rows feed the first screen.",
        "paths": ["logs/", "data/operator_activity.json"],
    },
    {
        "id": "active-models",
        "title": "Active models",
        "badge": "model",
        "action_id": "open-model-routing-map",
        "endpoints": ["/api/operator/models", "/api/operator/model-ops-plan", "/api/offline-control/models/primary"],
        "proof": "Primary model, local model endpoints, Ollama status, and model-operation gates are visible.",
        "paths": ["data/settings.json", "data/operator_activity.json"],
    },
    {
        "id": "offline-security",
        "title": "Offline and security state",
        "badge": "safe",
        "action_id": "open-offline",
        "endpoints": ["/api/offline-control/status", "/api/operator/autonomy-plan", "/api/operator/policy"],
        "proof": "Offline posture, trust tiers, approval gates, and network break-glass state are surfaced.",
        "paths": ["data/operator_policy.json", "data/settings.json"],
    },
    {
        "id": "active-jobs",
        "title": "Active jobs",
        "badge": "jobs",
        "action_id": "open-operations-queue",
        "endpoints": ["/api/tasks/runs/recent?limit=9", "/api/operator/activity-plan"],
        "proof": "Running, failed, retryable, and policy-blocked work is routed into the operations queue.",
        "paths": ["data/operator_activity.json", "data/tasks.json"],
    },
    {
        "id": "recent-memory",
        "title": "Recent memory",
        "badge": "mem",
        "action_id": "open-memory-profile",
        "endpoints": ["/api/memory", "/api/notes", "/api/operator/memory-plan", "/api/operator/profile"],
        "proof": "Profile, preferences, memory, notes, and recall status are visible without writing memories.",
        "paths": ["data/memory.json", "data/operator_profile.json", "data/notes.json"],
    },
    {
        "id": "tasks",
        "title": "Tasks",
        "badge": "task",
        "action_id": "open-work-preflight",
        "endpoints": ["/api/tasks?include_last_run=true", "/api/operator/workday-plan"],
        "proof": "Open tasks, scheduled work, recent runs, and workday preflight rows are visible.",
        "paths": ["data/tasks.json", "data/task_runs.json"],
    },
    {
        "id": "calendar",
        "title": "Calendar",
        "badge": "cal",
        "action_id": "open-work-preflight",
        "endpoints": ["/api/calendar/events", "/api/operator/workday-plan"],
        "proof": "Calendar window, event review, and scheduling gates feed the workday panel.",
        "paths": ["data/calendar.json", "data/settings.json"],
    },
    {
        "id": "code-workspaces",
        "title": "Code workspaces",
        "badge": "code",
        "action_id": "open-code-workspace-map",
        "endpoints": ["/api/code-workspaces", "/api/operator/code-test-plan", "/api/operator/build-watch-plan"],
        "proof": "Workspace inventory, runner status, candidate test/build commands, and approval gates are visible.",
        "paths": ["data/code_workspaces.json", "data/code_workspace/"],
    },
    {
        "id": "training-jobs",
        "title": "Training jobs",
        "badge": "train",
        "action_id": "open-training-run-plan",
        "endpoints": ["/api/training/status", "/api/operator/training-plan", "/api/operator/model-ops-plan"],
        "proof": "Datasets, artifacts, jobs, fine-tune readiness, and model-creation boundaries are visible.",
        "paths": ["data/training/", "data/training_jobs.json", "data/models/"],
    },
    {
        "id": "alerts",
        "title": "Alerts",
        "badge": "alert",
        "action_id": "open-activity-preflight",
        "endpoints": ["/api/operator/checks", "/api/operator/services", "/api/operator/activity-plan"],
        "proof": "Service warnings, failed jobs, policy blocks, backup gaps, and activity issues are collected for review.",
        "paths": ["logs/", "data/operator_activity.json", "data/backups/"],
    },
]


ENTRY_POINTS = [
    {
        "id": "dashboard",
        "title": "Command Center dashboard",
        "badge": "dash",
        "action_id": "open-operator-runbook",
        "detail": "Primary situational-awareness surface for system, models, jobs, work, memory, code, training, and alerts.",
    },
    {
        "id": "command-palette",
        "title": "Global command palette",
        "badge": "pal",
        "action_id": "open-command-palette",
        "detail": "All major console areas remain reachable by command search.",
    },
    {
        "id": "text-command",
        "title": "Text command input",
        "badge": "text",
        "action_id": "summarize-today",
        "detail": "Natural-language commands route through the command catalog and target-experience proof.",
    },
    {
        "id": "voice-command",
        "title": "Voice mode",
        "badge": "vox",
        "action_id": "open-voice-preflight",
        "detail": "Voice setup uses the same command routing layer after browser microphone permission.",
    },
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


def _section_row(section: dict[str, Any], commands: dict[str, dict[str, Any]]) -> dict[str, Any]:
    action_id = section["action_id"]
    action_ready = action_id in commands
    state = "ok" if action_ready else "warn"
    return {
        "id": section["id"],
        "state": state,
        "badge": section["badge"],
        "title": section["title"],
        "detail": f"{section['proof']} action={action_id if action_ready else f'{action_id}:missing'}",
        "proof": section["proof"],
        "action_id": action_id,
        "action_ready": action_ready,
        "endpoints": list(section["endpoints"]),
        "paths": list(section["paths"]),
        "executes": False,
        "uses_network": False,
        "writes_files": False,
    }


def _entry_row(entry: dict[str, Any], commands: dict[str, dict[str, Any]]) -> dict[str, Any]:
    action_id = entry["action_id"]
    action_ready = action_id in commands
    return {
        "id": entry["id"],
        "state": "ok" if action_ready else "warn",
        "badge": entry["badge"],
        "title": entry["title"],
        "detail": f"{entry['detail']} action={action_id if action_ready else f'{action_id}:missing'}",
        "action_id": action_id,
        "action_ready": action_ready,
        "executes": False,
    }


def _api_action(path: str, title: str, *, writes: bool = False) -> dict[str, Any]:
    return {
        "method": "GET" if not writes else "POST",
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "requires_approval": writes,
        "uses_network": False,
    }


def run_operator_console_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return read-only proof that Command Center covers the required home-screen areas."""
    owner = owner or "local"
    configured = configured if isinstance(configured, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    commands_by_id = _command_map(commands)
    section_rows = [_section_row(section, commands_by_id) for section in CONSOLE_SECTIONS]
    entry_rows = [_entry_row(entry, commands_by_id) for entry in ENTRY_POINTS]
    ready_rows = [row for row in section_rows if row["state"] == "ok"]
    entry_ready = [row for row in entry_rows if row["state"] == "ok"]
    endpoint_paths = sorted({path for section in CONSOLE_SECTIONS for path in section["endpoints"]})
    data_paths = sorted({path for section in CONSOLE_SECTIONS for path in section["paths"]})
    guard_rows = [
        {
            "state": "ok",
            "badge": "read",
            "title": "Read-only dashboard proof",
            "detail": "This endpoint lists console sections, data feeds, commands, and safety gates only.",
        },
        {
            "state": "ok",
            "badge": "gate",
            "title": "Actions stay permissioned",
            "detail": "Opening a section is separate from executing shell, file, model, training, repair, or network actions.",
        },
        {
            "state": "ok",
            "badge": "local",
            "title": "Local-first visibility",
            "detail": "Dashboard evidence points at local data files, Docker volumes, and in-app endpoints.",
        },
    ]
    api_actions = [
        _api_action("/api/operator/console-plan", "Read Command Center console plan"),
        *[_api_action(path, f"Read {path} dashboard feed") for path in endpoint_paths],
        _api_action("/api/operator/activity", "Write activity after explicit command execution", writes=True),
    ]
    state = "ok" if len(ready_rows) == len(section_rows) else "warn"
    return {
        "mode": "read-only-console-readiness-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "section_count": len(section_rows),
            "ready_count": len(ready_rows),
            "issue_count": len(section_rows) - len(ready_rows),
            "entry_count": len(entry_rows),
            "entry_ready_count": len(entry_ready),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(data_paths),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "policy_count": len(policy),
            "dashboard_sections_ready": len(ready_rows) == len(section_rows),
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "approves_actions": False,
        },
        "section_rows": section_rows,
        "entry_rows": entry_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "required_sections": [section["id"] for section in CONSOLE_SECTIONS],
        "configured": {
            "commands": bool(configured.get("commands", bool(commands_by_id))),
            "workflows": bool(configured.get("workflows", bool(workflows))),
            "policy": bool(configured.get("policy", bool(policy))),
        },
        "approval": {
            "required": False,
            "gate": "Read-only console readiness proof",
            "policy": (
                "This endpoint only proves Command Center section coverage, entry points, data feeds, and guard rails. "
                "It does not route commands, execute commands, approve actions, start workflows, start jobs, "
                "run shell commands, write files, restart services, train models, export data, delete records, or use network access."
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
