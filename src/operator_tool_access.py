"""Read-only tool, skill, and MCP access posture plan for Cleverly."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent_tools import TOOL_TAGS
from src.constants import DATA_DIR
from src.operator_command_router import DEFAULT_TRUST_POLICY, TRUST_LEVELS
from src.settings import load_features, load_settings, offline_mode
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS


SHELL_TOOLS = {"bash", "python", "code_workspace"}
FILE_TOOLS = {
    "read_file",
    "write_file",
    "create_document",
    "edit_document",
    "update_document",
    "suggest_document",
    "manage_documents",
    "code_workspace",
    "edit_image",
}
NETWORK_TOOLS = {
    "web_search",
    "web_fetch",
    "api_call",
    "app_api",
    "trigger_research",
    "manage_research",
    "download_model",
    "serve_model",
    "search_hf_models",
    "adopt_served_model",
    "manage_endpoints",
    "manage_webhooks",
    "list_email_accounts",
    "list_emails",
    "read_email",
    "send_email",
    "reply_to_email",
    "bulk_email",
    "archive_email",
    "delete_email",
    "mark_email_read",
    "resolve_contact",
    "manage_contact",
    "manage_calendar",
}
STATE_TOOLS = {
    "write_file",
    "create_document",
    "edit_document",
    "update_document",
    "generate_image",
    "edit_image",
    "manage_session",
    "manage_memory",
    "manage_skills",
    "manage_tasks",
    "manage_notes",
    "manage_calendar",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_documents",
    "manage_settings",
    "ui_control",
    "send_email",
    "reply_to_email",
    "bulk_email",
    "archive_email",
    "delete_email",
    "mark_email_read",
    "manage_contact",
    "download_model",
    "serve_model",
    "stop_served_model",
    "cancel_download",
    "serve_preset",
    "adopt_served_model",
    "code_workspace",
    "app_api",
}
COMMUNICATION_TOOLS = {
    "send_email",
    "reply_to_email",
    "bulk_email",
    "archive_email",
    "delete_email",
    "mark_email_read",
    "list_email_accounts",
    "list_emails",
    "read_email",
    "resolve_contact",
    "manage_contact",
}
CREDENTIAL_TOOLS = {
    "manage_settings",
    "manage_tokens",
    "manage_webhooks",
    "manage_mcp",
    "manage_endpoints",
    "api_call",
    "app_api",
}
CORE_OPERATOR_TOOLS = {
    "manage_settings",
    "manage_mcp",
    "manage_skills",
    "manage_memory",
    "manage_tasks",
    "manage_calendar",
    "manage_notes",
    "code_workspace",
    "app_api",
}
APPROVAL_GATED_TOOLS = (
    SHELL_TOOLS
    | FILE_TOOLS
    | NETWORK_TOOLS
    | STATE_TOOLS
    | COMMUNICATION_TOOLS
    | CREDENTIAL_TOOLS
    | {"manage_mcp", "manage_skills", "manage_settings", "manage_tokens"}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return value is not False and value not in ("false", "False", "0", 0, "")


def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, str]:
    normalized = dict(DEFAULT_TRUST_POLICY)
    if isinstance(policy, dict):
        for level in TRUST_LEVELS:
            mode = str(policy.get(level) or normalized[level]).lower()
            normalized[level] = mode if mode in {"auto", "ask"} else normalized[level]
    return normalized


def _settings_disabled(settings: dict[str, Any]) -> set[str]:
    raw = settings.get("disabled_tools") if isinstance(settings, dict) else []
    if not isinstance(raw, list):
        return set()
    return {_trim(item, 160) for item in raw if _trim(item, 160)}


def _tool_catalog_rows(
    tools: list[dict[str, Any]] | None,
    *,
    disabled: set[str],
    public_blocked: set[str],
) -> list[dict[str, Any]]:
    if tools is None:
        names = sorted(TOOL_TAGS | set(BUILTIN_TOOL_DESCRIPTIONS))
        source_rows = [{"id": name, "name": name, "description": BUILTIN_TOOL_DESCRIPTIONS.get(name, "")} for name in names]
    else:
        source_rows = [row for row in tools if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for row in source_rows:
        name = _trim(row.get("id") or row.get("name") or row.get("tool") or row.get("tool_name"), 160)
        if not name:
            continue
        row_disabled = name in disabled or row.get("enabled") is False
        categories = []
        if name in SHELL_TOOLS:
            categories.append("shell")
        if name in FILE_TOOLS:
            categories.append("file")
        if name in NETWORK_TOOLS:
            categories.append("network")
        if name in STATE_TOOLS:
            categories.append("state")
        if name in COMMUNICATION_TOOLS:
            categories.append("communication")
        if name in CREDENTIAL_TOOLS:
            categories.append("credential")
        if name == "manage_mcp":
            categories.append("mcp")
        if name == "manage_skills":
            categories.append("skills")
        if not categories:
            categories.append("local")
        requires_approval = name in APPROVAL_GATED_TOOLS
        rows.append(
            {
                "id": name,
                "state": "warn" if row_disabled else "ok",
                "badge": categories[0],
                "title": name,
                "detail": _trim(row.get("description") or BUILTIN_TOOL_DESCRIPTIONS.get(name) or "Tool catalog entry", 240),
                "categories": categories,
                "enabled": not row_disabled,
                "disabled": row_disabled,
                "public_blocked": name in public_blocked,
                "network_capable": name in NETWORK_TOOLS,
                "runs_shell": name in SHELL_TOOLS,
                "reads_or_writes_files": name in FILE_TOOLS,
                "writes_state": name in STATE_TOOLS,
                "credential_surface": name in CREDENTIAL_TOOLS,
                "requires_approval": requires_approval,
                "executes": False,
            }
        )
    rows.sort(key=lambda item: (not bool(item.get("disabled")), item["title"]))
    return rows


def _skill_catalog_rows(owner: str, skills: list[dict[str, Any]] | None, data_root: Path) -> list[dict[str, Any]]:
    source_rows = skills
    if source_rows is None:
        skills_root = data_root / "skills"
        legacy_file = data_root / "skills.json"
        source_rows = []
        if skills_root.exists() or legacy_file.exists():
            try:
                from services.memory.skills import SkillsManager

                source_rows = SkillsManager(str(data_root)).load(owner=owner)
            except Exception:
                source_rows = []
    rows: list[dict[str, Any]] = []
    for row in source_rows or []:
        if not isinstance(row, dict):
            continue
        name = _trim(row.get("name") or row.get("id") or row.get("title"), 160)
        if not name:
            continue
        status = _trim(row.get("status") or "draft", 40).lower()
        rows.append(
            {
                "id": name,
                "state": "ok" if status == "published" else "loading",
                "badge": "skill",
                "title": name,
                "detail": _trim(row.get("description") or row.get("when_to_use") or "Skill metadata", 240),
                "category": _trim(row.get("category") or "general", 80),
                "status": status,
                "source": _trim(row.get("source") or "local", 80),
                "tags": [_trim(tag, 40) for tag in (row.get("tags") or []) if _trim(tag, 40)][:8],
                "audit_verdict": _trim(row.get("audit_verdict"), 80),
                "owner_scoped": bool(row.get("owner")),
                "reads_body": False,
                "executes": False,
            }
        )
    rows.sort(key=lambda item: (item["status"] != "published", item["title"]))
    return rows


def _mcp_rows(mcp_servers: list[dict[str, Any]] | None, features: dict[str, Any], offline: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    mcp_enabled = _bool(features.get("mcp"), True) and not offline
    if mcp_servers is None:
        try:
            from core.database import McpServer, SessionLocal

            db = SessionLocal()
            try:
                mcp_servers = []
                for srv in db.query(McpServer).all():
                    mcp_servers.append(
                        {
                            "id": getattr(srv, "id", ""),
                            "name": getattr(srv, "name", ""),
                            "transport": getattr(srv, "transport", ""),
                            "is_enabled": bool(getattr(srv, "is_enabled", False)),
                            "disabled_tools": getattr(srv, "disabled_tools", None),
                        }
                    )
            finally:
                db.close()
        except Exception:
            mcp_servers = []
    for row in mcp_servers or []:
        if not isinstance(row, dict):
            continue
        name = _trim(row.get("name") or row.get("id") or "MCP server", 160)
        enabled = bool(row.get("is_enabled", row.get("enabled", True)))
        status = _trim(row.get("status") or ("configured" if enabled else "disabled"), 80)
        tool_count = row.get("tool_count", row.get("enabled_tool_count", 0))
        try:
            disabled_count = len(row.get("disabled_tools") or []) if not isinstance(row.get("disabled_tools"), str) else 0
        except TypeError:
            disabled_count = 0
        rows.append(
            {
                "id": _trim(row.get("id") or name, 160),
                "state": "ok" if mcp_enabled and enabled else "warn",
                "badge": "mcp",
                "title": name,
                "detail": f"transport={_trim(row.get('transport') or 'unknown', 40)} status={status}",
                "enabled": enabled,
                "mcp_feature_enabled": mcp_enabled,
                "transport": _trim(row.get("transport"), 40),
                "tool_count": int(tool_count or 0),
                "disabled_tool_count": disabled_count,
                "connects": False,
                "executes": False,
                "uses_network": False,
                "requires_approval": True,
            }
        )
    return rows


def _toggle_rows(disabled: set[str], tool_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {row["id"] for row in tool_rows}
    rows: list[dict[str, Any]] = []
    for name in sorted(disabled):
        rows.append(
            {
                "id": name,
                "state": "warn" if name in known else "loading",
                "badge": "off",
                "title": name,
                "detail": "Disabled in settings" if name in known else "Disabled in settings but not found in current tool registry",
                "known_tool": name in known,
                "requires_approval": True,
                "executes": False,
            }
        )
    return rows


def _guard_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "tool-access-read-only",
            "state": "ok",
            "badge": "read",
            "title": "Read-only access posture",
            "detail": "This plan inventories tool, skill, MCP, setting, and approval metadata only.",
        },
        {
            "id": "tool-access-sensitive-management",
            "state": "ok",
            "badge": "gate",
            "title": "Sensitive management stays gated",
            "detail": "manage_mcp, manage_settings, manage_skills, shell, filesystem, network, and communication tools require explicit command execution outside this plan.",
        },
        {
            "id": "tool-access-no-secret-read",
            "state": "ok",
            "badge": "key",
            "title": "Secret values are not read",
            "detail": "Credential posture belongs to the credentials plan; this plan only flags credential-capable tool surfaces.",
        },
    ]


def _api_actions() -> list[dict[str, Any]]:
    return [
        {"method": "GET", "path": "/api/operator/tool-access-plan", "title": "Read tool access posture", "requires_approval": False, "writes": False, "executes": False},
        {"method": "GET", "path": "/api/tools", "title": "Read tool toggles", "requires_approval": False, "writes": False, "executes": False},
        {"method": "POST", "path": "/api/tools", "title": "Update disabled tools", "requires_approval": True, "writes": True, "executes": False},
        {"method": "GET", "path": "/api/skills", "title": "Read skill metadata", "requires_approval": False, "writes": False, "executes": False},
        {"method": "POST", "path": "/api/skills", "title": "Create skill", "requires_approval": True, "writes": True, "executes": False},
        {"method": "GET", "path": "/api/mcp/servers", "title": "Read MCP servers", "requires_approval": False, "writes": False, "executes": False},
        {"method": "POST", "path": "/api/mcp/servers", "title": "Add/connect MCP server", "requires_approval": True, "writes": True, "executes": True},
        {"method": "POST", "path": "/api/auth/settings", "title": "Change settings", "requires_approval": True, "writes": True, "executes": False},
    ]


def _entry_rows(
    *,
    tool_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    mcp_rows: list[dict[str, Any]],
    policy: dict[str, str],
) -> list[dict[str, Any]]:
    visible = bool(tool_rows)
    weak_danger = policy.get("danger") != "ask" and any(
        row.get("enabled") and (row.get("runs_shell") or row.get("reads_or_writes_files"))
        for row in tool_rows
    )
    weak_network = policy.get("network") != "ask" and any(row.get("enabled") and row.get("network_capable") for row in tool_rows)
    state = "warn" if visible and (weak_danger or weak_network) else ("ok" if visible else "error")
    common = {
        "command_id": "open-capability-map",
        "trust_command_id": "open-trust-controls",
        "palette_command_id": "open-command-palette",
        "offline_command_id": "open-offline",
        "activity_command_id": "open-activity-preflight",
        "tool_access_api": "/api/operator/tool-access-plan",
        "tools_api": "/api/tools",
        "skills_api": "/api/skills",
        "mcp_api": "/api/mcp/servers",
        "requires_approval": True,
        "ready": visible,
        "executes": False,
        "executes_tools": False,
        "runs_shell": False,
        "writes_files": False,
        "changes_settings": False,
        "adds_mcp_servers": False,
        "connects_mcp": False,
        "deletes_mcp": False,
        "publishes_skills": False,
        "uses_network": False,
        "reads_secret_values": False,
    }
    inventory = (
        f"{len(tool_rows)} tool(s), {len(skill_rows)} skill(s), and "
        f"{len(mcp_rows)} MCP server(s) visible before any tool execution."
    )
    return [
        {
            **common,
            "id": "tool-access-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard tool access route",
            "detail": f"The Capability Map opens read-only tool posture from the dashboard; {inventory}",
            "action": "open-capability-map",
            "actionLabel": "Capability",
        },
        {
            **common,
            "id": "tool-access-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed tool request route",
            "detail": "Typed requests for shell, filesystem, network, skill, or MCP access route to posture and trust review before any tool can run.",
            "action": "open-capability-map",
            "actionLabel": "Review",
        },
        {
            **common,
            "id": "tool-access-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette tool route",
            "detail": "The command palette can open tool access review, trust controls, and offline controls without changing settings or executing tools.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "tool-access-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice tool request route",
            "detail": "Voice mode can route tool-use requests to the same read-only posture layer before shell, file, MCP, or network capabilities are used.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "tool-access-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow tool handoff",
            "detail": "Automation handoffs can show tool gates, trust mode, offline state, and API boundaries before a workflow receives tool access.",
            "action": "open-capability-map",
            "actionLabel": "Gate",
        },
    ]


def _alert_rows(
    *,
    tool_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    mcp_rows: list[dict[str, Any]],
    toggle_rows: list[dict[str, Any]],
    policy: dict[str, str],
    features: dict[str, Any],
    offline: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    enabled_tools = [row for row in tool_rows if row.get("enabled")]
    enabled_shell = [row for row in enabled_tools if row.get("runs_shell")]
    enabled_file = [row for row in enabled_tools if row.get("reads_or_writes_files")]
    enabled_network = [row for row in enabled_tools if row.get("network_capable")]
    disabled_core = [row for row in toggle_rows if row["id"] in CORE_OPERATOR_TOOLS]
    if not tool_rows:
        rows.append({
            "id": "tool-access-registry-empty",
            "state": "error",
            "badge": "tool",
            "title": "Tool registry is not visible",
            "detail": "Cleverly cannot prove operator tool access without the local tool catalog.",
            "action": "open-capability-map",
            "actionLabel": "Capability",
            "requires_approval": False,
        })
    if disabled_core:
        rows.append({
            "id": "tool-access-core-tools-disabled",
            "state": "warn",
            "badge": "off",
            "title": "Core operator tools disabled",
            "detail": f"{len(disabled_core)} core operator tool(s) are disabled: {', '.join(row['id'] for row in disabled_core[:5])}.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if enabled_shell and policy["danger"] != "ask":
        rows.append({
            "id": "tool-access-shell-not-ask-gated",
            "state": "error",
            "badge": "shell",
            "title": "Shell-capable tools are not danger ask-gated",
            "detail": f"{len(enabled_shell)} shell/code execution tool(s) are enabled while danger policy is {policy['danger']}.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if enabled_file and policy["danger"] != "ask":
        rows.append({
            "id": "tool-access-file-not-ask-gated",
            "state": "error",
            "badge": "file",
            "title": "Filesystem tools are not danger ask-gated",
            "detail": f"{len(enabled_file)} filesystem-capable tool(s) are enabled while danger policy is {policy['danger']}.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        })
    if enabled_network and not offline and policy["network"] != "ask":
        rows.append({
            "id": "tool-access-network-not-ask-gated",
            "state": "warn",
            "badge": "net",
            "title": "Network-capable tools are not network ask-gated",
            "detail": f"{len(enabled_network)} network-capable tool(s) are enabled while network policy is {policy['network']}.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "requires_approval": True,
        })
    if _bool(features.get("mcp"), True) and not offline and not mcp_rows:
        rows.append({
            "id": "tool-access-mcp-enabled-empty",
            "state": "warn",
            "badge": "mcp",
            "title": "MCP is enabled but no server inventory is visible",
            "detail": "The operator can manage MCP, but no configured MCP server metadata was found for this plan.",
            "action": "open-capability-map",
            "actionLabel": "Capability",
            "requires_approval": False,
        })
    if not skill_rows:
        rows.append({
            "id": "tool-access-skills-empty",
            "state": "warn",
            "badge": "skill",
            "title": "No owner-scoped skills visible",
            "detail": "Cleverly can manage skills, but no local skill metadata is visible for this owner.",
            "action": "open-capability-map",
            "actionLabel": "Capability",
            "requires_approval": False,
        })
    return rows[:16]


def run_operator_tool_access_plan(
    owner: str = "local",
    *,
    tools: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    data_root: str | Path | None = None,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Return a read-only access posture for tools, skills, and MCP."""
    owner = owner or "local"
    settings_data = settings if isinstance(settings, dict) else load_settings()
    features_data = features if isinstance(features, dict) else load_features()
    normalized_policy = _normalize_policy(policy)
    root = Path(data_root) if data_root is not None else Path(os.getenv("DATA_DIR") or DATA_DIR)
    offline_active = offline_mode() if offline is None else bool(offline)
    disabled = _settings_disabled(settings_data)
    public_blocked = set(NON_ADMIN_BLOCKED_TOOLS)
    tool_rows = _tool_catalog_rows(tools, disabled=disabled, public_blocked=public_blocked)
    skill_rows = _skill_catalog_rows(owner, skills, root)
    mcp_access_rows = _mcp_rows(mcp_servers, features_data, offline_active)
    toggle_rows = _toggle_rows(disabled, tool_rows)
    guard_rows = _guard_rows()
    entry_rows = _entry_rows(
        tool_rows=tool_rows,
        skill_rows=skill_rows,
        mcp_rows=mcp_access_rows,
        policy=normalized_policy,
    )
    alert_rows = _alert_rows(
        tool_rows=tool_rows,
        skill_rows=skill_rows,
        mcp_rows=mcp_access_rows,
        toggle_rows=toggle_rows,
        policy=normalized_policy,
        features=features_data,
        offline=offline_active,
    )
    enabled_tool_count = len([row for row in tool_rows if row.get("enabled")])
    disabled_tool_count = len([row for row in tool_rows if row.get("disabled")])
    network_tool_count = len([row for row in tool_rows if row.get("network_capable")])
    shell_tool_count = len([row for row in tool_rows if row.get("runs_shell")])
    file_tool_count = len([row for row in tool_rows if row.get("reads_or_writes_files")])
    approval_gated_count = len([row for row in tool_rows if row.get("requires_approval")])
    published_skill_count = len([row for row in skill_rows if row.get("status") == "published"])
    draft_skill_count = len([row for row in skill_rows if row.get("status") != "published"])
    mcp_tool_count = sum(int(row.get("tool_count") or 0) for row in mcp_access_rows)
    critical = [row for row in alert_rows if row.get("state") == "error"]
    state = "error" if critical else ("warn" if alert_rows else "ok")
    return {
        "mode": "read-only-tool-access-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "tool_count": len(tool_rows),
            "enabled_tool_count": enabled_tool_count,
            "disabled_tool_count": disabled_tool_count,
            "network_tool_count": network_tool_count,
            "shell_tool_count": shell_tool_count,
            "file_tool_count": file_tool_count,
            "approval_gated_count": approval_gated_count,
            "skill_count": len(skill_rows),
            "published_skill_count": published_skill_count,
            "draft_skill_count": draft_skill_count,
            "mcp_enabled": _bool(features_data.get("mcp"), True) and not offline_active,
            "mcp_server_count": len(mcp_access_rows),
            "mcp_tool_count": mcp_tool_count,
            "tool_access_alert_count": len(alert_rows),
            "critical_tool_access_alert_count": len(critical),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("ready")]),
            "offline": offline_active,
            "executes_tools": False,
            "runs_shell": False,
            "writes_files": False,
            "changes_settings": False,
            "adds_mcp_servers": False,
            "connects_mcp": False,
            "deletes_mcp": False,
            "publishes_skills": False,
            "uses_network": False,
            "reads_secret_values": False,
            "next_action": "Open Capability Map, Trust Controls, or Offline Control to review tool posture before granting autonomy.",
        },
        "policy": normalized_policy,
        "tool_rows": tool_rows[:80],
        "skill_rows": skill_rows[:40],
        "mcp_rows": mcp_access_rows[:24],
        "toggle_rows": toggle_rows[:40],
        "guard_rows": guard_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "api_actions": _api_actions(),
        "approval": {
            "required": False,
            "gate": "Tool access posture audit only",
            "policy": (
                "This endpoint only reports local tool registry, skill metadata, MCP metadata, disabled-tool settings, "
                "and approval posture. It does not execute tools, run shell commands, write files, change settings, "
                "add MCP servers, connect MCP servers, delete MCP servers, publish skills, use network access, or read secret values."
            ),
            "disallowed_by_default": [
                "execute tools",
                "run shell",
                "write files",
                "change settings",
                "add MCP server",
                "connect MCP",
                "delete MCP",
                "publish skills",
                "use network",
                "read secret values",
            ],
        },
        "paths": {
            "data_root": str(root),
            "settings": str(root / "settings.json"),
            "skills": str(root / "skills"),
            "legacy_skills": str(root / "skills.json"),
            "mcp": "app.db:mcp_servers",
        },
    }
