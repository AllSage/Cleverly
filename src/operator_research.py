"""Read-only research and SearXNG readiness plan for the Cleverly console."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, SEARXNG_INSTANCE
from src.settings import DEFAULT_FEATURES, load_features, load_settings, offline_mode


PROVIDER_INFO = {
    "searxng": {"label": "SearXNG", "needs_key": False, "needs_url": True, "key": ""},
    "brave": {"label": "Brave Search", "needs_key": True, "needs_url": False, "key": "brave_api_key"},
    "duckduckgo": {"label": "DuckDuckGo", "needs_key": False, "needs_url": False, "key": ""},
    "google_pse": {"label": "Google PSE", "needs_key": True, "needs_url": False, "key": "google_pse_key"},
    "tavily": {"label": "Tavily", "needs_key": True, "needs_url": False, "key": "tavily_api_key"},
    "serper": {"label": "Serper", "needs_key": True, "needs_url": False, "key": "serper_api_key"},
    "disabled": {"label": "Disabled", "needs_key": False, "needs_url": False, "key": ""},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _bool_feature(features: dict[str, Any], key: str, default: bool = True) -> bool:
    value = features.get(key, DEFAULT_FEATURES.get(key, default))
    return bool(value)


def _provider_id(settings: dict[str, Any]) -> str:
    value = _trim(settings.get("research_search_provider") or settings.get("search_provider") or "searxng", 80).lower()
    return value if value in PROVIDER_INFO else "searxng"


def _provider_label(provider: str) -> str:
    return PROVIDER_INFO.get(provider, {}).get("label") or provider or "Search"


def _fallback_chain(settings: dict[str, Any]) -> list[str]:
    raw = settings.get("search_fallback_chain") or []
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, list):
        return []
    return [_trim(item, 80) for item in raw[:6] if _trim(item, 80) and _trim(item, 80) != "disabled"]


def _report_time(report: dict[str, Any]) -> float:
    for key in ("completed_at", "updated_at", "started_at", "created_at"):
        value = report.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if value:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    return 0.0


def _report_rows(owner: str, reports: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if isinstance(reports, list):
        source = reports
    else:
        source = []
        data_dir = Path(DATA_DIR) / "deep_research"
        try:
            paths = sorted(data_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]
        except OSError:
            paths = []
        for path in paths:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            item_owner = item.get("owner")
            if owner != "local" and item_owner != owner:
                continue
            if owner == "local" and item_owner not in (None, "", "local"):
                continue
            item = dict(item)
            item.setdefault("id", path.stem)
            source.append(item)
    rows: list[dict[str, Any]] = []
    for item in sorted(source, key=_report_time, reverse=True)[:20]:
        if not isinstance(item, dict):
            continue
        sources = item.get("sources") if isinstance(item.get("sources"), list) else []
        rows.append({
            "id": _trim(item.get("id") or item.get("session_id"), 160),
            "state": "error" if str(item.get("status") or "").lower() in {"failed", "error"} else "ok",
            "badge": "report",
            "title": _trim(item.get("query") or item.get("title") or "Research report", 240),
            "detail": f"{len(sources)} source{'s' if len(sources) != 1 else ''}; status {item.get('status') or 'done'}",
            "status": _trim(item.get("status") or "done", 80),
            "source_count": len(sources),
            "started_at": item.get("started_at") or "",
            "completed_at": item.get("completed_at") or item.get("updated_at") or "",
        })
    return rows


def _active_rows(active_jobs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in active_jobs or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "id": _trim(item.get("session_id") or item.get("id"), 160),
            "state": "warn",
            "badge": "job",
            "title": _trim(item.get("query") or item.get("title") or "Active research job", 240),
            "detail": f"{item.get('status') or item.get('state') or 'running'}; started {item.get('started_at') or 'local'}",
            "status": _trim(item.get("status") or item.get("state") or "running", 80),
        })
    return rows[:12]


def _api_action(
    path: str,
    title: str,
    *,
    method: str = "GET",
    writes: bool = False,
    starts_job: bool = False,
    uses_network: bool = False,
    requires_approval: bool = False,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "starts_job": starts_job,
        "uses_network": uses_network,
        "requires_approval": requires_approval,
    }


def _research_alert_rows(
    *,
    features: dict[str, Any],
    settings: dict[str, Any],
    provider: str,
    search_url: str,
    offline: bool,
    active_rows: list[dict[str, Any]],
    report_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    deep_research = _bool_feature(features, "deep_research", False)
    web_search = _bool_feature(features, "web_search", True)
    network_integrations = _bool_feature(features, "network_integrations", True)
    provider_info = PROVIDER_INFO.get(provider, PROVIDER_INFO["searxng"])
    key_field = provider_info.get("key") or ""
    key_ready = not provider_info.get("needs_key") or bool(_trim(settings.get(key_field), 20))
    if not deep_research:
        rows.append({
            "id": "research-feature-disabled",
            "state": "warn",
            "badge": "flag",
            "title": "Deep Research feature disabled",
            "detail": "Research jobs stay unavailable until the Deep Research feature is enabled.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if offline or not network_integrations:
        rows.append({
            "id": "research-network-blocked",
            "state": "warn",
            "badge": "net",
            "title": "Research source gathering blocked",
            "detail": "Offline or network-integration policy blocks web-backed source gathering.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if not web_search:
        rows.append({
            "id": "research-web-search-disabled",
            "state": "warn",
            "badge": "web",
            "title": "Web Search feature disabled",
            "detail": "Deep Research source discovery needs Web Search enabled unless using local-only reports.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if provider == "disabled":
        rows.append({
            "id": "research-provider-disabled",
            "state": "error",
            "badge": "search",
            "title": "Research search provider disabled",
            "detail": "No search provider is selected for Deep Research source discovery.",
            "action": "open-research-preflight",
            "actionLabel": "Research",
            "requires_approval": False,
            "uses_network": False,
        })
    elif provider_info.get("needs_url") and not search_url:
        rows.append({
            "id": "research-searxng-url-missing",
            "state": "error",
            "badge": "search",
            "title": "SearXNG URL missing",
            "detail": "The configured research provider needs a local SearXNG URL.",
            "action": "open-research-preflight",
            "actionLabel": "Research",
            "requires_approval": False,
            "uses_network": False,
        })
    elif not key_ready:
        rows.append({
            "id": f"research-{provider}-key-missing",
            "state": "error",
            "badge": "key",
            "title": f"{_provider_label(provider)} API key missing",
            "detail": f"The selected research provider needs `{key_field}` before web search can run.",
            "action": "open-research-preflight",
            "actionLabel": "Research",
            "requires_approval": False,
            "uses_network": False,
        })
    if not _trim(settings.get("research_model") or settings.get("default_model")) and not _trim(settings.get("research_endpoint_id") or settings.get("default_endpoint_id")):
        rows.append({
            "id": "research-model-route-missing",
            "state": "warn",
            "badge": "model",
            "title": "Research model route missing",
            "detail": "No research/default model route is configured for synthesis after sources are collected.",
            "action": "open-model-preflight",
            "actionLabel": "Models",
            "requires_approval": False,
            "uses_network": False,
        })
    if active_rows:
        rows.append({
            "id": "research-active-jobs",
            "state": "warn",
            "badge": "job",
            "title": "Active research jobs running",
            "detail": f"{len(active_rows)} active research job{'s' if len(active_rows) != 1 else ''} should be monitored before starting another.",
            "action": "open-research",
            "actionLabel": "Research",
            "requires_approval": False,
            "uses_network": False,
        })
    failed_reports = [row for row in report_rows if row.get("state") == "error"]
    if failed_reports:
        rows.append({
            "id": "research-failed-reports",
            "state": "error",
            "badge": "report",
            "title": "Failed research reports need review",
            "detail": f"{len(failed_reports)} saved research report{'s' if len(failed_reports) != 1 else ''} ended in a failed state.",
            "action": "open-library",
            "actionLabel": "Library",
            "requires_approval": False,
            "uses_network": False,
        })
    rows.append({
        "id": "research-start-approval-required",
        "state": "warn",
        "badge": "ask",
        "title": "Research start approval required",
        "detail": "Starting Deep Research can use network access and model tokens; this plan only reviews readiness.",
        "action": "open-research",
        "actionLabel": "Research",
        "requires_approval": True,
        "uses_network": True,
    })
    return rows[:16]


def _entry_rows(
    *,
    deep_research: bool,
    source_gathering_ready: bool,
    model_ready: bool,
    active_count: int,
) -> list[dict[str, Any]]:
    feature_state = "ok" if deep_research else "warn"
    workflow_state = "ok" if source_gathering_ready and model_ready and active_count == 0 else "warn"
    workflow_detail = (
        "Workflow handoff can open the research panel with provider, model, and policy evidence ready."
        if workflow_state == "ok"
        else "Workflow handoff stays in review mode until feature, network policy, provider, model, and active-job checks are clear."
    )
    common = {
        "command_id": "open-research-preflight",
        "start_command_id": "open-research",
        "approval_api": "/api/research/start",
        "requires_approval": True,
        "executes": False,
        "starts_research": False,
        "runs_search": False,
        "writes_reports": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "research-dashboard-route",
            "entry": "dashboard",
            "state": feature_state,
            "badge": "dash",
            "title": "Dashboard research preflight",
            "detail": "The Research card opens this read-only preflight before any web-backed research job starts.",
            "action": "open-research-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "research-text-route",
            "entry": "text",
            "state": feature_state,
            "badge": "text",
            "title": "Typed research request route",
            "detail": "Typed research requests route to Research Operations Preflight before the Deep Research panel can start a job.",
            "action": "open-research-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "research-palette-route",
            "entry": "palette",
            "state": feature_state,
            "badge": "cmd",
            "title": "Palette research route",
            "detail": "The command palette exposes research review separately from the network-capable research start API.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "research-voice-route",
            "entry": "voice",
            "state": feature_state,
            "badge": "voice",
            "title": "Voice research route",
            "detail": "Voice transcripts use the same local command route and open research preflight before any network-capable job.",
            "action": "start-voice-command",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "research-workflow-route",
            "entry": "workflow",
            "state": workflow_state,
            "badge": "flow",
            "title": "Workflow research handoff",
            "detail": workflow_detail,
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _handoff_rows(
    *,
    source_gathering_ready: bool,
    model_ready: bool,
    report_count: int,
) -> list[dict[str, Any]]:
    common = {
        "requires_approval": False,
        "executes": False,
        "starts_research": False,
        "starts_jobs": False,
        "runs_search": False,
        "writes_reports": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "research-local-document-handoff",
            "state": "ok",
            "badge": "docs",
            "title": "Local document evidence handoff",
            "detail": "Research-style questions can route to local document search first for offline evidence review.",
            "action": "open-documents-preflight",
            "actionLabel": "Files",
            "target_api": "/api/operator/document-search-plan",
        },
        {
            **common,
            "id": "research-saved-report-handoff",
            "state": "ok" if report_count else "loading",
            "badge": "report",
            "title": "Saved research report handoff",
            "detail": f"{report_count} saved report{'s' if report_count != 1 else ''} can be reviewed before starting new research.",
            "action": "open-library",
            "actionLabel": "Library",
            "target_api": "/api/research/library",
        },
        {
            **common,
            "id": "research-gallery-evidence-handoff",
            "state": "ok",
            "badge": "media",
            "title": "Gallery evidence handoff",
            "detail": "Image and upload evidence stays in the local library/gallery preflight before any model-backed media action.",
            "action": "open-library-preflight",
            "actionLabel": "Library",
            "target_api": "/api/operator/gallery-plan",
        },
        {
            **common,
            "id": "research-workspace-synthesis-handoff",
            "state": "ok" if model_ready else "warn",
            "badge": "work",
            "title": "Workspace synthesis handoff",
            "detail": "Research notes, local document evidence, and saved reports can move into the code/workspace map for follow-up work.",
            "action": "open-code-workspace-map",
            "actionLabel": "Workspace",
            "target_api": "/api/operator/workspace-plan",
        },
        {
            **common,
            "id": "research-web-source-approval-handoff",
            "state": "ok" if source_gathering_ready and model_ready else "warn",
            "badge": "ask",
            "title": "Web source approval handoff",
            "detail": "Deep Research start remains a separate approval step because it can use network access and model tokens.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "target_api": "/api/research/start",
            "approval_api": "/api/research/start",
            "requires_approval": True,
            "network_after_approval": True,
        },
    ]


def run_operator_research_plan(
    owner: str = "local",
    *,
    features: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    active_jobs: list[dict[str, Any]] | None = None,
    reports: list[dict[str, Any]] | None = None,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Return read-only Deep Research, SearXNG, and report-archive evidence."""
    owner = owner or "local"
    try:
        loaded_settings = load_settings()
    except Exception:
        loaded_settings = {}
    try:
        loaded_features = load_features()
    except Exception:
        loaded_features = {}
    settings = {**loaded_settings, **(settings or {})}
    features = {**loaded_features, **(features or {})}
    offline_state = offline_mode() if offline is None else bool(offline)
    provider = _provider_id(settings)
    provider_info = PROVIDER_INFO.get(provider, PROVIDER_INFO["searxng"])
    search_url = _trim(settings.get("search_url") or (SEARXNG_INSTANCE if provider == "searxng" else ""), 300)
    fallback_chain = _fallback_chain(settings)
    active = _active_rows(active_jobs)
    reports_rows = _report_rows(owner, reports)
    deep_research = _bool_feature(features, "deep_research", False)
    web_search = _bool_feature(features, "web_search", True)
    network_integrations = _bool_feature(features, "network_integrations", True)
    provider_ready = provider != "disabled"
    if provider_info.get("needs_url"):
        provider_ready = provider_ready and bool(search_url)
    if provider_info.get("needs_key"):
        provider_ready = provider_ready and bool(_trim(settings.get(provider_info.get("key")), 20))
    source_gathering_ready = deep_research and web_search and network_integrations and provider_ready and not offline_state
    research_model = _trim(settings.get("research_model") or settings.get("default_model"), 160)
    research_endpoint = _trim(settings.get("research_endpoint_id") or settings.get("default_endpoint_id"), 160)
    model_ready = bool(research_model or research_endpoint)
    feature_rows = [
        {
            "id": "deep-research",
            "state": "ok" if deep_research else "warn",
            "badge": "flag",
            "title": "Deep Research feature",
            "detail": "Enabled for this user" if deep_research else "Disabled by feature policy",
            "action": "open-research-preflight",
            "actionLabel": "Research",
        },
        {
            "id": "web-search",
            "state": "ok" if web_search else "warn",
            "badge": "web",
            "title": "Web Search feature",
            "detail": "Web search is enabled" if web_search else "Web search is disabled by feature policy",
            "action": "open-offline",
            "actionLabel": "Policy",
        },
        {
            "id": "network-integrations",
            "state": "ok" if network_integrations and not offline_state else "warn",
            "badge": "net",
            "title": "Network posture",
            "detail": "Network integrations can be used when a user starts research" if network_integrations and not offline_state else "Offline/network policy blocks web-backed research",
            "action": "open-offline",
            "actionLabel": "Policy",
        },
    ]
    provider_rows = [
        {
            "id": "provider",
            "state": "ok" if provider_ready else "error",
            "badge": "search",
            "title": "Research search provider",
            "detail": f"{_provider_label(provider)}; url {'configured' if search_url else 'missing'}; key {'required' if provider_info.get('needs_key') else 'not required'}",
            "action": "open-research-preflight",
            "actionLabel": "Research",
        },
        {
            "id": "fallbacks",
            "state": "ok" if fallback_chain or provider == "duckduckgo" else "warn",
            "badge": "fall",
            "title": "Search fallback chain",
            "detail": ", ".join(fallback_chain) if fallback_chain else "No fallback providers configured",
            "action": "open-research-preflight",
            "actionLabel": "Research",
        },
    ]
    model_rows = [
        {
            "id": "research-model",
            "state": "ok" if model_ready else "warn",
            "badge": "model",
            "title": "Research model route",
            "detail": research_model or research_endpoint or "No research/default model route configured",
            "action": "open-model-preflight",
            "actionLabel": "Models",
        }
    ]
    job_rows = [
        {
            "id": "active-jobs",
            "state": "warn" if active else "ok",
            "badge": "job",
            "title": "Active research jobs",
            "detail": f"{len(active)} active job{'s' if len(active) != 1 else ''}",
            "action": "open-research",
            "actionLabel": "Research",
        },
        {
            "id": "saved-reports",
            "state": "ok" if reports_rows else "loading",
            "badge": "report",
            "title": "Saved research reports",
            "detail": f"{len(reports_rows)} report{'s' if len(reports_rows) != 1 else ''} visible in local archive",
            "action": "open-library",
            "actionLabel": "Library",
        },
    ]
    api_actions = [
        _api_action("/api/operator/research-plan", "Read research operations plan"),
        _api_action("/api/research/active", "Read active research jobs"),
        _api_action("/api/research/library", "Read saved research reports"),
        _api_action("/api/search/config", "Read search configuration"),
        _api_action("/api/search/providers", "Read search provider catalog"),
        _api_action("/api/research/start", "Start Deep Research", method="POST", writes=True, starts_job=True, uses_network=True, requires_approval=True),
        _api_action("/api/search", "Run standalone web search", method="POST", uses_network=True, requires_approval=True),
    ]
    alert_rows = _research_alert_rows(
        features=features,
        settings=settings,
        provider=provider,
        search_url=search_url,
        offline=offline_state,
        active_rows=active,
        report_rows=reports_rows,
    )
    entry_rows = _entry_rows(
        deep_research=deep_research,
        source_gathering_ready=source_gathering_ready,
        model_ready=model_ready,
        active_count=len(active),
    )
    handoff_rows = _handoff_rows(
        source_gathering_ready=source_gathering_ready,
        model_ready=model_ready,
        report_count=len(reports_rows),
    )
    return {
        "mode": "read-only-research-operations-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
            "deep_research_enabled": deep_research,
            "web_search_enabled": web_search,
            "network_integrations_enabled": network_integrations,
            "offline": offline_state,
            "provider": provider,
            "provider_ready": provider_ready,
            "source_gathering_ready": source_gathering_ready,
            "active_job_count": len(active),
            "report_count": len(reports_rows),
            "research_alert_count": len(alert_rows),
            "critical_research_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "runs_search": False,
            "starts_research": False,
            "starts_jobs": False,
            "writes_reports": False,
            "uses_network": False,
        },
        "feature_rows": feature_rows,
        "provider_rows": provider_rows,
        "model_rows": model_rows,
        "job_rows": job_rows,
        "active_rows": active,
        "report_rows": reports_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Research readiness only",
            "policy": (
                "This endpoint only inspects local research readiness, saved report metadata, search settings, "
                "and API gates. It does not start research, run web search, fetch URLs, write reports, call "
                "SearXNG, query external providers, approve network access, or use network access."
            ),
        },
        "paths": {
            "reports": "data/deep_research",
            "settings": "data/settings.json",
            "features": "data/features.json",
            "search_cache": "data/search",
            "searxng_config": "cleverly-searxng-data:/etc/searxng",
            "searxng_cache": "cleverly-searxng-cache:/var/cache/searxng",
        },
    }
