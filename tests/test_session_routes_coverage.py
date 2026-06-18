import importlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    def __init__(self, field=None, value=None, op="eq"):
        self.field = field
        self.value = value
        self.op = op

    def __or__(self, other):
        return Expr(None, (self, other), "or")

    def __and__(self, other):
        return Expr(None, (self, other), "and")


class Sort:
    def __init__(self, field, reverse=False):
        self.field = field
        self.reverse = reverse

    def nulls_last(self):
        return self


class CountExpr:
    def __init__(self, field):
        self.field = field


class Column:
    def __init__(self, name, owner):
        self.name = name
        self.owner = owner

    def __eq__(self, other):
        return Expr(self.name, other)

    def __ne__(self, other):
        return Expr(self.name, other, "ne")

    def __lt__(self, other):
        return Expr(self.name, other, "lt")

    def in_(self, values):
        return Expr(self.name, set(values), "in")

    def ilike(self, value, **_kwargs):
        return Expr(self.name, value, "ilike")

    def isnot(self, value):
        return Expr(self.name, value, "isnot")

    def asc(self):
        return Sort(self.name)

    def desc(self):
        return Sort(self.name, reverse=True)


class Row(SimpleNamespace):
    def __init__(self, fields, values):
        super().__init__(**dict(zip(fields, values)))
        self._fields = list(fields)
        self._values = list(values)

    def __getitem__(self, index):
        return self._values[index]


class FakeDbSession:
    id = Column("id", "session")
    name = Column("name", "session")
    folder = Column("folder", "session")
    total_input_tokens = Column("total_input_tokens", "session")
    total_output_tokens = Column("total_output_tokens", "session")
    is_important = Column("is_important", "session")
    created_at = Column("created_at", "session")
    updated_at = Column("updated_at", "session")
    last_message_at = Column("last_message_at", "session")
    mode = Column("mode", "session")
    message_count = Column("message_count", "session")
    archived = Column("archived", "session")
    owner = Column("owner", "session")
    model = Column("model", "session")
    endpoint_url = Column("endpoint_url", "session")

    def __init__(self, **kwargs):
        now = kwargs.get("updated_at", datetime.utcnow())
        self.id = kwargs.get("id", "s1")
        self.name = kwargs.get("name", "Main")
        self.folder = kwargs.get("folder")
        self.total_input_tokens = kwargs.get("total_input_tokens", 1)
        self.total_output_tokens = kwargs.get("total_output_tokens", 2)
        self.is_important = kwargs.get("is_important", False)
        self.created_at = kwargs.get("created_at", now)
        self.updated_at = now
        self.last_message_at = kwargs.get("last_message_at", now)
        self.mode = kwargs.get("mode", "chat")
        self.message_count = kwargs.get("message_count", 0)
        self.archived = kwargs.get("archived", False)
        self.owner = kwargs.get("owner", "alice")
        self.model = kwargs.get("model", "GLM-5.2")
        self.endpoint_url = kwargs.get("endpoint_url", "http://local/v1")


class FakeDocument:
    session_id = Column("session_id", "document")
    is_active = Column("is_active", "document")
    current_content = Column("current_content", "document")

    def __init__(self, session_id="s1", current_content="body", is_active=True):
        self.session_id = session_id
        self.current_content = current_content
        self.is_active = is_active


class FakeGalleryImage:
    session_id = Column("session_id", "image")

    def __init__(self, session_id="s1"):
        self.session_id = session_id


class FakeDbMessage:
    id = Column("id", "message")
    session_id = Column("session_id", "message")
    role = Column("role", "message")
    content = Column("content", "message")
    timestamp = Column("timestamp", "message")

    def __init__(self, session_id="s1", role="user", content="hello", timestamp=None):
        self.id = f"{session_id}-{role}-{content}"
        self.session_id = session_id
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.utcnow()


class FakeModelEndpoint:
    id = Column("id", "endpoint")

    def __init__(self, id="ep1", api_key="secret", base_url="http://local/v1"):
        self.id = id
        self.api_key = api_key
        self.base_url = base_url


class SimpleChatMessage:
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


