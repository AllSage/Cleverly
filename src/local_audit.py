"""Small local JSONL audit log for offline/security-relevant actions."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR


MAX_ACTION_LEN = 80
MAX_DETAIL_CHARS = 4000


def audit_path() -> Path:
    root = Path(os.getenv("DATA_DIR") or DATA_DIR) / "audit"
    root.mkdir(parents=True, exist_ok=True)
    return root / "local-audit.jsonl"


def append_audit(action: str, detail: dict[str, Any] | None = None, *, user: str = "", source: str = "app") -> dict[str, Any]:
    action = (action or "event").strip()[:MAX_ACTION_LEN]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user": (user or "")[:120],
        "source": (source or "app")[:80],
        "detail": detail or {},
    }
    text = json.dumps(record, sort_keys=True, default=str)
    if len(text) > MAX_DETAIL_CHARS:
        record["detail"] = {"truncated": True, "summary": str(detail)[:MAX_DETAIL_CHARS]}
        text = json.dumps(record, sort_keys=True, default=str)
    with audit_path().open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return record


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    path = audit_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines[-max(1, min(int(limit or 100), 500)):]):
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out
