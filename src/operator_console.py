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
        "id": "local-data-map",
        "title": "Local Data Map",
        "badge": "data",
        "action_id": "open-local-data-map",
        "endpoints": ["/api/operator/data-plan", "/api/operator/file-ops-plan", "/api/operator/credentials-plan"],
        "proof": "Local data scopes, sensitive stores, backup boundaries, and file/credential gates are visible before data work.",
        "paths": ["data/", "logs/", "data/backups/"],
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
        "id": "research-library",
        "title": "Research and library",
        "badge": "lib",
        "action_id": "open-library-preflight",
        "endpoints": ["/api/operator/document-search-plan", "/api/operator/research-plan", "/api/operator/gallery-plan", "/api/operator/workspace-plan"],
        "proof": "Local documents, Library records, Gallery media, research reports, RAG/search readiness, and SearXNG gates are visible before retrieval or networked research.",
        "paths": ["data/personal_docs/", "data/uploads/", "data/research/", "data/gallery/"],
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
    {
        "id": "agent-workflows",
        "title": "Agent workflow handoff",
        "badge": "flow",
        "action_id": "open-automation-map",
        "detail": "Agent workflows and automation handoffs remain visible from the console without starting jobs.",
    },
]


ALERT_FEEDS = [
    ("console", "/api/operator/console-plan", "alert_rows", "console_alert_count"),
    ("services", "/api/operator/services-plan", "alert_rows", "service_alert_count"),
    ("service-snapshot", "/api/operator/services", "alert_rows", "service_snapshot_alert_count"),
    ("checks", "/api/operator/checks", "alert_rows", "checks_alert_count"),
    ("container-checks", "/api/operator/checks", "container_plan.alert_rows", "checks_container_alert_count"),
    ("docker-runtime", "/api/operator/docker-runtime-plan", "alert_rows", "docker_runtime_alert_count"),
    ("models", "/api/operator/models", "alert_rows", "model_snapshot_alert_count"),
    ("model-ops", "/api/operator/model-ops-plan", "alert_rows", "model_alert_count"),
    ("ai-runtime", "/api/operator/ai-runtime-plan", "alert_rows", "ai_runtime_alert_count"),
    ("briefing", "/api/operator/briefing", "alert_rows", "briefing_alert_count"),
    ("command-layer", "/api/operator/command-layer-plan", "alert_rows", "command_layer_alert_count"),
    ("voice", "/api/operator/voice-plan", "alert_rows", "voice_alert_count"),
    ("note-to-task", "/api/operator/note-task-draft", "alert_rows", "note_task_alert_count"),
    ("activity", "/api/operator/activity-plan", "alert_rows", "activity_alert_count"),
    ("recovery", "/api/operator/recovery-plan", "alert_rows", "recovery_alert_count"),
    ("backup", "/api/operator/backup-plan", "alert_rows", "backup_alert_count"),
    ("change-brief", "/api/operator/change-brief", "alert_rows", "change_alert_count"),
    ("code-tests", "/api/operator/code-test-plan", "alert_rows", "code_alert_count"),
    ("build-watch", "/api/operator/build-watch-plan", "alert_rows", "build_watch_alert_count"),
    ("document-search", "/api/operator/document-search-plan", "alert_rows", "document_search_alert_count"),
    ("research", "/api/operator/research-plan", "alert_rows", "research_alert_count"),
    ("gallery", "/api/operator/gallery-plan", "alert_rows", "gallery_alert_count"),
    ("file-ops", "/api/operator/file-ops-plan", "alert_rows", "file_ops_alert_count"),
    ("workspace", "/api/operator/workspace-plan", "alert_rows", "workspace_alert_count"),
    ("training", "/api/operator/training-plan", "alert_rows", "training_alert_count"),
    ("automation-plan", "/api/operator/automation-plan", "alert_rows", "automation_operations_alert_count"),
    ("autonomy", "/api/operator/autonomy-plan", "alert_rows", "automation_alert_count"),
    ("approvals", "/api/operator/approval-plan", "alert_rows", "approval_alert_count"),
    ("loops", "/api/operator/loops-plan", "alert_rows", "loops_alert_count"),
    ("memory", "/api/operator/memory-plan", "alert_rows", "memory_alert_count"),
    ("work-ops", "/api/operator/work-ops-plan", "alert_rows", "work_ops_alert_count"),
    ("workday", "/api/operator/workday-plan", "alert_rows", "alert_count"),
    ("notes", "/api/operator/notes-plan", "alert_rows", "notes_alert_count"),
    ("calendar", "/api/operator/calendar-plan", "alert_rows", "calendar_alert_count"),
    ("tasks", "/api/operator/tasks-plan", "alert_rows", "tasks_alert_count"),
    ("runtime", "/api/operator/runtime-plan", "alert_rows", "runtime_alert_count"),
    ("data", "/api/operator/data-plan", "alert_rows", "data_alert_count"),
    ("credentials", "/api/operator/credentials-plan", "alert_rows", "credential_alert_count"),
    ("safety", "/api/operator/safety-plan", "alert_rows", "safety_alert_count"),
    ("toolchain", "/api/operator/toolchain-plan", "alert_rows", "toolchain_alert_count"),
    ("tool-access", "/api/operator/tool-access-plan", "alert_rows", "tool_access_alert_count"),
    ("experience", "/api/operator/experience-plan", "alert_rows", "experience_alert_count"),
    ("goal", "/api/operator/goal-plan", "alert_rows", "goal_alert_count"),
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


def _alert_feed_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feed_id, endpoint, row_key, count_key in ALERT_FEEDS:
        rows.append({
            "id": f"alert-feed-{feed_id}",
            "state": "ok",
            "badge": "alert",
            "title": f"Alert feed: {feed_id}",
            "detail": f"{endpoint} exposes {row_key} with summary key {count_key}.",
            "endpoint": endpoint,
            "row_key": row_key,
            "count_key": count_key,
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "executes": False,
            "routes_commands": False,
            "starts_workflows": False,
            "starts_jobs": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
        })
    return rows


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
    writes_activity: bool = False,
    approves_actions: bool = False,
    creates_backup: bool = False,
    restores_data: bool = False,
    changes_policy: bool = False,
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
        "runs_shell": False,
        "runs_docker": False,
        "writes_files": False,
        "writes_activity": False,
        "approves_actions": False,
        "creates_backup": False,
        "restores_data": False,
        "changes_policy": False,
        "exports_data": False,
        "deletes_records": False,
        "restarts_services": False,
        "uses_network": False,
        "gated_operation": {
            "routes_commands": routes_commands,
            "starts_workflows": starts_workflows,
            "writes_activity": writes_activity,
            "approves_actions": approves_actions,
            "creates_backup": creates_backup,
            "restores_data": restores_data,
            "changes_policy": changes_policy,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    *,
    section_ready_count: int,
    section_count: int,
    entry_ready_count: int,
    entry_count: int,
    critical_alert_count: int,
    alert_feed_ready_count: int,
    alert_feed_count: int,
    configured: dict[str, bool],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    dashboard_ready = section_count > 0 and section_ready_count >= section_count
    entry_ready = entry_count > 0 and entry_ready_count >= entry_count
    alerts_ready = alert_feed_count > 0 and alert_feed_ready_count >= alert_feed_count
    policy_ready = bool(configured.get("policy")) and bool(policy)
    safe_policy = str(policy.get("network", "")).lower() != "auto" and str(policy.get("danger", "")).lower() != "auto"
    alert_state = "error" if critical_alert_count else ("ok" if alerts_ready else "warn")
    return [
        _handoff_row(
            "console-dashboard-status-handoff",
            "ok" if dashboard_ready else "warn",
            "dash",
            "Dashboard status handoff",
            "The first screen can hand off system, models, jobs, memory, data, work, code, training, and alerts to owning preflights.",
            "open-console-readiness-audit",
            "Audit",
            target_api="/api/operator/console-plan",
        ),
        _handoff_row(
            "console-command-layer-handoff",
            "ok" if entry_ready else "warn",
            "cmd",
            "Command layer handoff",
            "Dashboard, text, palette, voice, and workflow entry points route through the backend command layer before execution.",
            "open-capability-map",
            "Commands",
            target_api="/api/operator/command-layer-plan",
            routes_commands=True,
        ),
        _handoff_row(
            "console-alert-feed-handoff",
            alert_state,
            "alert",
            "Alert feed handoff",
            f"{alert_feed_ready_count}/{alert_feed_count} local alert feed(s) expose review rows for the activity and operations queue.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity-plan",
        ),
        _handoff_row(
            "console-approval-queue-handoff",
            "ok" if policy_ready else "warn",
            "gate",
            "Approval queue handoff",
            "Permissioned autonomy stays visible through approval evidence before shell, filesystem, network, model, repair, or workflow actions.",
            "open-trust-controls",
            "Approvals",
            target_api="/api/operator/approval-plan",
            approval_command_id="request-approval-decision",
            approves_actions=True,
        ),
        _handoff_row(
            "console-activity-ledger-handoff",
            "ok",
            "log",
            "Activity ledger handoff",
            "Approved automation and operator actions can be recorded with status, logs, retry, recovery, and local route proof.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            writes_activity=True,
        ),
        _handoff_row(
            "console-recovery-handoff",
            "warn" if critical_alert_count else "ok",
            "recover",
            "Recovery and rollback handoff",
            "Failed or risky operations can move from the console into recovery planning before retry, rollback, restore, or repair.",
            "open-recovery-map",
            "Recovery",
            target_api="/api/operator/recovery-plan",
            approval_command_id="request-recovery-action",
            requires_approval=True,
            restores_data=True,
        ),
        _handoff_row(
            "console-backup-readiness-handoff",
            "ok" if dashboard_ready and alerts_ready else "warn",
            "backup",
            "Backup readiness handoff",
            "Sensitive file, data, model, training, and repair work can route to backup verification before local state changes.",
            "prepare-backup",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_command_id="prepare-backup",
            requires_approval=True,
            creates_backup=True,
            restores_data=True,
        ),
        _handoff_row(
            "console-safety-policy-handoff",
            "ok" if policy_ready and safe_policy else "warn",
            "safe",
            "Safety policy handoff",
            "Network, destructive, credential, shell, Docker, and filesystem boundaries stay behind local-first policy and explicit approval.",
            "open-offline",
            "Policy",
            target_api="/api/operator/safety-plan",
            approval_command_id="request-network-break-glass",
            changes_policy=True,
            uses_network=True,
        ),
    ]


def _console_alert_rows(
    section_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in section_rows:
        if row.get("state") == "ok":
            continue
        rows.append(
            {
                "id": f"console-section-{row.get('id') or 'missing'}",
                "state": "error" if row.get("id") in {"system-status", "offline-security", "alerts"} else "warn",
                "badge": row.get("badge") or "dash",
                "title": f"Dashboard section action missing: {row.get('title') or row.get('id')}",
                "detail": row.get("detail") or "Command Center section route is not available.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    for row in entry_rows:
        if row.get("state") == "ok":
            continue
        rows.append(
            {
                "id": f"console-entry-{row.get('id') or 'missing'}",
                "state": "warn",
                "badge": row.get("badge") or "entry",
                "title": f"Console entry point missing: {row.get('title') or row.get('id')}",
                "detail": row.get("detail") or "Operator entry point route is not available.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    if not configured.get("commands"):
        rows.append(
            {
                "id": "console-command-catalog-missing",
                "state": "error",
                "badge": "cmd",
                "title": "Command catalog missing",
                "detail": "Command Center needs the operator command catalog to prove dashboard routes and command-palette coverage.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    if not configured.get("workflows"):
        rows.append(
            {
                "id": "console-workflow-catalog-missing",
                "state": "warn",
                "badge": "flow",
                "title": "Workflow catalog missing",
                "detail": "Agent Loop and automation workflow exposure is not configured in the console proof.",
                "action": "open-automation-map",
                "actionLabel": "Automation",
                "requires_approval": False,
            }
        )
    if not configured.get("policy"):
        rows.append(
            {
                "id": "console-policy-evidence-missing",
                "state": "warn",
                "badge": "trust",
                "title": "Trust policy evidence missing",
                "detail": "Console readiness cannot prove permissioned autonomy without persisted trust-policy evidence.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": False,
            }
        )
    return rows[:20]


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
    configured_summary = {
        "commands": bool(configured.get("commands", bool(commands_by_id))),
        "workflows": bool(configured.get("workflows", bool(workflows))),
        "policy": bool(configured.get("policy", bool(policy))),
    }
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
    alert_rows = _console_alert_rows(section_rows, entry_rows, configured_summary)
    alert_feed_rows = _alert_feed_rows()
    critical_alert_count = len([row for row in alert_rows if row.get("state") == "error"])
    handoff_rows = _handoff_rows(
        section_ready_count=len(ready_rows),
        section_count=len(section_rows),
        entry_ready_count=len(entry_ready),
        entry_count=len(entry_rows),
        critical_alert_count=critical_alert_count,
        alert_feed_ready_count=len([row for row in alert_feed_rows if row.get("state") == "ok"]),
        alert_feed_count=len(alert_feed_rows),
        configured=configured_summary,
        policy=policy,
    )
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
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len(entry_ready),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(data_paths),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "policy_count": len(policy),
            "console_alert_count": len(alert_rows),
            "critical_console_alert_count": critical_alert_count,
            "alert_feed_count": len(alert_feed_rows),
            "alert_feed_ready_count": len([row for row in alert_feed_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
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
        "alert_rows": alert_rows,
        "alert_feed_rows": alert_feed_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "required_sections": [section["id"] for section in CONSOLE_SECTIONS],
        "configured": configured_summary,
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
