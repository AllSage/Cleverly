import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.integrations as integrations


def install_secret_codec(monkeypatch):
    monkeypatch.setattr(integrations, "encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr(integrations, "decrypt", lambda value: value.removeprefix("enc:"))
    monkeypatch.setattr(integrations, "is_encrypted", lambda value: value.startswith("enc:"))


def test_storage_secret_roundtrip_and_crud(monkeypatch, tmp_path):
    install_secret_codec(monkeypatch)
    data_file = tmp_path / "integrations.json"
    monkeypatch.setattr(integrations, "DATA_FILE", str(data_file))
    monkeypatch.setattr(integrations, "safe_chmod", lambda path, mode: None)

    assert integrations.load_integrations() == []

    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text("{bad", encoding="utf-8")
    assert integrations.load_integrations() == []
    data_file.write_text(json.dumps({"bad": "shape"}), encoding="utf-8")
    assert integrations.load_integrations() == []

    data_file.write_text(json.dumps([{"id": "plain", "name": "Plain", "api_key": "secret"}]), encoding="utf-8")
    loaded = integrations.load_integrations()
    assert loaded == [{"id": "plain", "name": "Plain", "api_key": "secret"}]
    assert json.loads(data_file.read_text(encoding="utf-8"))[0]["api_key"] == "enc:secret"

    saved = [{"id": "saved", "name": "Saved", "api_key": "top"}]
    integrations.save_integrations(saved)
    assert json.loads(data_file.read_text(encoding="utf-8"))[0]["api_key"] == "enc:top"

    assert integrations.mask_integration_secret({"api_key": "abcdefgh"})["api_key"] == "abcd****"
    assert integrations.mask_integration_secret({"name": "No key"}) == {"name": "No key"}
    assert integrations.get_integration("saved")["api_key"] == "top"
    assert integrations.get_integration("missing") is None

    monkeypatch.setattr(integrations.uuid, "uuid4", lambda: SimpleNamespace(hex="abc123456789ffff"))
    added = integrations.add_integration({"preset": "miniflux", "base_url": "https://rss.test/v1", "api_key": "key"})
    assert added["id"] == "abc123456789"
    assert added["name"] == "Miniflux"
    assert added["enabled"] is True

    updated = integrations.update_integration("abc123456789", {"id": "changed", "name": "Renamed"})
    assert updated["id"] == "abc123456789"
    assert updated["name"] == "Renamed"
    assert integrations.update_integration("missing", {"name": "Nope"}) is None

    assert integrations.delete_integration("abc123456789") is True
    assert integrations.delete_integration("missing") is False


class FakeResponse:
    def __init__(self, *, status_code=200, text="", headers=None, json_data=None, json_raises=False):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json_data = json_data
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("bad json")
        return self._json_data


class FakeAsyncClient:
    responses = []
    requests = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, params=None, json=None, headers=None, auth=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": dict(headers or {}),
                "auth": auth,
            }
        )
        next_item = self.responses.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        return next_item


