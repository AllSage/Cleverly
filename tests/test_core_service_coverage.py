import asyncio
import datetime as dt
import importlib
import json
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import Response


def test_local_audit_append_truncate_read_and_error_paths(monkeypatch, tmp_path):
    from src import local_audit

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    path = local_audit.audit_path()
    assert path.parent == tmp_path / "audit"
    assert path.name == "local-audit.jsonl"

    record = local_audit.append_audit("  model_benchmark  ", {"ok": True}, user="alice", source="operator")
    assert record["action"] == "model_benchmark"
    assert record["user"] == "alice"

    long_record = local_audit.append_audit("x" * 120, {"blob": "y" * 6000}, user="u" * 200, source="s" * 120)
    assert len(long_record["action"]) == local_audit.MAX_ACTION_LEN
    assert len(long_record["user"]) == 120
    assert len(long_record["source"]) == 80
    assert long_record["detail"]["truncated"] is True

    path.write_text(path.read_text(encoding="utf-8") + "not-json\n[]\n", encoding="utf-8")
    rows = local_audit.read_audit(limit=10)
    assert rows[0]["detail"]["truncated"] is True
    assert rows[1]["action"] == "model_benchmark"

    assert len(local_audit.read_audit(limit=0)) == 2

    original_read_text = local_audit.Path.read_text

    def broken_read_text(self, *args, **kwargs):
        if str(self).endswith("local-audit.jsonl"):
            raise OSError("cannot read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(local_audit.Path, "read_text", broken_read_text)
    assert local_audit.read_audit() == []

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty-data"))
    assert local_audit.read_audit() == []


def test_middleware_admin_paths_and_security_headers(monkeypatch):
    from core import middleware

    class AuthManager:
        is_configured = True

        def is_admin(self, user):
            return user == "admin"

    def req(user=None, headers=None):
        return SimpleNamespace(
            headers=headers or {},
            state=SimpleNamespace(current_user=user),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=AuthManager())),
            url=SimpleNamespace(path="/"),
        )

    assert middleware.require_admin(req("admin")) is None
    with pytest.raises(HTTPException) as denied:
        middleware.require_admin(req("alice"))
    assert denied.value.status_code == 403

    assert middleware.require_admin(req(headers={middleware.INTERNAL_TOOL_HEADER: middleware.INTERNAL_TOOL_TOKEN})) is None
    assert middleware.require_admin(req(user="internal-tool")) is None

    class BadHeaders:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("bad headers")

    with pytest.raises(HTTPException):
        middleware.require_admin(req("alice", headers=BadHeaders()))

    async def run_path(path):
        request = req("admin")
        request.url.path = path

        async def call_next(_request):
            return Response("ok")

        mw = middleware.SecurityHeadersMiddleware(app=object())
        return await mw.dispatch(request, call_next), request.state.csp_nonce

    normal, nonce = asyncio.run(run_path("/chat"))
    assert normal.headers["X-Frame-Options"] == "DENY"
    normal_csp = normal.headers["Content-Security-Policy"]
    assert f"nonce-{nonce}" in normal_csp
    assert "https://cdn.jsdelivr.net" not in normal_csp
    assert "script-src 'self'" in normal_csp

    report, _ = asyncio.run(run_path("/api/research/report/abc"))
    assert "https:" in report.headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in report.headers

    tool, _ = asyncio.run(run_path("/api/tools/x/render"))
    assert "Content-Security-Policy" not in tool.headers
    assert tool.headers["X-Content-Type-Options"] == "nosniff"