class FakeQuery:
    def __init__(self, db, source, items=None, *, columns=None, aggregate_count=False):
        self.db = db
        self.source = source
        self.items = list(source if items is None else items)
        self.columns = columns or []
        self.aggregate_count = aggregate_count

    def _clone(self, items=None):
        return FakeQuery(
            self.db,
            self.source,
            self.items if items is None else items,
            columns=self.columns,
            aggregate_count=self.aggregate_count,
        )

    def _value(self, item, field):
        return getattr(item, field, None)

    def _matches(self, item, condition):
        if condition is True or condition is None:
            return True
        if condition is False:
            return False
        if not isinstance(condition, Expr):
            return True
        if condition.op == "or":
            return any(self._matches(item, sub) for sub in condition.value)
        if condition.op == "and":
            return all(self._matches(item, sub) for sub in condition.value)
        value = self._value(item, condition.field)
        if condition.op == "eq":
            return value == condition.value
        if condition.op == "ne":
            return value != condition.value
        if condition.op == "lt":
            return value < condition.value
        if condition.op == "in":
            return value in condition.value
        if condition.op == "ilike":
            needle = str(condition.value).strip("%").lower()
            return needle in str(value or "").lower()
        if condition.op == "isnot":
            return value is not condition.value
        return True

    def filter(self, *conditions):
        items = list(self.items)
        for condition in conditions:
            items = [item for item in items if self._matches(item, condition)]
        return self._clone(items)

    def group_by(self, *_args, **_kwargs):
        return self

    def distinct(self):
        if not self.columns:
            return self
        seen = set()
        unique = []
        field = self.columns[0]
        for item in self.items:
            value = self._value(item, field)
            if value not in seen:
                seen.add(value)
                unique.append(item)
        return self._clone(unique)

    def order_by(self, *sorts):
        items = list(self.items)
        for sort in reversed([s for s in sorts if isinstance(s, Sort)]):
            items.sort(key=lambda item: self._value(item, sort.field) or "", reverse=sort.reverse)
        return self._clone(items)

    def offset(self, offset):
        return self._clone(self.items[offset:])

    def limit(self, limit):
        return self._clone(self.items[:limit])

    def update(self, values, synchronize_session=False):
        for item in self.items:
            for key, value in values.items():
                setattr(item, key, value)
        return len(self.items)

    def delete(self):
        count = 0
        for item in list(self.items):
            if item in self.source:
                self.source.remove(item)
                count += 1
        return count

    def _aggregate_rows(self):
        rows = {}
        for msg in self.items:
            rows[msg.session_id] = rows.get(msg.session_id, 0) + 1
        return list(rows.items())

    def all(self):
        if self.aggregate_count:
            return self._aggregate_rows()
        if self.columns:
            return [
                Row(self.columns, [self._value(item, field) for field in self.columns])
                for item in self.items
            ]
        return self.items

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.items)


