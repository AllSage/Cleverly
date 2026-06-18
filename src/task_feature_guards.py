"""Feature guards for scheduled task routes and execution."""

from __future__ import annotations

import re
import logging
from typing import Any, Mapping

from src.settings import load_features

logger = logging.getLogger(__name__)

EMAIL_ACTIONS = {
    "summarize_emails",
    "draft_email_replies",
    "extract_email_events",
    "mark_email_boundaries",
    "learn_sender_signatures",
    "check_email_urgency",
}

RESEARCH_ACTIONS = {"tidy_research"}
EMAIL_EVENTS = {"email_received"}
RESEARCH_EVENTS = {"research_completed"}


def feature_flags() -> dict[str, Any]:
    """Load feature flags, failing closed for background-capable online features."""
    try:
        return load_features() or {}
    except Exception as exc:
        logger.warning("Task feature flag check failed; disabling online task features: %s", exc)
        return {
            "deep_research": False,
            "email": False,
            "webhooks": False,
            "mcp": False,
        }


def feature_enabled(features: Mapping[str, Any], key: str) -> bool:
    return features.get(key) is not False


def is_email_output_target(output: str | None) -> bool:
    target = (output or "").strip()
    if target in {"email", "email:self"}:
        return True
    if target.startswith("email:"):
        return True
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target))


def is_mcp_output_target(output: str | None) -> bool:
    return (output or "").strip().startswith("mcp__")


def task_feature_disabled_reason(
    values: Mapping[str, Any],
    features: Mapping[str, Any] | None = None,
) -> str | None:
    """Return a user-facing reason when a task uses a disabled feature."""
    features = features or feature_flags()
    task_type = (values.get("task_type") or "llm").strip()
    trigger_type = (values.get("trigger_type") or "schedule").strip()
    trigger_event = (values.get("trigger_event") or "").strip()
    output_target = (values.get("output_target") or "session").strip()
    action = (values.get("action") or "").strip()

    if task_type == "research" and not feature_enabled(features, "deep_research"):
        return "Research tasks are disabled in offline mode."
    if trigger_type == "webhook" and not feature_enabled(features, "webhooks"):
        return "Webhook tasks are disabled in offline mode."
    if is_email_output_target(output_target) and not feature_enabled(features, "email"):
        return "Email task output is disabled in offline mode."
    if is_mcp_output_target(output_target) and not feature_enabled(features, "mcp"):
        return "MCP task output is disabled in offline mode."
    if action in EMAIL_ACTIONS and not feature_enabled(features, "email"):
        return "Email task actions are disabled in offline mode."
    if action in RESEARCH_ACTIONS and not feature_enabled(features, "deep_research"):
        return "Research task actions are disabled in offline mode."
    if trigger_event in EMAIL_EVENTS and not feature_enabled(features, "email"):
        return "Email task events are disabled in offline mode."
    if trigger_event in RESEARCH_EVENTS and not feature_enabled(features, "deep_research"):
        return "Research task events are disabled in offline mode."
    return None


def action_allowed(action: str, features: Mapping[str, Any] | None = None) -> bool:
    features = features or feature_flags()
    if action in EMAIL_ACTIONS and not feature_enabled(features, "email"):
        return False
    if action in RESEARCH_ACTIONS and not feature_enabled(features, "deep_research"):
        return False
    return True


def event_allowed(event_name: str, features: Mapping[str, Any] | None = None) -> bool:
    features = features or feature_flags()
    if event_name in EMAIL_EVENTS and not feature_enabled(features, "email"):
        return False
    if event_name in RESEARCH_EVENTS and not feature_enabled(features, "deep_research"):
        return False
    return True
