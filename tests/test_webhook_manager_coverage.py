import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import sys
from types import SimpleNamespace

import pytest

if "src.database" in sys.modules and not hasattr(sys.modules["src.database"], "Webhook"):
    sys.modules["src.database"].Webhook = SimpleNamespace

import src.webhook_manager as manager_module


class Expr:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return Expr(self.name, other)


class FakeWebhook(SimpleNamespace):
    id = Column("id")
    is_active = Column("is_active")


class FakeQuery:
    def __init__(self, db):
        self.db = db
        self.filters = []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def all(self):
        return list(self.db.webhooks)

    def update(self, values):
        if self.db.update_raises:
            raise RuntimeError("update failed")
        self.db.updates.append(values)
        return 1


class FakeDB:
    def __init__(self, webhooks=None, *, update_raises=False):
        self.webhooks = webhooks or []
        self.update_raises = update_raises
        self.updates = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def query(self, _model):
        return FakeQuery(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class FakeClient:
    def __init__(self, *, status_code=204, raises=False):
        self.status_code = status_code
        self.raises = raises
        self.posts = []
        self.closed = False

    async def post(self, url, content, headers):
        self.posts.append((url, content, headers))
        if self.raises:
            raise RuntimeError("connect to http://10.0.0.1:8000 failed")
        return SimpleNamespace(status_code=self.status_code)

    async def aclose(self):
        self.closed = True


class ApiKeyManager:
    def __init__(self, *, raises=False):
        self.raises = raises

    def decrypt_api_key(self, value):
        if self.raises:
            raise RuntimeError("bad key")
        return f"plain:{value}"


def install_db(monkeypatch, db):
    monkeypatch.setattr(manager_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(manager_module, "Webhook", FakeWebhook)
    return db


def make_manager(api_key_manager=None, client=None):
    manager = manager_module.WebhookManager(api_key_manager=api_key_manager)
    manager._client = client or FakeClient()
    return manager


def test_constructor_does_not_create_http_client_offline(monkeypatch):
    monkeypatch.setattr(manager_module, "offline_mode", lambda: True)
    monkeypatch.setattr(
        manager_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("offline webhook manager should not create HTTP clients")),
    )

    manager = manager_module.WebhookManager()

    assert manager._client is None


def test_hostname_resolution_and_private_url_detection(monkeypatch):
    def fake_getaddrinfo(hostname, _port):
        if hostname == "bad.test":
            raise OSError("dns failed")
        return [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("not-an-ip", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert manager_module._resolve_hostname_ips("public.test") == [ipaddress.ip_address("8.8.8.8")]
    assert manager_module._resolve_hostname_ips("bad.test") == []
    assert manager_module._ip_is_private(ipaddress.ip_address("127.0.0.1")) is True
    assert manager_module._ip_is_private(ipaddress.ip_address("8.8.8.8")) is False

    assert manager_module._is_private_url("http:///missing-host") is True
    assert manager_module._is_private_url("http://localhost/hook") is True
    assert manager_module._is_private_url("http://service.internal/hook") is True
    assert manager_module._is_private_url("http://127.0.0.1/hook") is True
    assert manager_module._is_private_url("http://bad.test/hook") is True
    assert manager_module._is_private_url("http://public.test/hook") is False
    assert manager_module._is_private_url("http://[bad") is True


def test_validation_helpers(monkeypatch):
    monkeypatch.setattr(manager_module, "_is_private_url", lambda url: "private" in url)

    assert manager_module.validate_webhook_url(" https://example.test/hook ") == "https://example.test/hook"
    with pytest.raises(ValueError, match="too long"):
        manager_module.validate_webhook_url("https://example.test/" + ("a" * 2049))
    with pytest.raises(ValueError, match="http or https"):
        manager_module.validate_webhook_url("ftp://example.test")
    with pytest.raises(ValueError, match="hostname"):
        manager_module.validate_webhook_url("https:///hook")
    with pytest.raises(ValueError, match="private/internal"):
        manager_module.validate_webhook_url("https://private.test")

    assert manager_module.validate_events(" chat.completed, session.created ") == "chat.completed,session.created"
    with pytest.raises(ValueError, match="At least one"):
        manager_module.validate_events(" , ")
    with pytest.raises(ValueError, match="Invalid events"):
        manager_module.validate_events("chat.completed,bad.event")

    error = "failed at http://secret.example:8080 from 10.1.2.3:9000"
    assert manager_module.sanitize_error(error, max_len=50) == "failed at [redacted-url] from [redacted]"


def test_secret_decryption_branches():
    assert make_manager()._decrypt_secret(None) is None
    assert make_manager()._decrypt_secret("legacy-secret") == "legacy-secret"
    assert make_manager(ApiKeyManager())._decrypt_secret("encrypted") == "plain:encrypted"
    assert make_manager(ApiKeyManager(raises=True))._decrypt_secret("legacy") == "legacy"


@pytest.mark.asyncio
async def test_fire_and_forget_running_loop_schedules_task(monkeypatch):
    manager = make_manager()
    calls = []

    async def fake_fire(event, payload):
        calls.append((event, payload))

    monkeypatch.setattr(manager, "fire", fake_fire)
    manager.fire_and_forget("not.allowed", {"x": 1})
    manager.fire_and_forget("chat.completed", {"ok": True})
    await asyncio.sleep(0)

    assert calls == [("chat.completed", {"ok": True})]


def test_fire_and_forget_sync_thread_uses_configured_loop(monkeypatch):
    manager = make_manager()
    captured = []

    class Loop:
        def is_running(self):
            return True

    async def fake_fire(event, payload):
        return (event, payload)

    def fake_run_coroutine_threadsafe(coro, loop):
        captured.append(loop)
        coro.close()
        return "future"

    monkeypatch.setattr(manager, "fire", fake_fire)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    loop = Loop()
    manager.set_loop(loop)
    manager.fire_and_forget("chat.message", {"text": "hi"})

    assert captured == [loop]


@pytest.mark.asyncio
async def test_fire_filters_matching_webhooks_and_schedules_delivery(monkeypatch):
    webhooks = [
        FakeWebhook(id="a", url="https://example.test/a", secret="enc-a", events="chat.completed,session.created"),
        FakeWebhook(id="b", url="https://example.test/b", secret="enc-b", events="session.created"),
    ]
    db = install_db(monkeypatch, FakeDB(webhooks))
    manager = make_manager(ApiKeyManager())
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    await manager.fire("not.allowed", {"ignored": True})
    await manager.fire("chat.completed", {"answer": 42})

    assert db.closed == 1
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_fire_skips_when_webhooks_feature_disabled(monkeypatch):
    db = install_db(monkeypatch, FakeDB([
        FakeWebhook(id="a", url="https://example.test/a", secret="", events="chat.completed"),
    ]))
    manager = make_manager()
    scheduled = []

    monkeypatch.setattr(manager_module, "offline_mode", lambda: False)
    monkeypatch.setattr(manager_module, "load_features", lambda: {"webhooks": False})
    monkeypatch.setattr(asyncio, "create_task", lambda coro: scheduled.append(coro))

    await manager.fire("chat.completed", {"answer": 42})

    assert scheduled == []
    assert db.closed == 0


@pytest.mark.asyncio
async def test_deliver_test_decrypts_and_delegates(monkeypatch):
    manager = make_manager(ApiKeyManager())
    delivered = []

    async def fake_deliver(webhook_id, url, secret, event, payload):
        delivered.append((webhook_id, url, secret, event, payload))

    monkeypatch.setattr(manager, "_deliver", fake_deliver)
    await manager.deliver_test("wh1", "https://example.test/hook", "enc-secret")

    assert delivered == [
        (
            "wh1",
            "https://example.test/hook",
            "plain:enc-secret",
            "webhook.test",
            {"message": "Test ping from Cleverly"},
        )
    ]


@pytest.mark.asyncio
async def test_deliver_skips_offline_and_invalid_urls(monkeypatch):
    manager = make_manager()
    monkeypatch.setattr(manager_module, "offline_mode", lambda: True)
    await manager._deliver("wh1", "https://example.test/hook", None, "chat.completed", {})

    monkeypatch.setattr(manager_module, "offline_mode", lambda: False)
    monkeypatch.setattr(
        manager_module,
        "validate_webhook_url",
        lambda _url: (_ for _ in ()).throw(ValueError("private")),
    )
    await manager._deliver("wh1", "https://example.test/hook", None, "chat.completed", {})

    assert manager._client.posts == []


@pytest.mark.asyncio
async def test_deliver_posts_signed_payload_and_updates_status(monkeypatch):
    db = install_db(monkeypatch, FakeDB())
    client = FakeClient(status_code=202)
    manager = make_manager(client=client)
    monkeypatch.setattr(manager_module, "offline_mode", lambda: False)
    monkeypatch.setattr(manager_module, "validate_webhook_url", lambda url: url)

    await manager._deliver("wh1", "https://example.test/hook", "secret", "chat.completed", {"answer": 42})

    url, body, headers = client.posts[0]
    assert url == "https://example.test/hook"
    assert json.loads(body)["data"] == {"answer": 42}
    assert headers["X-Cleverly-Event"] == "chat.completed"
    assert headers["X-Cleverly-Signature"] == hmac.new(
        b"secret", body.encode(), hashlib.sha256
    ).hexdigest()
    assert db.updates[0]["last_status_code"] == 202
    assert db.updates[0]["last_error"] is None
    assert db.commits == 1
    assert db.closed == 1


@pytest.mark.asyncio
async def test_deliver_records_sanitized_error_and_rolls_back_update_failure(monkeypatch):
    db = install_db(monkeypatch, FakeDB())
    manager = make_manager(client=FakeClient(raises=True))
    monkeypatch.setattr(manager_module, "offline_mode", lambda: False)
    monkeypatch.setattr(manager_module, "validate_webhook_url", lambda url: url)

    await manager._deliver("wh1", "https://example.test/hook", None, "chat.completed", {})

    assert db.updates[0]["last_status_code"] is None
    assert db.updates[0]["last_error"] == "connect to [redacted-url] failed"
    assert db.commits == 1
    assert db.closed == 1

    failing_db = install_db(monkeypatch, FakeDB(update_raises=True))
    manager = make_manager(client=FakeClient(raises=True))
    await manager._deliver("wh2", "https://example.test/hook", None, "chat.completed", {})

    assert failing_db.rollbacks == 1
    assert failing_db.closed == 1


@pytest.mark.asyncio
async def test_close_closes_client():
    client = FakeClient()
    manager = make_manager(client=client)

    await manager.close()

    assert client.closed is True


@pytest.mark.asyncio
async def test_close_without_client_is_noop():
    manager = manager_module.WebhookManager()

    await manager.close()

    assert manager._client is None
