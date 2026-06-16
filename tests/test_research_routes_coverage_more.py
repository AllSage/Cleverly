import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

import routes.research_routes as research_routes


def _endpoint(router, path: str, method: str | None = None):
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", "") == path
        and (method is None or method in getattr(route, "methods", set()))
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def _request(user="alice", headers=None, auth_manager=None):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        headers=headers or {},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _write_research(session_id, data):
    path = Path("data/deep_research") / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class FakeResearchHandler:
    def __init__(self):
        self._active_tasks = {}
        self.statuses = {}
        self.results = {}
        self.sources = {}
        self.raw_findings = {}
        self.reports = {}
        self.cancelled = []
        self.cleared = []
        self.hidden = []
        self.unhidden = []
        self.started = []

    def get_status(self, session_id):
        value = self.statuses.get(session_id)
        if isinstance(value, list):
            if value:
                return value.pop(0)
            return None
        return value

    def cancel_research(self, session_id):
        self.cancelled.append(session_id)
        return session_id != "no-cancel"

    def get_result(self, session_id):
        return self.results.get(session_id)

    def get_sources(self, session_id):
        return self.sources.get(session_id)

    def get_raw_findings(self, session_id):
        return self.raw_findings.get(session_id)

    def clear_result(self, session_id):
        self.cleared.append(session_id)

    def get_report_html(self, session_id):
        value = self.reports.get(session_id)
        if isinstance(value, BaseException):
            raise value
        return value

    def hide_image(self, session_id, url):
        self.hidden.append((session_id, url))
        return session_id != "missing"

    def unhide_all_images(self, session_id):
        self.unhidden.append(session_id)
        return session_id != "missing"

    def start_research(self, **kwargs):
        self.started.append(kwargs)


class FakeSession:
    def __init__(self, endpoint_url="http://session", model="session-model", headers=None, owner="alice"):
        self.endpoint_url = endpoint_url
        self.model = model
        self.headers = headers if headers is not None else {"Authorization": "Bearer token"}
        self.owner = owner
        self.messages = []

    def add_message(self, message):
        self.messages.append(message)


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}
        self.created = []
        self.saved = 0

    def get_session(self, session_id):
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    def create_session(self, **kwargs):
        self.created.append(kwargs)
        sess = FakeSession(
            endpoint_url=kwargs.get("endpoint_url", ""),
            model=kwargs.get("model", ""),
            headers={},
            owner=kwargs.get("owner"),
        )
        self.sessions[kwargs["session_id"]] = sess
        return sess

    def save_sessions(self):
        self.saved += 1


class FakeDb:
    def __init__(self, endpoint=None):
        self.endpoint = endpoint
        self.closed = False

    def query(self, model):
        return self

    def filter(self, *conditions):
        return self

    def first(self):
        return self.endpoint

    def close(self):
        self.closed = True


class FakeEndpoint:
    id = "endpoint-column"
    is_enabled = True

    def __init__(self, base_url="http://base", api_key="key", cached_models=None):
        self.base_url = base_url
        self.api_key = api_key
        self.cached_models = cached_models


def install_fake_database(monkeypatch, endpoint):
    database = types.ModuleType("src.database")
    fake_db = FakeDb(endpoint)
    database.SessionLocal = lambda: fake_db
    database.ModelEndpoint = FakeEndpoint
    monkeypatch.setitem(sys.modules, "src.database", database)
    return fake_db