def test_auth_helpers_privileges_and_owner_filter(monkeypatch):
    from src import auth_helpers

    class AuthManager:
        is_configured = True

        def __init__(self, privs=None):
            self.privs = privs if privs is not None else {"can_upload": True}

        def get_privileges(self, user):
            if self.privs == "raise":
                raise RuntimeError("auth failed")
            return self.privs

    def req(user=None, auth=None, host="203.0.113.10"):
        return SimpleNamespace(
            state=SimpleNamespace(current_user=user),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth)),
            client=SimpleNamespace(host=host),
            headers={},
        )

    assert auth_helpers.get_current_user(req("alice")) == "alice"
    assert auth_helpers.require_user(req("alice", AuthManager())) == "alice"
    assert auth_helpers.require_user(req(None, None, host="127.0.0.1")) == ""

    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert auth_helpers.require_user(req(None, AuthManager(), host="127.0.0.1")) == ""
    proxied = req(None, AuthManager(), host="127.0.0.1")
    proxied.headers = {"x-forwarded-for": "198.51.100.20"}
    with pytest.raises(HTTPException) as proxied_unauth:
        auth_helpers.require_user(proxied)
    assert proxied_unauth.value.status_code == 401
    monkeypatch.delenv("AUTH_ENABLED", raising=False)

    with pytest.raises(HTTPException) as unauth:
        auth_helpers.require_user(req(None, AuthManager()))
    assert unauth.value.status_code == 401

    with pytest.raises(HTTPException):
        auth_helpers.require_user(req(None, None, host="198.51.100.2"))

    assert auth_helpers.require_privilege(req("alice", AuthManager({"can_upload": True})), "can_upload") == "alice"
    assert auth_helpers.require_privilege(req("alice", None), "can_upload") == "alice"
    assert auth_helpers.require_privilege(req("alice", AuthManager("raise")), "can_upload") == "alice"
    assert auth_helpers.require_privilege(req(None, None, host="localhost"), "can_upload") == ""

    with pytest.raises(HTTPException) as forbidden:
        auth_helpers.require_privilege(req("alice", AuthManager({"can_upload": False})), "can_upload")
    assert forbidden.value.status_code == 403
    assert "can upload" in forbidden.value.detail

    class Expr:
        def __init__(self, text):
            self.text = text

        def __eq__(self, other):
            return Expr(f"{self.text}={other}")

        def __or__(self, other):
            return Expr(f"({self.text}|{other.text})")

        def __repr__(self):
            return self.text

    class Model:
        owner = Expr("owner")

    class Query:
        def __init__(self):
            self.filters = []

        def filter(self, expr):
            self.filters.append(repr(expr))
            return self

    query = Query()
    assert auth_helpers.owner_filter(query, Model, "") is query
    assert query.filters == []
    auth_helpers.owner_filter(query, Model, "alice")
    auth_helpers.owner_filter(query, Model, "alice", include_shared=False)
    assert query.filters[0].startswith("(")
    assert query.filters[1] == "owner=alice"


def test_search_cache_key_and_cleanup(monkeypatch, tmp_path):
    from src.search import cache as search_cache

    assert search_cache.generate_cache_key("abc") == search_cache.hashlib.sha256(b"abc").hexdigest()

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    now = dt.datetime.now()
    old = now - dt.timedelta(days=2)
    fresh = now
    for key in ("old", "fresh", "lru1", "lru2"):
        (cache_dir / f"{key}.cache").write_text(key, encoding="utf-8")
    index = {
        "old": old,
        "fresh": fresh,
        "missing": fresh,
        "lru1": old,
        "lru2": fresh,
    }
    monkeypatch.setattr(search_cache, "CACHE_MAX_ENTRIES", 1)
    search_cache.cache_metrics.update({"hits": 0, "misses": 0, "evictions": 0})

    search_cache.cleanup_cache(cache_dir, index, max_age=dt.timedelta(days=1))

    assert "old" not in index
    assert "missing" not in index
    assert len(index) == 1
    assert search_cache.cache_metrics["evictions"] >= 4
    assert not (cache_dir / "old.cache").exists()


def test_services_search_cache_module_alias_uses_same_behaviors(monkeypatch, tmp_path):
    from services.search import cache as service_cache

    assert service_cache.generate_cache_key("abc") == service_cache.hashlib.sha256(b"abc").hexdigest()

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    for key in ("expired", "fresh", "lru"):
        (cache_dir / f"{key}.cache").write_text("x", encoding="utf-8")
    service_cache.cache_metrics.update({"hits": 0, "misses": 0, "evictions": 0})
    index = {
        "expired": dt.datetime.now() - dt.timedelta(days=3),
        "missing": dt.datetime.now(),
        "fresh": dt.datetime.now(),
        "lru": dt.datetime.now() - dt.timedelta(minutes=1),
    }
    monkeypatch.setattr(service_cache, "CACHE_MAX_ENTRIES", 1)

    service_cache.cleanup_cache(cache_dir, index, max_age=dt.timedelta(days=1))

    assert len(index) == 1
    assert "expired" not in index
    assert "missing" not in index
    assert service_cache.cache_metrics["evictions"] >= 3


