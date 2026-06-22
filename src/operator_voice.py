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
            "state": "loading",
            "badge": "log",
            "title": "Activity timeline coverage",
            "detail": "Voice actions should be recorded in the local operator activity ledger when routed through the Command Center.",
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
            "voice_command_script": "static/js/voiceCommand.js",
            "voice_recorder_script": "static/js/voiceRecorder.js",
            "tts_script": "static/js/tts-ai.js",
        },
    }
