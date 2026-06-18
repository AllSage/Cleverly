import json
import importlib.util
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_RESOLVER_PATH = Path(__file__).resolve().parents[1] / "src" / "endpoint_resolver.py"
_SPEC = importlib.util.spec_from_file_location("endpoint_resolver_under_test", _RESOLVER_PATH)
resolver = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(resolver)


class FakeQuery:
    def __init__(self, endpoint=None, *, raises=False):
        self.endpoint = endpoint
        self.raises = raises
        self.filters = []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def first(self):
        if self.raises:
            raise RuntimeError("query failed")
        return self.endpoint


class FakeDB:
    def __init__(self, endpoint=None, *, raises=False):
        self.endpoint = endpoint
        self.raises = raises
        self.closed = False
        self.queries = []

    def query(self, model):
        query = FakeQuery(self.endpoint, raises=self.raises)
        self.queries.append((model, query))
        return query

    def close(self):
        self.closed = True


class FakeEndpoint(SimpleNamespace):
    id = object()
    is_enabled = object()

    def __init__(
        self,
        base_url="https://api.openai.test/v1/chat/completions",
        api_key="key",
        models=None,
    ):
        super().__init__(base_url=base_url, api_key=api_key, models=models, is_enabled=True)


def install_settings(monkeypatch, settings, overrides=None):
    import src.settings as settings_module

    overrides = overrides or {}

    def get_user_setting(key, owner, default=None):
        return overrides.get((key, owner), overrides.get(key, default))

    monkeypatch.setattr(settings_module, "load_settings", lambda: dict(settings))
    monkeypatch.setattr(settings_module, "get_user_setting", get_user_setting)


def install_db(monkeypatch, endpoint=None, *, raises=False):
    db = FakeDB(endpoint, raises=raises)
    monkeypatch.setattr(resolver, "SessionLocal", lambda: db)
    monkeypatch.setattr(resolver, "ModelEndpoint", FakeEndpoint)
    return db


def test_first_chat_model_skips_non_chat_models_and_falls_back():
    assert resolver._first_chat_model(["text-embedding-ada-002", "tts-1", "chat-good"]) == "chat-good"
    assert resolver._first_chat_model(["text-embedding-ada-002"]) == "text-embedding-ada-002"
    assert resolver._first_chat_model([]) is None


def test_tailscale_resolution_dns_cache_and_success(monkeypatch):
    resolver._tailscale_cache.clear()
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *_args: calls.append(host) or [("ok",)])

    assert resolver._resolve_tailscale_host("host.local") is None
    assert resolver._resolve_tailscale_host("host.local") is None
    assert calls == ["host.local"]

    resolver._tailscale_cache.clear()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: (_ for _ in ()).throw(socket.gaierror("missing")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "Peer": {
                        "p1": {
                            "HostName": "box",
                            "DNSName": "box.tailnet.ts.net.",
                            "TailscaleIPs": ["100.64.0.7"],
                        }
                    }
                }
            ),
        ),
    )

    assert resolver._resolve_tailscale_host("box") == "100.64.0.7"


def test_tailscale_resolution_handles_command_failures(monkeypatch):
    resolver._tailscale_cache.clear()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args: (_ for _ in ()).throw(socket.gaierror("missing")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tailscale missing")),
    )

    assert resolver._resolve_tailscale_host("unknown") is None
    assert resolver._tailscale_cache["unknown"] is None


def test_tailscale_resolution_skips_dns_and_command_offline(monkeypatch):
    resolver._tailscale_cache.clear()
    monkeypatch.setattr(resolver, "offline_mode", lambda: True)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("offline resolver should not query DNS")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("offline resolver should not invoke tailscale")),
    )

    assert resolver._resolve_tailscale_host("box") is None
    assert resolver.resolve_url("http://box:8080/v1") == "http://box:8080/v1"
    assert resolver._tailscale_cache["box"] is None


def test_url_builders_normalize_providers_and_tailscale(monkeypatch):
    monkeypatch.setattr(resolver, "_resolve_tailscale_host", lambda host: "100.64.0.8" if host == "box" else None)

    assert resolver.resolve_url("not a url") == "not a url"
    assert resolver.resolve_url("http://box:8080/v1") == "http://100.64.0.8:8080/v1"
    assert resolver.normalize_base(" https://api.test/v1/models/ ") == "https://api.test/v1"
    assert resolver.normalize_base("https://api.test/v1/chat/completions") == "https://api.test/v1"
    assert resolver.normalize_base("https://api.test/v1/completions") == "https://api.test/v1"
    assert resolver.normalize_base("https://api.anthropic.com/v1/messages") == "https://api.anthropic.com"
    assert resolver.normalize_base("http://localhost:11434/api/generate") == "http://localhost:11434/api"

    assert resolver.build_chat_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"
    assert resolver.build_models_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/models"
    assert resolver.build_chat_url("https://ollama.com") == "https://ollama.com/api/chat"
    assert resolver.build_models_url("https://ollama.com/api") == "https://ollama.com/api/tags"
    assert resolver.build_chat_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1/chat/completions"
    assert resolver.build_models_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1/models"
    assert resolver._anthropic_api_root("https://api.openai.test/v1") == "https://api.openai.test/v1"
    assert resolver._ollama_api_root("https://api.openai.test/v1") == "https://api.openai.test/v1"


