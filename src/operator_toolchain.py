"""Read-only integration proof for the Cleverly local toolchain."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


TOOLCHAIN_MODULES = [
    {
        "id": "offline-control",
        "title": "Offline Control",
        "badge": "local",
        "area": "Safety",
        "action_id": "open-offline",
        "endpoints": ["/api/offline-control/status", "/api/offline-control/audit?limit=20"],
        "paths": ["data/settings.json", "data/operator_policy.json"],
        "proof": "Offline posture, storage mode, egress policy, and local readiness are first-class console signals.",
        "network_capable": False,
    },
    {
        "id": "ollama",
        "title": "Ollama and local models",
        "badge": "ollama",
        "area": "Models",
        "action_id": "open-model-routing-map",
        "endpoints": ["/api/operator/models", "/api/operator/model-ops-plan", "/api/offline-control/models/primary"],
        "paths": ["data/cleverly-primary-model.json", "data/ollama", "cleverly-ollama:/root/.ollama"],
        "proof": "Primary model, bundled Ollama, local endpoints, and model-operation gates share one model route map.",
        "network_capable": True,
    },
    {
        "id": "chromadb-rag",
        "title": "ChromaDB and RAG",
        "badge": "chroma",
        "area": "Knowledge",
        "action_id": "open-embedding-preflight",
        "endpoints": ["/api/rag/stats", "/api/embeddings/models", "/api/operator/document-search-plan"],
        "paths": ["cleverly-chromadb-data:/data", "data/personal_docs", "data/uploads"],
        "proof": "Vector/RAG readiness and local document search evidence are routed into Library and embedding preflights.",
        "network_capable": False,
    },
    {
        "id": "searxng-research",
        "title": "SearXNG and research",
        "badge": "searx",
        "area": "Research",
        "action_id": "open-research-preflight",
        "endpoints": ["/api/search/config", "/api/search/providers", "/api/research/library?limit=20"],
        "paths": ["cleverly-searxng-data:/etc/searxng", "cleverly-searxng-cache:/var/cache/searxng", "data/research"],
        "proof": "Research/search is integrated but remains governed by Offline Control and explicit network policy.",
        "network_capable": True,
    },
    {
        "id": "training-lab",
        "title": "Training Lab",
        "badge": "train",
        "area": "Models",
        "action_id": "open-training-run-plan",
        "endpoints": ["/api/training/status", "/api/operator/training-plan"],
        "paths": ["data/training", "data/models", "data/training_jobs.json"],
        "proof": "Datasets, starter artifacts, LoRA jobs, and model creation plans are surfaced before training starts.",
        "network_capable": True,
    },
    {
        "id": "code-workspace",
        "title": "Code Workspace",
        "badge": "code",
        "area": "Code",
        "action_id": "open-code-workspace-map",
        "endpoints": ["/api/code-workspaces", "/api/operator/code-test-plan", "/api/operator/build-watch-plan"],
        "paths": ["data/code-workspaces", "data/code_workspace", "cleverly-code-worker"],
        "proof": "Repo imports, worker status, snapshots, test plans, and build-watch gates are available from one code map.",
        "network_capable": False,
    },
    {
        "id": "voice-io",
        "title": "Voice I/O",
        "badge": "voice",
        "area": "Input",
        "action_id": "open-voice-preflight",
        "endpoints": ["/api/stt/stats", "/api/tts/stats", "/api/operator/voice-plan"],
        "paths": ["data/settings.json", "data/tts_cache"],
        "proof": "Browser voice setup and STT/TTS readiness route through the same command and approval layer.",
        "network_capable": True,
    },
    {
        "id": "tasks",
        "title": "Tasks",
        "badge": "task",
        "area": "Work",
        "action_id": "open-work-preflight",
        "endpoints": ["/api/tasks?include_last_run=true", "/api/tasks/runs/recent?limit=9", "/api/operator/workday-plan"],
        "paths": ["data/app.db:scheduled_tasks", "data/tasks.json", "data/task_runs.json"],
        "proof": "Scheduled work, recent runs, policy blocks, and task review actions are visible before automation runs.",
        "network_capable": True,
    },
    {
        "id": "calendar",
        "title": "Calendar",
        "badge": "cal",
        "area": "Work",
        "action_id": "open-calendar",
        "endpoints": ["/api/calendar/events", "/api/operator/workday-plan"],
        "paths": ["data/app.db:calendars,calendar_events", "data/settings.json"],
        "proof": "Calendar events and scheduling gates are integrated into the workday preflight.",
        "network_capable": True,
    },
    {
        "id": "memory",
        "title": "Memory",
        "badge": "mem",
        "area": "Memory",
        "action_id": "open-memory-profile",
        "endpoints": ["/api/memory", "/api/operator/memory-plan", "/api/operator/profile"],
        "paths": ["data/memory.json", "data/memory_doc.md", "data/operator_profile.json"],
        "proof": "Preferences, decisions, projects, model choices, and workflows are routed through a local memory profile.",
        "network_capable": False,
    },
    {
        "id": "notes",
        "title": "Notes",
        "badge": "note",
        "area": "Memory",
        "action_id": "open-notes",
        "endpoints": ["/api/notes", "/api/operator/note-task-draft"],
        "paths": ["data/app.db:notes", "data/notes.json"],
        "proof": "Notes, reminders, and note-to-task drafts are connected to Tasks without auto-saving drafts.",
        "network_capable": False,
    },
    {
        "id": "library",
        "title": "Library",
        "badge": "lib",
        "area": "Knowledge",
        "action_id": "open-library-preflight",
        "endpoints": ["/api/documents/library", "/api/operator/document-search-plan", "/api/research/library?limit=20"],
        "paths": ["data/personal_docs", "data/uploads", "data/research"],
        "proof": "Documents, saved research, uploads, and local search preflights share the Library surface.",
        "network_capable": False,
    },
    {
        "id": "gallery",
        "title": "Gallery",
        "badge": "img",
        "area": "Library",
        "action_id": "open-gallery",
        "endpoints": ["/api/gallery/stats", "/api/upload/stats"],
        "paths": ["data/gallery", "data/uploads"],
        "proof": "Generated images, uploads, and local media stats are included in the same toolchain inventory.",
        "network_capable": False,
    },
    {
        "id": "agent-loops",
        "title": "Agent Loops",
        "badge": "loop",
        "area": "Automation",
        "action_id": "open-loops",
        "endpoints": ["/api/operator/workflows", "/api/operator/autonomy-plan", "/api/operator/build-watch-plan"],
        "paths": ["data/operator_workflows.json", "data/operator_activity.json"],
        "proof": "Repeatable local workflows, build-watch loops, and autonomy gates are visible before loop start.",
        "network_capable": True,
    },
    {
        "id": "backups-recovery",
        "title": "Backups and recovery",
        "badge": "backup",
        "area": "Safety",
        "action_id": "prepare-backup",
        "endpoints": ["/api/operator/backup-plan", "/api/offline-control/audit?limit=20"],
        "paths": ["data/backups", "cleverly-data:/app/data", "cleverly-logs:/app/logs"],
        "proof": "Backup scope, restore drills, recovery routing, and export approval boundaries are integrated.",
        "network_capable": False,
    },
    {
        "id": "docker-services",
        "title": "Docker support services",
        "badge": "svc",
        "area": "Runtime",
        "action_id": "open-local-services-map",
        "endpoints": ["/api/operator/services", "/api/operator/repair-plan", "/api/operator/runtime-plan"],
        "paths": ["docker-compose.yml", "docker/", "logs/"],
        "proof": "App, proxy, code worker, Ollama, ChromaDB, SearXNG, ntfy, volumes, and repair gates are mapped.",
        "network_capable": False,
    },
]


ENTRY_POINTS = [
    {
        "id": "dashboard",
        "title": "Toolchain band",
        "badge": "dash",
        "action_id": "open-operator-runbook",
        "detail": "The Command Center dashboard inventories the local modules as one operating console.",
    },
    {
        "id": "palette",
        "title": "Command palette",
        "badge": "pal",
        "action_id": "open-command-palette",
        "detail": "Named modules are reachable by searchable command routes.",
    },
    {
        "id": "voice",
        "title": "Voice command path",
        "badge": "vox",
        "action_id": "open-voice-preflight",
        "detail": "Voice commands use the same command catalog after browser permission.",
    },
    {
        "id": "activity",
        "title": "Activity timeline",
        "badge": "log",
        "action_id": "open-activity-preflight",
        "detail": "Executed module actions record local status, result, retry, and recovery evidence.",
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


def _module_row(module: dict[str, Any], commands: dict[str, dict[str, Any]]) -> dict[str, Any]:
    action_id = module["action_id"]
    action_ready = action_id in commands
    return {
        "id": module["id"],
        "state": "ok" if action_ready else "warn",
        "badge": module["badge"],
        "title": module["title"],
        "area": module["area"],
        "detail": f"{module['proof']} action={action_id if action_ready else f'{action_id}:missing'}",
        "proof": module["proof"],
        "action_id": action_id,
        "action_ready": action_ready,
        "endpoints": list(module["endpoints"]),
        "paths": list(module["paths"]),
        "network_capable": bool(module["network_capable"]),
        "executes": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
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


def run_operator_toolchain_plan(
    owner: str = "local",
    *,
    commands: list[dict[str, Any]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return read-only proof that the named local modules are integrated."""
    owner = owner or "local"
    configured = configured if isinstance(configured, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    commands_by_id = _command_map(commands)
    module_rows = [_module_row(module, commands_by_id) for module in TOOLCHAIN_MODULES]
    entry_rows = [_entry_row(entry, commands_by_id) for entry in ENTRY_POINTS]
    ready_rows = [row for row in module_rows if row["state"] == "ok"]
    entry_ready = [row for row in entry_rows if row["state"] == "ok"]
    network_modules = [row for row in module_rows if row["network_capable"]]
    endpoint_paths = sorted({path for module in TOOLCHAIN_MODULES for path in module["endpoints"]})
    data_paths = sorted({path for module in TOOLCHAIN_MODULES for path in module["paths"]})
    api_actions = [
        _api_action("/api/operator/toolchain-plan", "Read Toolchain integration plan"),
        *[
            _api_action(path, f"Read {path} toolchain feed", uses_network=False)
            for path in endpoint_paths
        ],
        _api_action("/api/operator/activity", "Write activity after explicit tool command", writes=True),
    ]
    guard_rows = [
        {
            "state": "ok",
            "badge": "read",
            "title": "Read-only integration proof",
            "detail": "This endpoint lists module wiring, command routes, data paths, and API feeds only.",
        },
        {
            "state": "ok",
            "badge": "net",
            "title": "Network-capable modules stay gated",
            "detail": f"{len(network_modules)} modules can involve network features, but this plan does not call them.",
        },
        {
            "state": "ok",
            "badge": "act",
            "title": "Execution stays in owning tools",
            "detail": "Training, code, task, backup, research, model, and repair actions require explicit tool commands and trust policy.",
        },
    ]
    state = "ok" if len(ready_rows) == len(module_rows) else "warn"
    return {
        "mode": "read-only-toolchain-integration-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "module_count": len(module_rows),
            "ready_count": len(ready_rows),
            "issue_count": len(module_rows) - len(ready_rows),
            "entry_count": len(entry_rows),
            "entry_ready_count": len(entry_ready),
            "network_capable_count": len(network_modules),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(data_paths),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "policy_count": len(policy),
            "routes_commands": False,
            "executes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "approves_actions": False,
        },
        "module_rows": module_rows,
        "entry_rows": entry_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "required_modules": [module["id"] for module in TOOLCHAIN_MODULES],
        "configured": {
            "commands": bool(configured.get("commands", bool(commands_by_id))),
            "workflows": bool(configured.get("workflows", bool(workflows))),
            "policy": bool(configured.get("policy", bool(policy))),
        },
        "approval": {
            "required": False,
            "gate": "Read-only toolchain integration proof",
            "policy": (
                "This endpoint only proves local module wiring, command entry points, API feeds, data paths, and safety posture. "
                "It does not route commands, execute commands, approve actions, start workflows, start jobs, run shell commands, "
                "write files, restart services, train models, download models, query web search, export data, delete records, or use network access."
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
