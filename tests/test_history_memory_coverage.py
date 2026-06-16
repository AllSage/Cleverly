import asyncio
import datetime as dt
import importlib
import json
import sys
import types
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
    def __init__(self, name, value):
        self.name = name
        self.value = value


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return Expr(self.name, other)

    def desc(self):
        return self

    def asc(self):
        return self


class RequestLike:
    def __init__(self, user="alice", body=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def form(self):
        return self._body


class ChatMessage:
    def __init__(self, role, content, metadata=None):
        self.role = role
        self.content = content
        self.metadata = metadata

    def to_dict(self):
        result = {"role": self.role, "content": self.content}
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def get(self, key, default=None):
        return getattr(self, key, default)


class SessionObj:
    def __init__(self, session_id="s1", history=None):
        self.id = session_id
        self.name = "Chat"
        self.model = "model"
        self.endpoint_url = "http://local"
        self.headers = {"H": "1"}
        self.owner = "alice"
        self.history = list(history or [])
        self.message_count = len(self.history)

    def add_message(self, msg):
        self.history.append(msg)
        self.message_count = len(self.history)

    def get_context_messages(self):
        return [m.to_dict() if isinstance(m, ChatMessage) else m for m in self.history]


def test_history_routes_message_lifecycle_fork_topics_and_compact(monkeypatch):
    import routes.history_routes as history_routes

    class DbChatMessage:
        id = Column("msg_id")
        session_id = Column("session_id")
        role = Column("role")
        timestamp = Column("timestamp")
        created_at = Column("created_at")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.timestamp = kwargs.get("timestamp", dt.datetime(2026, 1, 1))
            self.created_at = kwargs.get("created_at", self.timestamp)
            self.meta_data = kwargs.get("meta_data", "")

    class DbSession:
        id = Column("session_row_id")

        def __init__(self, session_id="s1"):
            self.id = session_id
            self.message_count = 0
            self.updated_at = None

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model
            self.filters = []

        def filter(self, *conditions):
            self.filters.extend(c for c in conditions if isinstance(c, Expr))
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            if self.model is DbChatMessage:
                return list(self.db.messages)
            return []

        def first(self):
            if self.model is DbSession:
                return self.db.db_session
            if self.model is DbChatMessage:
                rows = list(self.db.messages)
                for condition in self.filters:
                    if condition.name == "msg_id":
                        rows = [m for m in rows if m.id == condition.value]
                    if condition.name == "session_id":
                        rows = [m for m in rows if m.session_id == condition.value]
                    if condition.name == "role":
                        rows = [m for m in rows if m.role == condition.value]
                return rows[-1] if rows else None
            return None

    class DB:
        def __init__(self):
            self.messages = [
                DbChatMessage(id="m1", session_id="s1", role="user", content="hi", meta_data="{"),
                DbChatMessage(id="m2", session_id="s1", role="assistant", content="hello", meta_data=json.dumps({"_db_id": "m2"})),
                DbChatMessage(id="m3", session_id="s1", role="assistant", content="there", meta_data=""),
            ]
            self.db_session = DbSession("s1")
            self.deleted = []
            self.added = []
            self.commits = 0
            self.closed = 0

        def query(self, model):
            return Query(self, model)

        def delete(self, row):
            self.deleted.append(row)
            if row in self.messages:
                self.messages.remove(row)

        def add(self, row):
            self.added.append(row)
            self.messages.append(row)

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed += 1

    class Manager:
        def __init__(self):
            self.sessions = {
                "s1": SessionObj(
                    "s1",
                    [
                        ChatMessage("user", "visible"),
                        ChatMessage("assistant", "hidden", metadata={"hidden": True}),
                        {"role": "assistant", "content": "dict", "metadata": {"_db_id": "m2"}},
                        {"role": "assistant", "content": "hidden dict", "metadata": {"hidden": True}},
                    ],
                )
            }
            self.saved = 0

        def get_session(self, session_id):
            if session_id not in self.sessions:
                raise KeyError(session_id)
            return self.sessions[session_id]

        def add_message(self, session_id, msg):
            self.get_session(session_id).add_message(msg)

        def truncate_messages(self, session_id, keep_count):
            session = self.get_session(session_id)
            removed = max(0, len(session.history) - keep_count)
            session.history = session.history[-keep_count:] if keep_count else []
            return removed

        def save_sessions(self):
            self.saved += 1

        def create_session(self, **kwargs):
            session = SessionObj(kwargs["session_id"])
            session.name = kwargs["name"]
            session.endpoint_url = kwargs["endpoint_url"]
            session.model = kwargs["model"]
            session.owner = kwargs.get("owner")
            self.sessions[session.id] = session
            return session

    db = DB()
    manager = Manager()
    monkeypatch.setattr(history_routes, "DbChatMessage", DbChatMessage)
    monkeypatch.setattr(history_routes, "DbSession", DbSession)
    monkeypatch.setattr(history_routes, "ChatMessage", ChatMessage)
    monkeypatch.setattr(history_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(history_routes.uuid, "uuid4", lambda: "forked")
    monkeypatch.setattr(history_routes, "analyze_topics", lambda manager, owner=None: {"owner": owner, "topics": ["a"]})

    auth_helpers = importlib.import_module("src.auth_helpers")
    monkeypatch.setattr(auth_helpers, "get_current_user", lambda request: request.state.current_user)
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    router = history_routes.setup_history_routes(manager)
    request = RequestLike("alice")

    history = asyncio.run(_endpoint(router, "/api/history/{session_id}")(request, "s1"))
    assert history["history"] == [
        {"role": "user", "content": "visible"},
        {"role": "assistant", "content": "dict", "metadata": {"_db_id": "m2"}},
    ]
    with pytest.raises(HTTPException) as missing_history:
        asyncio.run(_endpoint(router, "/api/history/{session_id}")(request, "missing"))
    assert missing_history.value.status_code == 404

    manager.sessions["empty"] = SessionObj("empty", [])
    fallback = asyncio.run(_endpoint(router, "/api/history/{session_id}")(request, "empty"))
    assert fallback["history"][0]["metadata"]["timestamp"] == "2026-01-01T00:00:00Z"
    assert manager.sessions["empty"].history[0].role == "user"

    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/truncate", "POST")(RequestLike(body={"keep_count": 1}), "s1"))[
        "truncated"
    ] == 3
    with pytest.raises(HTTPException):
        asyncio.run(_endpoint(router, "/api/session/{session_id}/truncate", "POST")(RequestLike(body={"keep_count": 1}), "missing"))

    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/message", "POST")(RequestLike(body={"role": "assistant", "content": "saved"}), "s1")) == {
        "status": "ok"
    }
    with pytest.raises(HTTPException) as empty_message:
        asyncio.run(_endpoint(router, "/api/session/{session_id}/message", "POST")(RequestLike(body={"content": ""}), "s1"))
    assert empty_message.value.status_code == 400

    manager.sessions["s1"].history = [
        ChatMessage("user", "a", metadata={"_db_id": "m1"}),
        {"role": "assistant", "content": "b", "metadata": {"_db_id": "m2"}},
        ChatMessage("assistant", "c", metadata={"_db_id": "m3"}),
    ]
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/delete-messages", "POST")(RequestLike(body={"msg_ids": ["m2"]}), "s1")) == {
        "status": "ok",
        "deleted": 1,
    }
    assert all((m.metadata if isinstance(m, ChatMessage) else m.get("metadata")).get("_db_id") != "m2" for m in manager.sessions["s1"].history)
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/delete-messages", "POST")(RequestLike(body={"indices": [0]}), "s1"))[
        "deleted"
    ] >= 0
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/delete-messages", "POST")(RequestLike(body={}), "s1")) == {
        "status": "ok",
        "deleted": 0,
    }

    db.messages.append(DbChatMessage(id="edit", session_id="s1", role="assistant", content="old", meta_data="{}"))
    manager.sessions["s1"].history = [ChatMessage("assistant", "old", metadata={"_db_id": "edit"})]
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/edit-message", "POST")(RequestLike(body={"msg_id": "edit", "content": "new"}), "s1")) == {
        "status": "ok"
    }
    assert manager.sessions["s1"].history[0].metadata["edited"] is True
    with pytest.raises(HTTPException) as edit_bad:
        asyncio.run(_endpoint(router, "/api/session/{session_id}/edit-message", "POST")(RequestLike(body={"msg_id": "", "content": None}), "s1"))
    assert edit_bad.value.status_code == 400

    manager.sessions["s1"].history = [ChatMessage("user", "u"), {"role": "assistant", "content": "a"}]
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/mark-stopped", "POST")(request, "s1")) == {"status": "ok"}
    assert manager.sessions["s1"].history[-1]["metadata"]["stopped"] is True
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/update-last-meta", "POST")(RequestLike(body={"metadata": {"variant": 1}}), "s1")) == {
        "status": "ok"
    }
    assert manager.sessions["s1"].history[-1]["metadata"]["variant"] == 1

    manager.sessions["s1"].history = [
        ChatMessage("assistant", "one", metadata={"a": 1}),
        ChatMessage("user", "previous response was interrupted"),
        ChatMessage("assistant", "two", metadata={"stopped": True, "b": 2}),
    ]
    merged = asyncio.run(_endpoint(router, "/api/session/{session_id}/merge-last-assistant", "POST")(RequestLike(body={"separator": " "}), "s1"))
    assert merged == {"status": "ok", "merged": True}
    assert manager.sessions["s1"].history[0].content == "one two"
    assert "stopped" not in manager.sessions["s1"].history[0].metadata
    assert asyncio.run(_endpoint(router, "/api/session/{session_id}/merge-last-assistant", "POST")(RequestLike(body={}), "s1")) == {
        "status": "ok",
        "merged": False,
    }

    manager.sessions["s1"].history = [ChatMessage("user", "one"), ChatMessage("assistant", "two")]
    forked = asyncio.run(_endpoint(router, "/api/session/{session_id}/fork", "POST")(RequestLike(body={"keep_count": 2}), "s1"))
    assert forked["id"] == "forked"
    assert manager.sessions["forked"].history[1].content == "two"
    with pytest.raises(HTTPException):
        asyncio.run(_endpoint(router, "/api/session/{session_id}/fork", "POST")(RequestLike(body={"keep_count": 1}), "missing"))

    assert asyncio.run(_endpoint(router, "/api/conversations/topics")(request)) == {"owner": "alice", "topics": ["a"]}
    monkeypatch.setattr(history_routes, "analyze_topics", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("topics bad")))
    with pytest.raises(HTTPException) as topics_bad:
        asyncio.run(_endpoint(router, "/api/conversations/topics")(request))
    assert topics_bad.value.status_code == 500

    manager.sessions["s1"].history = [ChatMessage("user", "short")]
    assert "Not enough" in asyncio.run(_endpoint(router, "/api/session/{session_id}/compact", "POST")(request, "s1"))["message"]
    with pytest.raises(HTTPException) as compact_missing:
        asyncio.run(_endpoint(router, "/api/session/{session_id}/compact", "POST")(request, "missing"))
    assert compact_missing.value.status_code == 404