def test_header_builder_provider_variants():
    assert resolver.build_headers(None, "https://api.anthropic.com") == {
        "anthropic-version": "2023-06-01"
    }
    assert resolver.build_headers("ant", "https://api.anthropic.com") == {
        "x-api-key": "ant",
        "anthropic-version": "2023-06-01",
    }
    assert resolver.build_headers("or", "https://openrouter.ai/api/v1") == {
        "Authorization": "Bearer or",
        "HTTP-Referer": "https://github.com/AllSage/Cleverly",
        "X-OpenRouter-Title": "Cleverly",
    }
    assert resolver.build_headers("key", "https://api.openai.test/v1") == {
        "Authorization": "Bearer key"
    }


def test_resolve_endpoint_returns_fallback_when_settings_unavailable(monkeypatch):
    import src.settings as settings_module

    monkeypatch.setattr(settings_module, "load_settings", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )


def test_resolve_endpoint_uses_default_for_unset_utility(monkeypatch):
    install_settings(
        monkeypatch,
        {"default_endpoint_id": "ep1", "default_model": "default-model"},
    )
    db = install_db(monkeypatch, FakeEndpoint(models=json.dumps(["text-embedding-ada-002", "chat-a"])))

    assert resolver.resolve_endpoint("utility") == (
        "https://api.openai.test/v1/chat/completions",
        "default-model",
        {"Authorization": "Bearer key"},
    )
    assert db.closed is True


def test_resolve_endpoint_skips_remote_when_offline_or_feature_disabled(monkeypatch):
    install_settings(monkeypatch, {"default_endpoint_id": "ep1", "default_model": "remote-model"})
    install_db(monkeypatch, FakeEndpoint(base_url="https://api.openai.test/v1", models=["remote-model"]))
    monkeypatch.setattr(resolver, "offline_mode", lambda: True)
    monkeypatch.setattr(resolver, "load_features", lambda: {"external_model_endpoints": True})
    monkeypatch.setattr(
        resolver,
        "build_chat_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("blocked endpoints should not be resolved")),
    )

    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )

    monkeypatch.setattr(resolver, "offline_mode", lambda: False)
    monkeypatch.setattr(resolver, "load_features", lambda: {"external_model_endpoints": False})
    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )

    monkeypatch.setattr(resolver, "load_features", lambda: (_ for _ in ()).throw(RuntimeError("settings down")))
    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )


def test_resolve_endpoint_allows_local_endpoint_when_offline(monkeypatch):
    install_settings(monkeypatch, {"default_endpoint_id": "ep1", "default_model": "local-model"})
    install_db(monkeypatch, FakeEndpoint(base_url="http://ollama:11434/v1", api_key="", models=["local-model"]))
    monkeypatch.setattr(resolver, "offline_mode", lambda: True)
    monkeypatch.setattr(resolver, "load_features", lambda: {"external_model_endpoints": False})

    assert resolver.resolve_endpoint("default") == (
        "http://ollama:11434/v1/chat/completions",
        "local-model",
        {},
    )


def test_resolve_endpoint_falls_back_to_utility_then_default(monkeypatch):
    install_settings(
        monkeypatch,
        {
            "utility_endpoint_id": "",
            "utility_model": "",
            "default_endpoint_id": "ep-default",
            "default_model": "default-model",
        },
        {"utility_endpoint_id": "ep-utility", "utility_model": "utility-model"},
    )
    install_db(monkeypatch, FakeEndpoint(base_url="https://ollama.com/api/tags", api_key=""))

    assert resolver.resolve_endpoint("research") == (
        "https://ollama.com/api/chat",
        "utility-model",
        {},
    )

    install_settings(
        monkeypatch,
        {
            "utility_endpoint_id": "",
            "utility_model": "",
            "default_endpoint_id": "ep-default",
            "default_model": "default-model",
        },
    )
    install_db(monkeypatch, FakeEndpoint())
    assert resolver.resolve_endpoint("task")[1] == "default-model"

    install_settings(monkeypatch, {"utility_endpoint_id": "", "default_endpoint_id": ""})
    assert resolver.resolve_endpoint("task", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )


