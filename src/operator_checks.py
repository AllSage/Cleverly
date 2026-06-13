"""Operator-facing startup and air-gap checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR
from src.offline_policy import evaluate_offline_policy
from src.settings import get_setting


def _check(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def run_operator_checks() -> dict[str, Any]:
    policy = evaluate_offline_policy(include_db=True)
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    runner = (os.getenv("CODE_WORKSPACE_RUNNER") or "in-process").strip().lower()
    worker_dir = Path(os.getenv("CODE_WORKSPACE_WORKER_DIR") or data_dir / "code-workspaces" / ".worker")
    model_key = (get_setting("code_workspace_model_key", "") or "").strip()

    checks = list(policy["checks"])
    checks.append(_check(
        "code-model-key",
        "Code Workspace model key",
        "ok" if model_key else "warn",
        f"Configured as {model_key}" if model_key else "Not set; Code agent will refuse to run until set",
    ))
    checks.append(_check(
        "code-worker-dir-ready",
        "Code Workspace worker queue",
        "ok" if runner == "worker" and worker_dir.exists() else ("warn" if runner == "worker" else "warn"),
        f"runner={runner}; worker_dir={worker_dir}",
    ))
    summary = {
        "ok": sum(1 for item in checks if item["status"] == "ok"),
        "warn": sum(1 for item in checks if item["status"] == "warn"),
        "fail": sum(1 for item in checks if item["status"] == "fail"),
    }
    return {
        "checks": checks,
        "summary": summary,
        "strict": policy.get("strict", True),
        "offline": policy.get("offline", False),
        "break_glass": policy.get("break_glass", False),
    }
