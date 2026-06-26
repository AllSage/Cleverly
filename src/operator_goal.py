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
        "endpoints": ["/api/operator/console-plan", "/api/operator/command-layer-plan", "/api/operator/experience-plan"],
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
        "endpoints": ["/api/operator/toolchain-plan", "/api/operator/ai-runtime-plan", "/api/operator/workspace-plan", "/api/operator/models", "/api/training/status", "/api/code-workspaces"],
        "paths": ["data/models/", "data/training/", "data/code_workspace/", "docker-compose.yml"],
        "proof": "Models, fine-tuning, code workspaces, documents, research, gallery/media, notes, schedules, backups, and containers are mapped from one console.",
    },
    {
        "id": "clear-visibility",
        "title": "Clear visibility",
        "badge": "audit",
        "action_id": "open-activity-preflight",
        "action_label": "Activity",
        "endpoints": ["/api/operator/activity", "/api/operator/activity-plan", "/api/operator/recovery-plan"],
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
        "endpoints": ["/api/operator/safety-plan", "/api/operator/data-plan", "/api/operator/file-ops-plan", "/api/operator/code-test-plan"],
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
        "endpoints": ["/api/operator/console-plan", "/api/operator/workspace-plan", "/api/operator/runtime-plan", "/api/operator/model-ops-plan"],
        "paths": ["data/operator_activity.json", "data/settings.json", "logs/"],
        "proof": "The main screen shows system status, models, security state, jobs, memory, tasks, calendar, code, training, and alerts.",
    },
    {
        "id": "command-layer",
        "title": "Command layer routes requests",
        "badge": "route",
        "action_id": "open-capability-map",
        "action_label": "Map",
        "endpoints": ["/api/operator/command-layer-plan", "/api/operator/routes", "/api/operator/experience-plan", "/api/operator/commands"],
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
        "endpoints": ["/api/operator/automation-plan", "/api/operator/autonomy-plan", "/api/operator/activity-plan", "/api/operator/recovery-plan", "/api/operator/build-watch-plan"],
        "paths": ["data/operator_activity.json", "data/operator_workflows.json", "data/task_runs.json"],
        "proof": "Long-running work, retries, repair plans, training jobs, and build loops are logged locally and trust-gated.",
    },
    {
        "id": "docker-runtime-reliability",
        "title": "Local-first features work in Docker runtime",
        "badge": "run",
        "action_id": "open-local-services-map",
        "action_label": "Services",
        "endpoints": ["/health", "/api/operator/services", "/api/operator/docker-runtime-plan", "/api/operator/runtime-plan", "/api/operator/ai-runtime-plan", "/api/operator/toolchain-plan"],
        "paths": ["cleverly-data:/app/data", "cleverly-logs:/app/logs", "cleverly-ollama:/root/.ollama", "docker-compose.yml"],
        "proof": "Docker health, support services, data volumes, caches, model stores, and repair gates are inspectable without running repairs.",
    },
]