def install_endpoint_builders(monkeypatch):
    import src.endpoint_resolver as endpoint_resolver

    monkeypatch.setattr(endpoint_resolver, "normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(endpoint_resolver, "build_chat_url", lambda base: f"{base}/v1/chat/completions")
    monkeypatch.setattr(endpoint_resolver, "build_headers", lambda api_key, base: {"Authorization": f"Bearer {api_key}"})


def test_helpers_first_chat_model_and_resolve_endpoint(monkeypatch):
    calls = []

    def fake_resolve(kind, **kwargs):
        calls.append((kind, kwargs))
        return "url", "model", {"h": "v"}

    monkeypatch.setattr(research_routes, "resolve_endpoint", fake_resolve)

    assert research_routes._first_chat_model(["text-embedding-ada-002", "llama3"]) == "llama3"
    assert research_routes._first_chat_model(["embedding-only"]) == "embedding-only"
    assert research_routes._first_chat_model([]) == ""
    assert research_routes._resolve_research_endpoint(FakeSession("fallback-url", "fallback-model", {"x": "y"})) == (
        "url",
        "model",
        {"h": "v"},
    )
    assert calls == [
        (
            "research",
            {
                "fallback_url": "fallback-url",
                "fallback_model": "fallback-model",
                "fallback_headers": {"x": "y"},
            },
        )
    ]


def test_active_status_cancel_result_and_report_routes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = FakeResearchHandler()
    handler._active_tasks = {
        "mine": {"owner": "alice", "status": "running", "query": "Q", "progress": {"step": 1}, "started_at": 5},
        "done": {"owner": "alice", "status": "done"},
        "theirs": {"owner": "bob", "status": "running"},
    }
    handler.statuses["mine"] = {"status": "running", "progress": {"step": 1}}
    handler.results["mine"] = "final"
    handler.sources["mine"] = [{"url": "u"}]
    handler.raw_findings["mine"] = ["raw"]
    handler.reports["mine"] = "<html>report</html>"
    _write_research("mine", {"owner": "alice"})
    router = research_routes.setup_research_routes(handler)

    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/active")(_request(user=None)))
    assert excinfo.value.status_code == 401

    assert _run(_endpoint(router, "/api/research/active")(_request())) == {
        "active": [{"session_id": "mine", "query": "Q", "status": "running", "progress": {"step": 1}, "started_at": 5}]
    }
    assert _run(_endpoint(router, "/api/research/status/{session_id}")("mine", _request())) == handler.statuses["mine"]
    assert _run(_endpoint(router, "/api/research/cancel/{session_id}", "POST")("mine", _request())) == {"cancelled": True}
    assert _run(_endpoint(router, "/api/research/result/{session_id}", "POST")("mine", _request())) == {
        "result": "final",
        "sources": [{"url": "u"}],
        "raw_findings": ["raw"],
    }
    assert handler.cleared == ["mine"]
    response = _run(_endpoint(router, "/api/research/report/{session_id}")("mine", _request()))
    assert isinstance(response, HTMLResponse)
    assert response.body == b"<html>report</html>"

    for path, method in [
        ("/api/research/status/{session_id}", None),
        ("/api/research/cancel/{session_id}", "POST"),
        ("/api/research/result/{session_id}", "POST"),
    ]:
        with pytest.raises(HTTPException) as excinfo:
            _run(_endpoint(router, path, method)("theirs", _request()))
        assert excinfo.value.status_code == 404

    handler.statuses["mine"] = None
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/status/{session_id}")("mine", _request()))
    assert excinfo.value.status_code == 404

    handler.results["mine"] = None
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/result/{session_id}", "POST")("mine", _request()))
    assert excinfo.value.status_code == 404

    handler.reports["mine"] = None
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/report/{session_id}")("mine", _request()))
    assert excinfo.value.status_code == 404

    handler.reports["mine"] = RuntimeError("render failed")
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/report/{session_id}")("mine", _request()))
    assert excinfo.value.status_code == 500

    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/report/{session_id}")("missing", _request()))
    assert excinfo.value.status_code == 404

    Path("data/deep_research/bad-owner.json").write_text("{", encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/report/{session_id}")("bad-owner", _request()))
    assert excinfo.value.status_code == 404

    handler._active_tasks.pop("done")
    _write_research("done", {"owner": "alice"})
    handler.statuses["done"] = {"status": "done"}
    assert _run(_endpoint(router, "/api/research/status/{session_id}")("done", _request())) == {"status": "done"}
    Path("data/deep_research/corrupt.json").write_text("{", encoding="utf-8")
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/status/{session_id}")("corrupt", _request()))
    assert excinfo.value.status_code == 404


def test_disk_ownership_image_library_detail_archive_delete(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = FakeResearchHandler()
    router = research_routes.setup_research_routes(handler)
    _write_research("one", {
        "owner": "alice",
        "query": "Alpha Topic",
        "category": "cat",
        "sources": [1, 2],
        "status": "done",
        "stats": {"Duration": "10s", "Rounds": 2},
        "started_at": 1,
        "completed_at": 20,
    })
    _write_research("gamma", {"owner": "alice", "query": "Gamma", "sources": [], "completed_at": 11})
    _write_research("two", {"owner": "alice", "query": "Beta", "sources": [1], "completed_at": 10, "archived": True})
    _write_research("bob", {"owner": "bob", "query": "Alpha Topic", "completed_at": 30})
    Path("data/deep_research/bad.json").write_text("{", encoding="utf-8")

    body_cls = _endpoint(router, "/api/research/{session_id}/hide-image", "POST").__annotations__["body"]
    assert _run(_endpoint(router, "/api/research/{session_id}/hide-image", "POST")("one", body_cls(url="http://img"), _request())) == {"ok": True}
    assert _run(_endpoint(router, "/api/research/{session_id}/unhide-images", "POST")("one", _request())) == {"ok": True}
    assert handler.hidden == [("one", "http://img")]
    assert handler.unhidden == ["one"]

    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}/hide-image", "POST")("bob", body_cls(url="x"), _request()))
    assert excinfo.value.status_code == 404

    _write_research("missing", {"owner": "alice"})
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}/hide-image", "POST")("missing", body_cls(url="x"), _request()))
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}/unhide-images", "POST")("missing", _request()))
    assert excinfo.value.status_code == 404

    lib = _run(_endpoint(router, "/api/research/library")(_request(), search="alpha", sort="recent", limit=10, archived=False))
    assert lib["total"] == 1
    assert lib["research"][0]["id"] == "one"
    assert lib["research"][0]["source_count"] == 2

    archived = _run(_endpoint(router, "/api/research/library")(_request(), search=None, sort="oldest", limit=10, archived=True))
    assert [item["id"] for item in archived["research"]] == ["two"]
    most_messages = _run(_endpoint(router, "/api/research/library")(_request(), search=None, sort="most-messages", limit=1, archived=False))
    assert most_messages["total"] == 3
    assert most_messages["research"][0]["id"] == "one"
    alpha_sorted = _run(_endpoint(router, "/api/research/library")(_request(), search=None, sort="alpha", limit=10, archived=False))
    assert [item["query"] for item in alpha_sorted["research"][:2]] == ["", "Alpha Topic"]

    assert _run(_endpoint(router, "/api/research/detail/{session_id}")("one", _request()))["query"] == "Alpha Topic"
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/detail/{session_id}")("no-such-detail", _request()))
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/detail/{session_id}")("bad", _request()))
    assert excinfo.value.status_code == 500
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/detail/{session_id}")("bob", _request()))
    assert excinfo.value.status_code == 404

    assert _run(_endpoint(router, "/api/research/{session_id}/archive", "POST")("one", _request(), archived=True)) == {
        "ok": True,
        "id": "one",
        "archived": True,
    }
    assert json.loads(Path("data/deep_research/one.json").read_text(encoding="utf-8"))["archived"] is True
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}/archive", "POST")("bob", _request(), archived=True))
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}/archive", "POST")("no-such", _request(), archived=True))
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}/archive", "POST")("bad", _request(), archived=True))
    assert excinfo.value.status_code == 500

    assert _run(_endpoint(router, "/api/research/{session_id}", "DELETE")("one", _request())) == {"deleted": True}
    assert _run(_endpoint(router, "/api/research/{session_id}", "DELETE")("no-such-delete", _request())) == {"deleted": False}
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}", "DELETE")("bob", _request()))
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(router, "/api/research/{session_id}", "DELETE")("bad", _request()))
    assert excinfo.value.status_code == 404


