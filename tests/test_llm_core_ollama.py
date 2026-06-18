"""Regression tests for native Ollama Cloud provider handling."""
import asyncio

import httpx
import pytest

from src import llm_core


def test_detects_ollama_cloud_native_provider():
    assert llm_core._detect_provider("https://ollama.com/api") == "ollama"
    assert llm_core._detect_provider("https://ollama.com/api/chat") == "ollama"


def test_llm_call_posts_native_ollama_payload(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json"] = json
        seen["timeout"] = timeout
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    result = llm_core.llm_call(
        "https://ollama.com/api",
        "gpt-oss:120b-test",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=7,
        headers={"Authorization": "Bearer ollama-key"},
        timeout=11,
    )

    assert result == "OK"
    assert seen["url"] == "https://ollama.com/api/chat"
    assert seen["headers"]["Authorization"] == "Bearer ollama-key"
    assert seen["json"]["stream"] is False
    assert seen["json"]["options"] == {"temperature": 0.2, "num_predict": 7}


def test_llm_core_blocks_remote_sync_calls_before_network(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("network call should be blocked")

    monkeypatch.setattr(llm_core.httpx, "post", fail_post)

    monkeypatch.setattr(llm_core, "offline_mode", lambda: True)
    monkeypatch.setattr(llm_core, "load_features", lambda: {"external_model_endpoints": True})
    with pytest.raises(llm_core.HTTPException) as offline_err:
        llm_core.llm_call("https://api.openai.com/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])
    assert offline_err.value.status_code == 403

    monkeypatch.setattr(llm_core, "offline_mode", lambda: False)
    monkeypatch.setattr(llm_core, "load_features", lambda: {"external_model_endpoints": False})
    with pytest.raises(llm_core.HTTPException) as disabled_err:
        llm_core.llm_call("https://api.openai.com/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])
    assert disabled_err.value.status_code == 403

    monkeypatch.setattr(llm_core, "load_features", lambda: (_ for _ in ()).throw(RuntimeError("settings down")))
    with pytest.raises(llm_core.HTTPException) as fail_closed_err:
        llm_core.llm_call("https://api.openai.com/v1/chat/completions", "m", [{"role": "user", "content": "hi"}])
    assert fail_closed_err.value.status_code == 403


def test_llm_core_allows_local_sync_calls_when_remote_endpoints_disabled(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "local ok"}}]})

    monkeypatch.setattr(llm_core, "offline_mode", lambda: True)
    monkeypatch.setattr(llm_core, "load_features", lambda: {"external_model_endpoints": False})
    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    result = llm_core.llm_call(
        "http://ollama:11434/v1/chat/completions",
        "local-model",
        [{"role": "user", "content": "hi"}],
    )

    assert result == "local ok"
    assert seen["url"] == "http://ollama:11434/v1/chat/completions"


def test_llm_core_blocks_remote_async_and_stream_before_network(monkeypatch):
    class BlockedClient:
        async def post(self, *_args, **_kwargs):
            raise AssertionError("async network call should be blocked")

        def stream(self, *_args, **_kwargs):
            raise AssertionError("stream network call should be blocked")

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: BlockedClient())
    monkeypatch.setattr(llm_core, "offline_mode", lambda: False)
    monkeypatch.setattr(llm_core, "load_features", lambda: {"external_model_endpoints": False})

    with pytest.raises(llm_core.HTTPException) as async_err:
        asyncio.run(
            llm_core.llm_call_async(
                "https://api.openai.com/v1/chat/completions",
                "m",
                [{"role": "user", "content": "hi"}],
            )
        )
    assert async_err.value.status_code == 403

    chunks = asyncio.run(
        _collect_stream(
            llm_core.stream_llm(
                "https://api.openai.com/v1/chat/completions",
                "m",
                [{"role": "user", "content": "hi"}],
            )
        )
    )
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert '"status": 403' in chunks[0]


def test_llm_core_model_listing_blocks_remote_before_network(monkeypatch):
    def fail_get(*_args, **_kwargs):
        raise AssertionError("model listing network call should be blocked")

    monkeypatch.setattr(llm_core.httpx, "get", fail_get)
    monkeypatch.setattr(llm_core, "offline_mode", lambda: False)
    monkeypatch.setattr(llm_core, "load_features", lambda: {"external_model_endpoints": False})

    assert llm_core.list_model_ids("https://api.openai.com/v1/chat/completions") == []


async def _collect_stream(stream):
    return [chunk async for chunk in stream]