def install_http(monkeypatch, responses):
    FakeAsyncClient.responses = list(responses)
    FakeAsyncClient.requests = []
    monkeypatch.setattr(integrations.httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient.requests


@pytest.mark.asyncio
async def test_execute_api_call_validation_and_auth(monkeypatch):
    monkeypatch.setattr(
        integrations,
        "load_integrations",
        lambda: [
            {"id": "disabled", "name": "Disabled", "enabled": False, "base_url": "https://x.test"},
            {"id": "nobase", "name": "NoBase", "enabled": True},
            {"id": "min", "preset": "miniflux", "name": "Min", "base_url": "https://rss.test/v1", "auth_type": "header", "api_key": "tok"},
            {"id": "bearer", "name": "Bearer", "base_url": "https://api.test/api", "auth_type": "bearer", "api_key": "bear"},
            {"id": "query", "name": "Query", "base_url": "https://api.test", "auth_type": "query", "auth_param": "token", "api_key": "q"},
            {"id": "basic", "name": "Basic", "base_url": "https://api.test", "auth_type": "basic", "api_key": "u:p"},
        ],
    )

    assert (await integrations.execute_api_call("missing", "GET", "/x"))["exit_code"] == 1
    assert "disabled" in (await integrations.execute_api_call("disabled", "GET", "/x"))["error"]
    assert "base_url" in (await integrations.execute_api_call("nobase", "GET", "/x"))["error"]
    assert "start with" in (await integrations.execute_api_call("min", "GET", "x"))["error"]
    assert "protocol" in (await integrations.execute_api_call("min", "GET", "/https://evil.test"))["error"]

    requests = install_http(
        monkeypatch,
        [
            FakeResponse(headers={"content-type": "application/json"}, json_data={"ok": True}),
            FakeResponse(headers={"content-type": "text/html"}, text="<h1>Hello</h1> <b>world</b>"),
            FakeResponse(headers={"content-type": "text/plain"}, text="plain"),
            FakeResponse(headers={"content-type": "text/plain"}, text="plain no params"),
            FakeResponse(headers={"content-type": "text/plain"}, text="basic"),
        ],
    )

    assert (await integrations.execute_api_call("min", "post", "/v1/feeds", body={"x": 1}, extra_headers={"X": "1"}))["exit_code"] == 0
    assert requests[-1]["method"] == "POST"
    assert requests[-1]["url"] == "https://rss.test/v1/feeds"
    assert requests[-1]["headers"]["X-Auth-Token"] == "tok"
    assert requests[-1]["headers"]["X"] == "1"

    assert "Hello world" in (await integrations.execute_api_call("bearer", "GET", "/states"))["output"]
    assert requests[-1]["headers"]["Authorization"] == "Bearer bear"

    assert "plain" in (await integrations.execute_api_call("query", "GET", "/items", params={"a": 1}))["output"]
    assert requests[-1]["params"] == {"a": 1, "token": "q"}

    assert "plain no params" in (await integrations.execute_api_call("query", "GET", "/items"))["output"]
    assert requests[-1]["params"] == {"token": "q"}

    assert "basic" in (await integrations.execute_api_call("basic", "GET", "/secure"))["output"]
    assert requests[-1]["auth"] is not None


@pytest.mark.asyncio
async def test_execute_api_call_response_and_exception_branches(monkeypatch):
    long_text = "x" * 12001
    monkeypatch.setattr(
        integrations,
        "load_integrations",
        lambda: [
            {"id": "ok", "name": "OK", "base_url": "https://api.test", "auth_type": "none", "enabled": True},
        ],
    )
    install_http(
        monkeypatch,
        [
            FakeResponse(headers={"content-type": "application/json"}, text="{bad", json_raises=True),
            FakeResponse(status_code=404, headers={"content-type": "text/plain"}, text="missing"),
            FakeResponse(headers={"content-type": "text/plain"}, text=long_text),
            integrations.httpx.TimeoutException("slow"),
            integrations.httpx.RequestError("network down"),
            RuntimeError("boom"),
        ],
    )

    assert "{bad" in (await integrations.execute_api_call("ok", "GET", "/bad-json"))["output"]
    assert (await integrations.execute_api_call("ok", "GET", "/missing"))["exit_code"] == 1
    truncated = await integrations.execute_api_call("ok", "GET", "/long")
    assert truncated["output"].endswith("... (truncated)")
    assert "timed out" in (await integrations.execute_api_call("ok", "GET", "/slow"))["error"]
    assert "network down" in (await integrations.execute_api_call("ok", "GET", "/network"))["error"]
    assert "Unexpected error: boom" == (await integrations.execute_api_call("ok", "GET", "/boom"))["error"]


def test_find_strip_prompt_and_migration(monkeypatch, tmp_path):
    monkeypatch.setattr(
        integrations,
        "load_integrations",
        lambda: [
            {"id": "one", "name": "Named", "enabled": True, "description": "Useful API"},
            {"id": "two", "name": "Off", "enabled": False},
        ],
    )
    assert integrations._strip_html_tags("<p>A</p>\n <b>B</b>") == "A B"
    assert integrations._find_integration("one")["name"] == "Named"
    assert integrations._find_integration("named")["id"] == "one"
    assert integrations._find_integration("missing") is None

    monkeypatch.setattr(integrations, "offline_mode", lambda: True)
    assert integrations.get_integrations_prompt() == ""
    monkeypatch.setattr(integrations, "offline_mode", lambda: False)
    prompt = integrations.get_integrations_prompt()
    assert "Named" in prompt and "Useful API" in prompt and "Off" not in prompt
    monkeypatch.setattr(integrations, "load_integrations", lambda: [])
    assert integrations.get_integrations_prompt() == ""

    fake_src = tmp_path / "src"
    data_dir = tmp_path / "data"
    fake_src.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(integrations, "__file__", str(fake_src / "integrations.py"))

    integrations.migrate_from_settings()

    settings_path = data_dir / "settings.json"
    settings_path.write_text("{bad", encoding="utf-8")
    integrations.migrate_from_settings()

    settings_path.write_text(json.dumps({"miniflux_url": "", "miniflux_api_key": "key"}), encoding="utf-8")
    integrations.migrate_from_settings()

    settings_path.write_text(json.dumps({"miniflux_url": "https://rss.test/", "miniflux_api_key": "key"}), encoding="utf-8")
    monkeypatch.setattr(integrations, "load_integrations", lambda: [{"preset": "miniflux"}])
    integrations.migrate_from_settings()
    assert json.loads(settings_path.read_text(encoding="utf-8"))["miniflux_api_key"] == "key"

    added = []
    monkeypatch.setattr(integrations, "load_integrations", lambda: [])
    monkeypatch.setattr(integrations, "add_integration", lambda data: added.append(data))
    integrations.migrate_from_settings()
    assert added == [{"preset": "miniflux", "base_url": "https://rss.test", "api_key": "key"}]
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {}
