"""Webhook, API Token, and sync chat routes."""

import asyncio
import uuid
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Form
from pydantic import BaseModel, Field

from core.database import SessionLocal, Webhook
from src.webhook_manager import WebhookManager, validate_webhook_url, validate_events
from src.offline_policy import is_local_model_url
from src.settings import load_features, offline_mode
from src.auth_helpers import require_api_scope, owner_filter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["webhooks"])

# Input limits
MAX_NAME_LEN = 100
MAX_URL_LEN = 2048
MAX_SECRET_LEN = 256
MAX_MESSAGE_LEN = 32_000


from core.middleware import require_admin as _require_admin


def _feature_enabled(key: str) -> bool:
    if offline_mode():
        return False
    try:
        return (load_features() or {}).get(key) is not False
    except Exception as exc:
        logger.warning("Webhook feature check failed; disabling %s: %s", key, exc)
        return False


def _webhooks_enabled() -> bool:
    return _feature_enabled("webhooks")


def _external_endpoint_allowed(base_url: str) -> bool:
    if is_local_model_url(base_url):
        return True
    return _feature_enabled("external_model_endpoints")


def _raise_webhooks_disabled():
    raise HTTPException(403, "Webhooks are disabled in offline mode")


def _raise_external_endpoint_disabled():
    raise HTTPException(403, "External chat endpoints are disabled in offline mode")


