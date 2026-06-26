"""Read-only activity timeline evidence for the Cleverly operator console."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from importlib import util as importlib_util
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR

try:
    from core.atomic_io import atomic_write_json
except ModuleNotFoundError:
    _atomic_io_path = Path(__file__).resolve().parents[1] / "core" / "atomic_io.py"
    _atomic_io_spec = importlib_util.spec_from_file_location("_cleverly_atomic_io", _atomic_io_path)
    if _atomic_io_spec is None or _atomic_io_spec.loader is None:
        raise
    _atomic_io_module = importlib_util.module_from_spec(_atomic_io_spec)
    _atomic_io_spec.loader.exec_module(_atomic_io_module)
    atomic_write_json = _atomic_io_module.atomic_write_json


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
MAX_ACTIVITY_RECORDS = 500
MAX_ACTIVITY_EVENTS = 25
MAX_ACTIVITY_STRING = 4000
MAX_ACTIVITY_LIST_ITEMS = 80
ACTIVITY_FILE = Path(DATA_DIR) / "operator_activity.json"
_ACTIVITY_LOCK = threading.RLock()


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


def _trim_write_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_ACTIVITY_STRING]
    if depth >= 6:
        return str(value)[:MAX_ACTIVITY_STRING]
    if isinstance(value, list):
        return [_trim_write_value(item, depth=depth + 1) for item in value[:MAX_ACTIVITY_LIST_ITEMS]]
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_ACTIVITY_LIST_ITEMS]:
            if isinstance(key, str):
                clean[key[:120]] = _trim_write_value(item, depth=depth + 1)
        return clean
    return str(value)[:MAX_ACTIVITY_STRING]


def _normalize_write_record(record: dict[str, Any], owner: str) -> dict[str, Any]:
    clean = _trim_write_value(record)
    if not isinstance(clean, dict):
        raise ValueError("Activity record must be an object")
    activity_id = str(clean.get("id") or clean.get("activity_id") or "").strip()
    if not activity_id:
        activity_id = f"op-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    now = _utc_now()
    clean["id"] = activity_id[:160]
    clean["owner"] = owner or "local"
    clean.setdefault("created_at", now)
    clean["updated_at"] = str(clean.get("updated_at") or now)
    if isinstance(clean.get("events"), list):
        clean["events"] = clean["events"][:MAX_ACTIVITY_EVENTS]
    return clean


def upsert_operator_activity_record(
    record: dict[str, Any],
    *,
    owner: str = "local",
    activity_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write or replace one local operator activity record."""
    path = Path(activity_path) if activity_path is not None else ACTIVITY_FILE
    clean = _normalize_write_record(record, owner or "local")
    with _ACTIVITY_LOCK:
        records = [item for item in _load_records(path) if isinstance(item, dict)]
        records = [
            item for item in records
            if not (
                str(item.get("owner") or "local") == clean["owner"]
                and str(item.get("id") or "") == clean["id"]
            )
        ]
        records.insert(0, clean)
        records.sort(key=_timestamp, reverse=True)
        atomic_write_json(path, {"version": 1, "records": records[:MAX_ACTIVITY_RECORDS]}, indent=2)
    return clean


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