class FakeDB:
    def __init__(self):
        old = datetime.utcnow() - timedelta(minutes=30)
        self.sessions = [
            FakeDbSession(id="s1", name="Main", message_count=8),
            FakeDbSession(id="s2", name="Archived", archived=True, message_count=2),
            FakeDbSession(id="star", name="Starred", is_important=True),
            FakeDbSession(id="empty", name="Untitled", message_count=0),
            FakeDbSession(id="throw", name="hi", message_count=1),
            FakeDbSession(id="ghost", name="Nobody", created_at=old, updated_at=old, message_count=0),
            FakeDbSession(id="other", name="Other", owner="bob"),
        ]
        self.docs = [FakeDocument("s1")]
        self.images = [FakeGalleryImage("s1")]
        self.messages = [
            FakeDbMessage("s1", "user", "hello"),
            FakeDbMessage("s1", "assistant", "reply"),
            FakeDbMessage("throw", "user", "hi"),
            FakeDbMessage("ghost", "user", "gone"),
        ]
        self.endpoints = [FakeModelEndpoint()]
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.deleted = []

    def query(self, *models):
        first = models[0]
        if first is FakeDbSession:
            return FakeQuery(self, self.sessions)
        if first is FakeDocument:
            return FakeQuery(self, self.docs)
        if first is FakeGalleryImage:
            return FakeQuery(self, self.images)
        if first is FakeDbMessage:
            return FakeQuery(self, self.messages)
        if first is FakeModelEndpoint:
            return FakeQuery(self, self.endpoints)
        if isinstance(first, Column):
            owner_map = {
                "session": self.sessions,
                "document": self.docs,
                "image": self.images,
                "message": self.messages,
                "endpoint": self.endpoints,
            }
            columns = [m.name for m in models if isinstance(m, Column)]
            aggregate = any(isinstance(m, CountExpr) for m in models)
            return FakeQuery(self, owner_map[first.owner], columns=columns, aggregate_count=aggregate)
        return FakeQuery(self, [])

    def delete(self, obj):
        self.deleted.append(obj)
        for source in (self.sessions, self.docs, self.images, self.messages, self.endpoints):
            if obj in source:
                source.remove(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


@dataclass
class FakeSession:
    id: str
    name: str
    endpoint_url: str = "http://local/v1"
    model: str = "GLM-5.2"
    rag: bool = False
    owner: str = "alice"
    archived: bool = False
    headers: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    is_important: bool = False

    def add_message(self, message):
        self.history.append(message)


class FakeSessionManager:
    def __init__(self, db):
        self.db = db
        self.sessions = {
            "s1": FakeSession(
                "s1",
                "Main",
                history=[
                    SimpleChatMessage("user", "m1"),
                    SimpleChatMessage("assistant", "m2"),
                    SimpleChatMessage("user", "m3"),
                    SimpleChatMessage("assistant", "m4"),
                    SimpleChatMessage("user", "m5"),
                    SimpleChatMessage("assistant", "m6"),
                    SimpleChatMessage("user", "m7"),
                    SimpleChatMessage("assistant", "m8"),
                ],
            ),
            "s2": FakeSession("s2", "Archived", archived=True),
            "star": FakeSession("star", "Starred", is_important=True),
            "empty": FakeSession("empty", "Untitled"),
            "throw": FakeSession("throw", "hi", history=[SimpleChatMessage("user", "hi")]),
            "ghost": FakeSession("ghost", "Nobody"),
        }
        self.saved = 0
        self.deleted = []

    def get_sessions_for_user(self, user):
        return {sid: s for sid, s in self.sessions.items() if s.owner == user}

    def create_session(self, session_id, name, endpoint_url, model, rag, owner):
        session = FakeSession(session_id, name, endpoint_url, model, rag, owner)
        self.sessions[session_id] = session
        self.db.sessions.append(
            FakeDbSession(id=session_id, name=name, endpoint_url=endpoint_url, model=model, owner=owner)
        )
        return session

    def get_session(self, sid):
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]

    def update_session_name(self, sid, name):
        self.sessions[sid].name = name
        row = next((s for s in self.db.sessions if s.id == sid), None)
        if row:
            row.name = name

    def delete_session(self, sid):
        self.deleted.append(sid)
        return self.sessions.pop(sid, None) is not None

    def save_sessions(self):
        self.saved += 1

    def replace_messages(self, sid, messages):
        self.sessions[sid].history = list(messages)
        return True

    def _load_session_from_db(self, sid):
        row = next((s for s in self.db.sessions if s.id == sid), None)
        if row:
            self.sessions[sid] = FakeSession(row.id, row.name, row.endpoint_url, row.model, owner=row.owner)