def test_start_route_endpoint_id_and_fallbacks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = FakeResearchHandler()
    router = research_routes.setup_research_routes(handler)
    start = _endpoint(router, "/api/research/start", "POST")
    body_cls = start.__annotations__["body"]

    import src.auth_helpers as auth_helpers
    monkeypatch.setattr(auth_helpers, "require_privilege", lambda request, privilege: "alice")
    install_endpoint_builders(monkeypatch)
    db = install_fake_database(monkeypatch, FakeEndpoint(cached_models=json.dumps(["text-embedding-ada", "chat-model"])))

    result = _run(start(body_cls(query="topic", endpoint_id="ep1", max_rounds=0, category="cat"), _request()))

    assert result["status"] == "running"
    assert db.closed is True
    assert handler.started[-1]["query"] == "topic"
    assert handler.started[-1]["llm_endpoint"] == "http://base/v1/chat/completions"
    assert handler.started[-1]["llm_model"] == "chat-model"
    assert handler.started[-1]["max_rounds"] == 20
    assert handler.started[-1]["category"] == "cat"
    assert handler.started[-1]["owner"] == "alice"

    db = install_fake_database(monkeypatch, None)
    with pytest.raises(HTTPException) as excinfo:
        _run(start(body_cls(query="topic", endpoint_id="missing"), _request()))
    assert excinfo.value.status_code == 404
    assert db.closed is True

    db = install_fake_database(monkeypatch, FakeEndpoint(cached_models="{bad"))
    result = _run(start(body_cls(query="bad cached models", endpoint_id="ep1"), _request()))
    assert result["status"] == "running"
    assert handler.started[-1]["llm_model"] == ""
    assert db.closed is True

    calls = []
    values = {
        "research": ("", "", {}),
        "utility": ("", "", {}),
        "default": ("http://default", "default-model", {"H": "D"}),
        "chat": ("http://chat", "chat-model", {}),
    }
    monkeypatch.setattr(
        research_routes,
        "resolve_endpoint",
        lambda kind, **kwargs: calls.append(kind) or values[kind],
    )
    result = _run(start(body_cls(query="fallback", model="override", max_rounds=3, search_provider="local"), _request()))
    assert calls == ["research", "utility", "default"]
    assert handler.started[-1]["llm_endpoint"] == "http://default"
    assert handler.started[-1]["llm_model"] == "override"
    assert handler.started[-1]["max_rounds"] == 3
    assert handler.started[-1]["search_provider"] == "local"

    values.update({"research": ("", "", {}), "utility": ("", "", {}), "default": ("", "", {}), "chat": ("", "", {})})
    install_fake_database(monkeypatch, FakeEndpoint(base_url="http://last", api_key="last-key", cached_models=json.dumps(["fallback-model"])))
    result = _run(start(body_cls(query="db fallback"), _request()))
    assert handler.started[-1]["llm_endpoint"] == "http://last/v1/chat/completions"
    assert handler.started[-1]["llm_model"] == "fallback-model"

    install_fake_database(monkeypatch, FakeEndpoint(base_url="http://bad-json", api_key="bad-key", cached_models="{bad"))
    result = _run(start(body_cls(query="db fallback bad cached models"), _request()))
    assert handler.started[-1]["llm_endpoint"] == "http://bad-json/v1/chat/completions"
    assert handler.started[-1]["llm_model"] == ""

    install_fake_database(monkeypatch, None)
    with pytest.raises(HTTPException) as excinfo:
        _run(start(body_cls(query="no endpoint"), _request()))
    assert excinfo.value.status_code == 400


