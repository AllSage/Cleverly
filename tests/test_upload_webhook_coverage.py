import asyncio
import datetime as dt
import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


def _fresh_module(name: str):
    if name == "routes.webhook_routes":
        sys.modules.pop("src.webhook_manager", None)
        sys.modules.pop("src.database", None)
    sys.modules.pop(name, None)
    return importlib.import_module(name)


class Expr:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return Expr(self.name, other)


class RequestLike:
    def __init__(
        self,
        *,
        user="alice",
        body=None,
        client_host="127.0.0.1",
        auth_manager=None,
        api_token=True,
        scopes=None,
        token_owner="alice",
    ):
        self._body = body or {}
        self.client = SimpleNamespace(host=client_host)
        self.state = SimpleNamespace(
            current_user=user,
            user=user,
            api_token=api_token,
            api_token_scopes=scopes if scopes is not None else ["chat"],
            api_token_owner=token_owner,
        )
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))

    async def json(self):
        return self._body


def test_upload_routes_upload_download_vision_and_cleanup(monkeypatch, tmp_path):
    upload_routes = _fresh_module("routes.upload_routes")
    constants = importlib.import_module("src.constants")
    doc_processor = importlib.import_module("src.document_processor")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(upload_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(upload_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(doc_processor, "analyze_image_with_vl", lambda path: f"vision:{path}")

    class Upload:
        def __init__(self, filename):
            self.filename = filename

    class UploadHandler:
        def __init__(self):
            self.upload_rate_log = {}
            self.max_concurrent_uploads = 2
            self.saved = []
            self.cleanups = 0
            self.rate_cleanups = 0
            self.valid = True
            self.inside = True

        def save_upload(self, upload, client_ip, owner=None):
            if upload.filename == "bad.txt":
                raise RuntimeError("bad upload")
            meta = {
                "id": f"id-{upload.filename}",
                "name": upload.filename,
                "mime": "text/plain",
                "size": 10,
                "hash": "abc",
                "uploaded_at": "now",
                "owner": owner,
            }
            self.saved.append((upload.filename, client_ip, owner))
            return meta

        def cleanup_old_uploads(self):
            self.cleanups += 1
            return 3

        def get_upload_stats(self):
            return {"files": len(self.saved)}

        def validate_upload_id(self, file_id):
            return self.valid and ".." not in file_id

        def inside_base_dir(self, path):
            return self.inside and str(path).startswith(str(upload_dir))

        def cleanup_rate_limits(self):
            self.rate_cleanups += 1

    handler = UploadHandler()
    router, periodic_cleanup = upload_routes.setup_upload_routes(handler)

    with pytest.raises(HTTPException) as empty_upload:
        asyncio.run(_endpoint(router, "/api/upload", "POST")(RequestLike(), []))
    assert empty_upload.value.status_code == 400

    handler.upload_rate_log["10.0.0.5"] = [9999999999.0]
    handler.max_concurrent_uploads = 1
    with pytest.raises(HTTPException) as limited:
        asyncio.run(_endpoint(router, "/api/upload", "POST")(RequestLike(client_host="10.0.0.5"), [Upload("a.txt")]))
    assert limited.value.status_code == 429

    handler.upload_rate_log.clear()
    handler.max_concurrent_uploads = 2
    uploaded = asyncio.run(_endpoint(router, "/api/upload", "POST")(RequestLike(), [Upload("bad.txt"), Upload("good.txt")]))
    assert uploaded["files"][0]["id"] == "id-good.txt"
    assert handler.saved[-1] == ("good.txt", "127.0.0.1", "alice")

    with pytest.raises(HTTPException) as all_failed:
        asyncio.run(_endpoint(router, "/api/upload", "POST")(RequestLike(), [Upload("bad.txt")]))
    assert all_failed.value.status_code == 500

    assert asyncio.run(_endpoint(router, "/api/upload/cleanup", "POST")(RequestLike())) == {
        "status": "success",
        "files_cleaned": 3,
    }
    assert asyncio.run(_endpoint(router, "/api/upload/stats")(RequestLike())) == {"files": 1}
    handler.get_upload_stats = lambda: (_ for _ in ()).throw(RuntimeError("stats failed"))
    with pytest.raises(HTTPException) as stats_failed:
        asyncio.run(_endpoint(router, "/api/upload/stats")(RequestLike()))
    assert stats_failed.value.status_code == 500

    image_id = "image.png"
    image_path = upload_dir / image_id
    image_path.write_bytes(b"not really an image")
    (upload_dir / "doc.txt").write_text("doc", encoding="utf-8")
    (upload_dir / "uploads.json").write_text(
        json.dumps(
            {
                "img": {"id": image_id, "name": "Original.png", "owner": "alice"},
                "doc": {"id": "doc.txt", "name": "Doc.txt", "owner": "alice"},
            }
        ),
        encoding="utf-8",
    )

    handler.valid = False
    with pytest.raises(HTTPException) as invalid_file:
        asyncio.run(_endpoint(router, "/api/upload/{file_id}")(RequestLike(), "bad"))
    assert invalid_file.value.status_code == 400

    handler.valid = True
    with pytest.raises(HTTPException) as missing_file:
        asyncio.run(_endpoint(router, "/api/upload/{file_id}")(RequestLike(), "missing.png"))
    assert missing_file.value.status_code == 404

    handler.inside = False
    with pytest.raises(HTTPException) as outside:
        asyncio.run(_endpoint(router, "/api/upload/{file_id}")(RequestLike(), image_id))
    assert outside.value.status_code == 403

    handler.inside = True

    class AuthManager:
        is_configured = True

        def is_admin(self, user):
            return False

    with pytest.raises(HTTPException) as denied_owner:
        asyncio.run(_endpoint(router, "/api/upload/{file_id}")(RequestLike(user="bob", auth_manager=AuthManager()), image_id))
    assert denied_owner.value.status_code == 404

    response = asyncio.run(_endpoint(router, "/api/upload/{file_id}")(RequestLike(auth_manager=AuthManager()), image_id, thumb=1))
    assert isinstance(response, FileResponse)
    assert str(response.path).endswith(image_id)

    with pytest.raises(HTTPException) as not_image:
        asyncio.run(_endpoint(router, "/api/upload/{file_id}/vision")(RequestLike(), "doc.txt"))
    assert not_image.value.status_code == 400

    vision = asyncio.run(_endpoint(router, "/api/upload/{file_id}/vision")(RequestLike(), image_id, force=1))
    assert vision["cached"] is False
    assert "image.png" in vision["text"]
    cached = asyncio.run(_endpoint(router, "/api/upload/{file_id}/vision")(RequestLike(), image_id))
    assert cached == {"text": vision["text"], "cached": True}

    with pytest.raises(HTTPException) as bad_vision_text:
        asyncio.run(_endpoint(router, "/api/upload/{file_id}/vision", "PUT")(RequestLike(body={"text": 3}), image_id))
    assert bad_vision_text.value.status_code == 400
    assert asyncio.run(_endpoint(router, "/api/upload/{file_id}/vision", "PUT")(RequestLike(body={"text": "edited"}), image_id)) == {
        "ok": True
    }
    assert (upload_dir / ".vision" / f"{image_id}.txt").read_text(encoding="utf-8") == "edited"

    sleep_calls = 0

    async def fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise StopAsyncIteration

    monkeypatch.setattr(upload_routes.asyncio, "sleep", fake_sleep)
    with pytest.raises(StopAsyncIteration):
        asyncio.run(periodic_cleanup())
    assert handler.rate_cleanups == 1


def test_webhook_routes_crud_and_sync_chat_paths(monkeypatch):
    webhook_routes = _fresh_module("routes.webhook_routes")
    monkeypatch.setattr(webhook_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(webhook_routes, "offline_mode", lambda: False)
    monkeypatch.setattr(webhook_routes, "validate_webhook_url", lambda url: url.strip())
    monkeypatch.setattr(webhook_routes, "validate_events", lambda events: ",".join(e.strip() for e in events.split(",") if e.strip()))
    monkeypatch.setattr(webhook_routes.uuid, "uuid4", lambda: "new-webhook-id")

    class FakeWebhook:
        id = Column("id")
        is_active = Column("is_active")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.last_triggered_at = kwargs.get("last_triggered_at")
            self.last_status_code = kwargs.get("last_status_code")
            self.last_error = kwargs.get("last_error")
            self.created_at = kwargs.get("created_at", dt.datetime(2026, 1, 1))

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model
            self.filters = []

        def filter(self, *conditions):
            self.filters.extend(c for c in conditions if isinstance(c, Expr))
            return self

        def all(self):
            return list(self.db.webhooks) if self.model is FakeWebhook else []

        def first(self):
            if self.model is FakeWebhook:
                rows = list(self.db.webhooks)
                for condition in self.filters:
                    if condition.name == "id":
                        rows = [row for row in rows if row.id == condition.value]
                return rows[0] if rows else None
            return self.db.endpoint

        def delete(self):
            row = self.first()
            if not row:
                return 0
            self.db.webhooks.remove(row)
            return 1

    class DB:
        def __init__(self):
            self.webhooks = [
                FakeWebhook(
                    id="wh1",
                    name="Hook",
                    url="https://example.test/hook",
                    secret="secret",
                    events="chat.completed,session.created",
                    is_active=True,
                    last_triggered_at=dt.datetime(2026, 1, 2),
                    last_status_code=200,
                    created_at=dt.datetime(2026, 1, 1),
                )
            ]
            self.endpoint = None
            self.added = []
            self.commits = 0
            self.closed = 0

        def query(self, model):
            return Query(self, model)

        def add(self, row):
            self.added.append(row)
            self.webhooks.append(row)

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed += 1

    class ApiKeyManager:
        def encrypt_api_key(self, value):
            return f"enc:{value}"

    class WebhookManager:
        def __init__(self):
            self.delivered = []
            self.fired = []

        async def deliver_test(self, webhook_id, url, secret):
            self.delivered.append((webhook_id, url, secret))

        async def fire(self, event, payload):
            self.fired.append((event, payload))

    db = DB()
    manager = WebhookManager()
    monkeypatch.setattr(webhook_routes, "Webhook", FakeWebhook)
    monkeypatch.setattr(webhook_routes, "SessionLocal", lambda: db)
    router = webhook_routes.setup_webhook_routes(manager, auth_manager=None, session_manager=None, api_key_manager=ApiKeyManager())

    listed = _endpoint(router, "/api/webhooks")(RequestLike())
    assert listed[0]["id"] == "wh1"
    assert listed[0]["events"] == ["chat.completed", "session.created"]

    monkeypatch.setattr(webhook_routes, "offline_mode", lambda: True)
    assert _endpoint(router, "/api/webhooks")(RequestLike()) == []
    with pytest.raises(HTTPException) as offline_create:
        _endpoint(router, "/api/webhooks", "POST")(RequestLike(), name="Hook", url="https://example.test", events="chat.completed")
    assert offline_create.value.status_code == 403
    monkeypatch.setattr(webhook_routes, "offline_mode", lambda: False)

    with pytest.raises(HTTPException) as missing_name:
        _endpoint(router, "/api/webhooks", "POST")(RequestLike(), name="  ", url="https://example.test", events="chat.completed")
    assert missing_name.value.status_code == 400

    created = _endpoint(router, "/api/webhooks", "POST")(
        RequestLike(),
        name="New hook",
        url=" https://example.test/new ",
        secret="topsecret",
        events="chat.completed",
    )
    assert created == {"id": "new-webh", "name": "New hook"}
    assert db.added[-1].secret == "enc:topsecret"

    assert asyncio.run(_endpoint(router, "/api/webhooks/{webhook_id}/test", "POST")(RequestLike(), "wh1")) == {"status": "sent"}
    assert manager.delivered[-1] == ("wh1", "https://example.test/hook", "secret")
    with pytest.raises(HTTPException) as missing_test:
        asyncio.run(_endpoint(router, "/api/webhooks/{webhook_id}/test", "POST")(RequestLike(), "missing"))
    assert missing_test.value.status_code == 404

    toggled = _endpoint(router, "/api/webhooks/{webhook_id}", "PATCH")(RequestLike(), "wh1")
    assert toggled == {"id": "wh1", "is_active": False}
    assert _endpoint(router, "/api/webhooks/{webhook_id}", "DELETE")(RequestLike(), "wh1") == {"status": "deleted"}
    with pytest.raises(HTTPException) as missing_delete:
        _endpoint(router, "/api/webhooks/{webhook_id}", "DELETE")(RequestLike(), "missing")
    assert missing_delete.value.status_code == 404


def test_webhook_sync_chat_existing_and_direct_sessions(monkeypatch):
    webhook_routes = _fresh_module("routes.webhook_routes")
    monkeypatch.setattr(webhook_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(webhook_routes, "offline_mode", lambda: False)
    monkeypatch.setattr(webhook_routes.uuid, "uuid4", lambda: "session-new")

    class ChatMessage:
        def __init__(self, role, content, metadata=None):
            self.role = role
            self.content = content
            self.metadata = metadata

    class Session:
        def __init__(self, session_id, owner="alice", model="model", endpoint_url="http://local/chat"):
            self.id = session_id
            self.owner = owner
            self.model = model
            self.endpoint_url = endpoint_url
            self.headers = {}
            self.history = []

        def add_message(self, message):
            self.history.append(message)

    class SessionManager:
        def __init__(self):
            self.sessions = {"s1": Session("s1", owner="alice"), "other": Session("other", owner="bob")}
            self.saved = 0

        def get_session(self, session_id):
            if session_id not in self.sessions:
                raise KeyError(session_id)
            return self.sessions[session_id]

        def create_session(self, **kwargs):
            session = Session(kwargs["session_id"], owner=kwargs.get("owner"), model=kwargs["model"], endpoint_url=kwargs["endpoint_url"])
            self.sessions[session.id] = session
            return session

        def save_sessions(self):
            self.saved += 1

    class WebhookManager:
        async def fire(self, event, payload):
            return (event, payload)

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return SimpleNamespace(done=lambda: True)

    async def fake_llm(endpoint_url, model, messages, headers=None, timeout=None):
        assert messages[-1]["role"] == "user"
        assert timeout == 120
        return f"reply from {model}"

    core_models = importlib.import_module("core.models")
    auth_helpers = importlib.import_module("src.auth_helpers")
    llm_core = importlib.import_module("src.llm_core")
    endpoint_resolver = importlib.import_module("src.endpoint_resolver")
    monkeypatch.setattr(core_models, "ChatMessage", ChatMessage)
    monkeypatch.setattr(auth_helpers, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    monkeypatch.setattr(endpoint_resolver, "normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(endpoint_resolver, "build_chat_url", lambda base: f"{base}/chat")
    monkeypatch.setattr(endpoint_resolver, "build_headers", lambda key, base: {"Authorization": f"Bearer {key}", "Base": base})
    monkeypatch.setattr(endpoint_resolver, "build_models_url", lambda base: f"{base}/models")
    monkeypatch.setattr(webhook_routes.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(webhook_routes, "SessionLocal", lambda: SimpleNamespace(query=lambda model: SimpleNamespace(filter=lambda *_: SimpleNamespace(first=lambda: None)), close=lambda: None))

    session_manager = SessionManager()
    router = webhook_routes.setup_webhook_routes(WebhookManager(), auth_manager=None, session_manager=session_manager)
    sync_chat = _endpoint(router, "/api/v1/chat", "POST")

    with pytest.raises(HTTPException) as no_token:
        asyncio.run(sync_chat(RequestLike(api_token=False), SimpleNamespace(message="hi", session=None, api_key=None, model=None, base_url=None, provider=None)))
    assert no_token.value.status_code == 403

    with pytest.raises(HTTPException) as no_scope:
        asyncio.run(sync_chat(RequestLike(scopes=["memory"]), SimpleNamespace(message="hi", session=None, api_key=None, model=None, base_url=None, provider=None)))
    assert no_scope.value.status_code == 403

    with pytest.raises(HTTPException) as blank:
        asyncio.run(sync_chat(RequestLike(), SimpleNamespace(message="  ", session=None, api_key=None, model=None, base_url=None, provider=None)))
    assert blank.value.status_code == 400

    with pytest.raises(HTTPException) as wrong_owner:
        asyncio.run(sync_chat(RequestLike(token_owner="alice"), SimpleNamespace(message="hi", session="other", api_key=None, model=None, base_url=None, provider=None)))
    assert wrong_owner.value.status_code == 404

    existing = asyncio.run(sync_chat(RequestLike(token_owner="alice"), SimpleNamespace(message="hi", session="s1", api_key=None, model=None, base_url=None, provider=None)))
    assert existing == {"response": "reply from model", "session_id": "s1", "model": "model"}
    assert [m.role for m in session_manager.sessions["s1"].history] == ["user", "assistant"]

    with pytest.raises(HTTPException) as unknown_provider:
        asyncio.run(sync_chat(RequestLike(), SimpleNamespace(message="hi", session=None, api_key="sk", model="unknown-model", base_url=None, provider=None)))
    assert unknown_provider.value.status_code == 400

    direct = asyncio.run(
        sync_chat(
            RequestLike(token_owner="alice"),
            SimpleNamespace(message="hello", session=None, api_key="sk-direct", model="gpt-test", base_url=None, provider=None),
        )
    )
    assert direct["session_id"] == "session-new"
    assert direct["model"] == "gpt-test"
    assert session_manager.sessions["session-new"].endpoint_url == "https://api.openai.com/v1/chat"
    assert session_manager.sessions["session-new"].headers["Authorization"] == "Bearer sk-direct"
    assert created_tasks
