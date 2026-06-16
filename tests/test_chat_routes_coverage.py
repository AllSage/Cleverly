import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class Expr:
    def __init__(self, name, value=None):
        self.name = name
        self.value = value


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return Expr(self.name, other)

    def ilike(self, pattern):
        return Expr(self.name, pattern)

    def in_(self, values):
        return Expr(self.name, values)

    def desc(self):
        return self

    def contains(self, value):
        return Expr(self.name, value)


class RequestLike:
    def __init__(self, body=None, user="alice", headers=None):
        self._body = body or {}
        self.headers = headers or {"content-type": "application/json"}
        self.state = SimpleNamespace(current_user=user)
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def form(self):
        return {}


class ChatMessage:
    def __init__(self, role, content, metadata=None):
        self.role = role
        self.content = content
        self.metadata = metadata

    def get(self, key, default=None):
        return getattr(self, key, default)


class SessionObj:
    def __init__(self, session_id="s1"):
        self.id = session_id
        self.name = "Chat"
        self.endpoint_url = "http://local/v1/chat/completions"
        self.model = "model"
        self.headers = {"H": "1"}
        self.history = []

    def add_message(self, msg):
        self.history.append(msg)


class SessionManager:
    def __init__(self):
        self.sessions = {"s1": SessionObj("s1")}
        self.saved = 0

    def get_session(self, session_id):
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id]

    def save_sessions(self):
        self.saved += 1


async def _collect_streaming(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


def test_chat_helpers_clear_orphaned_endpoint_and_active_stream(monkeypatch):
    import routes.chat_routes as chat_routes

    chat_routes._active_streams.clear()
    chat_routes._stream_set("missing", status="done")
    chat_routes._active_streams["s1"] = {"status": "streaming"}
    chat_routes._stream_set("s1", partial="hi")
    assert chat_routes._active_streams["s1"]["partial"] == "hi"

    assert chat_routes._session_url_matches_endpoint("http://host/v1/chat/completions", "http://host/v1") is True
    assert chat_routes._session_url_matches_endpoint("http://other/v1", "http://host/v1") is False
    assert chat_routes._session_url_matches_endpoint("", "http://host/v1") is False

    class FakeEndpoint:
        is_enabled = Column("is_enabled")

        def __init__(self, base_url):
            self.base_url = base_url

    class FakeDBSession:
        id = Column("id")

        def __init__(self):
            self.endpoint_url = "old"
            self.model = "old"
            self.updated_at = None

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model

        def filter(self, *_args):
            return self

        def all(self):
            return self.db.endpoints if self.model is FakeEndpoint else []

        def first(self):
            return self.db.db_session if self.model is FakeDBSession else None

    class DB:
        def __init__(self, endpoints):
            self.endpoints = endpoints
            self.db_session = FakeDBSession()
            self.commits = 0
            self.rollbacks = 0
            self.closed = 0

        def query(self, model):
            return Query(self, model)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    monkeypatch.setattr(chat_routes, "ModelEndpoint", FakeEndpoint)
    monkeypatch.setattr(chat_routes, "DBSession", FakeDBSession)

    session = SessionObj("s1")
    matching_db = DB([FakeEndpoint("http://local/v1")])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: matching_db)
    assert chat_routes._clear_orphaned_session_endpoint(session) is False
    assert session.model == "model"

    orphan_db = DB([])
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: orphan_db)
    assert chat_routes._clear_orphaned_session_endpoint(session) is True
    assert session.endpoint_url == ""
    assert orphan_db.commits == 1