def test_memory_routes_crud_search_extract_import_and_audit(monkeypatch):
    import routes.memory_routes as memory_routes

    assert memory_routes._strip_list_prefix("12) Keep this") == "Keep this"
    assert memory_routes._strip_list_prefix("- bullet") == "bullet"

    class MemoryManager:
        def __init__(self):
            self.memories = [
                {"id": "m1", "text": "Alice likes blue", "category": "fact", "categories": ["fact"], "owner": "alice", "session_id": "s1", "timestamp": 100},
                {"id": "m2", "text": "Bob likes red", "category": "preference", "owner": "bob", "timestamp": 999999999999},
            ]
            self.saved = []

        def load(self, owner=None):
            if owner is None:
                return list(self.memories)
            return [m for m in self.memories if m.get("owner") == owner]

        def load_all(self):
            return list(self.memories)

        def save(self, memories):
            self.saved.append(list(memories))
            self.memories = list(memories)

        def find_duplicates(self, text, memories):
            return [m for m in memories if m["text"].lower() == text.lower()]

        def add_entry(self, text, source, category, owner=None):
            return {"id": f"new-{len(self.memories)}", "text": text, "source": source, "category": category, "owner": owner, "timestamp": 123}

        def get_relevant_memories(self, query, memories, threshold=0.0, max_items=20):
            return [m for m in memories if query.lower() in m.get("text", "").lower()][:max_items]

        def extract_memory_from_chat(self, history, session):
            return [{"text": "fallback memory"}]

    class SessionManager:
        def __init__(self):
            self.sessions = {"s1": SessionObj("s1", [ChatMessage("user", "Alice likes blue")])}

        def get_session(self, session_id):
            if session_id not in self.sessions:
                raise KeyError(session_id)
            return self.sessions[session_id]

    class Vector:
        healthy = True

        def __init__(self):
            self.added = []
            self.removed = []

        def add(self, memory_id, text):
            self.added.append((memory_id, text))

        def remove(self, memory_id):
            self.removed.append(memory_id)

    memory_manager = MemoryManager()
    session_manager = SessionManager()
    vector = Vector()
    monkeypatch.setattr(memory_routes, "get_current_user", lambda request: request.state.current_user)
    auth_helpers = importlib.import_module("src.auth_helpers")
    monkeypatch.setattr(auth_helpers, "require_privilege", lambda request, privilege: request.state.current_user)
    fire_events = []
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda *args: fire_events.append(args)
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    async def fake_llm(*args, **kwargs):
        return json.dumps([{"text": "Alice lives in Dallas"}, "Alice likes tea"])

    monkeypatch.setattr(memory_routes, "llm_call_async", fake_llm)
    monkeypatch.setattr(memory_routes, "resolve_endpoint", lambda *args, **kwargs: ("http://local", "model", {"H": "1"}))

    router = memory_routes.setup_memory_routes(memory_manager, session_manager, vector)
    request = RequestLike("alice")

    debug = _endpoint(router, "/api/memory/debug", "POST")(request, query="blue")
    assert debug["relevant_count"] == 1
    assert _endpoint(router, "/api/memory")(request)["memory"][0]["id"] == "m1"
    assert _endpoint(router, "/api/memory/search", "POST")(request, query="blue", session_id="s1", category="fact")["total"] == 1

    duplicate = asyncio.run(_endpoint(router, "/api/memory/add", "POST")(request, memory_routes.MemoryAddRequest(text="Alice likes blue", category="fact")))
    assert duplicate["message"] == "Memory already exists"
    added = asyncio.run(
        _endpoint(router, "/api/memory/add", "POST")(
            request,
            memory_routes.MemoryAddRequest(text="Alice has a cat", category="fact", source="user", session_id="s1"),
        )
    )
    assert added["ok"] is True
    assert vector.added[-1][1] == "Alice has a cat"
    assert fire_events[-1] == ("memory_added", "alice")
    with pytest.raises(HTTPException) as empty:
        asyncio.run(_endpoint(router, "/api/memory/add", "POST")(request, memory_routes.MemoryAddRequest(text=" ")))
    assert empty.value.status_code == 400

    timeline = _endpoint(router, "/api/memory/timeline")(request)
    assert timeline["timeline"][0]["session_name"] == "Chat"
    assert "timestamp_str" in timeline["timeline"][0]
    by_session = _endpoint(router, "/api/memory/by-session/{session_id}")(request, "s1")
    assert by_session["memory_count"] >= 1
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/memory/by-session/{session_id}")(request, "missing")

    extracted = asyncio.run(_endpoint(router, "/api/memory/extract", "POST")(request, session="s1"))
    assert extracted["suggestions"] == ["Alice lives in Dallas", "Alice likes tea"]
    monkeypatch.setattr(memory_routes, "llm_call_async", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("llm down")))
    assert asyncio.run(_endpoint(router, "/api/memory/extract", "POST")(request, session="s1")) == {"suggestions": ["fallback memory"]}
    with pytest.raises(HTTPException) as extract_unauth:
        asyncio.run(_endpoint(router, "/api/memory/extract", "POST")(RequestLike(None), session="s1"))
    assert extract_unauth.value.status_code == 401

    model_routes = types.ModuleType("routes.model_routes")
    model_routes._load_settings = lambda: {}
    model_routes._normalize_base = lambda url: url.rstrip("/")
    model_routes.build_chat_url = lambda base: base + "/chat/completions"
    monkeypatch.setitem(sys.modules, "routes.model_routes", model_routes)
    with pytest.raises(HTTPException) as no_model:
        asyncio.run(_endpoint(router, "/api/memory/audit", "POST")(request, session=None))
    assert no_model.value.status_code == 400

    async def fake_audit(*args, **kwargs):
        return {"before": 3, "after": 2, "already_tidy": True}

    monkeypatch.setattr(memory_routes, "audit_memories", fake_audit)
    audited = asyncio.run(_endpoint(router, "/api/memory/audit", "POST")(request, session="s1"))
    assert audited["removed"] == 1
    assert audited["already_tidy"] is True

    class Upload:
        def __init__(self, filename, data):
            self.filename = filename
            self.data = data

        async def read(self):
            return self.data

    direct = asyncio.run(_endpoint(router, "/api/memory/import", "POST")(request, session=None, file=Upload("memories.json", b'[{"text":"1. Direct","category":"fact"}]')))
    assert direct["suggestions"] == [{"text": "Direct", "category": "fact"}]
    empty_import = asyncio.run(_endpoint(router, "/api/memory/import", "POST")(request, session=None, file=Upload("empty.txt", b"   ")))
    assert empty_import["suggestions"] == []
    with pytest.raises(HTTPException) as bad_ext:
        asyncio.run(_endpoint(router, "/api/memory/import", "POST")(request, session=None, file=Upload("x.exe", b"x")))
    assert bad_ext.value.status_code == 400

    monkeypatch.setattr(memory_routes, "llm_call_async", lambda *args, **kwargs: asyncio.sleep(0, result="```json\n[{\"text\":\"2) Imported\",\"category\":\"goal\"}]\n```"))
    imported = asyncio.run(_endpoint(router, "/api/memory/import", "POST")(request, session=None, file=Upload("doc.txt", b"content")))
    assert imported["suggestions"] == [{"text": "Imported", "category": "goal"}]

    assert _endpoint(router, "/api/memory/{memory_id}/pin", "POST")(request, "m1", pinned=True) == {"ok": True, "pinned": True}
    assert _endpoint(router, "/api/memory/{memory_id}")(request, "m1")["memory"]["id"] == "m1"
    updated = _endpoint(router, "/api/memory/{memory_id}", "PUT")(request, "m1", text=" Updated ", category="preference")
    assert updated["ok"] is True
    assert vector.removed[-1] == "m1"
    assert _endpoint(router, "/api/memory/{memory_id}", "DELETE")(request, "m1")["ok"] is True
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/memory/{memory_id}")(request, "m1")
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/memory/{memory_id}/pin", "POST")(request, "missing", pinned=True)