RELEASE_GATE_ROWS = [
    {
        "id": "docker-runtime-started",
        "title": "Docker runtime starts",
        "badge": "docker",
        "action_id": "open-local-services-map",
        "action_label": "Services",
        "proof_key": "docker_runtime_verified",
        "endpoints": ["/health", "/api/operator/docker-runtime-plan", "/api/operator/services"],
        "paths": ["docker-compose.yml", "dist/fresh-machine-offline-smoke.json", "dist/no-network-container-smoke.json"],
        "proof": "Docker starts the full local runtime at http://127.0.0.1:7000 with app, proxy, data, logs, and support-service evidence.",
    },
    {
        "id": "command-center-default-screen",
        "title": "Command Center is the default usable screen",
        "badge": "ui",
        "action_id": "open-console-readiness-audit",
        "action_label": "Audit",
        "proof_key": "command_center_ui_verified",
        "endpoints": ["/", "/api/operator/console-plan", "/api/operator/goal-plan"],
        "paths": ["static/index.html", "static/js/commandCenter.js", "static/style.css"],
        "proof": "Opening Cleverly lands on a stable Command Center that surfaces real backend status, alerts, handoffs, activity, and next actions.",
    },
    {
        "id": "command-route-examples",
        "title": "Target commands route correctly",
        "badge": "route",
        "action_id": "open-capability-map",
        "action_label": "Map",
        "proof_key": "command_route_examples_verified",
        "endpoints": ["/api/operator/route", "/api/operator/command-layer-plan", "/api/operator/experience-plan"],
        "paths": ["data/operator_commands.json", "data/operator_workflows.json", "static/js/operatorCommands.js"],
        "proof": "The named v1 phrases route through text, palette, voice, and workflow proof before execution or approval.",
    },
    {
        "id": "permission-gates-visible",
        "title": "Permission gates are visible",
        "badge": "gate",
        "action_id": "open-autonomy-map",
        "action_label": "Autonomy",
        "proof_key": "permission_gate_ui_verified",
        "required_policies": ["approval", "network", "danger"],
        "endpoints": ["/api/operator/autonomy-plan", "/api/operator/approval-plan", "/api/operator/safety-plan"],
        "paths": ["data/operator_policy.json", "data/operator_activity.json"],
        "proof": "Risky Docker, shell, filesystem, credential, network, model, backup/restore, and policy actions show approval gates before execution.",
    },
    {
        "id": "activity-timeline-proofed",
        "title": "Activity timeline records operator work",
        "badge": "log",
        "action_id": "open-activity-preflight",
        "action_label": "Activity",
        "proof_key": "activity_timeline_verified",
        "endpoints": ["/api/operator/activity", "/api/operator/activity-plan", "/api/operator/recovery-plan"],
        "paths": ["data/operator_activity.json", "logs/"],
        "proof": "Suggested, approved, failed, retryable, and recovered operator actions show route proof, trust level, result/log metadata, retry, and recovery paths.",
    },
    {
        "id": "operator-plan-route-smokes",
        "title": "Operator-plan routes load in Docker",
        "badge": "smoke",
        "action_id": "open-operator-runbook",
        "action_label": "Runbook",
        "proof_key": "operator_route_smokes_passed",
        "endpoints": ["/api/operator/console-plan", "/api/operator/toolchain-plan", "/api/operator/goal-plan"],
        "paths": ["ci/operator_route_smoke.py", "ci/smoke-operator-routes.ps1", "ci/fresh-machine-offline-smoke.ps1", "ci/no-network-container-smoke.ps1", "dist/operator-route-smoke.json"],
        "proof": "Docker route smokes prove the main operator-plan endpoints load from the container runtime, not just from static code inspection.",
    },
    {
        "id": "focused-tests-js-checks",
        "title": "Focused tests and JavaScript checks pass",
        "badge": "test",
        "action_id": "open-operator-runbook",
        "action_label": "Runbook",
        "proof_key": "focused_tests_passed",
        "endpoints": ["/api/operator/goal-plan"],
        "paths": ["tests/test_core_service_coverage.py", "static/js/commandCenter.js"],
        "proof": "Focused backend tests, command-center JavaScript syntax checks, and whitespace checks pass for the v1 operator-console surface.",
    },
    {
        "id": "responsive-ui-inspection",
        "title": "Desktop and mobile UI inspected",
        "badge": "view",
        "action_id": "open-console-readiness-audit",
        "action_label": "Audit",
        "proof_key": "ui_inspection_passed",
        "endpoints": ["/", "/api/operator/console-plan"],
        "paths": ["static/style.css", "static/js/commandCenter.js"],
        "proof": "Browser inspection confirms the dashboard, command palette, voice entry, activity timeline, and approval surfaces are usable on desktop and mobile.",
    },
    {
        "id": "clean-commit-push",
        "title": "Work is committed and pushed cleanly",
        "badge": "git",
        "action_id": "open-cleverly-goal-prompt",
        "action_label": "Goal",
        "proof_key": "clean_commit_pushed",
        "endpoints": ["/api/operator/goal-plan"],
        "paths": [".git", "README.md", "tests/test_core_service_coverage.py"],
        "proof": "The v1 work is staged intentionally, committed, pushed, and the remaining worktree state is understood before declaring completion.",
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
        "id": "workspace-proof",
        "title": "Local workspace workbench proof",
        "badge": "work",
        "action_id": "open-code-workspace-map",
        "action_label": "Workspace",
        "endpoints": ["/api/operator/workspace-plan", "/api/operator/code-test-plan", "/api/operator/document-search-plan", "/api/operator/research-plan", "/api/operator/gallery-plan", "/api/operator/file-ops-plan"],
        "paths": ["data/code_workspace/", "data/personal_docs/", "data/deep_research/", "data/gallery/", "data/uploads/"],
        "proof": "Code workspaces, tests, build watch, local document search, research reports, gallery/media, file gates, and data paths are visible as one local workbench.",
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
        "endpoints": ["/api/operator/activity-plan", "/api/operator/recovery-plan", "/api/operator/activity"],
        "paths": ["data/operator_activity.json"],
        "proof": "Executed and retryable operator work has a durable local ledger path with status, result, trust, and recovery metadata.",
    },
    {
        "id": "runtime-proof",
        "title": "Runtime and service proof",
        "badge": "run",
        "action_id": "open-local-services-map",
        "action_label": "Services",
        "endpoints": ["/health", "/api/operator/docker-runtime-plan", "/api/operator/runtime-plan", "/api/operator/services"],
        "paths": ["cleverly-data:/app/data", "cleverly-logs:/app/logs", "docker-compose.yml"],
        "proof": "Runtime health, sealed volumes, local data roots, support-service status, and repair gates are exposed without starting jobs.",
    },
]