def test_start_route_internal_tool_owner_privilege_gate(monkeypatch):
    handler = FakeResearchHandler()
    router = research_routes.setup_research_routes(handler)
    start = _endpoint(router, "/api/research/start", "POST")
    body_cls = start.__annotations__["body"]

    import src.auth_helpers as auth_helpers
    monkeypatch.setattr(auth_helpers, "require_privilege", lambda request, privilege: "internal-tool")
    monkeypatch.setattr(research_routes, "resolve_endpoint", lambda kind, **kwargs: ("http://research", "model", {}))

    class AuthManager:
        is_configured = True

        def __init__(self, allowed):
            self.allowed = allowed

        def get_privileges(self, owner):
            if self.allowed == "raise":
                raise RuntimeError("privilege store down")
            return {"can_use_research": self.allowed}

    denied_request = _request(user="internal-tool", headers={"X-Cleverly-Owner": "alice"}, auth_manager=AuthManager(False))
    with pytest.raises(HTTPException) as excinfo:
        _run(start(body_cls(query="blocked"), denied_request))
    assert excinfo.value.status_code == 403

    allowed_request = _request(user="internal-tool", headers={"X-Cleverly-Owner": "alice"}, auth_manager=AuthManager(True))
    _run(start(body_cls(query="allowed"), allowed_request))
    assert handler.started[-1]["owner"] == "alice"

    raising_request = _request(user="internal-tool", headers={"X-Cleverly-Owner": "alice"}, auth_manager=AuthManager("raise"))
    _run(start(body_cls(query="privilege fallback"), raising_request))
    assert handler.started[-1]["owner"] == "alice"

    system_request = _request(user="internal-tool", headers={"X-Cleverly-Owner": "system"}, auth_manager=AuthManager(False))
    _run(start(body_cls(query="system"), system_request))
    assert handler.started[-1]["owner"] == "internal-tool"


