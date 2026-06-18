"""Fail-closed offline runtime policy checks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.constants import DATA_DIR
from src.settings import offline_mode


NETWORK_BREAK_GLASS_VALUE = "I_ACCEPT_NETWORK_RISK"
ONLINE_FEATURE_FLAGS = (
    "web_search",
    "web_fetch",
    "deep_research",
    "cookbook_downloads",
    "cookbook_dependency_installs",
    "cookbook_remote_servers",
    "external_model_endpoints",
    "network_integrations",
    "network_notifications",
    "webhooks",
    "mcp",
    "vault",
    "email",
)
MODEL_URL_ENVS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_URL",
    "RESEARCH_LLM_ENDPOINT",
    "EMBEDDING_URL",
)
NETWORK_COMMAND_RE = re.compile(
    r"(^|[;&|]\s*)(curl|wget|ssh|scp|sftp|ftp|nc|ncat|telnet|rsync)\b"
    r"|\bgit\s+(clone|pull|push|fetch|submodule|remote)\b"
    r"|\b(hf\s+download|ollama\s+pull)\b"
    r"|\b(pip|python\s+-m\s+pip)\s+install\b"
    r"|\b(npm|pnpm|yarn)\s+(install|add|audit|publish)\b",
    re.IGNORECASE,
)


class OfflinePolicyError(RuntimeError):
    """Raised when strict offline startup policy fails."""


@dataclass(frozen=True)
class PolicyCheck:
    id: str
    label: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def network_break_glass_enabled() -> bool:
    return os.getenv("CLEVERLY_ALLOW_NETWORK") == NETWORK_BREAK_GLASS_VALUE


def strict_policy_enabled() -> bool:
    if network_break_glass_enabled():
        return False
    return not _truthy(os.getenv("CLEVERLY_DISABLE_OFFLINE_POLICY"))


def is_local_model_url(base_url: str) -> bool:
    """Return True for loopback or Docker-service model endpoints only."""
    parsed = urlparse((base_url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}:
        return True
    if host.startswith("127."):
        return True
    # Compose service names are bare DNS labels such as "ollama" or "vllm".
    return "." not in host


def command_uses_network(command: str) -> bool:
    """Detect shell commands that commonly open outbound network connections."""
    return bool(NETWORK_COMMAND_RE.search(command or ""))


def _check(check_id: str, label: str, status: str, detail: str) -> PolicyCheck:
    return PolicyCheck(check_id, label, status, detail)


def _docker_like() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        data = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return any(marker in data for marker in ("docker", "containerd", "kubepods"))


def _enabled_online_features() -> list[str]:
    try:
        from src.settings import load_features

        features = load_features()
    except Exception:
        return ["feature load failed"]
    return [key for key in ONLINE_FEATURE_FLAGS if features.get(key) is not False]


def _external_model_envs() -> list[str]:
    external: list[str] = []
    for key in MODEL_URL_ENVS:
        value = (os.getenv(key) or "").strip()
        if value and not is_local_model_url(value):
            external.append(f"{key}={value}")
    return external


def _external_db_model_endpoints() -> list[str]:
    from core.database import ModelEndpoint, SessionLocal

    db = SessionLocal()
    try:
        rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        external = []
        for row in rows:
            base_url = getattr(row, "base_url", "") or ""
            if base_url and not is_local_model_url(base_url):
                external.append(f"{getattr(row, 'name', '') or getattr(row, 'id', '')}: {base_url}")
        return external
    finally:
        db.close()


def evaluate_offline_policy(*, include_db: bool = True) -> dict[str, Any]:
    """Return policy checks used by startup and the operator page."""
    checks: list[PolicyCheck] = []
    offline = offline_mode()
    break_glass = network_break_glass_enabled()

    if offline:
        checks.append(_check("offline-mode", "Offline mode", "ok", "CLEVERLY_OFFLINE is enabled"))
    elif break_glass:
        checks.append(_check(
            "offline-mode",
            "Offline mode",
            "warn",
            "Network break-glass is enabled with CLEVERLY_ALLOW_NETWORK",
        ))
    else:
        checks.append(_check(
            "offline-mode",
            "Offline mode",
            "fail",
            "CLEVERLY_OFFLINE is not enabled and no break-glass token is set",
        ))

    online_features = _enabled_online_features() if offline else []
    checks.append(_check(
        "online-features-hidden",
        "Online feature flags",
        "ok" if not online_features else "fail",
        "Online feature entrypoints are disabled" if not online_features else f"Still enabled: {', '.join(online_features)}",
    ))

    env_external = _external_model_envs() if offline else []
    checks.append(_check(
        "model-env-local",
        "Model URL environment",
        "ok" if not env_external else "fail",
        "Model URL env vars are blank or local-only" if not env_external else "; ".join(env_external),
    ))

    if include_db and offline:
        try:
            external_eps = _external_db_model_endpoints()
            checks.append(_check(
                "model-endpoints-local",
                "Configured model endpoints",
                "ok" if not external_eps else "fail",
                "Enabled model endpoints are local-only" if not external_eps else "; ".join(external_eps[:6]),
            ))
        except Exception as exc:
            checks.append(_check(
                "model-endpoints-local",
                "Configured model endpoints",
                "fail",
                f"Could not verify model endpoints: {type(exc).__name__}",
            ))

    bind = (os.getenv("APP_BIND") or "127.0.0.1").strip().lower()
    checks.append(_check(
        "loopback-bind",
        "Proxy bind",
        "ok" if bind in {"127.0.0.1", "localhost"} else "fail",
        f"APP_BIND={bind}",
    ))

    data_dir = Path(os.getenv("DATA_DIR") or DATA_DIR)
    checks.append(_check(
        "sealed-data-dir",
        "Data storage",
        "ok" if str(data_dir).replace("\\", "/").endswith("/app/data") else "warn",
        f"DATA_DIR={data_dir}",
    ))

    runner = (os.getenv("CODE_WORKSPACE_RUNNER") or ("worker" if _docker_like() else "in-process")).strip().lower()
    worker_dir = Path(os.getenv("CODE_WORKSPACE_WORKER_DIR") or data_dir / "code-workspaces" / ".worker")
    worker_required = _docker_like() or _truthy(os.getenv("CLEVERLY_REQUIRE_CODE_WORKER"))
    worker_ok = runner == "worker" and (worker_dir.exists() or not worker_required)
    checks.append(_check(
        "code-worker",
        "Code Workspace worker isolation",
        "ok" if worker_ok else ("fail" if worker_required else "warn"),
        f"runner={runner}; worker_dir={worker_dir}",
    ))

    summary = {
        "ok": sum(1 for item in checks if item.status == "ok"),
        "warn": sum(1 for item in checks if item.status == "warn"),
        "fail": sum(1 for item in checks if item.status == "fail"),
    }
    return {
        "strict": strict_policy_enabled(),
        "offline": offline,
        "break_glass": break_glass,
        "checks": [item.as_dict() for item in checks],
        "summary": summary,
    }


def enforce_startup_policy() -> dict[str, Any]:
    """Raise if strict offline startup policy detects a failing check."""
    report = evaluate_offline_policy(include_db=True)
    if strict_policy_enabled() and report["summary"]["fail"]:
        failures = [
            f"{item['label']}: {item['detail']}"
            for item in report["checks"]
            if item["status"] == "fail"
        ]
        raise OfflinePolicyError("Cleverly offline policy failed: " + " | ".join(failures))
    return report