def test_chat_endpoint_memory_llm_research_and_errors(monkeypatch):
    import core.database as core_database
    import routes.chat_routes as chat_routes

    manager = SessionManager()
    post_tasks = []
    touched = []
    monkeypatch.setattr(chat_routes, "ChatMessage", ChatMessage)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda sess: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda request, sess: None)
    monkeypatch.setattr(core_database, "update_session_last_accessed", lambda session_id: touched.append(session_id))
    monkeypatch.setattr(chat_routes, "clean_thinking_for_save", lambda text, md: (text.replace("<think>x</think>", ""), md))
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: post_tasks.append((args, kwargs)))
    monkeypatch.setattr(chat_routes, "_resolve_research_endpoint", lambda sess: ("http://research", "research-model", {"R": "1"}))

    async def fake_build_context(*_args, **_kwargs):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "hello"}],
            preface=[{"role": "system", "content": "sys"}],
            preset=SimpleNamespace(temperature=0.2, max_tokens=42, character_name="Ada"),
            uprefs={"theme": "dark"},
            user="alice",
            web_sources=[],
        )

    llm_calls = []

    async def fake_llm(endpoint_url, model, messages, headers=None, temperature=None, max_tokens=None, prompt_type=None):
        llm_calls.append((endpoint_url, model, messages, headers, temperature, max_tokens, prompt_type))
        return "<think>x</think>answer"

    class ChatHandler:
        async def handle_memory_command(self, sess, message):
            return "memory saved" if message == "remember this" else None

    class ResearchHandler:
        async def call_research_service(self, message, endpoint, model, llm_headers=None):
            assert endpoint == "http://research"
            return "research context"

    monkeypatch.setattr(chat_routes, "build_chat_context", fake_build_context)
    monkeypatch.setattr(chat_routes, "llm_call_async", fake_llm)

    router = chat_routes.setup_chat_routes(manager, ChatHandler(), object(), object(), ResearchHandler(), object())
    request = RequestLike()
    memory = asyncio.run(
        _endpoint(router, "/api/chat", "POST")(
            request,
            chat_routes.ChatRequest(message="remember this", session="s1"),
        )
    )
    assert memory == {"response": "memory saved"}

    reply = asyncio.run(
        _endpoint(router, "/api/chat", "POST")(
            request,
            chat_routes.ChatRequest(message="hello", session="s1", use_research=True, preset_id="p1"),
        )
    )
    assert reply == {"response": "<think>x</think>answer"}
    assert manager.sessions["s1"].history[-1].content == "answer"
    assert touched == ["s1"]
    assert post_tasks
    assert llm_calls[-1][2][1]["metadata"]["trusted"] is False

    with pytest.raises(HTTPException) as missing:
        asyncio.run(_endpoint(router, "/api/chat", "POST")(request, chat_routes.ChatRequest(message="x", session="missing")))
    assert missing.value.status_code == 404

    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda sess: True)
    with pytest.raises(HTTPException) as orphaned:
        asyncio.run(_endpoint(router, "/api/chat", "POST")(request, chat_routes.ChatRequest(message="x", session="s1")))
    assert orphaned.value.status_code == 400


