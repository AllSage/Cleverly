"""Read-only activity timeline evidence for the Cleverly operator console."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAILURE_RE = ("fail", "failed", "error", "exception", "blocked")
PENDING_RE = ("pending", "approval", "running", "queued", "waiting")
RECOVERY_TERMS = (
    "backup",
    "build",
    "code",
    "container",
    "delete",
    "docker",
    "file",
    "fix",
    "model",
    "repair",
    "restore",
    "retry",
    "shell",
    "snapshot",
    "train",
    "workspace",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _load_records(path: str | Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        source = data["records"]
    elif isinstance(data, list):
        source = data
    else:
        source = []
    return [item for item in source if isinstance(item, dict)]


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


def _events(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in record.get("events", []) if isinstance(item, dict)]


def _record_text(record: dict[str, Any]) -> str:
    values = [
        record.get("id"),
        record.get("command_id"),
        record.get("title"),
        record.get("category"),
        record.get("status"),
        record.get("detail"),
        record.get("result"),
        record.get("error"),
    ]
    return " ".join(_trim(value, 240).lower() for value in values if value)


def _has_detail(record: dict[str, Any]) -> bool:
    return bool(
        _trim(record.get("detail"))
        or _trim(record.get("result"))
        or _trim(record.get("error"))
        or _events(record)
    )


def _has_trust(record: dict[str, Any]) -> bool:
    return bool(_trim(record.get("trust")) or _trim(record.get("trust_mode")))


def _is_retryable(record: dict[str, Any]) -> bool:
    command_id = _trim(record.get("command_id"), 200)
    return bool(command_id and command_id != "chat-command")


def _is_failure(record: dict[str, Any]) -> bool:
    status = _trim(record.get("status") or record.get("state"), 120).lower()
    return any(term in status for term in FAILURE_RE)


def _is_pending(record: dict[str, Any]) -> bool:
    text = f"{record.get('status') or ''} {record.get('detail') or ''}".lower()
    return any(term in text for term in PENDING_RE)


def _needs_recovery(record: dict[str, Any]) -> bool:
    text = _record_text(record)
    return _is_failure(record) or any(term in text for term in RECOVERY_TERMS)


def _state(ok: bool, empty_ok: bool = False, count: int = 0) -> str:
    if ok:
        return "ok"
    if empty_ok and count == 0:
        return "loading"
    return "warn"


def run_operator_activity_plan(
    owner: str = "local",
    *,
    records: list[dict[str, Any]] | None = None,
    activity_path: str | Path = "data/operator_activity.json",
    limit: int = 80,
) -> dict[str, Any]:
    """Return a read-only activity timeline coverage plan."""
    owner = owner or "local"
    source_records = records if records is not None else _load_records(activity_path)
    owner_records = [
        item for item in source_records
        if isinstance(item, dict) and str(item.get("owner") or "local") == owner
    ]
    owner_records.sort(key=_timestamp, reverse=True)
    visible = owner_records[:max(0, min(limit, 500))]

    record_count = len(visible)
    event_count = sum(len(_events(item)) for item in visible)
    detail_count = sum(1 for item in visible if _has_detail(item))
    trust_count = sum(1 for item in visible if _has_trust(item))
    retryable = [item for item in visible if _is_retryable(item)]
    failures = [item for item in visible if _is_failure(item)]
    pending = [item for item in visible if _is_pending(item)]
    recovery = [item for item in visible if _needs_recovery(item)]
    missing_detail = [item for item in visible if not _has_detail(item)]
    missing_trust = [item for item in visible if not _has_trust(item)]
    latest = visible[0] if visible else {}

    coverage_rows = [
        {
            "id": "ledger",
            "state": "ok" if record_count else "warn",
            "badge": "log",
            "title": "Durable activity ledger",
            "detail": f"{record_count} owner-scoped record{'s' if record_count != 1 else ''} visible in {activity_path}",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            "id": "status-result",
            "state": _state(detail_count == record_count and record_count > 0, empty_ok=True, count=record_count),
            "badge": "result",
            "title": "Status, result, and log coverage",
            "detail": f"{detail_count}/{record_count} record{'s' if record_count != 1 else ''} include detail, result, error, or event logs",
            "action": latest.get("id") and f"activity-detail:{latest['id']}" or "open-activity-preflight",
            "actionLabel": "Inspect",
        },
        {
            "id": "trust-tags",
            "state": _state(trust_count == record_count and record_count > 0, empty_ok=True, count=record_count),
            "badge": "trust",
            "title": "Trust and approval tagging",
            "detail": f"{trust_count}/{record_count} record{'s' if record_count != 1 else ''} include trust or approval mode metadata",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "id": "retry",
            "state": "ok" if retryable else ("loading" if record_count == 0 else "warn"),
            "badge": "retry",
            "title": "Retry route coverage",
            "detail": f"{len(retryable)} routed command{'s' if len(retryable) != 1 else ''} can be replayed through current trust policy",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            "id": "recovery",
            "state": "warn" if recovery else ("loading" if record_count == 0 else "ok"),
            "badge": "recover",
            "title": "Recovery and rollback prompts",
            "detail": f"{len(recovery)} record{'s' if len(recovery) != 1 else ''} need recovery, rollback, snapshot, or owner-tool review",
            "action": "open-recovery-map",
            "actionLabel": "Recovery",
        },
        {
            "id": "failures",
            "state": "error" if failures else "ok",
            "badge": "fail",
            "title": "Failure visibility",
            "detail": f"{len(failures)} failed and {len(pending)} pending/waiting record{'s' if len(pending) != 1 else ''} visible",
            "action": "open-operations-queue",
            "actionLabel": "Queue",
        },
    ]

    gap_rows = [
        *[
            {
                "id": f"missing-detail-{idx}",
                "state": "warn",
                "badge": "log",
                "title": _trim(item.get("title") or item.get("command_id") or item.get("id") or "Activity record", 160),
                "detail": "Missing detail/result/error/event evidence",
                "action": item.get("id") and f"activity-detail:{item['id']}" or "open-activity-preflight",
                "actionLabel": "Inspect",
            }
            for idx, item in enumerate(missing_detail[:6], start=1)
        ],
        *[
            {
                "id": f"missing-trust-{idx}",
                "state": "warn",
                "badge": "trust",
                "title": _trim(item.get("title") or item.get("command_id") or item.get("id") or "Activity record", 160),
                "detail": "Missing trust/approval metadata",
                "action": item.get("id") and f"activity-detail:{item['id']}" or "open-activity-preflight",
                "actionLabel": "Inspect",
            }
            for idx, item in enumerate(missing_trust[:6], start=1)
        ],
    ][:8]

    recent_rows = [
        {
            "id": _trim(item.get("id"), 160),
            "state": "error" if _is_failure(item) else ("warn" if _is_pending(item) else "ok"),
            "badge": _trim(item.get("status") or "activity", 24),
            "title": _trim(item.get("title") or item.get("command_id") or "Operator command", 160),
            "detail": _trim(item.get("detail") or item.get("result") or item.get("category") or item.get("source") or "recorded", 300),
            "action": item.get("id") and f"activity-detail:{item['id']}" or "open-activity-preflight",
            "actionLabel": "Details",
            "event_count": len(_events(item)),
            "retryable": _is_retryable(item),
            "needs_recovery": _needs_recovery(item),
        }
        for item in visible[:8]
    ]

    api_actions = [
        {
            "id": "activity-plan",
            "method": "GET",
            "path": "/api/operator/activity-plan",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "activity-list",
            "method": "GET",
            "path": "/api/operator/activity",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "activity-upsert",
            "method": "POST",
            "path": "/api/operator/activity",
            "risk": "local-ledger-write",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "activity-delete",
            "method": "DELETE",
            "path": "/api/operator/activity/{activity_id}",
            "risk": "local-ledger-delete",
            "executes": False,
            "requires_approval": True,
        },
    ]

    state = "error" if failures else ("warn" if missing_detail or missing_trust or pending else ("ok" if record_count else "loading"))
    return {
        "mode": "read-only-activity-timeline-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "record_count": record_count,
            "event_count": event_count,
            "detail_count": detail_count,
            "trust_count": trust_count,
            "retryable_count": len(retryable),
            "recovery_count": len(recovery),
            "failure_count": len(failures),
            "pending_count": len(pending),
            "missing_detail_count": len(missing_detail),
            "missing_trust_count": len(missing_trust),
            "reads_activity": True,
            "writes_activity": False,
            "deletes_activity": False,
            "retries_commands": False,
            "runs_commands": False,
            "uses_network": False,
            "next_action": "Open Activity Preflight or Recovery Map to inspect command records, gaps, retry routes, and recovery notes.",
        },
        "coverage_rows": coverage_rows,
        "gap_rows": gap_rows,
        "recent_rows": recent_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Read-only timeline audit",
            "policy": "This endpoint only audits activity timeline evidence. It does not write records, delete records, retry commands, approve actions, restore data, restart services, run shell commands, or use network access.",
            "disallowed_by_default": [
                "retry command",
                "approve command",
                "delete activity",
                "restore data",
                "restart service",
            ],
        },
        "paths": {
            "activity": str(activity_path),
            "commands": "data/operator_commands.json",
            "workflows": "data/operator_workflows.json",
            "policy": "data/operator_policy.json",
        },
    }