def setup_webhook_routes(
    webhook_manager: WebhookManager,
    auth_manager,
    session_manager=None,
    api_key_manager=None,
) -> APIRouter:

    @router.get("/webhooks")
    def list_webhooks(request: Request):
        _require_admin(request)
        if not _webhooks_enabled():
            return []
        db = SessionLocal()
        try:
            hooks = db.query(Webhook).all()
            return [
                {
                    "id": w.id,
                    "name": w.name,
                    "url": w.url,
                    "has_secret": bool(w.secret),
                    "events": w.events.split(",") if w.events else [],
                    "is_active": w.is_active,
                    "last_triggered_at": w.last_triggered_at.isoformat() if w.last_triggered_at else None,
                    "last_status_code": w.last_status_code,
                    "last_error": w.last_error,
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                }
                for w in hooks
            ]
        finally:
            db.close()

    @router.post("/webhooks")
    def create_webhook(
        request: Request,
        name: str = Form(""),
        url: str = Form(""),
        secret: str = Form(""),
        events: str = Form(""),
    ):
        _require_admin(request)
        if not _webhooks_enabled():
            _raise_webhooks_disabled()
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            raise HTTPException(400, "Webhook name is required")
        try:
            url = validate_webhook_url(url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            events = validate_events(events)
        except ValueError as e:
            raise HTTPException(400, str(e))

        secret_val = secret.strip()[:MAX_SECRET_LEN] or None
        # Encrypt the secret at rest using the same Fernet key as API keys
        encrypted_secret = None
        if secret_val and api_key_manager:
            encrypted_secret = api_key_manager.encrypt_api_key(secret_val)
        elif secret_val:
            encrypted_secret = secret_val  # Fallback if no encryption available

        webhook_id = str(uuid.uuid4())[:8]
        db = SessionLocal()
        try:
            db.add(Webhook(
                id=webhook_id,
                name=name,
                url=url,
                secret=encrypted_secret,
                events=events,
                is_active=True,
            ))
            db.commit()
        finally:
            db.close()

        return {"id": webhook_id, "name": name}

    @router.post("/webhooks/{webhook_id}/test")
    async def test_webhook(request: Request, webhook_id: str):
        _require_admin(request)
        if not _webhooks_enabled():
            _raise_webhooks_disabled()
        db = SessionLocal()
        try:
            wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            if not wh:
                raise HTTPException(404, "Webhook not found")
            url, secret = wh.url, wh.secret
        finally:
            db.close()

        await webhook_manager.deliver_test(webhook_id, url, secret)
        return {"status": "sent"}

    @router.patch("/webhooks/{webhook_id}")
    def toggle_webhook(request: Request, webhook_id: str):
        _require_admin(request)
        if not _webhooks_enabled():
            _raise_webhooks_disabled()
        db = SessionLocal()
        try:
            wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            if not wh:
                raise HTTPException(404, "Webhook not found")
            wh.is_active = not wh.is_active
            db.commit()
            return {"id": webhook_id, "is_active": wh.is_active}
        finally:
            db.close()

    @router.delete("/webhooks/{webhook_id}")
    def delete_webhook(request: Request, webhook_id: str):
        _require_admin(request)
        db = SessionLocal()
        try:
            deleted = db.query(Webhook).filter(Webhook.id == webhook_id).delete()
            db.commit()
            if not deleted:
                raise HTTPException(404, "Webhook not found")
        finally:
            db.close()
        return {"status": "deleted"}

    # ================================================================
    # Sync Chat Endpoint (for n8n / Make / Activepieces)
    # ================================================================

    # Known provider base URLs — auto-resolved from api_key prefix or model name
    KNOWN_PROVIDERS = {
        "deepseek": "https://api.deepseek.com/v1",
        "openai": "https://api.openai.com/v1",
        "mistral": "https://api.mistral.ai/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "https://ollama.com/api",
        "fireworks": "https://api.fireworks.ai/inference/v1",
    }

    # Model prefix → provider mapping for auto-detection
    MODEL_PROVIDER_MAP = {
        "deepseek": "deepseek",
        "gpt-": "openai",
        "o1": "openai",
        "o3": "openai",
        "o4": "openai",
        "mistral": "mistral",
        "llama": "groq",
        "mixtral": "groq",
    }

    def _resolve_base_url(model: Optional[str], provider: Optional[str]) -> Optional[str]:
        """Try to auto-resolve a base URL from provider name or model prefix."""
        if provider and provider.lower() in KNOWN_PROVIDERS:
            return KNOWN_PROVIDERS[provider.lower()]
        if model:
            model_lower = model.lower()
            for prefix, prov in MODEL_PROVIDER_MAP.items():
                if model_lower.startswith(prefix):
                    return KNOWN_PROVIDERS[prov]
        return None

    class SyncChatRequest(BaseModel):
        message: str = Field(..., max_length=MAX_MESSAGE_LEN)
        model: Optional[str] = Field(None, max_length=200)
        session: Optional[str] = Field(None, max_length=100)
        api_key: Optional[str] = Field(None, max_length=256)
        base_url: Optional[str] = Field(None, max_length=MAX_URL_LEN)
        provider: Optional[str] = Field(None, max_length=50)

    @router.post("/v1/chat")
    async def sync_chat(request: Request, body: SyncChatRequest):
        token_owner = require_api_scope(request, "chat")

        from core.models import ChatMessage
        from src.llm_core import llm_call_async
        from core.database import ModelEndpoint
        from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, normalize_base

        message = body.message.strip()
        if not message:
            raise HTTPException(400, "Message is required")

        session_id = body.session
        sess = None

        # --- Case 1: Resume an existing session ---
        if session_id and session_manager:
            try:
                sess = session_manager.get_session(session_id)
            except (KeyError, Exception):
                raise HTTPException(404, "Session not found")
            # SECURITY: verify the API-token's user owns this session — without
            # this any token holder could resume any user's chat by passing its
            # ID. The token's user is on request.state.user (set by API-token
            # middleware); fall back to require_user if not present.
            _sess_owner = getattr(sess, "owner", None)
            if _sess_owner and _sess_owner != token_owner:
                raise HTTPException(404, "Session not found")

        # --- Case 2: Direct API key + model (no pre-configured endpoint needed) ---
        if not sess and body.api_key:
            api_key = body.api_key.strip()
            model = body.model or "deepseek-chat"

            # Resolve base_url: explicit > provider name > model prefix auto-detect
            base_url = body.base_url.strip().rstrip("/") if body.base_url else None
            if not base_url:
                base_url = _resolve_base_url(model, body.provider)
            if not base_url:
                raise HTTPException(400,
                    "Could not auto-detect provider. Pass base_url (e.g. 'https://api.deepseek.com/v1') "
                    "or provider ('deepseek', 'openai', 'groq', etc.)")

            base_url = normalize_base(base_url)
            if not _external_endpoint_allowed(base_url):
                _raise_external_endpoint_disabled()
            endpoint_url = build_chat_url(base_url)

            if not session_manager:
                raise HTTPException(500, "Session manager not available")

            sid = str(uuid.uuid4())
            sess = session_manager.create_session(
                session_id=sid, name="API Chat", endpoint_url=endpoint_url,
                model=model, owner=token_owner,
            )
            sess.headers = build_headers(api_key, base_url)
            session_manager.save_sessions()
            session_id = sid

        # --- Case 3: Fall back to first configured ModelEndpoint ---
        if not sess:
            db = SessionLocal()
            try:
                q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
                q = owner_filter(q, ModelEndpoint, token_owner)
                ep = q.first()
            finally:
                db.close()

            if not ep:
                raise HTTPException(400,
                    "No session, api_key, or configured endpoints. "
                    "Pass api_key + model, or configure an endpoint in Admin.")

            base_url = normalize_base(ep.base_url)
            if not _external_endpoint_allowed(base_url):
                _raise_external_endpoint_disabled()
            endpoint_url = build_chat_url(base_url)
            model = body.model or "auto"
            api_key = ep.api_key

            if model == "auto":
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        models_url = build_models_url(base_url)
                        hdrs = build_headers(api_key, base_url)
                        resp = await client.get(models_url, headers=hdrs)
                        resp.raise_for_status()
                        data = resp.json()
                        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                        if not ids:
                            ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                        model = ids[0] if ids else "auto"
                except Exception:
                    raise HTTPException(500, "Could not discover models from endpoint")

            if not session_manager:
                raise HTTPException(500, "Session manager not available")

            sid = str(uuid.uuid4())
            sess = session_manager.create_session(
                session_id=sid, name="API Chat", endpoint_url=endpoint_url,
                model=model, owner=token_owner,
            )
            if api_key:
                sess.headers = build_headers(api_key, base_url)
                session_manager.save_sessions()
            session_id = sid

        # --- Send message and get response ---
        if not _external_endpoint_allowed(getattr(sess, "endpoint_url", "") or ""):
            _raise_external_endpoint_disabled()

        sess.add_message(ChatMessage("user", message))

        messages = [{"role": m.role, "content": m.content} for m in sess.history]

        reply = await llm_call_async(
            sess.endpoint_url, sess.model, messages,
            headers=sess.headers, timeout=120,
        )
        sess.add_message(ChatMessage("assistant", reply))
        session_manager.save_sessions()

        asyncio.create_task(webhook_manager.fire("chat.completed", {
            "session_id": session_id, "model": sess.model,
            "user_message": message[:2000], "response": reply[:2000],
        }))

        return {"response": reply, "session_id": session_id, "model": sess.model}

    return router