class RequestLike:
    def __init__(self, user="alice", body=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body if body is not None else {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture()
def session_env(monkeypatch):
    import sqlalchemy
    import core.database as database
    import core.models as models
    import src.endpoint_resolver as endpoint_resolver
    import src.event_bus as event_bus
    import src.llm_core as llm_core
    import src.model_context as model_context
    import routes.session_routes as session_routes

    session_routes = importlib.reload(session_routes)
    db = FakeDB()
    manager = FakeSessionManager(db)
    monkeypatch.setattr(session_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(session_routes, "DbSession", FakeDbSession)
    monkeypatch.setattr(session_routes, "Document", FakeDocument)
    monkeypatch.setattr(session_routes, "GalleryImage", FakeGalleryImage)
    monkeypatch.setattr(session_routes, "ChatMessage", SimpleChatMessage)
    monkeypatch.setattr(session_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(models, "ChatMessage", SimpleChatMessage)
    monkeypatch.setattr(database, "ChatMessage", FakeDbMessage)
    monkeypatch.setattr(database, "ModelEndpoint", FakeModelEndpoint)
    monkeypatch.setattr(event_bus, "fire_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(endpoint_resolver, "build_headers", lambda key, base=None: {"Authorization": f"Bearer {key}"})
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda _kind: ("http://utility/v1", "utility-model", {}))
    async def fake_llm_call_async(*_args, **_kwargs):
        return "short summary"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)
    monkeypatch.setattr(model_context, "get_context_length", lambda *_args, **_kwargs: 8192)
    monkeypatch.setattr(
        sqlalchemy,
        "func",
        SimpleNamespace(
            trim=lambda column: column,
            count=lambda column: CountExpr(getattr(column, "name", "id")),
        ),
    )
    router = session_routes.setup_session_routes(
        manager,
        {"REQUEST_TIMEOUT": 2, "OPENAI_API_KEY": "sk-test", "SESSIONS_FILE": "sessions.json"},
    )
    return SimpleNamespace(router=router, db=db, manager=manager)


@pytest.mark.asyncio
async def test_session_lifecycle_export_archive_and_context(session_env):
    request = RequestLike()

    listed = _endpoint(session_env.router, "/api/sessions", "GET")(request)
    ids = {row["id"] for row in listed}
    assert "s1" in ids
    assert "ghost" not in ids
    assert listed[0]["has_documents"] is True
    assert listed[0]["has_images"] is True

    create = _endpoint(session_env.router, "/api/session", "POST")
    created = create(
        request,
        name="New Chat",
        endpoint_url="",
        model="",
        rag="true",
        skip_validation="true",
        api_key="",
        endpoint_id="",
    )
    assert created.name == "New Chat"
    assert created.rag is True

    rename = _endpoint(session_env.router, "/api/session/{sid}", "PATCH")
    renamed = rename(
        request,
        "s1",
        name="Renamed",
        folder="Work",
        model="GLM-5.2",
        endpoint_url="http://new/v1",
        endpoint_id=None,
    )
    assert renamed["name"] == "Renamed"
    assert renamed["folder"] == "Work"
    assert session_env.manager.sessions["s1"].endpoint_url == "http://new/v1"

    history = _endpoint(session_env.router, "/api/history/{sid}", "GET")(request, "s1")
    assert history["history"][0]["role"] == "user"

    export = _endpoint(session_env.router, "/api/session/{sid}/export", "GET")
    assert "Conversation:" in export(request, "s1", fmt="md", filename="chat.md").body.decode()
    assert "[USER]" in export(request, "s1", fmt="txt", filename="chat.txt").body.decode()
    assert json.loads(export(request, "s1", fmt="json", filename="chat.json").body)["name"] == "Renamed"
    assert "<!DOCTYPE html>" in export(request, "s1", fmt="html", filename="chat.html").body.decode()
    bad_header = export(request, "s1", fmt="md", filename="../bad\r\nX-Injected: yes.md").headers["content-disposition"]
    assert bad_header == 'attachment; filename="bad_X-Injected_yes.md"'
    assert "\r" not in bad_header and "\n" not in bad_header and ".." not in bad_header

    save_now = _endpoint(session_env.router, "/api/sessions/save", "POST")
    assert save_now(request) == {"ok": True, "path": "sessions.json"}

    openai = _endpoint(session_env.router, "/api/session/openai", "POST")
    openai_result = openai(request, name="ignored", model="gpt-test", rag="false")
    assert openai_result["model"] == "gpt-test"

    archive = _endpoint(session_env.router, "/api/session/{sid}/archive", "POST")
    assert archive(request, "s1") == {"status": "archived"}
    assert session_env.manager.sessions["s1"].archived is True

    archived = _endpoint(session_env.router, "/api/sessions/archived", "GET")(
        request, search="ren", offset=0, limit=10, sort="alpha", model="GLM"
    )
    assert archived["total"] == 1

    unarchive = _endpoint(session_env.router, "/api/session/{sid}/unarchive", "POST")
    assert unarchive(request, "s1") == {"status": "unarchived"}
    assert session_env.manager.sessions["s1"].archived is False

    context = await _endpoint(session_env.router, "/api/session/{session_id}/context_info", "GET")(request, "s1")
    assert context == {"context_length": 8192, "model": "GLM-5.2"}


@pytest.mark.asyncio
async def test_session_message_mutation_compaction_and_deletion(session_env, monkeypatch):
    request = RequestLike()

    inject = _endpoint(session_env.router, "/api/session/{sid}/inject_messages", "POST")
    injected = await inject(
        RequestLike(body={"messages": [{"role": "user", "content": "extra", "metadata": {"x": 1}}]}),
        "s1",
    )
    assert injected == {"ok": True, "count": 1}
    assert session_env.manager.saved == 1

    important = _endpoint(session_env.router, "/api/session/{session_id}/important", "POST")
    assert await important(request, "s1", important=True) == {"status": "success", "is_important": True}

    compact = _endpoint(session_env.router, "/api/session/{session_id}/compact", "POST")
    compacted = await compact(request, "s1")
    assert compacted["ok"] is True
    assert session_env.manager.sessions["s1"].history[0].metadata["compacted"] is True
    assert await important(request, "s1", important=False) == {"status": "success", "is_important": False}

    delete = _endpoint(session_env.router, "/api/session/{sid}", "DELETE")
    assert delete(request, "s1") == {"status": "deleted"}
    with pytest.raises(HTTPException) as missing:
        delete(request, "missing")
    assert missing.value.status_code == 404

    beacon = _endpoint(session_env.router, "/api/session/{sid}/delete", "POST")
    assert beacon(request, "s2") == {"status": "deleted"}

    bulk = _endpoint(session_env.router, "/api/sessions/bulk-delete", "POST")
    assert await bulk(RequestLike(body={"ids": ["star", "missing"]})) == {"deleted": 2}

    auto_sort = _endpoint(session_env.router, "/api/sessions/auto-sort", "POST")
    sorted_result = auto_sort(request, skip_llm=True)
    assert sorted_result["skipped_llm"] is True
    assert sorted_result["deleted_empty"] >= 1
    assert "throw" in session_env.manager.deleted

    with pytest.raises(HTTPException) as forbidden:
        _endpoint(session_env.router, "/api/session/{sid}", "DELETE")(RequestLike(user="bob"), "star")
    assert forbidden.value.status_code == 404


@pytest.mark.asyncio
async def test_session_error_paths_and_admin_delete(session_env, monkeypatch):
    import core.middleware as middleware

    request = RequestLike()

    with pytest.raises(HTTPException) as unauthenticated:
        _endpoint(session_env.router, "/api/sessions/save", "POST")(RequestLike(user=None))
    assert unauthenticated.value.status_code == 401

    create = _endpoint(session_env.router, "/api/session", "POST")
    with pytest.raises(HTTPException) as no_endpoint:
        create(
            request,
            name="Bad",
            endpoint_url="",
            model="",
            rag=None,
            skip_validation=None,
            api_key="",
            endpoint_id="",
        )
    assert no_endpoint.value.status_code == 400

    with pytest.raises(HTTPException) as not_owner:
        _endpoint(session_env.router, "/api/history/{sid}", "GET")(RequestLike(user="bob"), "s1")
    assert not_owner.value.status_code == 404

    session_env.db.sessions.append(FakeDbSession(id="protected", name="Protected", is_important=True))
    session_env.manager.sessions["protected"] = FakeSession("protected", "Protected")
    with pytest.raises(HTTPException) as starred:
        _endpoint(session_env.router, "/api/session/{sid}", "DELETE")(request, "protected")
    assert starred.value.status_code == 403

    too_short = FakeSession("tiny", "Tiny", history=[])
    session_env.manager.sessions["tiny"] = too_short
    session_env.db.sessions.append(FakeDbSession(id="tiny", name="Tiny"))
    with pytest.raises(HTTPException) as short:
        await _endpoint(session_env.router, "/api/session/{session_id}/compact", "POST")(request, "tiny")
    assert short.value.status_code == 400

    monkeypatch.setattr(middleware, "require_admin", lambda request: None)
    delete_all = _endpoint(session_env.router, "/api/sessions/all", "DELETE")
    result = delete_all(request)
    assert result["status"] == "deleted"
    assert result["count"] >= 1
