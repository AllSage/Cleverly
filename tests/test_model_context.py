"""Tests for model_context.py — local endpoint detection, token estimation, known model lookup."""

import pytest

import src.model_context as model_context
from src.model_context import _is_local_endpoint, estimate_tokens, _lookup_known


class TestIsLocalEndpoint:
    def test_localhost(self):
        assert _is_local_endpoint("http://localhost:5000/v1/chat/completions") is True

    def test_loopback_ipv4(self):
        assert _is_local_endpoint("http://127.0.0.1:8080/v1/chat/completions") is True

    def test_private_192_168(self):
        assert _is_local_endpoint("http://192.168.1.1:11434/v1/chat/completions") is True

    def test_private_10(self):
        assert _is_local_endpoint("http://10.0.0.5:8000/v1/chat/completions") is True

    def test_tailscale_100(self):
        # 100.64.0.0/10 is the CGNAT range Tailscale uses.
        assert _is_local_endpoint("http://100.64.0.1:5000/v1/chat/completions") is True

    def test_openai_is_remote(self):
        assert _is_local_endpoint("https://api.openai.com/v1/chat/completions") is False

    def test_anthropic_is_remote(self):
        assert _is_local_endpoint("https://api.anthropic.com/v1/messages") is False

    def test_empty_url(self):
        assert _is_local_endpoint("") is False

    def test_malformed_url(self):
        assert _is_local_endpoint("not-a-url") is False