def _extract_sse_payloads(chunks):
    payloads = []
    for chunk in chunks:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        assert text.startswith("data: ")
        payloads.append(json.loads(text[len("data: "):].strip()))
    return payloads


def test_stream_route_progress_done_error_and_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = FakeResearchHandler()
    handler._active_tasks["stream"] = {"owner": "alice", "status": "running"}
    router = research_routes.setup_research_routes(handler)
    stream = _endpoint(router, "/api/research/stream/{session_id}")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(research_routes.asyncio, "sleep", no_sleep)

    handler.statuses["stream"] = [
        {"status": "running", "progress": {"round": 1}},
        {"status": "done", "progress": {"round": 2}},
    ]
    response = _run(stream("stream", _request()))
    assert isinstance(response, StreamingResponse)

    async def collect(resp):
        return [chunk async for chunk in resp.body_iterator]

    payloads = _extract_sse_payloads(_run(collect(response)))
    assert payloads == [
        {"round": 1, "status": "running"},
        {"round": 2, "status": "done"},
        {"status": "done", "final": True},
    ]

    handler.statuses["stream"] = None
    response = _run(stream("stream", _request()))
    assert _extract_sse_payloads(_run(collect(response))) == [{"status": "not_found"}]

    handler.statuses["stream"] = [{"status": "error", "progress": {}}]
    handler._active_tasks["stream"]["result"] = "x" * 600
    response = _run(stream("stream", _request()))
    payloads = _extract_sse_payloads(_run(collect(response)))
    assert payloads[-1]["status"] == "error"
    assert payloads[-1]["final"] is True
    assert len(payloads[-1]["error"]) == 500

    with pytest.raises(HTTPException) as excinfo:
        _run(stream("missing", _request()))
    assert excinfo.value.status_code == 404


def test_result_peek_memory_disk_and_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    handler = FakeResearchHandler()
    handler._active_tasks["mine"] = {"owner": "alice"}
    handler.results["mine"] = "memory result"
    handler.sources["mine"] = ["source"]
    handler.raw_findings["mine"] = ["raw"]
    router = research_routes.setup_research_routes(handler)
    peek = _endpoint(router, "/api/research/result-peek/{session_id}", "POST")

    assert _run(peek("mine", _request())) == {
        "result": "memory result",
        "sources": ["source"],
        "raw_findings": ["raw"],
        "category": "",
    }

    handler.results["mine"] = None
    _write_research("mine", {"owner": "alice", "result": "disk", "sources": [1], "raw_findings": [2], "category": "c"})
    assert _run(peek("mine", _request())) == {
        "result": "disk",
        "sources": [1],
        "raw_findings": [2],
        "category": "c",
    }

    handler._active_tasks["empty"] = {"owner": "alice"}
    with pytest.raises(HTTPException) as excinfo:
        _run(peek("empty", _request()))
    assert excinfo.value.status_code == 404

    handler._active_tasks["theirs"] = {"owner": "bob"}
    with pytest.raises(HTTPException) as excinfo:
        _run(peek("theirs", _request()))
    assert excinfo.value.status_code == 404


