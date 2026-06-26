"""Operator readiness, air-gap checks, and local activity ledger."""

from __future__ import annotations

import html
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.atomic_io import atomic_write_json
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.constants import DATA_DIR
from src.operator_activity import run_operator_activity_plan
from src.operator_ai_runtime import run_operator_ai_runtime_plan
from src.operator_approvals import run_operator_approval_plan
from src.operator_automation import run_operator_automation_plan
from src.operator_autonomy import run_operator_autonomy_plan
from src.operator_backup import run_operator_backup_plan
from src.operator_briefing import run_operator_briefing_snapshot
from src.operator_build_watch import run_operator_build_watch_plan
from src.operator_calendar import run_operator_calendar_plan
from src.operator_change_brief import run_operator_change_brief
from src.operator_code import run_operator_code_test_plan
from src.operator_command_layer import run_operator_command_layer_plan
from src.operator_command_router import resolve_operator_route, resolve_operator_route_matrix
from src.operator_checks import run_operator_checks, run_operator_service_snapshot
from src.operator_console import run_operator_console_plan
from src.operator_credentials import run_operator_credentials_plan
from src.operator_data import run_operator_data_plan
from src.operator_documents import run_operator_document_search_plan
from src.operator_docker_runtime import run_operator_docker_runtime_plan
from src.operator_experience import run_operator_experience_plan
from src.operator_file_ops import run_operator_file_ops_plan
from src.operator_gallery import run_operator_gallery_plan
from src.operator_goal import run_operator_goal_plan
from src.operator_loops import run_operator_loops_plan
from src.operator_memory import run_operator_memory_plan
from src.operator_model_ops import run_operator_model_ops_plan
from src.operator_models import run_operator_model_snapshot
from src.operator_notes import run_operator_notes_plan
from src.operator_note_tasks import run_operator_note_task_draft
from src.operator_repair import run_operator_repair_plan
from src.operator_recovery import run_operator_recovery_plan
from src.operator_research import run_operator_research_plan
from src.operator_runtime import run_operator_runtime_plan
from src.operator_safety import run_operator_safety_plan
from src.operator_services import run_operator_services_plan
from src.operator_tasks import run_operator_tasks_plan
from src.operator_tool_access import run_operator_tool_access_plan
from src.operator_toolchain import run_operator_toolchain_plan
from src.operator_training import run_operator_training_plan
from src.operator_voice import run_operator_voice_plan
from src.operator_work_ops import run_operator_work_ops_plan
from src.operator_workspace import run_operator_workspace_plan
from src.operator_workday import run_operator_workday_plan

ACTIVITY_FILE = os.path.join(DATA_DIR, "operator_activity.json")
POLICY_FILE = os.path.join(DATA_DIR, "operator_policy.json")
COMMANDS_FILE = os.path.join(DATA_DIR, "operator_commands.json")
WORKFLOWS_FILE = os.path.join(DATA_DIR, "operator_workflows.json")
MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")
MAX_ACTIVITY_RECORDS = 500
MAX_ACTIVITY_EVENTS = 60
MAX_ACTIVITY_LIST_ITEMS = 80
MAX_ACTIVITY_STRING = 4000
MAX_COMMAND_RECORDS = 400
MAX_COMMAND_KEYWORDS = 24
MAX_COMMAND_STRING = 1000
MAX_WORKFLOW_RECORDS = 200
MAX_WORKFLOW_STEPS = 24
MAX_PROFILE_STRING = 1000
_ACTIVITY_LOCK = threading.RLock()
_POLICY_LOCK = threading.RLock()
_COMMANDS_LOCK = threading.RLock()
_WORKFLOWS_LOCK = threading.RLock()
_PROFILE_LOCK = threading.RLock()
TRUST_LEVELS = ("local", "approval", "network", "danger")
TRUST_MODES = ("auto", "ask")
DEFAULT_TRUST_POLICY = {
    "local": "auto",
    "approval": "ask",
    "network": "ask",
    "danger": "ask",
}
DEFAULT_OPERATOR_PROFILE = {
    "assistant_name": "Cleverly",
    "role": "Private local AI operating console",
    "tone": "Calm, direct, capable, privacy-first, and local-first",
    "privacy_posture": "Keep data, models, memory, tasks, and files local unless network features are explicitly enabled.",
    "autonomy_posture": "Suggest, ask, execute, or auto-execute according to trust tiers and visible approval gates.",
    "current_focus": "Build Cleverly into a unified local AI operator for models, code, memory, scheduling, automation, and system operations.",
    "principles": [
        "local-first",
        "permissioned autonomy",
        "visible activity and recovery",
        "safety by default",
    ],
}
PROFILE_BUCKETS = {
    "identity": "Identity",
    "preferences": "Preferences",
    "projects": "Projects & Goals",
    "decisions": "Decisions",
    "workflows": "Workflows",
    "contacts": "Contacts",
    "tasks": "Task Memories",
    "facts": "Other Facts",
}
PROFILE_REQUIRED_BUCKETS = ("identity", "preferences", "projects", "decisions", "workflows")

DEFAULT_COMMAND_CATALOG = [
    {
        "id": "summarize-today",
        "title": "Summarize Today",
        "subtitle": "Tasks, calendar, memory, notes, and local activity",
        "category": "Operator",
        "trust": "local",
        "priority": 50,
        "keywords": ["summarize today", "today briefing", "daily summary", "today"],
    },
    {
        "id": "request-container-fix",
        "title": "Ask To Fix Container Health",
        "subtitle": "Prepare an approval-gated repair pass for unhealthy local services",
        "category": "Safety",
        "trust": "approval",
        "alwaysAsk": True,
        "priority": 50,
        "keywords": ["check containers", "fix unhealthy containers", "container health", "docker repair", "service repair"],
    },
    {
        "id": "run-tests",
        "title": "Open Code Test Plan",
        "subtitle": "Review workspace, runner, snapshots, and approval gates before running tests",
        "category": "Code",
        "trust": "local",
        "priority": 48,
        "keywords": ["run tests", "code workspace tests", "open code workspace", "test plan", "workspace tests"],
    },
    {
        "id": "open-training-run-plan",
        "title": "Open Training Run Plan",
        "subtitle": "Review dataset, tiny-model path, LoRA limits, outputs, jobs, and safety gates before training",
        "category": "Models",
        "trust": "local",
        "priority": 48,
        "keywords": ["train a small model", "train model on dataset", "train on this dataset", "training run plan", "dataset training"],
    },
    {
        "id": "request-build-watch-loop",
        "title": "Start Build Watch Loop",
        "subtitle": "Open the build loop and send the approval-gated repo request",
        "category": "Automation",
        "trust": "approval",
        "alwaysAsk": True,
        "priority": 50,
        "keywords": ["watch repo until build passes", "watch this repo until the build passes", "build until green", "build watch loop"],
    },
    {
        "id": "draft-task-from-note",
        "title": "Draft Task From Latest Note",
        "subtitle": "Review local notes and open a scheduled task draft",
        "category": "Automation",
        "trust": "local",
        "priority": 44,
        "keywords": ["create task from note", "task from note", "draft task from note", "latest note task"],
    },
    {
        "id": "search-local-documents",
        "title": "Search Local Documents",
        "subtitle": "Search indexed personal documents with local RAG and keyword fallback",
        "category": "Library",
        "trust": "local",
        "priority": 46,
        "keywords": ["search local documents", "search my documents", "local document search", "document search"],
    },
    {
        "id": "explain-changes-since-yesterday",
        "title": "Explain Changes Since Yesterday",
        "subtitle": "Review the local repo and summarize recent changes",
        "category": "Code",
        "trust": "local",
        "priority": 45,
        "keywords": ["explain changes since yesterday", "what changed since yesterday", "changed since yesterday", "repo changes"],
    },
    {
        "id": "prepare-backup",
        "title": "Open Backup Verification Plan",
        "subtitle": "Review coverage, export scope, restore drill, full snapshots, and proof before backup work",
        "category": "Safety",
        "trust": "local",
        "priority": 46,
        "keywords": ["prepare backup", "verify backup", "backup verification", "backup and verify", "restore drill"],
    },
]