def test_chat_auxiliary_routes_search_inject_status_stop_and_rewrite(monkeypatch):
    import routes.chat_routes as chat_routes

    manager = SessionManager()
    manager.sessions["s1"].history = [ChatMessage("user", "hi"), ChatMessage("assistant", "old response")]
    monkeypatch.setattr(chat_routes, "ChatMessage", ChatMessage)
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(chat_routes, "get_current_user", lambda request: request.state.current_user)

    active = {"s1": True}
    chunks_by_session = {"s1": ["data: one\n\n", "data: [DONE]\n\n"]}
    monkeypatch.setattr(chat_routes.agent_runs, "is_active", lambda session_id: bool(active.get(session_id)))
    monkeypatch.setattr(chat_routes.agent_runs, "stop", lambda session_id: active.pop(session_id, None) is not None)

    async def subscribe(session_id):
        for chunk in chunks_by_session.get(session_id, []):
            yield chunk

    monkeypatch.setattr(chat_routes.agent_runs, "subscribe", subscribe)

    class FakeDBChatMessage:
        session_id = Column("session_id")
        content = Column("content")
        role = Column("role")
        timestamp = Column("timestamp")
        created_at = Column("created_at")

        def __init__(self):
            self.session_id = "s1"
            self.content = "This is a searchable response"
            self.role = "assistant"
            self.timestamp = dt.datetime(2026, 1, 1)

    class FakeDBSession:
        id = Column("id")
        name = Column("name")
        archived = Column("archived")
        owner = Column("owner")

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model
            self.limited = None

        def join(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def limit(self, limit):
            self.limited = limit
            return self

        def all(self):
            return [(self.db.search_msg, "Session Name")]

        def first(self):
            return self.db.rewrite_msg

    class DB:
        def __init__(self):
            self.search_msg = FakeDBChatMessage()
            self.rewrite_msg = SimpleNamespace(content="old db")
            self.commits = 0
            self.rollbacks = 0
            self.closed = 0

        def query(self, model, *extra):
            return Query(self, model)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    db = DB()
    monkeypatch.setattr(chat_routes, "DBChatMessage", FakeDBChatMessage)
    monkeypatch.setattr(chat_routes, "DBSession", FakeDBSession)
    monkeypatch.setattr(chat_routes, "SessionLocal", lambda: db)

    async def fake_stream_llm(*_args, **_kwargs):
        yield 'data: {"delta": "new "}\n\n'
        yield 'data: {"delta": "answer"}\n\n'
        yield "data: [DONE]\n\n"

    research_utils = SimpleNamespace(strip_thinking=lambda text: text)
    monkeypatch.setitem(__import__("sys").modules, "src.research_utils", research_utils)
    monkeypatch.setattr(chat_routes, "stream_llm", fake_stream_llm)

    router = chat_routes.setup_chat_routes(manager, object(), object(), object(), object(), object())
    request = RequestLike()

    resumed = asyncio.run(_collect_streaming(asyncio.run(_endpoint(router, "/api/chat/resume/{session_id}")(request, "s1"))))
    assert "data: one" in resumed
    with pytest.raises(HTTPException) as no_resume:
        asyncio.run(_endpoint(router, "/api/chat/resume/{session_id}")(request, "missing"))
    assert no_resume.value.status_code == 404

    chat_routes._active_streams["s1"] = {"status": "streaming", "partial": "x"}
    assert asyncio.run(_endpoint(router, "/api/chat/stream_status/{session_id}")(request, "s1"))["partial"] == "x"
    chat_routes._active_streams.clear()
    active["s1"] = True
    assert asyncio.run(_endpoint(router, "/api/chat/stream_status/{session_id}")(request, "s1")) == {
        "status": "streaming",
        "detached": True,
    }
    active.clear()
    with pytest.raises(HTTPException) as no_status:
        asyncio.run(_endpoint(router, "/api/chat/stream_status/{session_id}")(request, "s1"))
    assert no_status.value.status_code == 404

    active["s1"] = True
    assert asyncio.run(_endpoint(router, "/api/chat/stop/{session_id}", "POST")(request, "s1")) == {"stopped": True}
    assert asyncio.run(_endpoint(router, "/api/chat/stop/{session_id}", "POST")(request, "s1")) == {"stopped": False}

    assert asyncio.run(_endpoint(router, "/api/inject_context/{session_id}", "POST")(request, "s1", context="quoted")) == {
        "status": "context_injected"
    }
    assert manager.sessions["s1"].history[-1].metadata["trusted"] is False
    with pytest.raises(HTTPException) as inject_missing:
        asyncio.run(_endpoint(router, "/api/inject_context/{session_id}", "POST")(request, "missing", context="x"))
    assert inject_missing.value.status_code == 404

    assert asyncio.run(_endpoint(router, "/api/search")(request, q="", limit=20)) == []
    searched = asyncio.run(_endpoint(router, "/api/search")(request, q="searchable", limit=5))
    assert searched[0]["content_snippet"] == "This is a searchable response"
    assert searched[0]["timestamp"] == "2026-01-01T00:00:00"

    with pytest.raises(HTTPException) as invalid_json:
        asyncio.run(_endpoint(router, "/api/rewrite", "POST")(RequestLike(body=RuntimeError("bad"))))
    assert invalid_json.value.status_code == 400
    with pytest.raises(HTTPException) as missing_fields:
        asyncio.run(_endpoint(router, "/api/rewrite", "POST")(RequestLike(body={"session_id": "s1"})))
    assert missing_fields.value.status_code == 400
    with pytest.raises(HTTPException) as missing_session:
        asyncio.run(
            _endpoint(router, "/api/rewrite", "POST")(
                RequestLike(body={"session_id": "missing", "original_text": "old", "instruction": "shorter"})
            )
        )
    assert missing_session.value.status_code == 404

    rewrite = asyncio.run(
        _endpoint(router, "/api/rewrite", "POST")(
            RequestLike(body={"session_id": "s1", "original_text": "old", "instruction": "better"})
        )
    )
    streamed = asyncio.run(_collect_streaming(rewrite))
    assert "new " in streamed
    assert manager.sessions["s1"].history[-2].content == "new answer"
    assert db.rewrite_msg.content == "new answer"
    assert manager.saved >= 2