def _has_log_evidence(record: dict[str, Any]) -> bool:
    return bool(
        _trim(record.get("detail"))
        or _trim(record.get("result"))
        or _trim(record.get("error"))
        or _trim(record.get("stdout"))
        or _trim(record.get("stderr"))
        or _trim(record.get("run_command"))
        or record.get("exit_code") is not None
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


def _has_rollback_evidence(record: dict[str, Any]) -> bool:
    return bool(
        _trim(record.get("rollback_hint"))
        or _trim(record.get("recovery_hint"))
        or not _needs_recovery(record)
    )


def _state(ok: bool, empty_ok: bool = False, count: int = 0) -> str:
    if ok:
        return "ok"
    if empty_ok and count == 0:
        return "loading"
    return "warn"


def _activity_alert_rows(
    record_count: int,
    failures: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    missing_detail: list[dict[str, Any]],
    missing_trust: list[dict[str, Any]],
    retryable: list[dict[str, Any]],
    recovery: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if record_count < 1:
        rows.append(
            {
                "id": "activity-ledger-empty",
                "state": "warn",
                "badge": "log",
                "title": "Activity ledger is empty",
                "detail": "Run routed commands through Cleverly so status, logs, trust, retry, and recovery metadata can be captured.",
                "action": "open-command-palette",
                "actionLabel": "Commands",
                "requires_approval": False,
            }
        )
    if failures:
        first = failures[0]
        rows.append(
            {
                "id": "failed-activity-records",
                "state": "error",
                "badge": "fail",
                "title": "Failed activity records need review",
                "detail": f"{len(failures)} failed record(s); latest: {_trim(first.get('title') or first.get('command_id') or first.get('id'), 160)}.",
                "action": first.get("id") and f"activity-detail:{first['id']}" or "open-activity-preflight",
                "actionLabel": "Inspect",
                "requires_approval": False,
            }
        )
    if pending:
        first = pending[0]
        rows.append(
            {
                "id": "pending-activity-records",
                "state": "warn",
                "badge": "hold",
                "title": "Pending activity records visible",
                "detail": f"{len(pending)} record(s) are pending, queued, waiting, or approval-gated.",
                "action": first.get("id") and f"activity-detail:{first['id']}" or "open-operations-queue",
                "actionLabel": "Review",
                "requires_approval": True,
            }
        )
    if missing_detail:
        rows.append(
            {
                "id": "activity-detail-gaps",
                "state": "warn",
                "badge": "log",
                "title": "Activity records missing detail",
                "detail": f"{len(missing_detail)} record(s) lack detail, result, error, or event evidence.",
                "action": "open-activity-preflight",
                "actionLabel": "Audit",
                "requires_approval": False,
            }
        )
    if missing_trust:
        rows.append(
            {
                "id": "activity-trust-gaps",
                "state": "warn",
                "badge": "trust",
                "title": "Activity records missing trust tags",
                "detail": f"{len(missing_trust)} record(s) lack trust or approval mode metadata.",
                "action": "open-trust-controls",
                "actionLabel": "Trust",
                "requires_approval": False,
            }
        )
    if recovery:
        rows.append(
            {
                "id": "activity-recovery-review",
                "state": "warn" if not failures else "error",
                "badge": "recover",
                "title": "Recovery review available",
                "detail": f"{len(recovery)} record(s) mention recovery, rollback, retry, model, file, build, shell, or repair context.",
                "action": "open-recovery-map",
                "actionLabel": "Recovery",
                "requires_approval": False,
            }
        )
    if retryable:
        rows.append(
            {
                "id": "activity-retry-policy",
                "state": "warn",
                "badge": "retry",
                "title": "Retries use current trust policy",
                "detail": f"{len(retryable)} routed command(s) can be replayed only through current permission gates.",
                "action": "open-activity-preflight",
                "actionLabel": "Retry",
                "requires_approval": True,
            }
        )
    rows.append(
        {
            "id": "activity-delete-approval",
            "state": "warn",
            "badge": "delete",
            "title": "Activity deletion requires approval",
            "detail": "Clearing local activity records is a ledger delete and remains approval-gated.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "requires_approval": True,
            "destructive": True,
        }
    )
    return rows[:8]


def _activity_action_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records[:10]:
        record_id = _trim(record.get("id"), 160)
        retryable = _is_retryable(record)
        log_ready = _has_log_evidence(record)
        rollback_ready = _has_rollback_evidence(record)
        needs_recovery = _needs_recovery(record)
        state = "ok" if log_ready and rollback_ready else "warn"
        rows.append({
            "id": f"activity-actions-{record_id or len(rows) + 1}",
            "state": state,
            "badge": "acts",
            "title": _trim(record.get("title") or record.get("command_id") or record_id or "Activity record", 160),
            "detail": "; ".join([
                "details available",
                "copy log ready" if log_ready else "copy log missing evidence",
                "retry gated" if retryable else "no retry route",
                "rollback/recovery ready" if rollback_ready else "rollback guidance missing",
            ]),
            "activity_id": record_id,
            "detail_action": record_id and f"activity-detail:{record_id}" or "open-activity-preflight",
            "copy_log_action": record_id and log_ready and f"copy-activity-log:{record_id}" or "open-activity-preflight",
            "retry_action": record_id and retryable and f"retry-activity:{record_id}" or "",
            "recovery_action": "open-recovery-map" if needs_recovery else "open-activity-preflight",
            "log_ready": log_ready,
            "retryable": retryable,
            "rollback_ready": rollback_ready,
            "needs_recovery": needs_recovery,
            "executes": False,
            "requires_approval": retryable,
            "copy_requires_approval": False,
            "retry_requires_approval": retryable,
            "recovery_requires_approval": False,
        })
    return rows


def _activity_handoff_row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    *,
    target_api: str = "/api/operator/activity-plan",
    approval_command_id: str = "open-activity-preflight",
    requires_approval: bool = False,
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
        "writes_activity": False,
        "deletes_activity": False,
        "retries_commands": False,
        "runs_commands": False,
        "approves_actions": False,
        "restores_data": False,
        "restarts_services": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _activity_handoff_rows(
    *,
    record_count: int,
    log_evidence_count: int,
    retryable_count: int,
    recovery_count: int,
    rollback_ready_count: int,
    missing_trust_count: int,
    pending_count: int,
) -> list[dict[str, Any]]:
    return [
        _activity_handoff_row(
            "activity-details-handoff",
            "ok" if record_count else "loading",
            "info",
            "Details handoff",
            f"{record_count} owner-scoped record(s) can open a local details view without executing follow-up work.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
        ),
        _activity_handoff_row(
            "activity-copy-log-handoff",
            "ok" if log_evidence_count else ("loading" if record_count == 0 else "warn"),
            "log",
            "Copy log handoff",
            f"{log_evidence_count}/{record_count} record(s) include copyable detail, result, error, stdout/stderr, command, exit code, or event evidence.",
            "open-activity-preflight",
            "Logs",
            target_api="/api/operator/activity",
        ),
        _activity_handoff_row(
            "activity-retry-checkpoint-handoff",
            "warn" if retryable_count else ("loading" if record_count == 0 else "ok"),
            "retry",
            "Retry checkpoint handoff",
            f"{retryable_count} routed command(s) can only replay through the current command trust policy and retry checkpoint.",
            "open-activity-preflight",
            "Retry",
            target_api="/api/operator/activity",
            approval_command_id="open-trust-controls",
            requires_approval=bool(retryable_count),
        ),
        _activity_handoff_row(
            "activity-recovery-rollback-handoff",
            "warn" if recovery_count or rollback_ready_count < record_count else ("loading" if record_count == 0 else "ok"),
            "recover",
            "Recovery and rollback handoff",
            f"{recovery_count} record(s) need recovery review; {rollback_ready_count}/{record_count} record(s) already have rollback-safe posture.",
            "open-recovery-map",
            "Recovery",
            target_api="/api/operator/recovery-plan",
        ),
        _activity_handoff_row(
            "activity-ledger-write-handoff",
            "ok",
            "write",
            "Ledger write handoff",
            "Approved command surfaces may write bounded status/result/log metadata to the local activity ledger, but this audit endpoint never writes records.",
            "open-activity-handoff-report",
            "Report",
            target_api="/api/operator/activity",
        ),
        _activity_handoff_row(
            "activity-delete-clear-handoff",
            "warn" if record_count else "loading",
            "del",
            "Delete and clear handoff",
            "Deleting or clearing activity records removes local audit evidence and stays behind explicit confirmation.",
            "open-activity-preflight",
            "Delete",
            target_api="/api/operator/activity/{activity_id}",
            approval_command_id="open-trust-controls",
            requires_approval=True,
        ),
        _activity_handoff_row(
            "activity-trust-review-handoff",
            "warn" if missing_trust_count or pending_count else ("loading" if record_count == 0 else "ok"),
            "trust",
            "Trust review handoff",
            f"{missing_trust_count} record(s) lack trust metadata and {pending_count} record(s) are pending or waiting for approval.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/activity-plan",
        ),
    ]


def _entry_rows(record_count: int, action_count: int) -> list[dict[str, Any]]:
    state = "ok" if record_count or action_count else "loading"
    common = {
        "command_id": "open-activity-preflight",
        "details_command_id": "latest-details",
        "recovery_command_id": "open-recovery-map",
        "trust_command_id": "open-trust-controls",
        "palette_command_id": "open-command-palette",
        "activity_api": "/api/operator/activity",
        "activity_plan_api": "/api/operator/activity-plan",
        "delete_api": "/api/operator/activity/{activity_id}",
        "requires_approval": True,
        "ready": True,
        "executes": False,
        "writes_activity": False,
        "deletes_activity": False,
        "retries_commands": False,
        "runs_commands": False,
        "approves_actions": False,
        "restores_data": False,
        "restarts_services": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "activity-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard activity preflight",
            "detail": "The Command Center opens the local activity ledger before any retry, delete, recovery, or trust-review action.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            **common,
            "id": "activity-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed activity request route",
            "detail": "Typed requests such as show what ran, retry this, or recover a failed command route to Activity Preflight first.",
            "action": "open-activity-preflight",
            "actionLabel": "Review",
        },
        {
            **common,
            "id": "activity-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette activity route",
            "detail": "The command palette exposes activity review, recovery, and trust controls without deleting records or replaying commands.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "activity-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice activity request route",
            "detail": "Voice mode can open activity and recovery review without retrying commands, approving actions, or clearing the ledger.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "activity-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow activity handoff",
            "detail": "Automation handoffs can show ledger evidence, retry gates, recovery prompts, and rollback hints before a workflow continues.",
            "action": "open-activity-handoff-report",
            "actionLabel": "Report",
        },
    ]


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
    log_evidence = [item for item in visible if _has_log_evidence(item)]
    rollback_ready = [item for item in visible if _has_rollback_evidence(item)]
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
    alert_rows = _activity_alert_rows(
        record_count,
        failures,
        pending,
        missing_detail,
        missing_trust,
        retryable,
        recovery,
    )
    action_rows = _activity_action_rows(visible)
    entry_rows = _entry_rows(record_count, len(action_rows))
    handoff_rows = _activity_handoff_rows(
        record_count=record_count,
        log_evidence_count=len(log_evidence),
        retryable_count=len(retryable),
        recovery_count=len(recovery),
        rollback_ready_count=len(rollback_ready),
        missing_trust_count=len(missing_trust),
        pending_count=len(pending),
    )

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
            "log_evidence_count": len(log_evidence),
            "retryable_count": len(retryable),
            "rollback_ready_count": len(rollback_ready),
            "recovery_count": len(recovery),
            "failure_count": len(failures),
            "pending_count": len(pending),
            "missing_detail_count": len(missing_detail),
            "missing_trust_count": len(missing_trust),
            "activity_alert_count": len(alert_rows),
            "critical_activity_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "action_affordance_count": len(action_rows),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("ready")]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
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
        "action_rows": action_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
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
