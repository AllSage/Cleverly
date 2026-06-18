"""Startup helpers for optional bundled model endpoints."""

from __future__ import annotations

import json
import logging
import os
import uuid
from urllib.parse import urlparse

import httpx

from core.database import ModelEndpoint, SessionLocal
from src.endpoint_resolver import normalize_base
from src.offline_policy import is_local_model_url
from src.settings import load_features, load_settings, save_settings, offline_mode

logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ollama_api_root(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    parsed = urlparse(base if "://" in base else "http://" + base)
    if not parsed.scheme or not parsed.netloc:
        return ""
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api"):
        return root + path
    return root + "/api"


def _ollama_models(base_url: str, timeout: float = 5.0) -> list[str]:
    root = _ollama_api_root(base_url)
    if not root:
        return []
    if is_local_model_url(root):
        allowed = True
    elif offline_mode():
        allowed = False
    else:
        try:
            allowed = (load_features() or {}).get("external_model_endpoints") is not False
        except Exception as exc:
            logger.warning("Bundled Ollama feature check failed; blocking remote probe: %s", exc)
            allowed = False
    if not allowed:
        logger.info("Bundled Ollama model probe blocked: %s", root)
        return []
    try:
        response = httpx.get(root + "/tags", timeout=timeout)
        response.raise_for_status()
        data = response.json() or {}
    except Exception as exc:
        logger.info("Bundled Ollama model probe skipped: %s", exc)
        return []
    models = []
    for item in data.get("models") or []:
        name = item.get("name") or item.get("model")
        if name and name not in models:
            models.append(name)
    return models


def seed_ollama_endpoint_from_env() -> None:
    """Register the optional Docker-bundled Ollama endpoint when requested."""
    if not _truthy(os.getenv("CLEVERLY_AUTO_ADD_OLLAMA")):
        return

    base_url = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_URL") or "").strip()
    if not base_url:
        return
    base_url = normalize_base(base_url)

    model_hint = (os.getenv("OLLAMA_MODEL") or "").strip()
    models = _ollama_models(base_url)
    if model_hint and model_hint in models:
        models = [model_hint] + [m for m in models if m != model_hint]
    elif model_hint and not models:
        models = [model_hint]
    if not models:
        logger.info("Bundled Ollama endpoint found no models at %s", base_url)
        return

    name = (os.getenv("CLEVERLY_OLLAMA_ENDPOINT_NAME") or "Bundled Ollama").strip()
    db = SessionLocal()
    try:
        endpoint = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base_url).first()
        if endpoint is None:
            endpoint = ModelEndpoint(
                id=str(uuid.uuid4())[:8],
                name=name,
                base_url=base_url,
                api_key=None,
                is_enabled=True,
                cached_models=json.dumps(models),
                model_type="llm",
                supports_tools=None,
                owner=None,
            )
            db.add(endpoint)
            logger.info("Registered bundled Ollama endpoint %s with %d model(s)", base_url, len(models))
        else:
            endpoint.name = endpoint.name or name
            endpoint.is_enabled = True
            endpoint.cached_models = json.dumps(models)
            endpoint.model_type = endpoint.model_type or "llm"
            logger.info("Updated bundled Ollama endpoint %s with %d model(s)", base_url, len(models))
        db.commit()

        settings = load_settings()
        if not settings.get("default_endpoint_id"):
            settings["default_endpoint_id"] = endpoint.id
            settings["default_model"] = models[0]
            save_settings(settings)
            logger.info("Set bundled Ollama model as default: %s", models[0])
    finally:
        db.close()
