"""Read-only credential and secret posture plan for Cleverly."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, FEATURES_FILE, SETTINGS_FILE
from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS
from src.settings import load_features, load_settings, offline_mode


SECRET_TERMS = ("api_key", "_key", "token", "secret", "password")
NETWORK_SECRET_KEYS = {
    "brave_api_key",
    "google_pse_key",
    "tavily_api_key",
    "serper_api_key",
    "app_public_url",
    "reminder_email_to",
}
NETWORK_FEATURE_KEYS = {
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
    "email",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _bool(value: Any) -> bool:
    return value is not False and value not in ("false", "False", "0", 0, None)


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, str]:
    normalized = dict(DEFAULT_TRUST_POLICY)
    if isinstance(policy, dict):
        for level in TRUST_LEVELS:
            mode = str(policy.get(level) or normalized[level]).lower()
            normalized[level] = mode if mode in {"auto", "ask"} else normalized[level]
    return normalized


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value not in (None, False, "")


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in SECRET_TERMS)


def _secret_setting_rows(settings: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(settings):
        if not _secret_key(key):
            continue
        has_value = _has_value(settings.get(key))
        rows.append({
            "id": key,
            "state": "warn" if has_value else "ok",
            "badge": "key",
            "title": key,
            "detail": "configured; value masked" if has_value else "not configured",
            "configured": has_value,
            "network_capable": key in NETWORK_SECRET_KEYS or "webhook" in key or "email" in key,
            "executes": False,
            "reads_secret": False,
            "requires_approval": has_value,
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        })
    return rows


def _feature_rows(features: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(NETWORK_FEATURE_KEYS | {"vault", "mcp", "sensitive_filter"}):
        enabled = _bool(features.get(key, True))
        rows.append({
            "id": key,
            "state": "warn" if key in NETWORK_FEATURE_KEYS and enabled else "ok",
            "badge": "net" if key in NETWORK_FEATURE_KEYS else "gate",
            "title": key.replace("_", " ").title(),
            "detail": "enabled" if enabled else "disabled",
            "enabled": enabled,
            "network_capable": key in NETWORK_FEATURE_KEYS,
            "executes": False,
            "reads_secret": False,
            "requires_approval": key in NETWORK_FEATURE_KEYS and enabled,
            "action": "open-offline" if key in NETWORK_FEATURE_KEYS else "open-trust-controls",
            "actionLabel": "Offline" if key in NETWORK_FEATURE_KEYS else "Trust",
        })
    return rows


def _path_row(row_id: str, path: Path, label: str, *, required: bool = False) -> dict[str, Any]:
    exists = path.exists()
    state = "ok" if exists else ("warn" if required else "loading")
    return {
        "id": row_id,
        "state": state,
        "badge": "key",
        "title": label,
        "detail": f"{path} {'exists' if exists else 'not found'}; contents not read",
        "path": str(path),
        "exists": exists,
        "required": required,
        "executes": False,
        "reads_secret": False,
        "requires_approval": exists,
        "action": "open-backup-preflight" if exists else "open-local-data-map",
        "actionLabel": "Backup" if exists else "Data",
    }


def _credential_path_rows(data_root: Path) -> list[dict[str, Any]]:
    return [
        _path_row("auth-json", data_root / "auth.json", "Auth store", required=True),
        _path_row("sessions-json", data_root / "sessions.json", "Session cache"),
        _path_row("settings-json", data_root / "settings.json", "Settings store", required=True),
        _path_row("features-json", data_root / "features.json", "Feature flag store", required=True),
        _path_row("vault-json", data_root / "vault.json", "Vault session/config"),
        _path_row("app-key", data_root / ".app_key", "Local encryption key"),
        _path_row("ssh-dir", data_root / "ssh", "Cookbook SSH material"),
    ]


def _api_action(action_id: str, method: str, path: str, *, risk: str, requires_approval: bool) -> dict[str, Any]:
    return {
        "id": action_id,
        "method": method,
        "path": path,
        "risk": risk,
        "executes": False,
        "reads_secret": False,
        "requires_approval": requires_approval,
    }


def _entry_rows(*, path_rows: list[dict[str, Any]], policy: dict[str, str]) -> list[dict[str, Any]]:
    required_ready = all(row["exists"] for row in path_rows if row.get("required"))
    state = "ok" if required_ready and policy.get("danger") == "ask" else "warn"
    common = {
        "command_id": "open-local-data-map",
        "trust_command_id": "open-trust-controls",
        "offline_command_id": "open-offline",
        "backup_command_id": "open-backup-preflight",
        "credentials_api": "/api/operator/credentials-plan",
        "vault_api": "/api/vault/unlock",
        "settings_api": "/api/auth/settings",
        "features_api": "/api/auth/features",
        "requires_approval": True,
        "executes": False,
        "reads_secrets": False,
        "returns_secret_values": False,
        "writes_credentials": False,
        "changes_settings": False,
        "unlocks_vault": False,
        "sends_email": False,
        "calls_network": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "credentials-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard credential preflight",
            "detail": "The Local Data Map opens masked credential posture before any vault unlock, settings change, email, or network-capable credential use.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            **common,
            "id": "credentials-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed credential request route",
            "detail": "Typed credential or secret requests route to masked posture and trust review without reading or returning secret values.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
        },
        {
            **common,
            "id": "credentials-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette credential route",
            "detail": "The command palette separates credential posture review from vault unlock and settings write APIs.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "credentials-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice credential route",
            "detail": "Voice mode can open credential preflight without speaking, reading, unlocking, or transmitting credential values.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "credentials-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow credential handoff",
            "detail": "Automation handoffs can review credential posture and egress policy, but credential use remains explicit and approval-gated.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _alert_rows(
    *,
    secret_rows: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    path_rows: list[dict[str, Any]],
    policy: dict[str, str],
    offline: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    configured_secret_count = len([row for row in secret_rows if row["configured"]])
    network_secret_count = len([row for row in secret_rows if row["configured"] and row["network_capable"]])
    network_feature_count = len([row for row in feature_rows if row["network_capable"] and row["enabled"]])
    vault_row = next((row for row in path_rows if row["id"] == "vault-json"), None)
    app_key_row = next((row for row in path_rows if row["id"] == "app-key"), None)
    missing_required = [row for row in path_rows if row["required"] and not row["exists"]]
    if configured_secret_count:
        rows.append({
            "id": "secret-settings-configured",
            "state": "warn",
            "badge": "key",
            "title": "Credential settings configured",
            "detail": f"{configured_secret_count} secret-like setting(s) are present; values are masked and not read by this plan.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if network_secret_count and not offline:
        rows.append({
            "id": "network-secrets-egress-enabled",
            "state": "warn",
            "badge": "net",
            "title": "Network-capable credentials with egress enabled",
            "detail": f"{network_secret_count} network-capable credential setting(s) are configured while offline mode is not active.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "requires_approval": True,
        })
    if network_feature_count and not offline:
        rows.append({
            "id": "network-credential-features-enabled",
            "state": "warn",
            "badge": "net",
            "title": "Network credential surfaces enabled",
            "detail": f"{network_feature_count} network-capable feature flag(s) are enabled; credential use should stay explicit.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "requires_approval": True,
        })
    if vault_row and vault_row["exists"]:
        rows.append({
            "id": "vault-session-config-present",
            "state": "warn",
            "badge": "vault",
            "title": "Vault config present",
            "detail": "data/vault.json exists; session values must never be copied into activity logs or chat output.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if app_key_row and app_key_row["exists"]:
        rows.append({
            "id": "local-encryption-key-present",
            "state": "warn",
            "badge": "key",
            "title": "Local encryption key present",
            "detail": "data/.app_key exists; backups should protect it separately from exported secrets.",
            "action": "open-backup-preflight",
            "actionLabel": "Backup",
            "requires_approval": True,
        })
    if missing_required:
        rows.append({
            "id": "credential-required-path-missing",
            "state": "error",
            "badge": "file",
            "title": "Required credential store path missing",
            "detail": f"{len(missing_required)} required credential metadata path(s) are not visible.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
            "requires_approval": False,
        })
    if policy["danger"] != "ask":
        rows.append({
            "id": "credential-danger-policy-not-ask",
            "state": "error",
            "badge": "risk",
            "title": "Credential actions are not danger ask-gated",
            "detail": f"High-risk trust tier is {policy['danger']}; credential and vault operations should ask by default.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    return rows[:12]


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
    approval_api: str = "",
    requires_approval: bool = False,
    network_after_approval: bool = False,
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
        "approval_api": approval_api,
        "requires_approval": requires_approval,
        "network_after_approval": network_after_approval,
        "executes": False,
        "reads_secrets": False,
        "returns_secret_values": False,
        "writes_credentials": False,
        "changes_settings": False,
        "unlocks_vault": False,
        "sends_email": False,
        "calls_network": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    configured_secret_count: int,
    network_secret_count: int,
    network_feature_count: int,
    vault_present: bool,
    app_key_present: bool,
    policy: dict[str, str],
    offline: bool,
) -> list[dict[str, Any]]:
    network_ready = offline or (network_secret_count == 0 and network_feature_count == 0)
    return [
        _handoff_row(
            "credential-masked-settings-handoff",
            "warn" if configured_secret_count else "ok",
            "key",
            "Masked settings review handoff",
            f"{configured_secret_count} configured secret-like setting(s) are visible only as metadata; values stay masked.",
            "open-trust-controls",
            "Trust",
            target_api="/api/auth/settings",
            approval_api="/api/auth/settings",
            requires_approval=bool(configured_secret_count),
        ),
        _handoff_row(
            "credential-vault-unlock-handoff",
            "warn" if vault_present else "loading",
            "vault",
            "Vault unlock handoff",
            "Vault config presence can be reviewed here, but unlock remains a separate approval-gated action that must not copy values into chat or Activity.",
            "open-trust-controls",
            "Vault",
            target_api="/api/vault/config",
            approval_api="/api/vault/unlock",
            requires_approval=True,
        ),
        _handoff_row(
            "credential-network-egress-handoff",
            "ok" if network_ready else "warn",
            "net",
            "Network credential egress handoff",
            f"{network_secret_count} network-capable credential setting(s) and {network_feature_count} network feature flag(s) require Offline Control review before egress.",
            "open-offline",
            "Offline",
            target_api="/api/offline-control/status",
            approval_api="/api/auth/features",
            requires_approval=not network_ready,
            network_after_approval=not network_ready,
        ),
        _handoff_row(
            "credential-feature-gate-handoff",
            "warn" if network_feature_count and not offline else "ok",
            "gate",
            "Feature gate handoff",
            "Network-capable features such as web search, webhooks, email, remote model endpoints, and downloads stay feature-gated and owner-reviewed.",
            "open-offline",
            "Features",
            target_api="/api/auth/features",
            approval_api="/api/auth/features",
            requires_approval=bool(network_feature_count and not offline),
            network_after_approval=bool(network_feature_count and not offline),
        ),
        _handoff_row(
            "credential-backup-key-handoff",
            "warn" if app_key_present or vault_present else "loading",
            "bak",
            "Backup and key protection handoff",
            "Auth stores, vault config, local encryption keys, and SSH material route to Backup before export, restore, or machine migration.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            approval_api="/api/offline-control/backup/export",
            requires_approval=app_key_present or vault_present,
        ),
        _handoff_row(
            "credential-activity-audit-handoff",
            "ok" if policy.get("danger") == "ask" else "error",
            "log",
            "Credential activity audit handoff",
            "Credential-adjacent actions should leave approval, result, retry, and recovery evidence without recording secret values.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity-plan",
            approval_api="/api/operator/approval-plan",
            requires_approval=True,
        ),
    ]


def run_operator_credentials_plan(
    owner: str = "local",
    *,
    settings: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    data_root: str | Path | None = None,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Return credential posture without reading or returning secret values."""
    owner = owner or "local"
    settings_data = settings if isinstance(settings, dict) else load_settings()
    feature_data = features if isinstance(features, dict) else load_features()
    normalized_policy = _normalize_policy(policy)
    root = Path(data_root) if data_root is not None else Path(os.getenv("DATA_DIR") or DATA_DIR)
    offline_active = offline_mode() if offline is None else bool(offline)
    secret_rows = _secret_setting_rows(settings_data)
    feature_rows = _feature_rows(feature_data)
    path_rows = _credential_path_rows(root)
    entry_rows = _entry_rows(path_rows=path_rows, policy=normalized_policy)
    alert_rows = _alert_rows(
        secret_rows=secret_rows,
        feature_rows=feature_rows,
        path_rows=path_rows,
        policy=normalized_policy,
        offline=offline_active,
    )
    configured_secrets = [row for row in secret_rows if row["configured"]]
    network_secrets = [row for row in configured_secrets if row["network_capable"]]
    network_features = [row for row in feature_rows if row["network_capable"] and row["enabled"]]
    existing_sensitive_paths = [row for row in path_rows if row["exists"]]
    vault_present = any(row["id"] == "vault-json" and row["exists"] for row in path_rows)
    app_key_present = any(row["id"] == "app-key" and row["exists"] for row in path_rows)
    handoff_rows = _handoff_rows(
        configured_secret_count=len(configured_secrets),
        network_secret_count=len(network_secrets),
        network_feature_count=len(network_features),
        vault_present=vault_present,
        app_key_present=app_key_present,
        policy=normalized_policy,
        offline=offline_active,
    )
    critical = [row for row in alert_rows if row["state"] == "error"]
    state = "error" if critical else ("warn" if alert_rows else "ok")
    return {
        "mode": "read-only-credential-posture-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "secret_setting_count": len(secret_rows),
            "configured_secret_count": len(configured_secrets),
            "network_secret_count": len(network_secrets),
            "network_feature_count": len(network_features),
            "sensitive_path_count": len(path_rows),
            "existing_sensitive_path_count": len(existing_sensitive_paths),
            "credential_alert_count": len(alert_rows),
            "critical_credential_alert_count": len(critical),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "offline": offline_active,
            "reads_secrets": False,
            "returns_secret_values": False,
            "writes_credentials": False,
            "changes_settings": False,
            "unlocks_vault": False,
            "sends_email": False,
            "calls_network": False,
            "runs_shell": False,
            "uses_network": False,
            "next_action": "Open Local Data Map, Offline Control, or Trust Controls to review credential posture before enabling network or vault workflows.",
        },
        "policy": normalized_policy,
        "secret_rows": secret_rows[:24],
        "feature_rows": feature_rows,
        "path_rows": path_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": [
            _api_action("credentials-plan", "GET", "/api/operator/credentials-plan", risk="read-only", requires_approval=False),
            _api_action("auth-status", "GET", "/api/auth/status", risk="read-only", requires_approval=False),
            _api_action("auth-settings", "GET", "/api/auth/settings", risk="masked-settings", requires_approval=False),
            _api_action("features", "GET", "/api/auth/features", risk="read-only", requires_approval=False),
            _api_action("vault-config", "GET", "/api/vault/config", risk="masked-vault-config", requires_approval=False),
            _api_action("vault-unlock", "POST", "/api/vault/unlock", risk="credential-unlock", requires_approval=True),
            _api_action("email-accounts", "GET", "/api/email/accounts", risk="masked-email-config", requires_approval=False),
            _api_action("calendar-config", "GET", "/api/calendar/config", risk="masked-calendar-config", requires_approval=False),
            _api_action("webhooks", "GET", "/api/webhooks", risk="masked-webhook-config", requires_approval=False),
        ],
        "approval": {
            "required": False,
            "gate": "Credential posture audit only",
            "policy": "This endpoint only reports credential metadata, file presence, masked setting posture, feature flags, and approval gates. It does not read secret values, return secret values, write credentials, change settings, unlock vaults, send email, call networks, or run shell commands.",
            "disallowed_by_default": [
                "read secret values",
                "return secret values",
                "write credentials",
                "change settings",
                "unlock vault",
                "send email",
                "call network",
                "run shell",
            ],
        },
        "paths": {
            "data_root": str(root),
            "settings": str(SETTINGS_FILE),
            "features": str(FEATURES_FILE),
            "auth": str(root / "auth.json"),
            "sessions": str(root / "sessions.json"),
            "vault": str(root / "vault.json"),
            "app_key": str(root / ".app_key"),
        },
    }
