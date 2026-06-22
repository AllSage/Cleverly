"""Read-only memory readiness planning for the Cleverly operator console."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


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
PROFILE_REQUIRED = ("identity", "preferences", "projects", "decisions", "workflows")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _memory_text(memory: dict[str, Any]) -> str:
    for key in ("text", "content", "summary", "title", "name", "value"):
        value = memory.get(key)
        if value is not None and str(value).strip():
            return _trim(value, 1000)
    return ""


def _memory_category(memory: dict[str, Any]) -> str:
    return _trim(memory.get("category") or memory.get("type") or memory.get("kind") or memory.get("source"), 120).lower()


def _classify_memory(memory: dict[str, Any]) -> str:
    category = _memory_category(memory)
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


def _timestamp(record: dict[str, Any]) -> float:
    for key in ("updated_at", "created_at", "timestamp", "at"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _as_records(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in records or [] if isinstance(item, dict)]


def _api_action(
    action_id: str,
    method: str,
    path: str,
    *,
    risk: str,
    requires_approval: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "method": method,
        "path": path,
        "risk": risk,
        "executes": False,
        "requires_approval": requires_approval,
        "note": note,
    }


def run_operator_memory_plan(
    owner: str = "local",
    *,
    memories: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
    prefs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a read-only proof of local memory/profile readiness and write gates."""
    owner = owner or "local"
    memory_records = sorted(_as_records(memories), key=_timestamp, reverse=True)
    note_records = sorted(_as_records(notes), key=_timestamp, reverse=True)
    prefs = prefs if isinstance(prefs, dict) else {}

    buckets = {
        key: {"key": key, "label": label, "count": 0, "examples": []}
        for key, label in PROFILE_BUCKETS.items()
    }
    for memory in memory_records:
        key = _classify_memory(memory)
        bucket = buckets.get(key) or buckets["facts"]
        bucket["count"] += 1
        if len(bucket["examples"]) < 4:
            bucket["examples"].append({
                "id": _trim(memory.get("id"), 160),
                "text": _trim(_memory_text(memory), 260),
                "category": _trim(memory.get("category") or memory.get("source") or "memory", 80),
                "pinned": bool(memory.get("pinned") or memory.get("pin")),
            })

    coverage_rows = []
    for key in PROFILE_REQUIRED:
        bucket = buckets[key]
        count = bucket["count"]
        coverage_rows.append({
            "id": f"profile-{key}",
            "state": "ok" if count else "warn",
            "badge": key[:4],
            "title": PROFILE_BUCKETS[key],
            "detail": f"{count} remembered record{'s' if count != 1 else ''}" if count else f"Seed {PROFILE_BUCKETS[key].lower()} for stronger operator context",
            "action": "open-memory-profile" if count else "seed-memory-profile",
            "actionLabel": "Profile" if count else "Seed",
            "executes": False,
            "requires_approval": False,
        })

    gap_rows = [
        {
            "id": f"gap-{row['id']}",
            "state": "warn",
            "badge": "seed",
            "title": f"Seed {row['title']}",
            "detail": row["detail"],
            "action": "seed-memory-profile",
            "actionLabel": "Seed",
        }
        for row in coverage_rows if row["state"] != "ok"
    ]

    memory_rows = [
        {
            "id": "memory-store",
            "state": "ok" if memory_records else "warn",
            "badge": "mem",
            "title": "Memory store",
            "detail": f"{len(memory_records)} owner-scoped memory record{'s' if len(memory_records) != 1 else ''} visible",
            "action": "open-memory",
            "actionLabel": "Memory",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "pinned-memory",
            "state": "ok" if any(memory.get("pinned") or memory.get("pin") for memory in memory_records) else "loading",
            "badge": "pin",
            "title": "Pinned recall",
            "detail": f"{sum(1 for memory in memory_records if memory.get('pinned') or memory.get('pin'))} pinned memory record{'s' if sum(1 for memory in memory_records if memory.get('pinned') or memory.get('pin')) != 1 else ''}",
            "action": "open-memory",
            "actionLabel": "Pin",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "notes",
            "state": "ok" if note_records else "loading",
            "badge": "note",
            "title": "Notes bridge",
            "detail": f"{len(note_records)} local note record{'s' if len(note_records) != 1 else ''} visible to the plan",
            "action": "open-notes",
            "actionLabel": "Notes",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "profile-coverage",
            "state": "ok" if all(row["state"] == "ok" for row in coverage_rows) else "warn",
            "badge": "prof",
            "title": "Operator profile coverage",
            "detail": f"{sum(1 for row in coverage_rows if row['state'] == 'ok')}/{len(coverage_rows)} required operator profile areas covered",
            "action": "open-memory-profile",
            "actionLabel": "Profile",
            "executes": False,
            "requires_approval": False,
        },
    ]

    recall_rows = [
        {
            "id": "memory-enabled",
            "state": "ok" if prefs.get("memory_enabled", True) is not False else "warn",
            "badge": "ctx",
            "title": "Memory in chat",
            "detail": "Saved memories can be recalled in chat context" if prefs.get("memory_enabled", True) is not False else "Memory recall is disabled in preferences",
            "action": "open-memory-preflight",
            "actionLabel": "Recall",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "auto-memory",
            "state": "ok" if prefs.get("auto_memory", True) is not False else "warn",
            "badge": "auto",
            "title": "Auto memory extraction",
            "detail": "Conversation memory extraction is enabled" if prefs.get("auto_memory", True) is not False else "Conversation memory extraction is disabled",
            "action": "open-memory-preflight",
            "actionLabel": "Auto",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "skill-recall",
            "state": "ok" if prefs.get("skills_enabled", True) is not False else "warn",
            "badge": "skill",
            "title": "Skill recall",
            "detail": "Saved local skills can be injected when relevant" if prefs.get("skills_enabled", True) is not False else "Skill recall is disabled in preferences",
            "action": "open-memory-preflight",
            "actionLabel": "Skills",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "default-model",
            "state": "ok" if prefs.get("default_model") else "loading",
            "badge": "model",
            "title": "Remembered model choice",
            "detail": f"default_model={_trim(prefs.get('default_model'), 160)}" if prefs.get("default_model") else "No per-user default model preference stored",
            "action": "open-model-routing-map",
            "actionLabel": "Models",
            "executes": False,
            "requires_approval": False,
        },
    ]

    guard_rows = [
        {
            "id": "read-only-plan",
            "state": "ok",
            "badge": "plan",
            "title": "Plan does not write memory",
            "detail": "This endpoint classifies existing local memory/profile evidence only.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "manage-privilege",
            "state": "ok",
            "badge": "priv",
            "title": "Memory writes require memory privilege",
            "detail": "Add, import, pin, update, delete, and tidy routes use the memory management privilege path.",
            "action": "open-memory",
            "actionLabel": "Memory",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "llm-extraction",
            "state": "warn",
            "badge": "llm",
            "title": "Extraction and tidy can call a model",
            "detail": "Memory extract, audit, and import are separate user actions that may use the configured local or endpoint model.",
            "action": "open-memory-preflight",
            "actionLabel": "Review",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "delete-gate",
            "state": "warn",
            "badge": "del",
            "title": "Delete is separate",
            "detail": "Memory deletion is not performed by this plan and remains a specific memory item action.",
            "action": "open-memory",
            "actionLabel": "Delete",
            "executes": False,
            "requires_approval": True,
        },
    ]

    api_actions = [
        _api_action("memory-plan", "GET", "/api/operator/memory-plan", risk="read-only", requires_approval=False, note="Returns memory/profile readiness only."),
        _api_action("operator-profile", "GET", "/api/operator/profile", risk="read-only", requires_approval=False, note="Returns owner-scoped operator profile and memory coverage."),
        _api_action("memory-list", "GET", "/api/memory", risk="local-read", requires_approval=False, note="Lists owner-scoped memory records."),
        _api_action("memory-search", "POST", "/api/memory/search", risk="local-read-query", requires_approval=False, note="Searches owner-scoped memory after user supplies a query."),
        _api_action("memory-add", "POST", "/api/memory/add", risk="local-memory-write", requires_approval=True, note="Adds one memory record after user action."),
        _api_action("memory-extract", "POST", "/api/memory/extract", risk="model-assisted-memory-write", requires_approval=True, note="Extracts suggestions from a chat session."),
        _api_action("memory-audit", "POST", "/api/memory/audit", risk="model-assisted-memory-rewrite", requires_approval=True, note="Tidies memory using the configured model route."),
        _api_action("memory-import", "POST", "/api/memory/import", risk="file-read-model-assisted-memory-write", requires_approval=True, note="Reads an uploaded file and extracts memory suggestions."),
        _api_action("memory-pin", "POST", "/api/memory/{memory_id}/pin", risk="local-memory-write", requires_approval=True, note="Pins or unpins one memory record."),
        _api_action("memory-update", "PUT", "/api/memory/{memory_id}", risk="local-memory-write", requires_approval=True, note="Updates one memory record."),
        _api_action("memory-delete", "DELETE", "/api/memory/{memory_id}", risk="local-memory-delete", requires_approval=True, note="Deletes one memory record."),
        _api_action("notes-list", "GET", "/api/notes", risk="local-read", requires_approval=False, note="Lists local notes for profile/task handoff context."),
    ]

    coverage_complete = sum(1 for row in coverage_rows if row["state"] == "ok")
    pinned_count = sum(1 for memory in memory_records if memory.get("pinned") or memory.get("pin"))
    state = "ok" if memory_records and coverage_complete == len(coverage_rows) else ("warn" if memory_records else "loading")
    return {
        "mode": "read-only-memory-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "memory_count": len(memory_records),
            "note_count": len(note_records),
            "pinned_count": pinned_count,
            "profile_complete_count": coverage_complete,
            "profile_total_count": len(coverage_rows),
            "profile_gap_count": len(coverage_rows) - coverage_complete,
            "memory_enabled": prefs.get("memory_enabled", True) is not False,
            "auto_memory": prefs.get("auto_memory", True) is not False,
            "skills_enabled": prefs.get("skills_enabled", True) is not False,
            "default_model": _trim(prefs.get("default_model"), 160),
            "reads_memories": True,
            "writes_memories": False,
            "adds_memories": False,
            "imports_files": False,
            "extracts_with_model": False,
            "audits_with_model": False,
            "pins_memories": False,
            "updates_memories": False,
            "deletes_memories": False,
            "changes_notes": False,
            "runs_automation": False,
            "runs_shell": False,
            "uses_network": False,
            "network_possible_after_user_action": False,
            "next_action": "Open Memory Profile to review coverage gaps, or Seed Profile to add durable local memories with explicit user action.",
        },
        "buckets": list(buckets.values()),
        "coverage_rows": coverage_rows,
        "memory_rows": memory_rows,
        "recall_rows": recall_rows,
        "guard_rows": guard_rows,
        "gap_rows": gap_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Read-only memory/profile audit",
            "policy": "This endpoint only audits local memory/profile readiness. It does not add memories, import files, extract memories, tidy or audit memories with a model, pin memories, update memories, delete memories, edit notes, run automation, run shell commands, or use network access.",
            "disallowed_by_default": [
                "add memory",
                "import memory file",
                "extract memory",
                "tidy memory",
                "pin memory",
                "update memory",
                "delete memory",
                "edit note",
            ],
        },
        "paths": {
            "memory": "data/memory.json",
            "memory_doc": "data/memory_doc.md",
            "profile": "data/user_prefs.json",
            "skills": "data/skills",
            "skills_index": "data/skills.json",
            "memory_vectors": "data/memory_vectors",
            "notes": "sqlite:notes",
        },
    }
