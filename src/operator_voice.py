"""Read-only voice I/O readiness planning for the Cleverly operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, SETTINGS_FILE


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _load_settings() -> dict[str, Any]:
    try:
        from src.settings import load_settings

        loaded = load_settings()
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _load_features() -> dict[str, Any]:
    try:
        from src.settings import load_features

        loaded = load_features()
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _offline_mode() -> bool:
    try:
        from src.settings import offline_mode

        return bool(offline_mode())
    except Exception:
        return False


def _enabled(settings: dict[str, Any], key: str, default: bool) -> bool:
    return settings.get(key, default) is not False


def _effective_provider(settings: dict[str, Any], enabled_key: str, provider_key: str, default_enabled: bool) -> str:
    if not _enabled(settings, enabled_key, default_enabled):
        return "disabled"
    provider = _trim(settings.get(provider_key) or "disabled", 160)
    return provider or "disabled"


def _provider_label(provider: str) -> str:
    if provider == "browser":
        return "Browser"
    if provider == "local":
        return "Local"
    if provider.startswith("endpoint:"):
        return "Endpoint"
    if provider == "disabled":
        return "Off"
    return provider


def _provider_scope(provider: str) -> str:
    if provider == "browser":
        return "browser-client"
    if provider == "local":
        return "local-service"
    if provider.startswith("endpoint:"):
        return "model-endpoint"
    if provider == "disabled":
        return "disabled"
    return "unknown"


def _stats_available(stats: dict[str, Any] | None) -> bool | None:
    if not isinstance(stats, dict) or not stats:
        return None
    value = stats.get("available", stats.get("ready"))
    return bool(value)


def _provider_state(provider: str, stats: dict[str, Any] | None, *, offline: bool) -> str:
    if provider == "disabled":
        return "warn"
    if provider == "browser":
        return "ok"
    if provider.startswith("endpoint:") and offline:
        return "warn"
    available = _stats_available(stats)
    if available is True:
        return "ok"
    if available is False:
        return "warn"
    return "loading"


def _provider_detail(
    kind: str,
    provider: str,
    *,
    model: str,
    stats: dict[str, Any] | None,
    offline: bool,
) -> str:
    label = _provider_label(provider)
    if provider == "disabled":
        return f"{kind} is disabled in settings"
    if provider == "browser":
        return f"{label} {kind.lower()} is configured; browser capability and permission are checked client-side"
    if provider == "local":
        available = _stats_available(stats)
        suffix = " available" if available is True else (" not loaded" if available is False else " readiness is not probed by this plan")
        return f"Local {kind.lower()}{f' using {model}' if model else ''}{suffix}"
    if provider.startswith("endpoint:"):
        if offline:
            return f"Endpoint {kind.lower()} is configured but offline mode should block remote endpoints unless explicitly allowed"
        return f"Endpoint {kind.lower()} is configured; review provider privacy before starting"
    return f"{label} {kind.lower()}{f' using {model}' if model else ''}"


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


def _entry_rows(
    *,
    stt_configured: bool,
    tts_configured: bool,
    endpoint_voice: bool,
    offline_enabled: bool,
) -> list[dict[str, Any]]:
    state = "ok" if stt_configured else "warn"
    io_state = "ok" if stt_configured and tts_configured else "warn"
    privacy_state = "warn" if endpoint_voice else "ok"
    network_detail = (
        "endpoint voice is configured; starting voice remains permissioned and policy-reviewed"
        if endpoint_voice
        else "voice preflight does not use network access"
    )
    return [
        {
            "id": "voice-dashboard-route",
            "entry": "dashboard",
            "state": io_state,
            "badge": "dash",
            "title": "Dashboard voice preflight",
            "detail": "The Voice I/O card opens this read-only preflight before any listening or speaking starts.",
            "command_id": "open-voice-preflight",
            "start_command_id": "start-voice-command",
            "action": "open-voice-preflight",
            "actionLabel": "Plan",
            "executes": False,
            "starts_microphone": False,
            "records_audio": False,
            "speaks_audio": False,
            "uses_network": False,
            "requires_permission": True,
            "requires_user_activation": True,
        },
        {
            "id": "voice-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed voice command route",
            "detail": "Typed requests such as start voice command route to the same approval-gated voice command.",
            "command_id": "start-voice-command",
            "preflight_command_id": "open-voice-preflight",
            "action": "start-voice-command" if stt_configured else "enable-browser-voice-mode",
            "actionLabel": "Start" if stt_configured else "Enable",
            "executes": False,
            "starts_microphone": False,
            "records_audio": False,
            "speaks_audio": False,
            "uses_network": False,
            "requires_permission": True,
            "requires_user_activation": True,
        },
        {
            "id": "voice-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette voice route",
            "detail": "The command palette exposes voice start and setup commands under the operator trust policy.",
            "command_id": "start-voice-command",
            "preflight_command_id": "open-voice-preflight",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "executes": False,
            "starts_microphone": False,
            "records_audio": False,
            "speaks_audio": False,
            "uses_network": False,
            "requires_permission": True,
            "requires_user_activation": True,
        },
        {
            "id": "voice-button-route",
            "entry": "voice",
            "state": state,
            "badge": "mic",
            "title": "Voice button activation",
            "detail": "Microphone activation still requires an explicit user action and the browser permission prompt.",
            "command_id": "start-voice-command",
            "preflight_command_id": "open-voice-preflight",
            "action": "start-voice-command" if stt_configured else "enable-browser-voice-mode",
            "actionLabel": "Start" if stt_configured else "Enable",
            "executes": False,
            "starts_microphone": False,
            "records_audio": False,
            "speaks_audio": False,
            "uses_network": False,
            "requires_permission": True,
            "requires_user_activation": True,
        },
        {
            "id": "voice-workflow-route",
            "entry": "workflow",
            "state": privacy_state if not offline_enabled else ("warn" if endpoint_voice else "ok"),
            "badge": "flow",
            "title": "Workflow transcript route",
            "detail": f"Recognized transcripts route through operatorCommands after permissioned capture; {network_detail}.",
            "command_id": "start-voice-command",
            "preflight_command_id": "open-voice-preflight",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "starts_microphone": False,
            "records_audio": False,
            "speaks_audio": False,
            "uses_network": False,
            "requires_permission": True,
            "requires_user_activation": True,
        },
    ]


def _voice_alert_row(
    *,
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    requires_approval: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": requires_approval,
        "executes": False,
        "starts_microphone": False,
        "records_audio": False,
        "uploads_audio": False,
        "transcribes_audio": False,
        "synthesizes_audio": False,
        "speaks_audio": False,
        "changes_settings": False,
        "routes_commands": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _alert_rows(
    *,
    stt_provider: str,
    tts_provider: str,
    stt_state: str,
    tts_state: str,
    stt_configured: bool,
    tts_configured: bool,
    endpoint_voice: bool,
    local_voice: bool,
    browser_pair: bool,
    offline_enabled: bool,
    endpoint_feature: Any,
) -> list[dict[str, Any]]:
    rows = [
        _voice_alert_row(
            row_id="voice-microphone-permission",
            state="warn",
            badge="mic",
            title="Microphone permission required",
            detail="Voice input cannot start until the owner triggers voice mode and the browser grants microphone access.",
            action="start-voice-command" if stt_configured else "open-voice-preflight",
            action_label="Voice" if stt_configured else "Plan",
            requires_approval=True,
        ),
        _voice_alert_row(
            row_id="voice-user-activation-required",
            state="warn",
            badge="click",
            title="Voice start requires user activation",
            detail="The dashboard can show voice readiness, but it cannot record, transcribe, or speak without an explicit user action.",
            action="open-voice-preflight",
            action_label="Plan",
        ),
    ]
    if not stt_configured:
        rows.append(_voice_alert_row(
            row_id="voice-stt-disabled",
            state="warn",
            badge="stt",
            title="Speech-to-text is disabled",
            detail="Enable browser or local STT before voice commands can listen for transcripts.",
            action="enable-browser-voice-mode",
            action_label="Enable",
        ))
    elif stt_state == "warn":
        rows.append(_voice_alert_row(
            row_id="voice-stt-readiness",
            state="warn",
            badge="stt",
            title="Speech-to-text readiness needs review",
            detail=f"STT provider {stt_provider} is configured but not fully ready in this plan.",
            action="open-voice-preflight",
            action_label="Review",
        ))
    if not tts_configured:
        rows.append(_voice_alert_row(
            row_id="voice-tts-disabled",
            state="warn",
            badge="tts",
            title="Text-to-speech is disabled",
            detail="Voice output stays text-only until browser or local TTS is enabled.",
            action="enable-browser-voice-mode",
            action_label="Enable",
        ))
    elif tts_state == "warn":
        rows.append(_voice_alert_row(
            row_id="voice-tts-readiness",
            state="warn",
            badge="tts",
            title="Text-to-speech readiness needs review",
            detail=f"TTS provider {tts_provider} is configured but not fully ready in this plan.",
            action="open-voice-preflight",
            action_label="Review",
        ))
    if not browser_pair:
        rows.append(_voice_alert_row(
            row_id="voice-browser-setup-review",
            state="warn",
            badge="setup",
            title="Browser voice setup is not fully local",
            detail="Browser STT and browser TTS are not both selected; review provider privacy before starting voice.",
            action="enable-browser-voice-mode",
            action_label="Setup",
        ))
    if local_voice:
        rows.append(_voice_alert_row(
            row_id="voice-local-service-gate",
            state="warn",
            badge="local",
            title="Local voice service route requires review",
            detail="Local STT/TTS can use server audio routes only after user-submitted audio or text is provided.",
            action="open-voice-preflight",
            action_label="Review",
            requires_approval=True,
        ))
    if endpoint_voice:
        rows.append(_voice_alert_row(
            row_id="voice-endpoint-privacy-review",
            state="warn",
            badge="net",
            title="Endpoint voice privacy review",
            detail=(
                "Offline mode is active and endpoint voice should remain blocked unless explicitly allowed."
                if offline_enabled
                else "Endpoint voice is configured; review network and provider policy before starting."
            ),
            action="open-offline",
            action_label="Policy",
            requires_approval=True,
        ))
    if endpoint_voice and endpoint_feature is False:
        rows.append(_voice_alert_row(
            row_id="voice-endpoint-feature-disabled",
            state="warn",
            badge="flag",
            title="Endpoint voice feature disabled",
            detail="External model endpoints are disabled while an endpoint voice provider is configured.",
            action="open-offline",
            action_label="Policy",
        ))
    return rows[:10]


def _handoff_row(
    *,
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    requires_approval: bool = False,
    requires_permission: bool = False,
    network_after_approval: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": requires_approval,
        "requires_permission": requires_permission,
        "network_after_approval": network_after_approval,
        "executes": False,
        "starts_microphone": False,
        "records_audio": False,
        "uploads_audio": False,
        "transcribes_audio": False,
        "synthesizes_audio": False,
        "speaks_audio": False,
        "changes_settings": False,
        "routes_commands": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    stt_configured: bool,
    tts_configured: bool,
    endpoint_voice: bool,
    offline_enabled: bool,
) -> list[dict[str, Any]]:
    route_state = "ok" if stt_configured else "warn"
    output_state = "ok" if tts_configured else "warn"
    privacy_state = "warn" if endpoint_voice else "ok"
    privacy_detail = (
        "Endpoint voice is configured; any transcript or speech provider egress remains outside this plan and needs policy review."
        if endpoint_voice
        else "No endpoint voice provider is configured; transcript handoff stays in the local operator command path."
    )
    if endpoint_voice and offline_enabled:
        privacy_detail = "Offline mode is active while endpoint voice is configured; endpoint egress should remain blocked unless explicitly allowed."
    return [
        _handoff_row(
            row_id="voice-permission-handoff",
            state="warn",
            badge="ask",
            title="Permission handoff",
            detail="Voice capture begins only after an owner action and the browser microphone permission prompt.",
            action="start-voice-command" if stt_configured else "enable-browser-voice-mode",
            action_label="Voice" if stt_configured else "Enable",
            requires_approval=True,
            requires_permission=True,
        ),
        _handoff_row(
            row_id="voice-transcript-route-handoff",
            state=route_state,
            badge="route",
            title="Transcript route handoff",
            detail="Recognized transcript text routes through operatorCommands and /api/operator/route before a local command runs.",
            action="start-voice-command" if stt_configured else "open-voice-preflight",
            action_label="Route",
            requires_approval=True,
            requires_permission=True,
        ),
        _handoff_row(
            row_id="voice-trust-gate-handoff",
            state="ok",
            badge="trust",
            title="Trust gate handoff",
            detail="Destructive, network, credential, filesystem, and shell actions keep the selected command's approval policy.",
            action="open-trust-controls",
            action_label="Trust",
        ),
        _handoff_row(
            row_id="voice-activity-ledger-handoff",
            state="ok",
            badge="log",
            title="Activity ledger handoff",
            detail="Voice start, listen, no-speech, route, cancel, and error states are recorded as metadata only; audio is not stored.",
            action="open-activity-preflight",
            action_label="Activity",
        ),
        _handoff_row(
            row_id="voice-output-review-handoff",
            state=output_state,
            badge="tts",
            title="Voice output review",
            detail="Speech output is a separate explicit action; this preflight does not synthesize or play audio.",
            action="open-voice-preflight",
            action_label="Review",
            requires_approval=not tts_configured,
        ),
        _handoff_row(
            row_id="voice-endpoint-privacy-handoff",
            state=privacy_state,
            badge="net",
            title="Endpoint privacy handoff",
            detail=privacy_detail,
            action="open-offline",
            action_label="Policy",
            requires_approval=endpoint_voice,
            network_after_approval=endpoint_voice,
        ),
    ]


def run_operator_voice_plan(
    owner: str = "local",
    *,
    settings: dict[str, Any] | None = None,
    stt_stats: dict[str, Any] | None = None,
    tts_stats: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    offline: bool | None = None,
    data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return a read-only plan for voice input, voice output, and routing readiness."""
    owner = owner or "local"
    settings_data = dict(settings if isinstance(settings, dict) else _load_settings())
    features_data = dict(features if isinstance(features, dict) else _load_features())
    offline_enabled = _offline_mode() if offline is None else bool(offline)
    data_path = Path(data_root) if data_root is not None else Path(DATA_DIR)

    stt_provider = _effective_provider(settings_data, "stt_enabled", "stt_provider", False)
    tts_provider = _effective_provider(settings_data, "tts_enabled", "tts_provider", True)
    stt_model = _trim(settings_data.get("stt_model") or (stt_stats or {}).get("model") or "base", 160)
    tts_model = _trim(settings_data.get("tts_model") or (tts_stats or {}).get("model") or "tts-1", 160)
    tts_voice = _trim(settings_data.get("tts_voice") or (tts_stats or {}).get("voice") or "alloy", 160)
    endpoint_voice = stt_provider.startswith("endpoint:") or tts_provider.startswith("endpoint:")
    local_voice = stt_provider == "local" or tts_provider == "local"
    browser_voice = stt_provider == "browser" or tts_provider == "browser"
    browser_pair = stt_provider == "browser" and tts_provider == "browser"
    stt_configured = stt_provider != "disabled"
    tts_configured = tts_provider != "disabled"
    stt_state = _provider_state(stt_provider, stt_stats, offline=offline_enabled)
    tts_state = _provider_state(tts_provider, tts_stats, offline=offline_enabled)
    endpoint_feature = features_data.get("external_model_endpoints", True)

    if endpoint_voice and offline_enabled:
        state = "warn"
    elif stt_configured and tts_configured:
        state = "ok" if "warn" not in (stt_state, tts_state) else "warn"
    else:
        state = "warn"

    input_rows = [
        {
            "id": "microphone-permission",
            "state": "warn",
            "badge": "mic",
            "title": "Microphone permission gate",
            "detail": "Browser microphone access is requested only when the user starts voice input.",
            "action": "start-voice-command" if stt_configured else "open-voice-preflight",
            "actionLabel": "Start" if stt_configured else "Review",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "stt-provider",
            "state": stt_state,
            "badge": "stt",
            "title": "Speech-to-text provider",
            "detail": _provider_detail("STT", stt_provider, model=stt_model, stats=stt_stats, offline=offline_enabled),
            "action": "enable-browser-voice-mode" if stt_provider == "disabled" else "start-voice-command",
            "actionLabel": "Enable" if stt_provider == "disabled" else "Start",
            "executes": False,
            "requires_approval": stt_provider != "browser",
        },
        {
            "id": "browser-stt",
            "state": "ok" if stt_provider == "browser" else "loading",
            "badge": "web",
            "title": "Browser speech recognition",
            "detail": "Backend can confirm browser STT is selected, but Web Speech API support is verified in the browser.",
            "action": "open-voice-preflight",
            "actionLabel": "Browser",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "server-transcribe-route",
            "state": "warn" if stt_provider in ("local",) or stt_provider.startswith("endpoint:") else "ok",
            "badge": "api",
            "title": "Server transcription route",
            "detail": "/api/stt/transcribe accepts user-submitted audio only after recording/upload starts.",
            "action": "start-voice-command",
            "actionLabel": "Route",
            "executes": False,
            "requires_approval": True,
        },
    ]

    output_rows = [
        {
            "id": "tts-provider",
            "state": tts_state,
            "badge": "tts",
            "title": "Text-to-speech provider",
            "detail": _provider_detail("TTS", tts_provider, model=tts_model, stats=tts_stats, offline=offline_enabled),
            "action": "enable-browser-voice-mode" if tts_provider == "disabled" else "open-voice-preflight",
            "actionLabel": "Enable" if tts_provider == "disabled" else "Review",
            "executes": False,
            "requires_approval": tts_provider != "browser",
        },
        {
            "id": "browser-tts",
            "state": "ok" if tts_provider == "browser" else "loading",
            "badge": "web",
            "title": "Browser speech synthesis",
            "detail": "Backend can confirm browser TTS is selected, but speechSynthesis support is verified in the browser.",
            "action": "open-voice-preflight",
            "actionLabel": "Browser",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "server-synthesize-route",
            "state": "warn" if tts_provider in ("local",) or tts_provider.startswith("endpoint:") else "ok",
            "badge": "api",
            "title": "Server synthesis route",
            "detail": "/api/tts/synthesize produces audio only when a user action sends text to speak.",
            "action": "open-voice-preflight",
            "actionLabel": "Route",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "tts-cache",
            "state": "ok",
            "badge": "cache",
            "title": "TTS cache location",
            "detail": str(data_path / "tts_cache"),
            "action": "open-voice-preflight",
            "actionLabel": "Cache",
            "executes": False,
            "requires_approval": False,
        },
    ]

    routing_rows = [
        {
            "id": "voice-command",
            "state": "ok" if stt_configured else "warn",
            "badge": "route",
            "title": "Voice command route",
            "detail": "Recognized transcripts route through start-voice-command and the same operator command catalog as typed commands.",
            "action": "start-voice-command" if stt_configured else "open-voice-preflight",
            "actionLabel": "Route",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "setup-route",
            "state": "ok" if browser_pair else "warn",
            "badge": "setup",
            "title": "Browser voice setup route",
            "detail": "enable-browser-voice-mode writes local STT/TTS settings only after the user triggers that command.",
            "action": "enable-browser-voice-mode",
            "actionLabel": "Enable",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "trust-policy",
            "state": "ok",
            "badge": "trust",
            "title": "Trust policy alignment",
            "detail": "Voice transcripts inherit the selected command's trust tier; destructive, network, credential, filesystem, and shell actions remain approval-gated.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "activity-ledger",
            "state": "ok",
            "badge": "log",
            "title": "Activity timeline coverage",
            "detail": "The browser voice controller records start, listening, no-speech, route, and error metadata in data/operator_activity.json; audio is not stored.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "executes": False,
            "requires_approval": False,
        },
    ]

    permission_rows = [
        {
            "id": "browser-permission",
            "state": "warn",
            "badge": "ask",
            "title": "Browser permission required",
            "detail": "The plan cannot grant microphone permission; the browser prompt appears only after a user starts listening.",
            "action": "start-voice-command" if stt_configured else "enable-browser-voice-mode",
            "actionLabel": "Voice",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "user-activation",
            "state": "ok",
            "badge": "click",
            "title": "User activation required",
            "detail": "Recording and speaking are initiated by explicit UI/command actions, not by loading the dashboard.",
            "action": "open-voice-preflight",
            "actionLabel": "Policy",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "endpoint-egress",
            "state": "warn" if endpoint_voice else "ok",
            "badge": "net",
            "title": "Endpoint privacy review",
            "detail": (
                "Endpoint voice provider configured; review network/privacy policy before starting."
                if endpoint_voice
                else "No endpoint voice provider is configured."
            ),
            "action": "open-offline",
            "actionLabel": "Policy",
            "executes": False,
            "requires_approval": endpoint_voice,
        },
        {
            "id": "settings-write",
            "state": "ok",
            "badge": "cfg",
            "title": "Settings changes are separate",
            "detail": "This plan does not change STT/TTS settings; browser voice setup is a separate user-triggered command.",
            "action": "open-voice-preflight",
            "actionLabel": "Plan",
            "executes": False,
            "requires_approval": False,
        },
    ]

    api_actions = [
        _api_action(
            "operator-voice-plan",
            "GET",
            "/api/operator/voice-plan",
            risk="read-only",
            requires_approval=False,
            note="Returns provider, permission, route, and local path evidence only.",
        ),
        _api_action(
            "stt-stats",
            "GET",
            "/api/stt/stats",
            risk="local-read",
            requires_approval=False,
            note="Reports STT configuration and availability; some providers may lazy-load when their stats route runs.",
        ),
        _api_action(
            "tts-stats",
            "GET",
            "/api/tts/stats",
            risk="local-read",
            requires_approval=False,
            note="Reports TTS configuration and cache status; some providers may lazy-load when their stats route runs.",
        ),
        _api_action(
            "stt-transcribe",
            "POST",
            "/api/stt/transcribe",
            risk="audio-transcription",
            requires_approval=True,
            note="Only transcribes an uploaded recording after the user starts recording or attaches audio.",
        ),
        _api_action(
            "tts-synthesize",
            "POST",
            "/api/tts/synthesize",
            risk="audio-output",
            requires_approval=True,
            note="Only produces speech after the user sends text to speak.",
        ),
        _api_action(
            "voice-settings",
            "POST",
            "/api/auth/settings",
            risk="local-settings-write",
            requires_approval=True,
            note="Browser voice setup writes STT/TTS settings only when the user chooses that command.",
        ),
    ]

    evidence_rows = [
        {
            "id": "settings",
            "state": "ok",
            "badge": "cfg",
            "title": "Voice settings source",
            "detail": str(SETTINGS_FILE),
            "action": "open-voice-preflight",
            "actionLabel": "Plan",
        },
        {
            "id": "local-first",
            "state": "warn" if endpoint_voice else "ok",
            "badge": "local",
            "title": "Local-first posture",
            "detail": (
                "Endpoint voice is configured; network use remains outside this plan and should be policy-reviewed."
                if endpoint_voice
                else "Browser/local voice providers keep voice operations on the device unless a separate endpoint is selected."
            ),
            "action": "open-offline",
            "actionLabel": "Policy",
        },
        {
            "id": "route-proof",
            "state": "ok" if stt_configured else "warn",
            "badge": "cmd",
            "title": "Command routing proof",
            "detail": "start-voice-command captures one transcript and routes it through operatorCommands.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            "id": "read-only-proof",
            "state": "ok",
            "badge": "safe",
            "title": "Plan-only proof",
            "detail": "This endpoint inventories readiness and gates without recording, transcribing, synthesizing, or changing settings.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
    ]
    entry_rows = _entry_rows(
        stt_configured=stt_configured,
        tts_configured=tts_configured,
        endpoint_voice=endpoint_voice,
        offline_enabled=offline_enabled,
    )
    alert_rows = _alert_rows(
        stt_provider=stt_provider,
        tts_provider=tts_provider,
        stt_state=stt_state,
        tts_state=tts_state,
        stt_configured=stt_configured,
        tts_configured=tts_configured,
        endpoint_voice=endpoint_voice,
        local_voice=local_voice,
        browser_pair=browser_pair,
        offline_enabled=offline_enabled,
        endpoint_feature=endpoint_feature,
    )
    handoff_rows = _handoff_rows(
        stt_configured=stt_configured,
        tts_configured=tts_configured,
        endpoint_voice=endpoint_voice,
        offline_enabled=offline_enabled,
    )

    return {
        "mode": "read-only-voice-io-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "stt_provider": stt_provider,
            "tts_provider": tts_provider,
            "stt_model": stt_model,
            "tts_model": tts_model,
            "tts_voice": tts_voice,
            "stt_configured": stt_configured,
            "tts_configured": tts_configured,
            "browser_stt_configured": stt_provider == "browser",
            "browser_tts_configured": tts_provider == "browser",
            "browser_voice_configured": browser_pair,
            "local_voice_configured": local_voice,
            "endpoint_voice_configured": endpoint_voice,
            "external_endpoint_feature_enabled": endpoint_feature is not False,
            "offline_mode": offline_enabled,
            "voice_command_ready": stt_configured,
            "voice_io_ready": stt_configured and tts_configured,
            "requires_browser_permission": True,
            "requires_user_activation": True,
            "records_audio": False,
            "starts_microphone": False,
            "transcribes_audio": False,
            "speaks_audio": False,
            "synthesizes_audio": False,
            "changes_settings": False,
            "runs_shell": False,
            "uses_network": False,
            "network_possible_after_start": endpoint_voice,
            "activity_metadata_only": True,
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": sum(1 for row in entry_rows if row["state"] == "ok"),
            "voice_alert_count": len(alert_rows),
            "critical_voice_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": sum(1 for row in handoff_rows if row["state"] == "ok"),
            "route_command_id": "start-voice-command",
            "setup_command_id": "enable-browser-voice-mode",
            "next_action": "Open Voice Operations Preflight, enable browser voice if desired, then start voice after browser microphone approval.",
        },
        "providers": {
            "stt": {
                "provider": stt_provider,
                "label": _provider_label(stt_provider),
                "scope": _provider_scope(stt_provider),
                "model": stt_model,
                "stats_available": _stats_available(stt_stats),
            },
            "tts": {
                "provider": tts_provider,
                "label": _provider_label(tts_provider),
                "scope": _provider_scope(tts_provider),
                "model": tts_model,
                "voice": tts_voice,
                "stats_available": _stats_available(tts_stats),
            },
        },
        "input_rows": input_rows,
        "output_rows": output_rows,
        "routing_rows": routing_rows,
        "permission_rows": permission_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "entry_rows": entry_rows,
        "alert_rows": alert_rows,
        "handoff_rows": handoff_rows,
        "approval": {
            "required": False,
            "gate": "User activation and browser permission",
            "policy": "This endpoint only audits voice readiness. It does not start the microphone, record audio, upload audio, transcribe audio, synthesize speech, speak audio, change STT/TTS settings, run commands, use shell access, or use network access.",
            "disallowed_by_default": [
                "start microphone",
                "record audio",
                "transcribe audio",
                "speak audio",
                "change voice settings",
                "route destructive commands",
            ],
        },
        "paths": {
            "settings": str(SETTINGS_FILE),
            "data": str(data_path),
            "tts_cache": str(data_path / "tts_cache"),
            "stt_route": "/api/stt/transcribe",
            "tts_route": "/api/tts/synthesize",
            "activity": "data/operator_activity.json",
            "voice_command_script": "static/js/voiceCommand.js",
            "voice_recorder_script": "static/js/voiceRecorder.js",
            "tts_script": "static/js/tts-ai.js",
        },
    }
