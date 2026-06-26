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
        "id": "text-command",
        "title": "Typed toolchain request",
        "badge": "text",
        "action_id": "open-capability-map",
        "detail": "Typed operator requests can open the local capability map before any module action runs.",
    },
    {
        "id": "command-palette",
        "title": "Command palette",
        "badge": "pal",
        "action_id": "open-command-palette",
        "detail": "Named modules are reachable by searchable command routes.",
    },
    {
        "id": "voice-command",
        "title": "Voice command path",
        "badge": "vox",
        "action_id": "open-voice-preflight",
        "detail": "Voice commands use the same command catalog after browser permission.",
    },
    {
        "id": "agent-workflows",
        "title": "Agent workflow handoff",
        "badge": "flow",
        "action_id": "open-automation-map",
        "detail": "Repeatable workflows can hand off to the same toolchain modules after trust policy review.",
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
        "routes_commands": False,
        "starts_workflows": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
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
    starts_workflows: bool = False,
    starts_models: bool = False,
    starts_training: bool = False,
    runs_search: bool = False,
    runs_shell: bool = False,
    runs_docker: bool = False,
    writes_activity: bool = False,
    changes_settings: bool = False,
    approves_actions: bool = False,
    downloads_models: bool = False,
    creates_backup: bool = False,
    restores_data: bool = False,
    restarts_services: bool = False,
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
        "starts_workflows": False,
        "starts_jobs": False,
        "starts_models": False,
        "starts_training": False,
        "runs_search": False,
        "runs_shell": False,
        "runs_docker": False,
        "writes_files": False,
        "writes_activity": False,
        "changes_settings": False,
        "approves_actions": False,
        "downloads_models": False,
        "creates_backup": False,
        "restores_data": False,
        "exports_data": False,
        "deletes_records": False,
        "restarts_services": False,
        "uses_network": False,
        "gated_operation": {
            "routes_commands": routes_commands,
            "starts_workflows": starts_workflows,
            "starts_models": starts_models,
            "starts_training": starts_training,
            "runs_search": runs_search,
            "runs_shell": runs_shell,
            "runs_docker": runs_docker,
            "writes_activity": writes_activity,
            "changes_settings": changes_settings,
            "approves_actions": approves_actions,
            "downloads_models": downloads_models,
            "creates_backup": creates_backup,
            "restores_data": restores_data,
            "restarts_services": restarts_services,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    module_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    configured: dict[str, bool],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    modules = {row.get("id"): row for row in module_rows}

    def module_ready(*ids: str) -> bool:
        return all(modules.get(row_id, {}).get("state") == "ok" for row_id in ids)

    entry_ready = bool(entry_rows) and all(row.get("state") == "ok" for row in entry_rows)
    policy_ready = bool(configured.get("policy")) and bool(policy)
    safe_network = str(policy.get("network", "")).lower() != "auto"
    safe_danger = str(policy.get("danger", "")).lower() != "auto"
    return [
        _handoff_row(
            "toolchain-command-layer-handoff",
            "ok" if entry_ready else "warn",
            "cmd",
            "Command-layer handoff",
            "Dashboard, text, palette, voice, and workflow entry points can route into the backend command layer before execution.",
            "open-capability-map",
            "Commands",
            target_api="/api/operator/command-layer-plan",
            routes_commands=True,
        ),
        _handoff_row(
            "toolchain-ai-runtime-handoff",
            "ok" if module_ready("ollama", "chromadb-rag", "training-lab") else "warn",
            "ai",
            "Local AI runtime handoff",
            "Ollama, ChromaDB/RAG, Training Lab, model inventory, and support-service status converge in the AI runtime plan.",
            "open-model-routing-map",
            "Models",
            target_api="/api/operator/ai-runtime-plan",
            approval_command_id="request-model-operation",
            requires_approval=True,
            starts_models=True,
            downloads_models=True,
            uses_network=True,
        ),
        _handoff_row(
            "toolchain-knowledge-rag-handoff",
            "ok" if module_ready("chromadb-rag", "library", "memory") else "warn",
            "rag",
            "Knowledge and RAG handoff",
            "ChromaDB, Library, personal documents, uploads, and memory evidence route into local document search and recall preflights.",
            "open-library-preflight",
            "Library",
            target_api="/api/operator/document-search-plan",
        ),
        _handoff_row(
            "toolchain-research-network-handoff",
            "ok" if module_ready("searxng-research") and policy_ready and safe_network else "warn",
            "net",
            "Research network handoff",
            "SearXNG and Deep Research stay behind Offline Control and explicit network policy before any web search can run.",
            "open-research-preflight",
            "Research",
            target_api="/api/operator/research-plan",
            approval_command_id="request-network-break-glass",
            requires_approval=True,
            runs_search=True,
            uses_network=True,
        ),
        _handoff_row(
            "toolchain-code-build-handoff",
            "ok" if module_ready("code-workspace") else "warn",
            "code",
            "Code and build handoff",
            "Code Workspace, test plans, snapshots, and Build Watch route through local workspace evidence before shell execution.",
            "open-code-workspace-map",
            "Code",
            target_api="/api/operator/workspace-plan",
            approval_command_id="run-workspace-command",
            requires_approval=True,
            runs_shell=True,
            writes_activity=True,
        ),
        _handoff_row(
            "toolchain-training-handoff",
            "ok" if module_ready("training-lab", "ollama") and policy_ready else "warn",
            "train",
            "Training and artifact handoff",
            "Training Lab hands datasets, jobs, starter artifacts, model routing, and artifact review to explicit training controls.",
            "open-training-run-plan",
            "Training",
            target_api="/api/operator/training-plan",
            approval_command_id="start-training-job",
            requires_approval=True,
            starts_training=True,
            downloads_models=True,
            writes_activity=True,
            uses_network=True,
        ),
        _handoff_row(
            "toolchain-work-automation-handoff",
            "ok" if module_ready("tasks", "calendar", "notes", "agent-loops") and entry_ready else "warn",
            "work",
            "Work and automation handoff",
            "Tasks, Calendar, Notes, reminders, Agent Loops, and activity evidence can move into the Work Operations plan before automation starts.",
            "open-work-preflight",
            "Work",
            target_api="/api/operator/work-ops-plan",
            approval_command_id="request-workflow-start",
            requires_approval=True,
            starts_workflows=True,
            writes_activity=True,
        ),
        _handoff_row(
            "toolchain-memory-profile-handoff",
            "ok" if module_ready("memory", "notes") else "warn",
            "mem",
            "Unified memory handoff",
            "Preferences, projects, decisions, model choices, recurring tasks, workflows, notes, and local profile evidence route through the memory plan.",
            "open-memory-profile",
            "Memory",
            target_api="/api/operator/memory-plan",
            changes_settings=True,
        ),
        _handoff_row(
            "toolchain-docker-services-handoff",
            "ok" if module_ready("docker-services", "offline-control") and policy_ready and safe_danger else "warn",
            "svc",
            "Docker services handoff",
            "Docker support services, sealed volumes, container status, and repair boundaries route into runtime/service plans before host commands.",
            "open-local-services-map",
            "Services",
            target_api="/api/operator/docker-runtime-plan",
            approval_command_id="request-container-fix",
            requires_approval=True,
            runs_docker=True,
            restarts_services=True,
        ),
        _handoff_row(
            "toolchain-backup-recovery-handoff",
            "ok" if module_ready("backups-recovery", "offline-control") else "warn",
            "safe",
            "Backup and recovery handoff",
            "Backup scope, restore drills, rollback hints, and recovery routes are visible before exports, restores, retries, or repairs.",
            "prepare-backup",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_command_id="prepare-backup",
            requires_approval=True,
            creates_backup=True,
            restores_data=True,
        ),
        _handoff_row(
            "toolchain-tool-access-safety-handoff",
            "ok" if policy_ready and safe_network and safe_danger else "warn",
            "tool",
            "Tool access and safety handoff",
            "Tool, skill, MCP, shell, filesystem, credential, destructive, and network boundaries route through tool-access and safety policy evidence.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/tool-access-plan",
            approval_command_id="request-approval-decision",
            requires_approval=True,
            changes_settings=True,
            approves_actions=True,
            uses_network=True,
        ),
    ]


def _toolchain_alert_rows(
    module_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    critical_modules = {"offline-control", "code-workspace", "backups-recovery", "docker-services"}
    for row in module_rows:
        if row.get("state") == "ok":
            continue
        module_id = str(row.get("id") or "module")
        rows.append(
            {
                "id": f"toolchain-module-{module_id}",
                "state": "error" if module_id in critical_modules else "warn",
                "badge": row.get("badge") or "tool",
                "title": f"Toolchain module route missing: {row.get('title') or module_id}",
                "detail": row.get("detail") or row.get("proof") or "Module command route is not available.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
                "uses_network": bool(row.get("network_capable")),
            }
        )
    for row in entry_rows:
        if row.get("state") == "ok":
            continue
        rows.append(
            {
                "id": f"toolchain-entry-{row.get('id') or 'missing'}",
                "state": "warn",
                "badge": row.get("badge") or "entry",
                "title": f"Toolchain entry point missing: {row.get('title') or row.get('id')}",
                "detail": row.get("detail") or "Toolchain entry route is not available.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    if not configured.get("commands"):
        rows.append(
            {
                "id": "toolchain-command-catalog-missing",
                "state": "error",
                "badge": "cmd",
                "title": "Command catalog missing",
                "detail": "Toolchain proof needs the operator command catalog to verify module entry points.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    if not configured.get("workflows"):
        rows.append(
            {
                "id": "toolchain-workflow-catalog-missing",
                "state": "warn",
                "badge": "flow",
                "title": "Workflow catalog missing",
                "detail": "Agent Loop workflow exposure is absent from the toolchain proof.",
                "action": "open-automation-map",
                "actionLabel": "Automation",
                "requires_approval": False,
            }
        )
    if not configured.get("policy"):
        rows.append(
            {
                "id": "toolchain-policy-evidence-missing",
                "state": "warn",
                "badge": "trust",
                "title": "Trust policy evidence missing",
                "detail": "Toolchain proof cannot confirm permissioned autonomy without policy evidence.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": False,
            }
        )
    return rows[:24]


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
    configured_summary = {
        "commands": bool(configured.get("commands", bool(commands_by_id))),
        "workflows": bool(configured.get("workflows", bool(workflows))),
        "policy": bool(configured.get("policy", bool(policy))),
    }
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
    alert_rows = _toolchain_alert_rows(module_rows, entry_rows, configured_summary)
    handoff_rows = _handoff_rows(module_rows, entry_rows, configured_summary, policy)
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
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len(entry_ready),
            "network_capable_count": len(network_modules),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(data_paths),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "policy_count": len(policy),
            "toolchain_alert_count": len(alert_rows),
            "critical_toolchain_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
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
        },
        "module_rows": module_rows,
        "entry_rows": entry_rows,
        "guard_rows": guard_rows,
        "alert_rows": alert_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "required_modules": [module["id"] for module in TOOLCHAIN_MODULES],
        "configured": configured_summary,
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
