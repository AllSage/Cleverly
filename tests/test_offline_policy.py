import pytest


def test_local_model_url_policy_allows_loopback_and_compose_names():
    from src.offline_policy import is_local_model_url

    assert is_local_model_url("http://localhost:11434/v1")
    assert is_local_model_url("http://127.0.0.1:11434/v1")
    assert is_local_model_url("http://127.3.4.5:8000/v1")
    assert is_local_model_url("http://host.docker.internal:11434/v1")
    assert is_local_model_url("http://ollama:11434/v1")
    assert is_local_model_url("http://vllm-worker:8000/v1")


@pytest.mark.parametrize("url", [
    "",
    "https://api.openai.com/v1",
    "https://api.z.ai/api/paas/v4",
    "http://192.168.1.50:8000/v1",
    "http://llm-host.local:8000/v1",
])
def test_local_model_url_policy_rejects_external_or_lan_urls(url):
    from src.offline_policy import is_local_model_url

    assert is_local_model_url(url) is False


def test_offline_policy_fails_closed_without_offline_flag(monkeypatch):
    from src import settings
    from src.offline_policy import evaluate_offline_policy

    monkeypatch.delenv("CLEVERLY_OFFLINE", raising=False)
    monkeypatch.delenv("CLEVERLY_ALLOW_NETWORK", raising=False)
    monkeypatch.delenv("CLEVERLY_DISABLE_OFFLINE_POLICY", raising=False)
    settings._invalidate_caches()
    try:
        report = evaluate_offline_policy(include_db=False)
    finally:
        settings._invalidate_caches()

    assert report["strict"] is True
    assert any(item["id"] == "offline-mode" and item["status"] == "fail" for item in report["checks"])


def test_offline_policy_flags_external_model_env(monkeypatch):
    from src import settings
    from src.offline_policy import evaluate_offline_policy

    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://api.openai.com/v1")
    settings._invalidate_caches()
    try:
        report = evaluate_offline_policy(include_db=False)
    finally:
        settings._invalidate_caches()

    assert any(item["id"] == "model-env-local" and item["status"] == "fail" for item in report["checks"])


def test_startup_policy_can_be_break_glassed(monkeypatch):
    from src import settings
    from src.offline_policy import NETWORK_BREAK_GLASS_VALUE, evaluate_offline_policy

    monkeypatch.delenv("CLEVERLY_OFFLINE", raising=False)
    monkeypatch.setenv("CLEVERLY_ALLOW_NETWORK", NETWORK_BREAK_GLASS_VALUE)
    settings._invalidate_caches()
    try:
        report = evaluate_offline_policy(include_db=False)
    finally:
        settings._invalidate_caches()

    assert report["strict"] is False
    assert any(item["id"] == "offline-mode" and item["status"] == "warn" for item in report["checks"])