CAPABILITY_ROWS = [
    {
        "id": "chat-reasoning",
        "title": "Chat and reasoning",
        "badge": "chat",
        "action_id": "open-command-palette",
        "action_label": "Commands",
        "endpoints": ["/api/operator/commands", "/api/operator/routes", "/api/operator/activity"],
        "paths": ["data/operator_commands.json", "data/operator_activity.json"],
        "proof": "Chat, typed requests, and command-palette routing share one local command catalog and activity ledger.",
    },
    {
        "id": "files-documents",
        "title": "Files and documents",
        "badge": "files",
        "action_id": "open-local-data-map",
        "action_label": "Data",
        "required_policies": ["danger"],
        "endpoints": ["/api/operator/data-plan", "/api/operator/file-ops-plan", "/api/operator/document-search-plan", "/api/documents/library"],
        "paths": ["data/uploads/", "data/personal_docs/", "data/operator_activity.json"],
        "proof": "File, document, upload, and local-search operations are visible before write, delete, export, or shell-capable work.",
    },
    {
        "id": "code-workspace",
        "title": "Code workspace operations",
        "badge": "code",
        "action_id": "open-code-workspace-map",
        "action_label": "Code",
        "required_policies": ["approval", "danger"],
        "endpoints": ["/api/code-workspaces", "/api/operator/code-test-plan", "/api/operator/build-watch-plan"],
        "paths": ["data/code_workspace/", "data/code-workspaces/", "cleverly-code-worker"],
        "proof": "Code Workspace, candidate tests, build-watch plans, snapshots, worker status, and approvals are mapped before commands run.",
    },
    {
        "id": "local-models",
        "title": "Local model control",
        "badge": "model",
        "action_id": "open-model-routing-map",
        "action_label": "Models",
        "endpoints": ["/api/operator/models", "/api/operator/model-ops-plan", "/api/offline-control/models/primary"],
        "paths": ["data/cleverly-primary-model.json", "data/models/", "cleverly-ollama:/root/.ollama"],
        "proof": "Primary model, local endpoints, Ollama state, model operations, and routing posture are controlled from one map.",
    },
    {
        "id": "task-monitoring",
        "title": "Task and job monitoring",
        "badge": "jobs",
        "action_id": "open-operations-queue",
        "action_label": "Queue",
        "endpoints": ["/api/tasks/runs/recent?limit=9", "/api/operator/activity-plan"],
        "paths": ["data/tasks.json", "data/task_runs.json", "data/operator_activity.json"],
        "proof": "Active, failed, retryable, and policy-blocked task runs appear in the operations queue before follow-up action.",
    },
    {
        "id": "activity-briefing",
        "title": "Summaries and activity briefing",
        "badge": "brief",
        "action_id": "summarize-today",
        "action_label": "Brief",
        "endpoints": ["/api/operator/briefing", "/api/operator/work-ops-plan", "/api/operator/change-brief", "/api/operator/workday-plan"],
        "paths": ["data/operator_activity.json", "data/tasks.json", "data/calendar.json", "data/notes.json"],
        "proof": "Today summaries and change briefs collect local tasks, calendar, memory, notes, services, and activity evidence.",
    },
    {
        "id": "automation-workflows",
        "title": "Automation workflows",
        "badge": "auto",
        "action_id": "open-automation-map",
        "action_label": "Automation",
        "required_policies": ["approval", "danger"],
        "endpoints": ["/api/operator/autonomy-plan", "/api/operator/loops-plan", "/api/operator/workflows"],
        "paths": ["data/operator_workflows.json", "data/operator_activity.json", "data/task_runs.json"],
        "proof": "Workflow catalogs, agent loops, webhook posture, handoff reports, and repeated work stay visible and approval-gated.",
    },
    {
        "id": "research-library",
        "title": "Research and local library",
        "badge": "research",
        "action_id": "open-research-preflight",
        "action_label": "Research",
        "required_policies": ["network"],
        "endpoints": ["/api/operator/research-plan", "/api/research/library?limit=20", "/api/search/config"],
        "paths": ["data/research/", "data/personal_docs/", "cleverly-searxng-data:/etc/searxng"],
        "proof": "Saved research, document library evidence, SearXNG posture, and source-gathering policy are reviewed before network use.",
    },
    {
        "id": "training-jobs",
        "title": "Training and fine-tuning jobs",
        "badge": "train",
        "action_id": "open-training-run-plan",
        "action_label": "Training",
        "required_policies": ["approval"],
        "endpoints": ["/api/training/status", "/api/operator/training-plan", "/api/operator/model-ops-plan"],
        "paths": ["data/training/", "data/training_jobs.json", "data/models/"],
        "proof": "Datasets, artifacts, LoRA readiness, candidate jobs, and model-creation boundaries are visible before training starts.",
    },
    {
        "id": "memory-profile",
        "title": "Persistent memory profile",
        "badge": "mem",
        "action_id": "open-memory-profile",
        "action_label": "Memory",
        "endpoints": ["/api/operator/memory-plan", "/api/memory", "/api/operator/profile"],
        "paths": ["data/memory.json", "data/memory_doc.md", "data/operator_profile.json"],
        "proof": "Preferences, projects, decisions, recurring tasks, model choices, and workflows stay in local memory/profile evidence.",
    },
    {
        "id": "scheduling",
        "title": "Tasks, notes, and scheduling",
        "badge": "work",
        "action_id": "open-work-preflight",
        "action_label": "Work",
        "required_policies": ["approval"],
        "endpoints": ["/api/operator/work-ops-plan", "/api/operator/workday-plan", "/api/operator/tasks-plan", "/api/operator/calendar-plan", "/api/operator/notes-plan"],
        "paths": ["data/app.db:scheduled_tasks", "data/app.db:calendar_events", "data/app.db:notes"],
        "proof": "Tasks, notes, reminders, calendar events, and note-to-task drafts are reviewed before scheduling or saving automation.",
    },
    {
        "id": "backup-recovery",
        "title": "Backups and recovery",
        "badge": "backup",
        "action_id": "prepare-backup",
        "action_label": "Backup",
        "required_policies": ["approval", "danger"],
        "endpoints": ["/api/operator/backup-plan", "/api/operator/recovery-plan", "/api/operator/activity-plan", "/api/offline-control/audit?limit=20"],
        "paths": ["data/backups/", "cleverly-data:/app/data", "cleverly-logs:/app/logs"],
        "proof": "Backup scope, restore drills, export approvals, retry records, and recovery notes are visible before data movement.",
    },
    {
        "id": "docker-services",
        "title": "Docker and support services",
        "badge": "docker",
        "action_id": "open-local-services-map",
        "action_label": "Services",
        "required_policies": ["approval", "danger"],
        "endpoints": ["/api/operator/services", "/api/operator/docker-runtime-plan", "/api/operator/repair-plan", "/api/operator/runtime-plan"],
        "paths": ["docker-compose.yml", "logs/", "cleverly-data:/app/data", "cleverly-chromadb-data:/data"],
        "proof": "Docker runtime, app service, worker, Ollama, ChromaDB, SearXNG, volumes, and repair gates are inspectable before fixes.",
    },
]