def test_settings_cache_offline_user_and_save_paths(monkeypatch, tmp_path):
    from src import settings

    settings_file = tmp_path / "settings.json"
    features_file = tmp_path / "features.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(settings, "FEATURES_FILE", str(features_file))
    monkeypatch.delenv("CLEVERLY_OFFLINE", raising=False)
    settings._invalidate_caches()

    assert settings.offline_mode() is False
    loaded = settings.load_settings()
    assert loaded["tts_provider"] == "disabled"
    assert settings.load_settings() is loaded
    assert settings.get_setting("missing", "fallback") == "fallback"

    settings_file.write_text(json.dumps({"tts_provider": "browser"}), encoding="utf-8")
    settings._invalidate_caches()
    assert settings.load_settings()["tts_provider"] == "browser"

    features_file.write_text(json.dumps({"web_search": False}), encoding="utf-8")
    settings._invalidate_caches()
    assert settings.load_features()["web_search"] is False
    assert settings.load_features() is settings.load_features()

    settings.save_settings({"tts_provider": "endpoint:x"})
    assert json.loads(settings_file.read_text(encoding="utf-8"))["tts_provider"] == "endpoint:x"
    settings.save_features({"memory": False})
    assert json.loads(features_file.read_text(encoding="utf-8"))["memory"] is False

    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    settings._invalidate_caches()
    offline_settings = settings.load_settings()
    assert offline_settings["search_provider"] == "disabled"
    assert offline_settings["search_fallback_chain"] == []
    offline_features = settings.load_features()
    assert offline_features["email"] is False
    assert offline_features["mcp"] is False

    prefs_module = types.SimpleNamespace(_load_for_user=lambda owner: {"vision_model": "local-vision", "empty": ""})
    monkeypatch.setitem(importlib.import_module("sys").modules, "routes.prefs_routes", prefs_module)
    assert settings.get_user_setting("vision_model", owner="alice") == "local-vision"
    assert settings.get_user_setting("tts_provider", owner="alice") == "endpoint:x"

    prefs_module._load_for_user = lambda owner: (_ for _ in ()).throw(RuntimeError("prefs bad"))
    assert settings.get_user_setting("vision_model", owner="alice", default="fallback") == ""

    settings_file.write_text("{", encoding="utf-8")
    features_file.write_text("{", encoding="utf-8")
    settings._invalidate_caches()
    assert settings.load_settings()["tts_provider"] == "disabled"
    assert settings.load_features()["memory"] is True


def test_startup_ollama_probe_blocks_external_url_offline(monkeypatch):
    from src import startup_endpoints

    monkeypatch.setattr(startup_endpoints, "offline_mode", lambda: True)
    monkeypatch.setattr(startup_endpoints, "load_features", lambda: {"external_model_endpoints": True})
    monkeypatch.setattr(startup_endpoints, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(startup_endpoints.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))

    assert startup_endpoints._ollama_models("https://ollama.example:11434") == []


def test_startup_ollama_probe_blocks_external_url_feature_disabled(monkeypatch):
    from src import startup_endpoints

    monkeypatch.setattr(startup_endpoints, "offline_mode", lambda: False)
    monkeypatch.setattr(startup_endpoints, "load_features", lambda: {"external_model_endpoints": False})
    monkeypatch.setattr(startup_endpoints, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(startup_endpoints.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))

    assert startup_endpoints._ollama_models("https://ollama.example:11434") == []


def test_memory_service_vector_and_keyword_paths(monkeypatch, tmp_path):
    import services.memory.service as memory_service

    class Manager:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.added = []
            self.deleted = []

        def add_memory(self, entry):
            self.added.append(entry)

        def search_memories(self, query, limit=5):
            return [{"id": "m1", "text": query, "timestamp": 1, "session_id": "s1"}][:limit]

        def get_memories(self, limit=100):
            return [{"id": "m2", "text": "all", "timestamp": 2, "session_id": None}][:limit]

        def delete_memory(self, memory_id):
            self.deleted.append(memory_id)
            return True

    class VectorStore:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.added = []

        def add(self, text, metadata):
            self.added.append((text, metadata))

        def search(self, query, k=5):
            return [{"id": "v1", "text": query, "timestamp": 3, "session_id": "s2", "metadata": {"score": 1}}]

    monkeypatch.setattr(memory_service, "MemoryManager", Manager)
    monkeypatch.setattr(memory_service, "MemoryVectorStore", VectorStore)
    monkeypatch.setattr(memory_service.os.path, "exists", lambda path: path.endswith("memory_vectors"))

    vector_service = memory_service.MemoryService(str(tmp_path))
    remembered = asyncio.run(vector_service.remember("remember me", session_id="s1"))
    assert remembered.id
    assert vector_service.manager.added[0]["text"] == "remember me"
    assert vector_service.vector_store.added[0][1]["session_id"] == "s1"
    recalled = asyncio.run(vector_service.recall("query"))
    assert recalled.memories[0].metadata == {"score": 1}

    monkeypatch.setattr(memory_service.os.path, "exists", lambda path: False)
    keyword_service = memory_service.MemoryService(str(tmp_path))
    keyword = asyncio.run(keyword_service.recall("needle", top_k=1))
    assert keyword.memories[0].text == "needle"
    assert keyword.total == 1
    assert keyword_service.get_all()[0].text == "all"
    assert keyword_service.delete("m2") is True
    assert keyword_service.manager.deleted == ["m2"]