def install_chat_message(monkeypatch):
    core_models = types.ModuleType("core.models")

    class ChatMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    core_models.ChatMessage = ChatMessage
    monkeypatch.setitem(sys.modules, "core.models", core_models)
    return ChatMessage


def test_spinoff_route_session_inheritance_disk_fallback_and_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_chat_message(monkeypatch)
    handler = FakeResearchHandler()
    sessions = FakeSessionManager()
    sessions.sessions["source"] = FakeSession("http://source", "source-model", {"Auth": "1"})
    handler.results["source"] = "report"
    handler.sources["source"] = ["s1", "s2"]
    _write_research("source", {"owner": "alice", "query": "A very long query " * 10})
    router = research_routes.setup_research_routes(handler, sessions)
    spinoff = _endpoint(router, "/api/research/spinoff/{session_id}", "POST")

    events = []
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda event, user: events.append((event, user))
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    result = _run(spinoff("source", _request()))

    assert result["source_count"] == 2
    assert result["name"].startswith("Follow-up: A very long query")
    created = sessions.created[-1]
    assert created["endpoint_url"] == "http://source"
    assert created["model"] == "source-model"
    new_session = sessions.sessions[result["session_id"]]
    assert new_session.headers == {"Auth": "1"}
    assert "=== REPORT ===\nreport" in new_session.messages[0].content
    assert new_session.messages[0].metadata == {"research_spinoff_from": "source"}
    assert events == [("session_created", "alice")]

    bare_router = research_routes.setup_research_routes(handler, None)
    with pytest.raises(HTTPException) as excinfo:
        _run(_endpoint(bare_router, "/api/research/spinoff/{session_id}", "POST")("source", _request()))
    assert excinfo.value.status_code == 500

    with pytest.raises(HTTPException) as excinfo:
        _run(spinoff("missing", _request()))
    assert excinfo.value.status_code == 404


def test_spinoff_route_resolves_fallbacks_and_database_last_resort(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_chat_message(monkeypatch)
    handler = FakeResearchHandler()
    sessions = FakeSessionManager()
    handler.results["rp-1"] = None
    _write_research("rp-1", {"owner": "alice", "query": "disk query", "result": "disk report", "sources": ["disk"]})
    router = research_routes.setup_research_routes(handler, sessions)
    spinoff = _endpoint(router, "/api/research/spinoff/{session_id}", "POST")

    calls = []
    values = {"chat": ("", "", {}), "research": ("http://research", "research-model", {"R": "1"}), "utility": ("http://utility", "utility-model", {})}
    monkeypatch.setattr(research_routes, "resolve_endpoint", lambda kind, **kwargs: calls.append(kind) or values[kind])

    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda event, user: (_ for _ in ()).throw(RuntimeError("event failed"))
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    result = _run(spinoff("rp-1", _request()))
    assert calls == ["chat", "research"]
    assert sessions.created[-1]["endpoint_url"] == "http://research"
    assert sessions.created[-1]["model"] == "research-model"
    assert result["source_count"] == 1

    Path("data/deep_research/rp-bad.json").write_text("{", encoding="utf-8")
    handler.results["rp-bad"] = "memory report"
    result = _run(spinoff("rp-bad", _request()))
    assert result["source_count"] == 0

    values.update({"chat": ("", "", {}), "research": ("", "", {}), "utility": ("", "", {})})
    install_endpoint_builders(monkeypatch)
    install_fake_database(monkeypatch, FakeEndpoint("http://db", "db-key", json.dumps(["db-model"])))
    result = _run(spinoff("rp-1", _request()))
    assert sessions.created[-1]["endpoint_url"] == "http://db/v1/chat/completions"
    assert sessions.created[-1]["model"] == "db-model"

    install_fake_database(monkeypatch, FakeEndpoint("http://db", "db-key", "{bad"))
    with pytest.raises(HTTPException) as excinfo:
        _run(spinoff("rp-1", _request()))
    assert excinfo.value.status_code == 400