IDENTITY_ROWS = [
    {
        "id": "cleverly-name",
        "title": "Name",
        "badge": "id",
        "value": "Cleverly",
        "proof": "The console identifies itself as Cleverly.",
    },
    {
        "id": "cleverly-role",
        "title": "Role",
        "badge": "role",
        "value": "Private local AI operating console",
        "proof": "Cleverly is positioned as a local operator for AI work, system operations, coding, research, scheduling, and automation.",
    },
    {
        "id": "cleverly-tone",
        "title": "Tone",
        "badge": "tone",
        "value": "Calm, direct, capable",
        "proof": "Cleverly should communicate as a practical operator rather than a generic chatbot.",
    },
    {
        "id": "cleverly-privacy",
        "title": "Privacy posture",
        "badge": "priv",
        "value": "Privacy-first and local-first",
        "proof": "Data, models, memory, tasks, files, and logs remain local unless network features are explicitly enabled.",
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


ENTRY_POINTS = [
    {
        "id": "dashboard",
        "entry": "dashboard",
        "title": "Goal prompt dashboard route",
        "badge": "dash",
        "action_id": "open-cleverly-goal-prompt",
        "detail": "The Command Center can open the full operating-console goal proof as a local dashboard surface.",
    },
    {
        "id": "text-command",
        "entry": "text",
        "title": "Typed goal request",
        "badge": "text",
        "action_id": "open-cleverly-goal-prompt",
        "detail": "Typed requests for Cleverly's goal or identity open the read-only goal prompt instead of executing work.",
    },
    {
        "id": "command-palette",
        "entry": "palette",
        "title": "Command palette route",
        "badge": "pal",
        "action_id": "open-command-palette",
        "detail": "The global command palette exposes the goal prompt alongside the console audit, capability map, and trust controls.",
    },
    {
        "id": "voice-command",
        "entry": "voice",
        "title": "Voice command route",
        "badge": "voice",
        "action_id": "open-voice-preflight",
        "detail": "Voice mode can route goal and identity requests through the same command layer after browser permission.",
    },
    {
        "id": "agent-workflows",
        "entry": "workflow",
        "title": "Agent workflow route",
        "badge": "flow",
        "action_id": "open-automation-map",
        "detail": "Agent workflows can hand off goal-readiness review without executing actions or changing trust policy.",
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


def _release_gate_row(
    item: dict[str, Any],
    commands: dict[str, dict[str, Any]],
    policy: dict[str, str],
    configured: dict[str, bool],
) -> dict[str, Any]:
    row = _goal_row(item, commands, policy)
    proof_key = _trim(item.get("proof_key"), 160)
    proof_ready = bool(proof_key and configured.get(proof_key))
    action_ready = bool(row.get("action_ready"))
    policy_ready = bool(row.get("policy_ready"))
    action_detail = item["action_id"] if action_ready else f"{item['action_id']}:missing"
    row["state"] = "ok" if action_ready and policy_ready and proof_ready else "warn"
    row["proof_key"] = proof_key
    row["proof_ready"] = proof_ready
    row["release_gate"] = True
    row["detail"] = "; ".join(
        [
            item["proof"],
            f"action={action_detail}",
            f"proof={proof_key}:{'present' if proof_ready else 'missing'}",
            *(
                [
                    "policy="
                    + ", ".join(
                        f"{level}:{policy.get(level, 'missing')}"
                        for level in row.get("required_policies", [])
                    )
                ]
                if row.get("required_policies")
                else []
            ),
        ]
    )
    return row


def _entry_row(entry: dict[str, Any], commands: dict[str, dict[str, Any]]) -> dict[str, Any]:
    action_id = entry["action_id"]
    action_ready = action_id in commands
    return {
        "id": entry["id"],
        "entry": entry["entry"],
        "state": "ok" if action_ready else "warn",
        "badge": entry["badge"],
        "title": entry["title"],
        "detail": f"{entry['detail']} action={action_id if action_ready else f'{action_id}:missing'}",
        "action": action_id,
        "actionLabel": "Open" if action_ready else "Review",
        "action_id": action_id,
        "action_ready": action_ready,
        "goal_api": "/api/operator/goal-plan",
        "executes": False,
        "routes_commands": False,
        "executes_commands": False,
        "starts_workflows": False,
        "starts_jobs": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
        "approves_actions": False,
    }


def _identity_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "state": "ok",
        "badge": item["badge"],
        "title": item["title"],
        "detail": f"{item['value']}; {item['proof']}",
        "value": item["value"],
        "proof": item["proof"],
        "action": "open-cleverly-goal-prompt",
        "actionLabel": "Goal",
        "executes": False,
        "routes_commands": False,
        "starts_workflows": False,
        "starts_jobs": False,
        "runs_shell": False,
        "writes_files": False,
        "uses_network": False,
        "approves_actions": False,
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
    starts_jobs: bool = False,
    starts_models: bool = False,
    starts_training: bool = False,
    runs_search: bool = False,
    runs_shell: bool = False,
    runs_docker: bool = False,
    writes_activity: bool = False,
    changes_policy: bool = False,
    approves_actions: bool = False,
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
        "changes_policy": False,
        "approves_actions": False,
        "creates_backup": False,
        "restores_data": False,
        "exports_data": False,
        "deletes_records": False,
        "restarts_services": False,
        "uses_network": False,
        "gated_operation": {
            "routes_commands": routes_commands,
            "starts_workflows": starts_workflows,
            "starts_jobs": starts_jobs,
            "starts_models": starts_models,
            "starts_training": starts_training,
            "runs_search": runs_search,
            "runs_shell": runs_shell,
            "runs_docker": runs_docker,
            "writes_activity": writes_activity,
            "changes_policy": changes_policy,
            "approves_actions": approves_actions,
            "creates_backup": creates_backup,
            "restores_data": restores_data,
            "restarts_services": restarts_services,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    requirement_rows: list[dict[str, Any]],
    release_gate_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    configured: dict[str, bool],
    policy: dict[str, str],
) -> list[dict[str, Any]]:
    ready = {row.get("id") for row in requirement_rows if row.get("state") == "ok"}
    release_ready = {row.get("id") for row in release_gate_rows if row.get("state") == "ok"}
    entry_ready = bool(entry_rows) and all(row.get("state") == "ok" for row in entry_rows)
    policy_ready = bool(configured.get("policy")) and bool(policy)
    safe_network = policy.get("network") == "ask"
    safe_danger = policy.get("danger") == "ask"
    approval_ready = policy.get("approval") == "ask"

    def ready_all(*ids: str) -> bool:
        return all(row_id in ready for row_id in ids)

    def release_all(*ids: str) -> bool:
        return all(row_id in release_ready for row_id in ids)

    return [
        _handoff_row(
            "goal-console-readiness-handoff",
            "ok" if ready_all("operator-style-ux", "situational-awareness", "console-proof") and release_all("command-center-default-screen", "responsive-ui-inspection") else "warn",
            "dash",
            "Console readiness handoff",
            "Goal gaps can hand off to the Command Center readiness audit for dashboard sections, entry routes, alert feeds, and console handoffs.",
            "open-console-readiness-audit",
            "Audit",
            target_api="/api/operator/console-plan",
        ),
        _handoff_row(
            "goal-command-layer-handoff",
            "ok" if entry_ready and ready_all("command-layer", "target-experience-proof", "chat-reasoning") and release_all("command-route-examples") else "warn",
            "route",
            "Command routing handoff",
            "Target requests and operator entry points route through the backend command layer before any command can execute.",
            "open-capability-map",
            "Commands",
            target_api="/api/operator/command-layer-plan",
            routes_commands=True,
        ),
        _handoff_row(
            "goal-autonomy-approval-handoff",
            "ok" if policy_ready and approval_ready and safe_danger and ready_all("permissioned-autonomy", "visible-permissioned-automation", "automation-workflows") and release_all("permission-gates-visible") else "warn",
            "auto",
            "Autonomy and approval handoff",
            "Permissioned autonomy, ask-first approvals, workflow starts, and queued automation route through autonomy and approval evidence.",
            "open-autonomy-map",
            "Autonomy",
            target_api="/api/operator/autonomy-plan",
            approval_command_id="request-approval-decision",
            requires_approval=True,
            starts_workflows=True,
            approves_actions=True,
        ),
        _handoff_row(
            "goal-memory-profile-handoff",
            "ok" if ready_all("unified-memory", "memory-proof", "memory-profile") else "warn",
            "mem",
            "Unified memory handoff",
            "Preferences, projects, decisions, recurring tasks, model choices, workflows, and profile gaps route through the memory plan.",
            "open-memory-profile",
            "Memory",
            target_api="/api/operator/memory-plan",
        ),
        _handoff_row(
            "goal-practical-control-handoff",
            "ok" if ready_all("practical-control", "toolchain-proof", "local-models", "training-jobs", "code-workspace") else "warn",
            "tool",
            "Practical control handoff",
            "Model control, training, code workspaces, research, documents, backup, and Docker control hand off to the Toolchain map before actions.",
            "open-operator-runbook",
            "Runbook",
            target_api="/api/operator/toolchain-plan",
            approval_command_id="request-toolchain-operation",
            requires_approval=True,
            starts_models=True,
            starts_training=True,
            runs_shell=True,
            runs_docker=True,
            uses_network=True,
        ),
        _handoff_row(
            "goal-activity-recovery-handoff",
            "ok" if ready_all("clear-visibility", "activity-proof", "activity-briefing", "backup-recovery") and release_all("activity-timeline-proofed") else "warn",
            "log",
            "Activity and recovery handoff",
            "Automated action evidence, retry metadata, rollback hints, logs, and recovery review route through the activity and recovery plans.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity-plan",
            approval_command_id="request-recovery-action",
            requires_approval=True,
            writes_activity=True,
            restores_data=True,
        ),
        _handoff_row(
            "goal-safety-boundary-handoff",
            "ok" if policy_ready and approval_ready and safe_network and safe_danger and ready_all("safety-by-default", "safety-proof", "files-documents") else "warn",
            "safe",
            "Safety boundary handoff",
            "Destructive, network, credential, filesystem, shell, Docker, backup, and recovery boundaries route through safety policy evidence.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/safety-plan",
            approval_command_id="request-network-break-glass",
            requires_approval=True,
            changes_policy=True,
            uses_network=True,
        ),
        _handoff_row(
            "goal-docker-runtime-handoff",
            "ok" if policy_ready and safe_danger and ready_all("docker-runtime-reliability", "runtime-proof", "docker-services") and release_all("docker-runtime-started", "operator-plan-route-smokes") else "warn",
            "run",
            "Docker runtime handoff",
            "Docker health, sealed volumes, support services, container status, and repair gates route through runtime and service plans.",
            "open-local-services-map",
            "Services",
            target_api="/api/operator/docker-runtime-plan",
            approval_command_id="request-container-fix",
            requires_approval=True,
            runs_docker=True,
            restarts_services=True,
        ),
        _handoff_row(
            "goal-target-experience-handoff",
            "ok" if entry_ready and ready_all("target-experience-proof", "command-layer", "operator-style-ux") and release_all("command-route-examples") else "warn",
            "target",
            "Target experience handoff",
            "Named requests like summarize today, run tests, train a model, search documents, watch builds, and prepare backups route through target proof.",
            "open-capability-map",
            "Map",
            target_api="/api/operator/experience-plan",
            routes_commands=True,
        ),
        _handoff_row(
            "goal-completion-audit-handoff",
            "ok" if len(ready) == len(requirement_rows) and len(release_ready) == len(release_gate_rows) and entry_ready else "warn",
            "goal",
            "Completion audit handoff",
            "The goal plan remains the requirement-by-requirement audit surface before claiming Cleverly is fully operating-console complete.",
            "open-cleverly-goal-prompt",
            "Goal",
            target_api="/api/operator/goal-plan",
        ),
    ]


def _goal_alert_rows(
    requirement_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    configured: dict[str, bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    critical_requirements = {
        "local-first",
        "permissioned-autonomy",
        "clear-visibility",
        "safety-by-default",
        "situational-awareness",
        "command-layer",
        "visible-permissioned-automation",
        "docker-runtime-reliability",
        "chat-reasoning",
        "files-documents",
        "code-workspace",
        "local-models",
        "task-monitoring",
        "activity-briefing",
        "automation-workflows",
        "research-library",
        "training-jobs",
        "memory-profile",
        "scheduling",
        "backup-recovery",
        "docker-services",
        "docker-runtime-started",
        "command-center-default-screen",
        "command-route-examples",
        "permission-gates-visible",
        "activity-timeline-proofed",
        "operator-plan-route-smokes",
        "focused-tests-js-checks",
        "responsive-ui-inspection",
        "clean-commit-push",
    }
    for row in requirement_rows:
        if row.get("state") == "ok":
            continue
        requirement_id = _trim(row.get("id"), 160) or "requirement"
        rows.append(
            {
                "id": f"goal-requirement-{requirement_id}",
                "state": "error" if requirement_id in critical_requirements else "warn",
                "badge": row.get("badge") or "goal",
                "title": f"Goal requirement not ready: {row.get('title') or requirement_id}",
                "detail": row.get("detail") or row.get("proof") or "Goal requirement needs review.",
                "action": row.get("action") or row.get("action_id") or "open-cleverly-goal-prompt",
                "actionLabel": row.get("actionLabel") or row.get("action_label") or "Review",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    for row in entry_rows:
        if row.get("state") == "ok":
            continue
        rows.append(
            {
                "id": f"goal-entry-{row.get('id') or 'missing'}",
                "state": "warn",
                "badge": row.get("badge") or "route",
                "title": f"Goal entry point missing: {row.get('title') or row.get('id')}",
                "detail": row.get("detail") or "Goal request route is not available.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    if not configured.get("commands"):
        rows.append(
            {
                "id": "goal-command-catalog-missing",
                "state": "error",
                "badge": "cmd",
                "title": "Goal command catalog missing",
                "detail": "Goal readiness proof needs the command catalog to verify dashboard, text, palette, voice, and workflow routes.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    if not configured.get("workflows"):
        rows.append(
            {
                "id": "goal-workflow-catalog-missing",
                "state": "warn",
                "badge": "flow",
                "title": "Goal workflow catalog missing",
                "detail": "Goal readiness proof cannot confirm agent-workflow handoff without workflow catalog evidence.",
                "action": "open-automation-map",
                "actionLabel": "Automation",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    if not configured.get("policy"):
        rows.append(
            {
                "id": "goal-policy-evidence-missing",
                "state": "warn",
                "badge": "trust",
                "title": "Goal trust policy evidence missing",
                "detail": "Goal readiness proof is using defaults because persisted trust-policy evidence is missing.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": False,
                "uses_network": False,
            }
        )
    return rows[:64]


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
    configured_summary = {
        "commands": bool(configured.get("commands", bool(commands_by_id))),
        "workflows": bool(configured.get("workflows", bool(workflows))),
        "policy": bool(configured.get("policy", policy is not None)),
    }
    for release_gate in RELEASE_GATE_ROWS:
        proof_key = _trim(release_gate.get("proof_key"), 160)
        if proof_key:
            configured_summary[proof_key] = bool(configured.get(proof_key))
    principle_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in GOAL_PRINCIPLES]
    definition_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in DEFINITION_ROWS]
    release_gate_rows = [
        _release_gate_row(item, commands_by_id, normalized_policy, configured_summary)
        for item in RELEASE_GATE_ROWS
    ]
    evidence_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in EVIDENCE_ROWS]
    capability_rows = [_goal_row(item, commands_by_id, normalized_policy) for item in CAPABILITY_ROWS]
    identity_rows = [_identity_row(item) for item in IDENTITY_ROWS]
    entry_rows = [_entry_row(entry, commands_by_id) for entry in ENTRY_POINTS]
    requirement_rows = [*principle_rows, *definition_rows, *release_gate_rows, *evidence_rows, *capability_rows]
    ready_rows = [row for row in requirement_rows if row["state"] == "ok"]
    release_gate_ready = [row for row in release_gate_rows if row["state"] == "ok"]
    entry_ready = [row for row in entry_rows if row["state"] == "ok"]
    endpoint_paths = sorted(
        {
            path
            for item in [*GOAL_PRINCIPLES, *DEFINITION_ROWS, *RELEASE_GATE_ROWS, *EVIDENCE_ROWS, *CAPABILITY_ROWS]
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
    alert_rows = _goal_alert_rows(requirement_rows, entry_rows, configured_summary)
    handoff_rows = _handoff_rows(requirement_rows, release_gate_rows, entry_rows, configured_summary, normalized_policy)
    return {
        "mode": "read-only-goal-readiness-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "ok" if len(ready_rows) == len(requirement_rows) else "warn",
            "principle_count": len(principle_rows),
            "identity_count": len(identity_rows),
            "identity_ready_count": len([row for row in identity_rows if row["state"] == "ok"]),
            "definition_count": len(definition_rows),
            "release_gate_count": len(release_gate_rows),
            "release_gate_ready_count": len(release_gate_ready),
            "evidence_count": len(evidence_rows),
            "capability_count": len(capability_rows),
            "capability_ready_count": len([row for row in capability_rows if row["state"] == "ok"]),
            "requirement_count": len(requirement_rows),
            "ready_count": len(ready_rows),
            "issue_count": len(requirement_rows) - len(ready_rows),
            "endpoint_count": len(endpoint_paths),
            "data_path_count": len(DATA_PATHS),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len(entry_ready),
            "command_count": len(commands_by_id),
            "workflow_count": len(workflows or []),
            "policy_count": len(normalized_policy),
            "goal_alert_count": len(alert_rows),
            "critical_goal_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
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
        "principle_rows": principle_rows,
        "identity_rows": identity_rows,
        "definition_rows": definition_rows,
        "release_gate_rows": release_gate_rows,
        "evidence_rows": evidence_rows,
        "capability_rows": capability_rows,
        "entry_rows": entry_rows,
        "alert_rows": alert_rows,
        "handoff_rows": handoff_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "required_endpoints": endpoint_paths,
        "configured": configured_summary,
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