DEFAULT_WORKFLOW_CATALOG = [
    {
        "id": "target-summarize-today",
        "phrase": "Summarize today.",
        "title": "Today Briefing",
        "area": "Briefing",
        "commandId": "summarize-today",
        "expectedRouteId": "summarize-today",
        "proof": "Read-only local briefing snapshot",
        "state": "ok",
    },
    {
        "id": "target-container-health",
        "phrase": "Check containers and fix anything unhealthy.",
        "title": "Approval-gated Container Repair Request",
        "area": "Services",
        "commandId": "request-container-fix",
        "approvalId": "request-container-fix",
        "expectedRouteId": "request-container-fix",
        "approvalMode": "ask",
        "proof": "Typed approval before any repair request",
        "state": "ok",
    },
    {
        "id": "target-code-tests",
        "phrase": "Open my code workspace and run the tests.",
        "title": "Code Test Plan",
        "area": "Code",
        "commandId": "run-tests",
        "expectedRouteId": "run-tests",
        "proof": "Read-only test plan before command execution",
        "state": "ok",
    },
    {
        "id": "target-training-run",
        "phrase": "Train a small model on this dataset.",
        "title": "Training Run Plan",
        "area": "Models",
        "commandId": "open-training-run-plan",
        "expectedRouteId": "open-training-run-plan",
        "proof": "Read-only training run plan before any job start",
        "state": "ok",
    },
    {
        "id": "target-build-watch",
        "phrase": "Watch this repo until the build passes.",
        "title": "Build Watch Loop Request",
        "area": "Automation",
        "commandId": "request-build-watch-loop",
        "approvalId": "request-build-watch-loop",
        "expectedRouteId": "request-build-watch-loop",
        "approvalMode": "ask",
        "proof": "Approval-gated loop before any build or file change",
        "state": "ok",
    },
    {
        "id": "target-note-task",
        "phrase": "Create a task from this note.",
        "title": "Note To Task Draft",
        "area": "Work",
        "commandId": "draft-task-from-note",
        "expectedRouteId": "draft-task-from-note",
        "proof": "Draft task surface opens before saving a scheduled task",
        "state": "ok",
    },
    {
        "id": "target-document-search",
        "phrase": "Search my local documents for this.",
        "title": "Local Document Search",
        "area": "Library",
        "commandId": "search-local-documents",
        "expectedRouteId": "search-local-documents",
        "proof": "Local RAG and keyword search route stays inside the app",
        "state": "ok",
    },
    {
        "id": "target-change-brief",
        "phrase": "Explain what changed since yesterday.",
        "title": "Change Brief",
        "area": "Code",
        "commandId": "explain-changes-since-yesterday",
        "expectedRouteId": "explain-changes-since-yesterday",
        "proof": "Read-only local change brief",
        "state": "ok",
    },
    {
        "id": "target-backup-verify",
        "phrase": "Prepare a backup and verify it.",
        "title": "Backup Verification Plan",
        "area": "Safety",
        "commandId": "prepare-backup",
        "expectedRouteId": "prepare-backup",
        "proof": "Read-only backup plan before export, restore, or file movement",
        "state": "ok",
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity_owner(request: Request) -> str:
    return get_current_user(request) or "local"


def _prefs_owner(owner: str) -> str | None:
    return None if owner == "local" else owner


def _trim_profile_string(value: Any, max_len: int = MAX_PROFILE_STRING) -> str:
    return str(value or "").strip()[:max_len]


def _trim_profile_list(value: Any, max_items: int = 12) -> list[str]:
    if isinstance(value, str):
        raw = [part.strip() for part in re.split(r"[\r\n]+", value) if part.strip()]
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    return [_trim_profile_string(item, 240) for item in raw[:max_items] if _trim_profile_string(item, 240)]


def _normalize_operator_profile(profile: Any) -> dict[str, Any]:
    raw = profile if isinstance(profile, dict) else {}
    normalized = dict(DEFAULT_OPERATOR_PROFILE)
    for key in (
        "assistant_name",
        "role",
        "tone",
        "user_alias",
        "locality",
        "response_style",
        "privacy_posture",
        "autonomy_posture",
        "current_focus",
        "default_model",
    ):
        if key in raw:
            normalized[key] = _trim_profile_string(raw.get(key))
    for key in ("principles", "projects", "workflows", "decisions"):
        if key in raw:
            normalized[key] = _trim_profile_list(raw.get(key))
    if "updated_at" in raw:
        normalized["updated_at"] = _trim_profile_string(raw.get("updated_at"), 80)
    return normalized


def _load_owner_prefs(owner: str) -> dict[str, Any]:
    try:
        from routes.prefs_routes import _load_for_user

        prefs = _load_for_user(_prefs_owner(owner))
        return prefs if isinstance(prefs, dict) else {}
    except Exception:
        return {}


def _save_owner_prefs(owner: str, prefs: dict[str, Any]) -> None:
    from routes.prefs_routes import _save_for_user

    _save_for_user(_prefs_owner(owner), prefs)


def _load_owner_memories(owner: str) -> list[dict[str, Any]]:
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    memories: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        item_owner = item.get("owner")
        if owner == "local":
            if item_owner not in (None, "", "local"):
                continue
        elif item_owner != owner:
            continue
        memories.append(item)
    return memories


def _memory_text(memory: dict[str, Any]) -> str:
    for key in ("text", "content", "summary", "title", "name", "value"):
        value = memory.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _memory_timestamp(memory: dict[str, Any]) -> float:
    for key in ("updated_at", "created_at", "timestamp", "at"):
        value = memory.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _classify_profile_memory(memory: dict[str, Any]) -> str:
    category = str(memory.get("category") or memory.get("type") or memory.get("kind") or "").lower()
    text = f"{category} {_memory_text(memory)}".lower()
    if re.search(r"\b(identity|personal|profile)\b", category) or re.search(r"\b(my name|call me|i am|i'm|i live|located in|based in)\b", text):
        return "identity"
    if re.search(r"\b(preference|preferences)\b", category) or re.search(r"\b(prefer|preference|favorite|favourite|like|dislike|default|use .* by default)\b", text):
        return "preferences"
    if re.search(r"\b(project|goal|objective)\b", category) or re.search(r"\b(project|goal|objective|building|working on|roadmap)\b", text):
        return "projects"
    if re.search(r"\b(decision|choice)\b", category) or re.search(r"\b(decided|decision|chose|chosen|going forward|use .* instead)\b", text):
        return "decisions"
    if re.search(r"\b(workflow|automation|recurring|routine)\b", category) or re.search(r"\b(workflow|automation|recurring|every day|every week|when i|always)\b", text):
        return "workflows"
    if re.search(r"\b(contact|person)\b", category) or re.search(r"@|\b(phone|email|address|contact)\b", text):
        return "contacts"
    if re.search(r"\b(task|todo|reminder)\b", category) or re.search(r"\b(task|todo|remind me|remember to|follow up|due|deadline)\b", text):
        return "tasks"
    return "facts"


def _profile_memory_summary(memories: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = {
        key: {"key": key, "label": label, "count": 0, "examples": []}
        for key, label in PROFILE_BUCKETS.items()
    }
    for memory in sorted(memories, key=_memory_timestamp, reverse=True):
        key = _classify_profile_memory(memory)
        bucket = buckets.get(key) or buckets["facts"]
        bucket["count"] += 1
        if len(bucket["examples"]) < 5:
            bucket["examples"].append({
                "id": str(memory.get("id") or ""),
                "text": _trim_profile_string(_memory_text(memory), 300),
                "category": str(memory.get("category") or "fact"),
                "source": str(memory.get("source") or ""),
                "timestamp": memory.get("updated_at") or memory.get("created_at") or memory.get("timestamp") or "",
            })
    coverage = []
    for key in PROFILE_REQUIRED_BUCKETS:
        count = buckets[key]["count"]
        coverage.append({
            "key": key,
            "label": PROFILE_BUCKETS[key],
            "count": count,
            "state": "ok" if count else "warn",
            "detail": f"{count} remembered record{'s' if count != 1 else ''}" if count else f"Seed {PROFILE_BUCKETS[key].lower()} for better operator context",
        })
    gaps = [row for row in coverage if not row["count"]]
    return {
        "total": len(memories),
        "buckets": list(buckets.values()),
        "coverage": {
            "rows": coverage,
            "complete": len(coverage) - len(gaps),
            "total": len(coverage),
            "percent": round(((len(coverage) - len(gaps)) / len(coverage)) * 100) if coverage else 100,
            "gaps": gaps,
        },
    }


def _policy_store() -> dict[str, Any]:
    try:
        with open(POLICY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            owners = data.get("owners")
            if isinstance(owners, dict):
                return {"version": 1, "owners": owners}
            if "policy" in data:
                return {"version": 1, "owners": {"local": data}}
    except FileNotFoundError:
        pass
    except Exception:
        return {"version": 1, "owners": {}}
    return {"version": 1, "owners": {}}


def _save_policy_store(store: dict[str, Any]) -> None:
    owners = store.get("owners")
    if not isinstance(owners, dict):
        owners = {}
    atomic_write_json(POLICY_FILE, {"version": 1, "owners": owners}, indent=2)


def _normalize_trust_policy(policy: Any) -> dict[str, str]:
    raw = policy if isinstance(policy, dict) else {}
    normalized = dict(DEFAULT_TRUST_POLICY)
    for level in TRUST_LEVELS:
        mode = str(raw.get(level) or normalized[level]).lower()
        normalized[level] = mode if mode in TRUST_MODES else DEFAULT_TRUST_POLICY[level]
    return normalized


def _owner_policy_record(store: dict[str, Any], owner: str) -> tuple[dict[str, Any], bool]:
    owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
    record = owners.get(owner)
    if isinstance(record, dict):
        return {
            "policy": _normalize_trust_policy(record.get("policy")),
            "updated_at": str(record.get("updated_at") or ""),
        }, True
    return {"policy": dict(DEFAULT_TRUST_POLICY), "updated_at": ""}, False


def _command_store() -> dict[str, Any]:
    try:
        with open(COMMANDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            owners = data.get("owners")
            if isinstance(owners, dict):
                return {"version": 1, "owners": owners}
            commands = data.get("commands")
            if isinstance(commands, list):
                return {"version": 1, "owners": {"local": data}}
        if isinstance(data, list):
            return {"version": 1, "owners": {"local": {"commands": data}}}
    except FileNotFoundError:
        pass
    except Exception:
        return {"version": 1, "owners": {}}
    return {"version": 1, "owners": {}}


def _save_command_store(store: dict[str, Any]) -> None:
    owners = store.get("owners")
    if not isinstance(owners, dict):
        owners = {}
    atomic_write_json(COMMANDS_FILE, {"version": 1, "owners": owners}, indent=2)


def _trim_command_string(value: Any, max_len: int = MAX_COMMAND_STRING) -> str:
    return str(value or "").strip()[:max_len]


def _trim_command_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [part.strip() for part in re.split(r"[,;\r\n]+", value) if part.strip()]
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    keywords: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _trim_command_string(item, 120)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        keywords.append(text)
        if len(keywords) >= MAX_COMMAND_KEYWORDS:
            break
    return keywords


def _normalize_command_catalog_record(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    command_id = _trim_command_string(record.get("id") or record.get("command_id"), 160)
    if not command_id:
        return None
    trust = str(record.get("trust") or "local").lower()
    if trust not in TRUST_LEVELS:
        trust = "local"
    clean: dict[str, Any] = {
        "id": command_id,
        "title": _trim_command_string(record.get("title") or command_id, 240),
        "subtitle": _trim_command_string(record.get("subtitle"), 500),
        "category": _trim_command_string(record.get("category") or "Operator", 120),
        "trust": trust,
        "alwaysAsk": bool(record.get("alwaysAsk") or record.get("always_ask")),
        "workflow": bool(record.get("workflow")),
        "keywords": _trim_command_keywords(record.get("keywords")),
    }
    priority = record.get("priority", 0)
    try:
        clean["priority"] = max(-1000, min(1000, int(priority)))
    except (TypeError, ValueError):
        clean["priority"] = 0
    return clean


def _normalize_command_catalog(commands: Any) -> list[dict[str, Any]]:
    if not isinstance(commands, list):
        raise HTTPException(400, "Command catalog must include a commands array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in commands:
        record = _normalize_command_catalog_record(item)
        if not record:
            continue
        command_id = record["id"]
        if command_id in seen:
            continue
        seen.add(command_id)
        normalized.append(record)
        if len(normalized) >= MAX_COMMAND_RECORDS:
            break
    return normalized


def _command_catalog_summary(commands: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    trust_counts = {level: 0 for level in TRUST_LEVELS}
    workflow_count = 0
    ask_first_count = 0
    for command in commands:
        category = _trim_command_string(command.get("category") or "Operator", 120) or "Operator"
        categories[category] = categories.get(category, 0) + 1
        trust = str(command.get("trust") or "local")
        trust_counts[trust if trust in TRUST_LEVELS else "local"] += 1
        if command.get("workflow"):
            workflow_count += 1
        if command.get("alwaysAsk"):
            ask_first_count += 1
    return {
        "count": len(commands),
        "categories": [
            {"category": category, "count": count}
            for category, count in sorted(categories.items(), key=lambda item: (-item[1], item[0].lower()))
        ],
        "trust_counts": trust_counts,
        "workflow_count": workflow_count,
        "ask_first_count": ask_first_count,
    }


def _owner_command_record(store: dict[str, Any], owner: str) -> tuple[dict[str, Any], bool]:
    owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
    record = owners.get(owner)
    if not isinstance(record, dict):
        commands = _normalize_command_catalog(DEFAULT_COMMAND_CATALOG)
        summary = _command_catalog_summary(commands)
        return {
            "commands": commands,
            "source": "builtin-v1-targets",
            "frontend_version": "builtin",
            "updated_at": "",
            **summary,
        }, False
    commands = _normalize_command_catalog(record.get("commands") or [])
    summary = _command_catalog_summary(commands)
    return {
        "commands": commands,
        "source": _trim_command_string(record.get("source"), 120),
        "frontend_version": _trim_command_string(record.get("frontend_version") or record.get("version"), 80),
        "updated_at": _trim_command_string(record.get("updated_at"), 80),
        **summary,
    }, True


def _workflow_store() -> dict[str, Any]:
    try:
        with open(WORKFLOWS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            owners = data.get("owners")
            if isinstance(owners, dict):
                return {"version": 1, "owners": owners}
            if isinstance(data.get("loops"), list) or isinstance(data.get("workflows"), list):
                return {"version": 1, "owners": {"local": data}}
    except FileNotFoundError:
        pass
    except Exception:
        return {"version": 1, "owners": {}}
    return {"version": 1, "owners": {}}


def _save_workflow_store(store: dict[str, Any]) -> None:
    owners = store.get("owners")
    if not isinstance(owners, dict):
        owners = {}
    atomic_write_json(WORKFLOWS_FILE, {"version": 1, "owners": owners}, indent=2)


def _normalize_workflow_loop_record(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    loop_id = _trim_command_string(record.get("id") or record.get("loop_id"), 160)
    if not loop_id:
        return None
    max_iterations = record.get("maxIterations", record.get("max_iterations", 0))
    try:
        max_iterations_value = max(0, min(100, int(max_iterations)))
    except (TypeError, ValueError):
        max_iterations_value = 0
    return {
        "id": loop_id,
        "title": _trim_command_string(record.get("title") or loop_id, 240),
        "category": _trim_command_string(record.get("category") or "Workflow", 120),
        "mode": _trim_command_string(record.get("mode") or "Manual", 80),
        "summary": _trim_command_string(record.get("summary"), 500),
        "goal": _trim_command_string(record.get("goal"), 500),
        "check": _trim_command_string(record.get("check"), 500),
        "exit": _trim_command_string(record.get("exit"), 500),
        "maxIterations": max_iterations_value,
        "tags": _trim_command_keywords(record.get("tags")),
        "steps": _trim_command_keywords(record.get("steps"))[:MAX_WORKFLOW_STEPS],
        "actionIds": _trim_command_keywords(record.get("actionIds") or record.get("action_ids"))[:MAX_WORKFLOW_STEPS],
    }


def _normalize_workflow_route_record(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    route_id = _trim_command_string(record.get("id") or record.get("commandId") or record.get("command_id"), 160)
    if not route_id:
        return None
    state = str(record.get("state") or "warn").lower()
    if state not in {"ok", "warn", "error", "loading"}:
        state = "warn"
    trust = str(record.get("trust") or "local").lower()
    if trust not in TRUST_LEVELS:
        trust = "local"
    return {
        "id": route_id,
        "commandId": _trim_command_string(record.get("commandId") or record.get("command_id") or route_id, 160),
        "approvalId": _trim_command_string(record.get("approvalId") or record.get("approval_id"), 160),
        "expectedRouteId": _trim_command_string(record.get("expectedRouteId") or record.get("expected_route_id"), 160),
        "phrase": _trim_command_string(record.get("phrase"), 300),
        "title": _trim_command_string(record.get("title") or route_id, 240),
        "plan": _trim_command_string(record.get("plan"), 500),
        "area": _trim_command_string(record.get("area") or "Workflow", 120),
        "proof": _trim_command_string(record.get("proof"), 500),
        "routeReady": bool(record.get("routeReady") or record.get("route_ready")),
        "approvalMode": _trim_command_string(record.get("approvalMode") or record.get("approval_mode"), 80),
        "mode": _trim_command_string(record.get("mode"), 80),
        "trust": trust,
        "state": state,
        "detail": _trim_command_string(record.get("detail"), 700),
    }


def _dedupe_workflow_records(records: Any, normalizer) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        record = normalizer(item)
        if not record:
            continue
        record_id = record["id"]
        if record_id in seen:
            continue
        seen.add(record_id)
        normalized.append(record)
        if len(normalized) >= MAX_WORKFLOW_RECORDS:
            break
    return normalized


def _workflow_catalog_summary(loops: list[dict[str, Any]], workflows: list[dict[str, Any]]) -> dict[str, Any]:
    loop_categories: dict[str, int] = {}
    workflow_areas: dict[str, int] = {}
    states = {"ok": 0, "warn": 0, "error": 0, "loading": 0}
    approval_gated = 0
    for loop in loops:
        category = _trim_command_string(loop.get("category") or "Workflow", 120) or "Workflow"
        loop_categories[category] = loop_categories.get(category, 0) + 1
    for workflow in workflows:
        area = _trim_command_string(workflow.get("area") or "Workflow", 120) or "Workflow"
        workflow_areas[area] = workflow_areas.get(area, 0) + 1
        state = str(workflow.get("state") or "warn")
        states[state if state in states else "warn"] += 1
        if workflow.get("approvalId") and str(workflow.get("approvalMode") or "").lower() == "ask":
            approval_gated += 1
    return {
        "loop_count": len(loops),
        "workflow_count": len(workflows),
        "ready_count": states["ok"],
        "approval_gated_count": approval_gated,
        "states": states,
        "loop_categories": [
            {"category": category, "count": count}
            for category, count in sorted(loop_categories.items(), key=lambda item: (-item[1], item[0].lower()))
        ],
        "workflow_areas": [
            {"area": area, "count": count}
            for area, count in sorted(workflow_areas.items(), key=lambda item: (-item[1], item[0].lower()))
        ],
    }


def _owner_workflow_record(store: dict[str, Any], owner: str) -> tuple[dict[str, Any], bool]:
    owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
    record = owners.get(owner)
    if not isinstance(record, dict):
        loops: list[dict[str, Any]] = []
        workflows = _dedupe_workflow_records(DEFAULT_WORKFLOW_CATALOG, _normalize_workflow_route_record)
        return {
            "loops": loops,
            "workflows": workflows,
            "source": "builtin-v1-targets",
            "frontend_version": "builtin",
            "updated_at": "",
            **_workflow_catalog_summary(loops, workflows),
        }, False
    loops = _dedupe_workflow_records(record.get("loops") or [], _normalize_workflow_loop_record)
    workflows = _dedupe_workflow_records(record.get("workflows") or [], _normalize_workflow_route_record)
    summary = _workflow_catalog_summary(loops, workflows)
    return {
        "loops": loops,
        "workflows": workflows,
        "source": _trim_command_string(record.get("source"), 120),
        "frontend_version": _trim_command_string(record.get("frontend_version") or record.get("version"), 80),
        "updated_at": _trim_command_string(record.get("updated_at"), 80),
        **summary,
    }, True


def _activity_store() -> dict[str, Any]:
    try:
        with open(ACTIVITY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            records = data.get("records")
            if isinstance(records, list):
                return {"version": 1, "records": records}
        if isinstance(data, list):
            return {"version": 1, "records": data}
    except FileNotFoundError:
        pass
    except Exception:
        return {"version": 1, "records": []}
    return {"version": 1, "records": []}


def _save_activity_store(store: dict[str, Any]) -> None:
    records = store.get("records")
    if not isinstance(records, list):
        records = []
    atomic_write_json(ACTIVITY_FILE, {"version": 1, "records": records[:MAX_ACTIVITY_RECORDS]}, indent=2)


def _trim_activity_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_ACTIVITY_STRING]
    if depth >= 6:
        return str(value)[:MAX_ACTIVITY_STRING]
    if isinstance(value, list):
        return [_trim_activity_value(item, depth=depth + 1) for item in value[:MAX_ACTIVITY_LIST_ITEMS]]
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_ACTIVITY_LIST_ITEMS]:
            if not isinstance(key, str):
                continue
            clean[key[:120]] = _trim_activity_value(item, depth=depth + 1)
        return clean
    return str(value)[:MAX_ACTIVITY_STRING]


def _normalize_activity_record(record: Any, owner: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise HTTPException(400, "Activity record must be an object")
    clean = _trim_activity_value(record)
    if not isinstance(clean, dict):
        raise HTTPException(400, "Activity record must be an object")
    activity_id = str(clean.get("id") or clean.get("activity_id") or "").strip()
    if not activity_id:
        raise HTTPException(400, "Activity record is missing id")
    if len(activity_id) > 160:
        raise HTTPException(400, "Activity id is too long")
    now = _utc_now()
    clean["id"] = activity_id
    clean["owner"] = owner
    clean.setdefault("created_at", now)
    clean["updated_at"] = str(clean.get("updated_at") or now)
    for key in ("retryable", "deletable", "retry_requires_approval", "clear_requires_approval"):
        if key in clean:
            clean[key] = bool(clean.get(key))
    for key in ("retry_command_id", "recovery_hint", "rollback_hint"):
        if key in clean:
            clean[key] = _trim_command_string(clean.get(key), 700)
    if isinstance(clean.get("events"), list):
        clean["events"] = clean["events"][:MAX_ACTIVITY_EVENTS]
    return clean


def _activity_timestamp(record: dict[str, Any]) -> float:
    for key in ("updated_at", "created_at", "timestamp", "at"):
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _records_for_owner(store: dict[str, Any], owner: str, limit: int) -> list[dict[str, Any]]:
    records = [
        record for record in store.get("records", [])
        if isinstance(record, dict) and str(record.get("owner") or "local") == owner
    ]
    records.sort(key=_activity_timestamp, reverse=True)
    return records[:max(0, min(limit, MAX_ACTIVITY_RECORDS))]


def _operator_route_context(owner: str) -> dict[str, Any]:
    with _COMMANDS_LOCK:
        command_store = _command_store()
        command_record, command_configured = _owner_command_record(command_store, owner)
    with _WORKFLOWS_LOCK:
        workflow_store = _workflow_store()
        workflow_record, workflow_configured = _owner_workflow_record(workflow_store, owner)
    with _POLICY_LOCK:
        policy_store = _policy_store()
        policy_record, policy_configured = _owner_policy_record(policy_store, owner)
    return {
        "commands": command_record["commands"],
        "loops": workflow_record["loops"],
        "workflows": workflow_record["workflows"],
        "policy": policy_record["policy"],
        "configured": {
            "commands": command_configured,
            "workflows": workflow_configured,
            "policy": policy_configured,
        },
        "paths": {
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
        },
    }


def _route_limit(value: Any) -> int:
    try:
        return max(1, min(20, int(value or 5)))
    except (TypeError, ValueError):
        return 5


def setup_operator_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/operator",
        tags=["operator"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/checks")
    def operator_checks():
        return {"ok": True, **run_operator_checks()}

    @router.get("/services")
    def operator_services():
        return {"ok": True, **run_operator_service_snapshot()}

    @router.get("/services-plan")
    def operator_services_plan(request: Request):
        owner = _activity_owner(request)
        return {"ok": True, **run_operator_services_plan(owner)}

    @router.get("/docker-runtime-plan")
    def operator_docker_runtime_plan(request: Request):
        owner = _activity_owner(request)
        checks = run_operator_checks()
        service_snapshot = run_operator_service_snapshot()
        runtime_plan = run_operator_runtime_plan(owner)
        services_plan = run_operator_services_plan(owner, service_snapshot=service_snapshot, checks=checks)
        repair_plan = run_operator_repair_plan()
        ai_runtime_plan = run_operator_ai_runtime_plan(
            owner,
            model_snapshot=run_operator_model_snapshot(),
            model_ops_plan=run_operator_model_ops_plan(owner),
            training_plan=run_operator_training_plan(owner),
            runtime_plan=runtime_plan,
            services_plan=services_plan,
        )
        return {
            "ok": True,
            **run_operator_docker_runtime_plan(
                owner,
                runtime_plan=runtime_plan,
                services_plan=services_plan,
                repair_plan=repair_plan,
                checks=checks,
                ai_runtime_plan=ai_runtime_plan,
            ),
        }

    @router.get("/credentials-plan")
    def operator_credentials_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_credentials_plan(
                owner,
                policy=context["policy"],
            ),
        }

    @router.get("/data-plan")
    def operator_data_plan(request: Request):
        return {"ok": True, **run_operator_data_plan(_activity_owner(request))}

    @router.get("/repair-plan")
    def operator_repair_plan():
        return {"ok": True, **run_operator_repair_plan()}

    @router.get("/recovery-plan")
    def operator_recovery_plan(request: Request):
        return {"ok": True, **run_operator_recovery_plan(_activity_owner(request))}

    @router.get("/runtime-plan")
    def operator_runtime_plan(request: Request):
        return {"ok": True, **run_operator_runtime_plan(_activity_owner(request))}

    @router.get("/console-plan")
    def operator_console_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_console_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                configured=context["configured"],
            ),
        }

    @router.get("/toolchain-plan")
    def operator_toolchain_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_toolchain_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                configured=context["configured"],
            ),
        }

    @router.get("/tool-access-plan")
    def operator_tool_access_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_tool_access_plan(
                owner,
                policy=context["policy"],
            ),
        }

    @router.get("/safety-plan")
    def operator_safety_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_safety_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                configured=context["configured"],
            ),
        }

    @router.get("/goal-plan")
    def operator_goal_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_goal_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                configured=context["configured"],
            ),
        }

    @router.get("/experience-plan")
    def operator_experience_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_experience_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                configured=context["configured"],
            ),
        }

    @router.get("/note-task-draft")
    def operator_note_task_draft(request: Request, note_id: str = ""):
        return {"ok": True, **run_operator_note_task_draft(_activity_owner(request), note_id=note_id)}

    @router.get("/notes-plan")
    def operator_notes_plan(request: Request):
        return {"ok": True, **run_operator_notes_plan(_activity_owner(request))}

    @router.get("/calendar-plan")
    def operator_calendar_plan(request: Request):
        return {"ok": True, **run_operator_calendar_plan(_activity_owner(request))}

    @router.get("/tasks-plan")
    def operator_tasks_plan(request: Request):
        return {"ok": True, **run_operator_tasks_plan(_activity_owner(request))}

    @router.get("/work-ops-plan")
    def operator_work_ops_plan(request: Request):
        owner = _activity_owner(request)
        return {
            "ok": True,
            **run_operator_work_ops_plan(
                owner,
                briefing_plan=run_operator_briefing_snapshot(owner),
                workday_plan=run_operator_workday_plan(owner),
                tasks_plan=run_operator_tasks_plan(owner),
                notes_plan=run_operator_notes_plan(owner),
                calendar_plan=run_operator_calendar_plan(owner),
            ),
        }

    @router.get("/change-brief")
    def operator_change_brief(request: Request, since: str = "yesterday"):
        return {"ok": True, **run_operator_change_brief(_activity_owner(request), since=since)}

    @router.get("/backup-plan")
    def operator_backup_plan(request: Request):
        return {"ok": True, **run_operator_backup_plan(_activity_owner(request))}

    @router.get("/code-test-plan")
    def operator_code_test_plan(request: Request):
        return {"ok": True, **run_operator_code_test_plan(_activity_owner(request))}

    @router.get("/build-watch-plan")
    def operator_build_watch_plan(request: Request):
        return {"ok": True, **run_operator_build_watch_plan(_activity_owner(request))}

    @router.get("/document-search-plan")
    def operator_document_search_plan(request: Request):
        app_state = getattr(getattr(request, "app", None), "state", None)
        personal_docs_manager = getattr(app_state, "personal_docs_manager", None)
        rag_manager = getattr(app_state, "rag_manager", None)
        return {
            "ok": True,
            **run_operator_document_search_plan(
                _activity_owner(request),
                personal_docs_manager=personal_docs_manager,
                rag_manager=rag_manager,
            ),
        }

    @router.get("/research-plan")
    def operator_research_plan(request: Request):
        return {"ok": True, **run_operator_research_plan(_activity_owner(request))}

    @router.get("/gallery-plan")
    def operator_gallery_plan(request: Request):
        return {"ok": True, **run_operator_gallery_plan(_activity_owner(request))}

    @router.get("/file-ops-plan")
    def operator_file_ops_plan(request: Request):
        return {"ok": True, **run_operator_file_ops_plan(_activity_owner(request))}

    @router.get("/workspace-plan")
    def operator_workspace_plan(request: Request):
        owner = _activity_owner(request)
        app_state = getattr(getattr(request, "app", None), "state", None)
        personal_docs_manager = getattr(app_state, "personal_docs_manager", None)
        rag_manager = getattr(app_state, "rag_manager", None)
        return {
            "ok": True,
            **run_operator_workspace_plan(
                owner,
                code_plan=run_operator_code_test_plan(owner),
                build_watch_plan=run_operator_build_watch_plan(owner),
                document_plan=run_operator_document_search_plan(
                    owner,
                    personal_docs_manager=personal_docs_manager,
                    rag_manager=rag_manager,
                ),
                research_plan=run_operator_research_plan(owner),
                gallery_plan=run_operator_gallery_plan(owner),
                file_ops_plan=run_operator_file_ops_plan(owner),
                data_plan=run_operator_data_plan(owner),
            ),
        }

    @router.get("/training-plan")
    def operator_training_plan(request: Request):
        return {"ok": True, **run_operator_training_plan(_activity_owner(request))}

    @router.get("/voice-plan")
    def operator_voice_plan(request: Request):
        return {"ok": True, **run_operator_voice_plan(_activity_owner(request))}

    @router.get("/autonomy-plan")
    def operator_autonomy_plan(request: Request, limit: int = 80):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = _records_for_owner(store, owner, limit)
        return {
            "ok": True,
            **run_operator_autonomy_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                activity=records,
                configured=context["configured"],
            ),
        }

    @router.get("/approval-plan")
    def operator_approval_plan(request: Request, limit: int = 80):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = _records_for_owner(store, owner, limit)
        return {
            "ok": True,
            **run_operator_approval_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                policy=context["policy"],
                activity=records,
                configured=context["configured"],
            ),
        }

    @router.get("/loops-plan")
    def operator_loops_plan(request: Request, limit: int = 80):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = _records_for_owner(store, owner, limit)
        return {
            "ok": True,
            **run_operator_loops_plan(
                owner,
                loops=context["loops"],
                workflows=context["workflows"],
                commands=context["commands"],
                policy=context["policy"],
                activity=records,
                configured=context["configured"],
            ),
        }

    @router.get("/automation-plan")
    def operator_automation_plan(request: Request, limit: int = 80):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = _records_for_owner(store, owner, limit)
        return {
            "ok": True,
            **run_operator_automation_plan(
                owner,
                loops=context["loops"],
                workflows=context["workflows"],
                commands=context["commands"],
                policy=context["policy"],
                activity=records,
                configured=context["configured"],
            ),
        }

    @router.get("/memory-plan")
    def operator_memory_plan(request: Request):
        owner = _activity_owner(request)
        with _PROFILE_LOCK:
            prefs = _load_owner_prefs(owner)
            memories = _load_owner_memories(owner)
        return {
            "ok": True,
            **run_operator_memory_plan(
                owner,
                memories=memories,
                prefs=prefs,
            ),
        }

    @router.get("/workday-plan")
    def operator_workday_plan(request: Request):
        return {"ok": True, **run_operator_workday_plan(_activity_owner(request))}

    @router.get("/model-ops-plan")
    def operator_model_ops_plan(request: Request):
        return {"ok": True, **run_operator_model_ops_plan(_activity_owner(request))}

    @router.get("/ai-runtime-plan")
    def operator_ai_runtime_plan(request: Request):
        owner = _activity_owner(request)
        return {
            "ok": True,
            **run_operator_ai_runtime_plan(
                owner,
                model_snapshot=run_operator_model_snapshot(),
                model_ops_plan=run_operator_model_ops_plan(owner),
                training_plan=run_operator_training_plan(owner),
                runtime_plan=run_operator_runtime_plan(owner),
                services_plan=run_operator_services_plan(owner),
            ),
        }

    @router.get("/models")
    def operator_models():
        return {"ok": True, **run_operator_model_snapshot()}

    @router.get("/briefing")
    def operator_briefing(request: Request):
        return {"ok": True, **run_operator_briefing_snapshot(_activity_owner(request))}

    @router.get("/activity-plan")
    def operator_activity_plan(request: Request, limit: int = 80):
        owner = _activity_owner(request)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = _records_for_owner(store, owner, limit)
        return {"ok": True, **run_operator_activity_plan(owner, records=records, activity_path=ACTIVITY_FILE, limit=limit)}

    @router.get("/policy")
    def operator_policy(request: Request):
        owner = _activity_owner(request)
        with _POLICY_LOCK:
            store = _policy_store()
            record, configured = _owner_policy_record(store, owner)
        return {
            "ok": True,
            "policy": record["policy"],
            "configured": configured,
            "updated_at": record.get("updated_at") or "",
            "defaults": dict(DEFAULT_TRUST_POLICY),
            "levels": list(TRUST_LEVELS),
            "modes": list(TRUST_MODES),
            "path": "data/operator_policy.json",
        }

    @router.post("/policy")
    def update_operator_policy(request: Request, body: dict[str, Any]):
        owner = _activity_owner(request)
        raw_policy = body.get("policy") if isinstance(body, dict) and "policy" in body else body
        policy = _normalize_trust_policy(raw_policy)
        record = {"policy": policy, "updated_at": _utc_now()}
        with _POLICY_LOCK:
            store = _policy_store()
            owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
            owners[owner] = record
            store["owners"] = owners
            _save_policy_store(store)
        return {
            "ok": True,
            "policy": policy,
            "configured": True,
            "updated_at": record["updated_at"],
            "path": "data/operator_policy.json",
        }

    @router.delete("/policy")
    def reset_operator_policy(request: Request):
        owner = _activity_owner(request)
        with _POLICY_LOCK:
            store = _policy_store()
            owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
            deleted = 1 if owner in owners else 0
            owners.pop(owner, None)
            store["owners"] = owners
            _save_policy_store(store)
        return {
            "ok": True,
            "deleted": deleted,
            "policy": dict(DEFAULT_TRUST_POLICY),
            "configured": False,
            "path": "data/operator_policy.json",
        }

    @router.get("/commands")
    def operator_commands(request: Request):
        owner = _activity_owner(request)
        with _COMMANDS_LOCK:
            store = _command_store()
            record, configured = _owner_command_record(store, owner)
        return {
            "ok": True,
            "owner": owner,
            "configured": configured,
            "commands": record["commands"],
            "count": record["count"],
            "categories": record["categories"],
            "trust_counts": record["trust_counts"],
            "workflow_count": record["workflow_count"],
            "ask_first_count": record["ask_first_count"],
            "source": record.get("source") or "",
            "frontend_version": record.get("frontend_version") or "",
            "updated_at": record.get("updated_at") or "",
            "limits": {
                "commands": MAX_COMMAND_RECORDS,
                "keywords": MAX_COMMAND_KEYWORDS,
            },
            "path": "data/operator_commands.json",
        }

    @router.post("/commands")
    def update_operator_commands(request: Request, body: dict[str, Any]):
        owner = _activity_owner(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "Command catalog payload must be an object")
        commands = _normalize_command_catalog(body.get("commands"))
        now = _utc_now()
        summary = _command_catalog_summary(commands)
        record = {
            "commands": commands,
            "source": _trim_command_string(body.get("source") or "browser", 120),
            "frontend_version": _trim_command_string(body.get("frontend_version") or body.get("version"), 80),
            "updated_at": now,
            **summary,
        }
        with _COMMANDS_LOCK:
            store = _command_store()
            owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
            owners[owner] = record
            store["owners"] = owners
            _save_command_store(store)
        return {
            "ok": True,
            "owner": owner,
            "configured": True,
            "commands": commands,
            "count": summary["count"],
            "categories": summary["categories"],
            "trust_counts": summary["trust_counts"],
            "workflow_count": summary["workflow_count"],
            "ask_first_count": summary["ask_first_count"],
            "source": record["source"],
            "frontend_version": record["frontend_version"],
            "updated_at": now,
            "path": "data/operator_commands.json",
        }

    @router.get("/route")
    def operator_route_query(request: Request, text: str, limit: int = 5):
        owner = _activity_owner(request)
        query = _trim_command_string(text, 1000)
        if not query:
            raise HTTPException(400, "Route text is required")
        context = _operator_route_context(owner)
        result = resolve_operator_route(
            query,
            context["commands"],
            context["workflows"],
            context["policy"],
            limit=_route_limit(limit),
        )
        return {
            "ok": True,
            "owner": owner,
            **result,
            "source_configured": context["configured"],
            "paths": context["paths"],
        }

    @router.post("/route")
    def operator_route_command(request: Request, body: dict[str, Any]):
        owner = _activity_owner(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "Route payload must be an object")
        query = _trim_command_string(body.get("text") or body.get("query") or body.get("command"), 1000)
        if not query:
            raise HTTPException(400, "Route text is required")
        context = _operator_route_context(owner)
        result = resolve_operator_route(
            query,
            context["commands"],
            context["workflows"],
            context["policy"],
            limit=_route_limit(body.get("limit")),
        )
        return {
            "ok": True,
            "owner": owner,
            **result,
            "source_configured": context["configured"],
            "paths": context["paths"],
        }

    @router.get("/routes")
    def operator_route_matrix(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        result = resolve_operator_route_matrix(
            context["commands"],
            context["workflows"],
            context["policy"],
        )
        return {
            "ok": True,
            "owner": owner,
            **result,
            "source_configured": context["configured"],
            "paths": context["paths"],
        }

    @router.get("/command-layer-plan")
    def operator_command_layer_plan(request: Request):
        owner = _activity_owner(request)
        context = _operator_route_context(owner)
        return {
            "ok": True,
            **run_operator_command_layer_plan(
                owner,
                commands=context["commands"],
                workflows=context["workflows"],
                loops=context["loops"],
                policy=context["policy"],
                configured=context["configured"],
                paths=context["paths"],
            ),
        }

    @router.get("/workflows")
    def operator_workflows(request: Request):
        owner = _activity_owner(request)
        with _WORKFLOWS_LOCK:
            store = _workflow_store()
            record, configured = _owner_workflow_record(store, owner)
        return {
            "ok": True,
            "owner": owner,
            "configured": configured,
            "loops": record["loops"],
            "workflows": record["workflows"],
            "loop_count": record["loop_count"],
            "workflow_count": record["workflow_count"],
            "ready_count": record["ready_count"],
            "approval_gated_count": record["approval_gated_count"],
            "states": record["states"],
            "loop_categories": record["loop_categories"],
            "workflow_areas": record["workflow_areas"],
            "source": record.get("source") or "",
            "frontend_version": record.get("frontend_version") or "",
            "updated_at": record.get("updated_at") or "",
            "limits": {
                "loops": MAX_WORKFLOW_RECORDS,
                "workflows": MAX_WORKFLOW_RECORDS,
                "steps": MAX_WORKFLOW_STEPS,
            },
            "path": "data/operator_workflows.json",
        }

    @router.post("/workflows")
    def update_operator_workflows(request: Request, body: dict[str, Any]):
        owner = _activity_owner(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "Workflow catalog payload must be an object")
        loops = _dedupe_workflow_records(body.get("loops"), _normalize_workflow_loop_record)
        workflows = _dedupe_workflow_records(body.get("workflows"), _normalize_workflow_route_record)
        now = _utc_now()
        summary = _workflow_catalog_summary(loops, workflows)
        record = {
            "loops": loops,
            "workflows": workflows,
            "source": _trim_command_string(body.get("source") or "browser", 120),
            "frontend_version": _trim_command_string(body.get("frontend_version") or body.get("version"), 80),
            "updated_at": now,
            **summary,
        }
        with _WORKFLOWS_LOCK:
            store = _workflow_store()
            owners = store.get("owners") if isinstance(store.get("owners"), dict) else {}
            owners[owner] = record
            store["owners"] = owners
            _save_workflow_store(store)
        return {
            "ok": True,
            "owner": owner,
            "configured": True,
            "loops": loops,
            "workflows": workflows,
            "loop_count": summary["loop_count"],
            "workflow_count": summary["workflow_count"],
            "ready_count": summary["ready_count"],
            "approval_gated_count": summary["approval_gated_count"],
            "states": summary["states"],
            "loop_categories": summary["loop_categories"],
            "workflow_areas": summary["workflow_areas"],
            "source": record["source"],
            "frontend_version": record["frontend_version"],
            "updated_at": now,
            "path": "data/operator_workflows.json",
        }

    @router.get("/profile")
    def operator_profile(request: Request):
        owner = _activity_owner(request)
        with _PROFILE_LOCK:
            prefs = _load_owner_prefs(owner)
            profile = _normalize_operator_profile(prefs.get("operator_profile"))
            memories = _load_owner_memories(owner)
            memory_summary = _profile_memory_summary(memories)
        preference_summary = {
            "memory_enabled": prefs.get("memory_enabled", True) is not False,
            "auto_memory": prefs.get("auto_memory", True) is not False,
            "skills_enabled": prefs.get("skills_enabled", True) is not False,
            "auto_skills": prefs.get("auto_skills", True) is not False,
            "default_model": prefs.get("default_model") or profile.get("default_model") or "",
            "default_endpoint_id": prefs.get("default_endpoint_id") or "",
        }
        return {
            "ok": True,
            "owner": owner,
            "profile": profile,
            "preferences": preference_summary,
            "memory": memory_summary,
            "paths": {
                "profile": "data/user_prefs.json",
                "memory": "data/memory.json",
            },
        }

    @router.post("/profile")
    def update_operator_profile(request: Request, body: dict[str, Any]):
        owner = _activity_owner(request)
        raw_profile = body.get("profile") if isinstance(body, dict) and "profile" in body else body
        profile = _normalize_operator_profile(raw_profile)
        profile["updated_at"] = _utc_now()
        with _PROFILE_LOCK:
            prefs = _load_owner_prefs(owner)
            prefs["operator_profile"] = profile
            _save_owner_prefs(owner, prefs)
        return {
            "ok": True,
            "owner": owner,
            "profile": profile,
            "path": "data/user_prefs.json",
        }

    @router.get("/activity")
    def operator_activity(request: Request, limit: int = 80):
        owner = _activity_owner(request)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = _records_for_owner(store, owner, limit)
        return {
            "ok": True,
            "activity": records,
            "count": len(records),
            "limit": max(0, min(limit, MAX_ACTIVITY_RECORDS)),
            "path": "data/operator_activity.json",
        }

    @router.post("/activity")
    def upsert_operator_activity(request: Request, body: dict[str, Any]):
        owner = _activity_owner(request)
        raw_record = body.get("record") or body.get("activity") or body
        record = _normalize_activity_record(raw_record, owner)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            records = [item for item in store.get("records", []) if isinstance(item, dict)]
            records = [
                item for item in records
                if not (str(item.get("owner") or "local") == owner and str(item.get("id") or "") == record["id"])
            ]
            records.insert(0, record)
            records.sort(key=_activity_timestamp, reverse=True)
            store["records"] = records[:MAX_ACTIVITY_RECORDS]
            _save_activity_store(store)
        return {"ok": True, "activity": record, "path": "data/operator_activity.json"}

    @router.delete("/activity/{activity_id}")
    def delete_operator_activity(activity_id: str, request: Request):
        owner = _activity_owner(request)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            before = [item for item in store.get("records", []) if isinstance(item, dict)]
            after = [
                item for item in before
                if not (str(item.get("owner") or "local") == owner and str(item.get("id") or "") == activity_id)
            ]
            store["records"] = after
            _save_activity_store(store)
        return {"ok": True, "deleted": len(before) - len(after), "path": "data/operator_activity.json"}

    @router.delete("/activity")
    def clear_operator_activity(request: Request):
        owner = _activity_owner(request)
        with _ACTIVITY_LOCK:
            store = _activity_store()
            before = [item for item in store.get("records", []) if isinstance(item, dict)]
            after = [item for item in before if str(item.get("owner") or "local") != owner]
            store["records"] = after
            _save_activity_store(store)
        return {"ok": True, "deleted": len(before) - len(after), "path": "data/operator_activity.json"}

    @router.get("/page", response_class=HTMLResponse)
    def operator_page(request: Request):
        nonce = html.escape(getattr(request.state, "csp_nonce", "") or "")
        return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cleverly Operator Status</title>
  <style>
    :root {{ color-scheme: dark; --bg:#101216; --panel:#171a20; --fg:#eef1f5; --muted:#a8b0bd; --border:#2a2f38; --ok:#49c37b; --warn:#e3b341; --fail:#ef5b5b; }}
    body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
    main {{ max-width:980px; margin:0 auto; padding:28px 18px; }}
    h1 {{ font-size:24px; margin:0 0 4px; letter-spacing:0; }}
    .sub {{ color:var(--muted); margin:0 0 20px; }}
    .summary {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
    .pill {{ border:1px solid var(--border); background:var(--panel); border-radius:8px; padding:8px 10px; font-weight:700; }}
    .checks {{ border:1px solid var(--border); border-radius:8px; overflow:hidden; background:var(--panel); }}
    .row {{ display:grid; grid-template-columns:96px minmax(170px,260px) 1fr; gap:10px; padding:11px 12px; border-top:1px solid var(--border); align-items:start; }}
    .row:first-child {{ border-top:0; }}
    .status {{ text-transform:uppercase; font-weight:800; font-size:12px; }}
    .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .fail {{ color:var(--fail); }}
    .label {{ font-weight:700; }}
    .detail {{ color:var(--muted); word-break:break-word; }}
    button {{ border:1px solid var(--border); border-radius:6px; padding:7px 10px; background:#20242c; color:var(--fg); cursor:pointer; }}
    @media(max-width:680px) {{ .row {{ grid-template-columns:1fr; gap:3px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>Cleverly Operator Status</h1>
    <p class="sub" id="policy-note">Loading checks...</p>
    <div class="summary" id="summary"></div>
    <section class="checks" id="checks"></section>
    <p><button id="refresh">Refresh</button></p>
  </main>
  <script nonce="{nonce}">
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    async function loadChecks() {{
      const res = await fetch('/api/operator/checks', {{ credentials: 'same-origin' }});
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || 'Operator checks failed');
      document.getElementById('policy-note').textContent = `strict=${{data.strict}} offline=${{data.offline}} break_glass=${{data.break_glass}}`;
      const s = data.summary || {{}};
      document.getElementById('summary').innerHTML = ['ok','warn','fail'].map(k => `<div class="pill ${{k}}">${{k}}: ${{s[k] || 0}}</div>`).join('');
      document.getElementById('checks').innerHTML = (data.checks || []).map(item => `
        <div class="row">
          <div class="status ${{esc(item.status)}}">${{esc(item.status)}}</div>
          <div class="label">${{esc(item.label)}}</div>
          <div class="detail">${{esc(item.detail)}}</div>
        </div>`).join('');
    }}
    document.getElementById('refresh').addEventListener('click', () => loadChecks().catch(err => alert(err.message)));
    loadChecks().catch(err => {{ document.getElementById('checks').innerHTML = `<div class="row"><div class="status fail">fail</div><div class="label">Status load</div><div class="detail">${{esc(err.message)}}</div></div>`; }});
  </script>
</body>
</html>""")

    return router
