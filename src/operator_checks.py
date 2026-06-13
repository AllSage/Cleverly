"""Operator-facing startup and air-gap checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR
from src.settings import get_setting, load_features, offline_mode


def _check(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def run_operator_checks() -> dict[str, Any]:
    features = load_features()
    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    runner = (os.getenv("CODE_WORKSPACE_RUNNER") or "in-process").strip().lower()
    worker_dir = Path(os.getenv("CODE_WORKSPACE_WORKER_DIR") or data_dir / "code-workspaces" / ".worker")
    model_key = (get_setting("code_workspace_model_key", "") or "").strip()

    checks = []
    checks.append(_check(
        "offline-mode",
        "Offline mode",
        "ok" if offline_mode() else "fail",
        "CLEVERLY_OFFLINE is enabled" if offline_mode() else "CLEVERLY_OFFLINE is not enabled",
    ))
    online_flags = [
        "web_search", "web_fetch", "deep_research", "cookbook_downloads",
        "cookbook_dependency_installs", "cookbook_remote_servers",
        "external_model_endpoints", "network_integrations", "network_notifications",
        "webhooks", "mcp", "vault", "email",
    ]
    enabled_online = [key for key in online_flags if features.get(key) is not False]
    checks.append(_check(
        "online-features-hidden",
        "Online feature flags",
        "ok" if not enabled_online else "warn",
        "Online feature entrypoints are disabled" if not enabled_online else f"Still enabled: {', '.join(enabled_online)}",
    ))
    checks.append(_check(
        "sealed-data-dir",
        "Data storage",
        "ok" if str(data_dir).replace("\\", "/").endswith("/app/data") else "warn",
        f"DATA_DIR={data_dir}",
    ))
    checks.append(_check(
        "code-worker",
        "Code Workspace command runner",
        "ok" if runner == "worker" and worker_dir.exists() else ("warn" if runner == "worker" else "warn"),
        f"runner={runner}; worker_dir={worker_dir}",
    ))
    checks.append(_check(
        "code-model-key",
        "Code Workspace model key",
        "ok" if model_key else "warn",
        f"Configured as {model_key}" if model_key else "Not set; Code agent will refuse to run until set",
    ))
    checks.append(_check(
        "loopback-bind",
        "Proxy bind",
        "ok" if (os.getenv("APP_BIND") or "127.0.0.1") in {"127.0.0.1", "localhost"} else "warn",
        f"APP_BIND={os.getenv('APP_BIND') or '127.0.0.1'}",
    ))
    summary = {
        "ok": sum(1 for item in checks if item["status"] == "ok"),
        "warn": sum(1 for item in checks if item["status"] == "warn"),
        "fail": sum(1 for item in checks if item["status"] == "fail"),
    }
    return {"checks": checks, "summary": summary}