class TestEstimateTokens:
    def test_empty_list(self):
        assert estimate_tokens([]) == 0

    def test_single_short_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        tokens = estimate_tokens(messages)
        # 4 overhead + int(5 * 0.3) = 4 + 1 = 5
        assert tokens == 5

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi there"},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0
        # Each message adds 4 overhead + chars * 0.3
        assert tokens == 4 + int(16 * 0.3) + 4 + int(8 * 0.3)

    def test_multimodal_content_list(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        tokens = estimate_tokens(messages)
        # 4 overhead + int(19 * 0.3) for the text item; image_url is ignored
        assert tokens == 4 + int(19 * 0.3)

    def test_missing_content_key(self):
        messages = [{"role": "assistant"}]
        tokens = estimate_tokens(messages)
        # 4 overhead + 0 content
        assert tokens == 4

    def test_scales_with_length(self):
        short = estimate_tokens([{"role": "user", "content": "short"}])
        long_text = "a" * 10000
        long = estimate_tokens([{"role": "user", "content": long_text}])
        assert long > short * 10


class TestLookupKnown:
    def test_claude_sonnet(self):
        assert _lookup_known("claude-sonnet-4-5") == 200000

    def test_gpt4o(self):
        assert _lookup_known("gpt-4o") == 128000

    def test_deepseek_r1(self):
        assert _lookup_known("deepseek-r1") == 64000

    def test_gemini_pro(self):
        assert _lookup_known("gemini-2.5-pro") == 1048576

    def test_unknown_model(self):
        assert _lookup_known("totally-unknown-model-xyz") is None

    def test_namespaced_model(self):
        """Models prefixed with provider/ should still match."""
        result = _lookup_known("openrouter/deepseek-r1")
        assert result == 64000

    def test_model_with_tag(self):
        """Models with :free or :extended suffixes should still match."""
        result = _lookup_known("deepseek-r1:free")
        assert result == 64000


class TestGetContextLength:
    def setup_method(self):
        model_context._context_cache.clear()

    def test_local_endpoint_requeries_same_model_after_restart(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return 8192 if len(calls) == 1 else 27000

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "http://127.0.0.1:8000/v1/chat/completions"
        model = "Qwen/Qwen3-14B"

        first = model_context.get_context_length(endpoint, model)
        second = model_context.get_context_length(endpoint, model)

        assert first == 8192
        assert second == 27000
        assert len(calls) == 2

    def test_remote_endpoint_keeps_cached_context(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return 200000 if len(calls) == 1 else 12345

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "https://api.openai.com/v1/chat/completions"
        model = "gpt-5"

        first = model_context.get_context_length(endpoint, model)
        second = model_context.get_context_length(endpoint, model)

        assert first == 200000
        assert second == 200000
        assert len(calls) == 1

    def test_remote_default_context_is_not_cached(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return model_context.DEFAULT_CONTEXT

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "https://api.example.test/v1/chat/completions"
        assert model_context.get_context_length(endpoint, "unknown") == model_context.DEFAULT_CONTEXT
        assert model_context.get_context_length(endpoint, "unknown") == model_context.DEFAULT_CONTEXT
        assert len(calls) == 2


class Response:
    def __init__(self, data=None, *, is_success=True):
        self._data = data
        self.is_success = is_success

    def json(self):
        return self._data


class TestQueryContextLength:
    def test_is_local_endpoint_handles_parser_error(self, monkeypatch):
        monkeypatch.setattr(model_context, "urlparse", lambda _url: (_ for _ in ()).throw(ValueError("bad")))

        assert model_context._is_local_endpoint("http://localhost") is False

    def test_local_slots_endpoint_reports_serving_context(self, monkeypatch):
        requests = []

        def fake_get(url, timeout=None):
            requests.append((url, timeout))
            return Response([{"n_ctx": 4096}])

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        assert model_context._query_context_length("http://localhost:8080/v1/chat/completions", "custom") == 4096
        assert requests == [("http://localhost:8080/slots", model_context.REQUEST_TIMEOUT)]

    def test_local_slots_error_falls_back_to_models_endpoint(self, monkeypatch):
        calls = []

        def fake_get(url, timeout=None):
            calls.append(url)
            if url.endswith("/slots"):
                raise RuntimeError("slots down")
            return Response({"data": [{"id": "custom", "context_length": 7777}]})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        assert model_context._query_context_length("http://localhost:8080/v1/chat/completions", "custom") == 7777
        assert calls == ["http://localhost:8080/slots", "http://localhost:8080/v1/models"]

    def test_models_endpoint_context_fields_and_meta(self, monkeypatch):
        responses = [
            Response({"data": [{"id": "provider/model-a", "context_window": 12345}]}),
            Response({"data": [{"id": "model-b", "meta": {"n_ctx": 2222}}]}),
            Response({"data": [{"id": "model-c", "model_extra": {"max_model_len": 3333}}]}),
        ]
        monkeypatch.setattr(model_context.httpx, "get", lambda *_args, **_kwargs: responses.pop(0))

        assert model_context._query_context_length("https://api.test/v1/chat/completions", "model-a") == 12345
        assert model_context._query_context_length("https://api.test/v1/chat/completions", "model-b") == 2222
        assert model_context._query_context_length("https://api.test/v1/chat/completions", "model-c") == 3333

    def test_known_model_context_wins_for_cloud_but_not_local(self, monkeypatch):
        responses = [
            Response({"data": [{"id": "gpt-4", "context_length": 4096}]}),
            Response([], is_success=False),
            Response({"data": [{"id": "llama-3", "context_length": 4096}]}),
            Response({"data": [{"id": "gpt-4", "context_length": 200000}]}),
        ]
        monkeypatch.setattr(model_context.httpx, "get", lambda *_args, **_kwargs: responses.pop(0))

        assert model_context._query_context_length("https://api.test/v1/chat/completions", "gpt-4") == 8192
        assert model_context._query_context_length("http://localhost:8080/v1/chat/completions", "llama-3") == 4096
        assert model_context._query_context_length("https://api.test/v1/chat/completions", "gpt-4") == 200000

    def test_known_only_default_and_request_errors(self, monkeypatch):
        responses = [
            Response({"data": [{"id": "other", "context_length": 123}]}),
            RuntimeError("down"),
        ]

        def fake_get(*_args, **_kwargs):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        assert model_context._query_context_length("https://api.test/v1/chat/completions", "gpt-4o") == 128000
        assert model_context._query_context_length("https://api.test/v1/chat/completions", "unknown-model") == model_context.DEFAULT_CONTEXT
