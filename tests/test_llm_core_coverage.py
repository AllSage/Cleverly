import asyncio
import json
import time
import types

import httpx
import pytest
from fastapi import HTTPException

from src import llm_core


class Response:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.is_success:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


@pytest.fixture(autouse=True)
def _clear_llm_state(monkeypatch):
    monkeypatch.setattr(llm_core, "offline_mode", lambda: False)
    llm_core._response_cache.clear()
    llm_core._dead_hosts.clear()
    llm_core._host_fails.clear()
    llm_core._model_activity.clear()
    yield
    llm_core._response_cache.clear()
    llm_core._dead_hosts.clear()
    llm_core._host_fails.clear()
    llm_core._model_activity.clear()


def test_provider_helpers_payloads_and_errors():
    assert llm_core._is_ollama_native_url("http://localhost:11434/api/chat") is True
    assert llm_core._is_ollama_native_url("https://ollama.com/api") is True
    assert llm_core._is_ollama_native_url("not a url") is False
    assert llm_core._ollama_api_root("https://ollama.com/v1/chat/completions") == "https://ollama.com/api"
    assert llm_core._ollama_api_root("http://x/api/generate") == "http://x/api"
    assert llm_core._normalize_ollama_url("http://x/api/tags") == "http://x/api/chat"
    assert llm_core._detect_provider("https://api.anthropic.com/v1/messages") == "anthropic"
    assert llm_core._detect_provider("https://openrouter.ai/api/v1/chat/completions") == "openrouter"
    assert llm_core._provider_label("https://api.x.ai/v1/chat/completions") == "xAI"
    assert llm_core._provider_label("http://127.0.0.1:8000/v1/chat/completions") == "local endpoint"
    assert llm_core._provider_headers("openrouter")["X-OpenRouter-Title"] == "Cleverly"
    assert llm_core._uses_max_completion_tokens("openai/o3-mini") is True
    assert llm_core._uses_max_completion_tokens("") is False
    assert llm_core._supports_thinking("Qwen3-8B") is True
    assert llm_core._supports_thinking("") is False

    ollama = llm_core._build_ollama_payload("m", [{"role": "user", "content": "hi"}], 0.2, 42, stream=True, tools=[{"x": 1}])
    assert ollama["stream"] is True
    assert ollama["options"] == {"temperature": 0.2, "num_predict": 42}
    assert ollama["tools"] == [{"x": 1}]
    assert llm_core._parse_ollama_response({"response": "fallback"}) == "fallback"

    content = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        {"type": "image_url", "image_url": {"url": "https://img.local/a.png"}},
        {"type": "image_url", "image_url": {"url": "data:bad"}},
        {"type": "text", "text": "hi"},
        "raw",
    ]
    converted = llm_core._convert_openai_content_to_anthropic(content)
    assert converted[0]["source"]["media_type"] == "image/png"
    assert converted[1]["source"]["type"] == "url"
    assert converted[-2:] == [{"type": "text", "text": "hi"}, "raw"]

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "use", "tool_calls": [{"id": "t1", "function": {"name": "tool", "arguments": "{\"a\":1}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "done"},
        {"role": "user", "content": content},
    ]
    payload = llm_core._build_anthropic_payload(
        "claude", messages, 0.1, 0, stream=True,
        tools=[{"type": "function", "function": {"name": "tool", "description": "d", "parameters": {"type": "object"}}}],
    )
    assert payload["system"] == "sys"
    assert payload["max_tokens"] == 4096
    assert payload["stream"] is True
    assert payload["tools"][0]["name"] == "tool"
    assert payload["messages"][0]["content"][1]["input"] == {"a": 1}
    assert llm_core._build_anthropic_headers({"Authorization": "Bearer sk", "X": "1"})["x-api-key"] == "sk"
    assert llm_core._parse_anthropic_response({"content": [{"type": "text", "text": "hello"}]}) == "hello"
    assert llm_core._normalize_anthropic_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"

    assert "rejected the API key" in llm_core._format_upstream_error(401, b"not json", "https://api.openai.com")
    assert "denied access" in llm_core._format_upstream_error(403, '{"error":{"message":"nope"}}', "https://api.anthropic.com")
    assert "returned 404" in llm_core._format_upstream_error(404, "", "https://groq.com")
    assert "rate-limited" in llm_core._format_upstream_error(429, '{"error":"slow"}', "https://openrouter.ai")
    assert "outage" in llm_core._format_upstream_error(503, b'{"error":{"detail":"down"}}', "https://mistral.ai")
    assert "HTTP 418" in llm_core._format_upstream_error(418, "teapot", "https://deepseek.com")


def test_cache_activity_and_host_health(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_core.time, "time", lambda: now[0])
    assert llm_core.seconds_since_model_activity("u", "m") is None
    llm_core.note_model_activity("u", "m")
    now[0] += 2.5
    assert llm_core.seconds_since_model_activity("u", "m") == 2.5
    llm_core.note_model_activity("", "m")

    url = "http://dead.local:1234/v1/chat/completions"
    assert llm_core._mark_host_dead(url) is False
    assert llm_core._mark_host_dead(url) is True
    assert llm_core._is_host_dead(url) is True
    now[0] += llm_core.DEAD_HOST_COOLDOWN + 1
    assert llm_core._is_host_dead(url) is False
    llm_core._mark_host_dead(url)
    llm_core._clear_host_dead(url)
    assert llm_core._host_key(url) not in llm_core._host_fails


def test_list_and_normalize_models(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/models"):
            return Response(payload={"data": [{"id": "org/model-a"}]})
        if url.endswith("/api/tags"):
            return Response(payload={"models": [{"name": "llama3"}, {"model": "qwen"}]})
        return Response(500, text="bad")

    monkeypatch.setattr(llm_core.httpx, "get", fake_get)
    assert "claude-opus-4" in llm_core.list_model_ids("https://api.anthropic.com/v1/messages")
    assert llm_core.list_model_ids("https://api.openai.com/v1/chat/completions") == ["org/model-a"]
    assert llm_core.list_model_ids("http://localhost:11434/api/chat") == ["llama3", "qwen"]
    assert llm_core.normalize_model_id("https://api.openai.com/v1/chat/completions", "model-a") == "org/model-a"
    assert llm_core.normalize_model_id("https://api.openai.com/v1/chat/completions", "missing") is None
    assert calls


def test_offline_mode_blocks_external_model_endpoints(monkeypatch):
    monkeypatch.setattr(llm_core, "offline_mode", lambda: True)
    monkeypatch.setattr(llm_core, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(llm_core.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(llm_core.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))

    class BlockedClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("network")

        def stream(self, *args, **kwargs):
            raise AssertionError("network")

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: BlockedClient())

    remote = "https://api.openai.com/v1/chat/completions"
    local = "http://localhost:11434/v1/chat/completions"
    assert llm_core.list_model_ids(remote) == []
    assert llm_core._external_model_endpoint_blocked(remote) is True
    assert llm_core._external_model_endpoint_blocked(local) is False

    with pytest.raises(HTTPException) as sync_exc:
        llm_core.llm_call(remote, "m", [{"role": "user", "content": "hi"}])
    assert sync_exc.value.status_code == 403

    async def async_call():
        await llm_core.llm_call_async(remote, "m", [{"role": "user", "content": "hi"}])

    with pytest.raises(HTTPException) as async_exc:
        asyncio.run(async_call())
    assert async_exc.value.status_code == 403

    async def collect_stream():
        return [chunk async for chunk in llm_core.stream_llm(remote, "m", [{"role": "user", "content": "hi"}])]

    chunks = asyncio.run(collect_stream())
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert '"status": 403' in chunks[0]


def test_sync_llm_call_success_cache_and_errors(monkeypatch):
    posts = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append((url, headers, json, timeout))
        if "bad-schema" in url:
            return Response(payload={"unexpected": True})
        if "status" in url:
            return Response(500, text="boom")
        if "anthropic" in url:
            return Response(payload={"content": [{"type": "text", "text": "anthropic reply"}]})
        if "ollama" in url:
            return Response(payload={"message": {"content": "ollama reply"}})
        return Response(payload={"choices": [{"message": {"content": "openai reply"}}]})

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    messages = [
        {"role": "system", "content": "one", "meta": "drop"},
        {"role": "system", "content": "two"},
        {"role": "user", "content": "hi"},
        "bad",
    ]

    assert llm_core.llm_call("https://api.openai.com/v1/chat/completions", "gpt-5-mini", messages, headers='{"A":"B"}', max_tokens=10) == "openai reply"
    assert posts[-1][2]["messages"][0]["content"] == "one\n\ntwo"
    assert posts[-1][2]["max_completion_tokens"] == 10
    assert llm_core.llm_call("https://api.openai.com/v1/chat/completions", "gpt-5-mini", messages, headers='{"A":"B"}', max_tokens=10) == "openai reply"
    assert len(posts) == 1

    assert llm_core.llm_call("https://api.anthropic.com", "claude", [{"role": "user", "content": "hi"}]) == "anthropic reply"
    assert llm_core.llm_call("https://ollama.com/api/chat", "llama", [{"role": "user", "content": "hi"}]) == "ollama reply"
    with pytest.raises(HTTPException) as exc:
        llm_core.llm_call("https://status.local/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])
    assert exc.value.status_code == 502
    with pytest.raises(HTTPException) as exc:
        llm_core.llm_call("https://bad-schema.local/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])
    assert exc.value.status_code == 502


def test_sync_and_async_fallbacks(monkeypatch):
    attempts = []

    def fake_llm_call(url, model, messages, headers=None, **kwargs):
        attempts.append((url, model, headers))
        if "bad" in url:
            raise RuntimeError("down")
        return f"ok:{model}"

    async def fake_llm_call_async(url, model, messages, headers=None, **kwargs):
        if "bad" in url:
            raise RuntimeError("down")
        return f"async:{model}"

    monkeypatch.setattr(llm_core, "llm_call", fake_llm_call)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)
    assert llm_core.llm_call_with_fallback(
        [("http://bad", "bad", {}), ("http://good", "good", {"H": "1"})],
        [{"role": "user", "content": "hi"}],
    ) == "ok:good"
    assert attempts[0][1] == "bad"
    assert asyncio.run(llm_core.llm_call_async_with_fallback(
        [("http://bad", "bad", {}), ("http://good", "good", {})],
        [{"role": "user", "content": "hi"}],
    )) == "async:good"
    with pytest.raises(HTTPException):
        llm_core.llm_call_with_fallback([], [])


def test_async_llm_call_success_retries_and_dead_host(monkeypatch):
    class FakeClient:
        def __init__(self, responses):
            self.responses = list(responses)

        async def post(self, *args, **kwargs):
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    async def success_case():
        client = FakeClient([Response(payload={"choices": [{"message": {"content": "async reply"}}]})])
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        return await llm_core.llm_call_async(
            "https://api.openai.com/v1/chat/completions",
            "gpt-4o",
            [{"role": "user", "content": "hi"}],
            max_tokens=5,
        )

    assert asyncio.run(success_case()) == "async reply"

    async def http_error_case():
        client = FakeClient([Response(401, text='{"error":{"message":"bad key"}}')])
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        await llm_core.llm_call_async("https://api.openai.com/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(http_error_case())
    assert exc.value.status_code == 401

    async def retry_case():
        client = FakeClient([httpx.RequestError("temporary"), Response(payload={"choices": [{"message": {"content": "after retry"}}]})])
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        original_sleep = asyncio.sleep
        monkeypatch.setattr(llm_core.asyncio, "sleep", lambda delay: original_sleep(0))
        return await llm_core.llm_call_async(
            "https://retry.local/v1/chat/completions",
            "m",
            [{"role": "user", "content": "hi"}],
            max_retries=2,
        )

    assert asyncio.run(retry_case()) == "after retry"

    async def connect_error_case():
        client = FakeClient([httpx.ConnectError("no route")])
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        await llm_core.llm_call_async("https://dead.local/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(connect_error_case())
    assert exc.value.status_code == 503

    llm_core._dead_hosts[llm_core._host_key("https://dead.local/v1/chat/completions")] = time.time() + 60
    with pytest.raises(HTTPException) as exc:
        asyncio.run(llm_core.llm_call_async("https://dead.local/v1/chat/completions", "m", [{"role": "user", "content": "hi"}]))
    assert exc.value.status_code == 503


def test_stream_fallback_precontent_and_after_output(monkeypatch):
    async def fake_stream(url, model, messages, headers=None, **kwargs):
        if model == "bad":
            yield 'event: error\ndata: {"error":"down"}\n\n'
        elif model == "late":
            yield 'data: {"delta":"hi"}\n\n'
            yield 'event: error\ndata: {"error":"late"}\n\n'
        else:
            yield 'data: {"delta":"ok"}\n\n'
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def collect(candidates):
        return [chunk async for chunk in llm_core.stream_llm_with_fallback(candidates, [{"role": "user", "content": "hi"}])]

    assert asyncio.run(collect([("u1", "bad", {}), ("u2", "good", {})])) == [
        'data: {"delta":"ok"}\n\n',
        "data: [DONE]\n\n",
    ]
    late = asyncio.run(collect([("u1", "late", {}), ("u2", "good", {})]))
    assert late[0].startswith("data:")
    assert late[1].startswith("event: error")
    empty = asyncio.run(collect([]))
    assert "No model endpoint configured" in empty[0]