def test_resolve_endpoint_uses_cached_model_list_and_owner_filter(monkeypatch):
    install_settings(monkeypatch, {"research_endpoint_id": "ep1", "research_model": ""})
    install_db(monkeypatch, FakeEndpoint(models=json.dumps(["embedding-model", "GLM-5.2"])))

    import src.auth_helpers as auth_helpers

    calls = []

    def owner_filter(query, model, owner):
        calls.append((query, model, owner))
        return query

    monkeypatch.setattr(auth_helpers, "owner_filter", owner_filter)

    assert resolver.resolve_endpoint("research", owner="alice")[1] == "GLM-5.2"
    assert calls and calls[0][1] is FakeEndpoint and calls[0][2] == "alice"


def test_resolve_endpoint_handles_missing_endpoint_bad_json_and_query_error(monkeypatch):
    install_settings(monkeypatch, {"default_endpoint_id": "ep1", "default_model": ""})
    db = install_db(monkeypatch, None)
    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )
    assert db.closed is True

    install_db(monkeypatch, FakeEndpoint(models="{bad"))
    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"})[1] == "fallback"

    install_db(monkeypatch, FakeEndpoint(), raises=True)
    assert resolver.resolve_endpoint("default", "http://fallback", "fallback", {"H": "1"}) == (
        "http://fallback",
        "fallback",
        {"H": "1"},
    )


def test_resolve_endpoint_by_id_variants(monkeypatch):
    assert resolver.resolve_endpoint_by_id("") is None

    db = install_db(monkeypatch, None)
    assert resolver.resolve_endpoint_by_id("missing") is None
    assert db.closed is True

    install_db(monkeypatch, FakeEndpoint(models=["text-embedding-ada-002", "chat-model"]))
    assert resolver.resolve_endpoint_by_id("ep1") == (
        "https://api.openai.test/v1/chat/completions",
        "chat-model",
        {"Authorization": "Bearer key"},
    )

    import src.auth_helpers as auth_helpers

    owner_calls = []

    def owner_filter(query, model, owner):
        owner_calls.append((model, owner))
        return query

    monkeypatch.setattr(auth_helpers, "owner_filter", owner_filter)
    assert resolver.resolve_endpoint_by_id("ep1", owner="alice")[1] == "chat-model"
    assert owner_calls[-1] == (FakeEndpoint, "alice")

    monkeypatch.setattr(auth_helpers, "owner_filter", lambda query, model, owner: FakeQuery(None))
    assert resolver.resolve_endpoint_by_id("ep1", owner="alice") is None

    install_db(monkeypatch, FakeEndpoint(models="{bad"))
    assert resolver.resolve_endpoint_by_id("ep1") is None

    install_db(monkeypatch, FakeEndpoint(), raises=True)
    assert resolver.resolve_endpoint_by_id("ep1") is None


def test_resolve_endpoint_by_id_skips_remote_when_feature_disabled(monkeypatch):
    install_db(monkeypatch, FakeEndpoint(base_url="https://api.openai.test/v1", models=["remote-model"]))
    monkeypatch.setattr(resolver, "offline_mode", lambda: False)
    monkeypatch.setattr(resolver, "load_features", lambda: {"external_model_endpoints": False})

    assert resolver.resolve_endpoint_by_id("ep1", "remote-model") is None


def test_fallback_candidate_helpers(monkeypatch):
    install_settings(
        monkeypatch,
        {
            "default_model_fallbacks": [
                {"endpoint_id": "a", "model": "m-a"},
                "bad",
                {"endpoint_id": "missing", "model": "m-missing"},
            ],
            "utility_model_fallbacks": [{"endpoint_id": "u", "model": "m-u"}],
            "vision_model_fallbacks": [{"endpoint_id": "v", "model": "m-v"}],
            "utility_endpoint_id": "",
        },
    )
    monkeypatch.setattr(
        resolver,
        "resolve_endpoint_by_id",
        lambda endpoint_id, model=None, owner=None: (f"http://{endpoint_id}:{owner or 'none'}", model, {}) if endpoint_id != "missing" else None,
    )

    assert resolver.resolve_chat_fallback_candidates() == [("http://a:none", "m-a", {})]
    assert resolver.resolve_utility_fallback_candidates(owner="alice") == [("http://a:alice", "m-a", {})]
    assert resolver.resolve_vision_fallback_candidates() == [("http://v:none", "m-v", {})]

    install_settings(
        monkeypatch,
        {"utility_endpoint_id": "u-primary", "utility_model_fallbacks": [{"endpoint_id": "u", "model": "m-u"}]},
    )
    assert resolver.resolve_utility_fallback_candidates() == [("http://u:none", "m-u", {})]


def test_fallback_candidates_return_empty_when_settings_fail(monkeypatch):
    import src.settings as settings_module

    monkeypatch.setattr(settings_module, "load_settings", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert resolver.resolve_chat_fallback_candidates() == []
    assert resolver.resolve_utility_fallback_candidates() == []
