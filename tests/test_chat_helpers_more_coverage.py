import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import chat_helpers


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def __ge__(self, other):
        return ("ge", self.name, other)

    def contains(self, other):
        return ("contains", self.name, other)


class FakeDBSession:
    id = Column("id")
    owner = Column("owner")


class FakeChatMessage:
    session_id = Column("session_id")
    role = Column("role")
    timestamp = Column("timestamp")

    def __init__(self, role, content, metadata=None):
        self.role = role
        self.content = content
        self.metadata = metadata or {}


class FakeEndpoint:
    is_enabled = Column("is_enabled")
    base_url = Column("base_url")

    def __init__(self, name="Endpoint", base_url="http://base/v1", api_key="sk", is_enabled=True):
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        self.is_enabled = is_enabled


class Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.updated = None

    def join(self, *args):
        return self

    def filter(self, *args):
        self.filters = args
        return self

    def count(self):
        return self.db.count

    def all(self):
        if self.model is FakeEndpoint:
            return list(self.db.endpoints)
        return []

    def first(self):
        if self.model is FakeEndpoint:
            return self.db.endpoint
        if self.model is FakeDBSession:
            return self.db.db_session
        return None

    def update(self, data):
        self.updated = data
        self.db.updated.append(data)
        return 1


class DB:
    def __init__(self, *, count=0, endpoints=None, endpoint=None, db_session=None, fail=False):
        self.count = count
        self.endpoints = list(endpoints or [])
        self.endpoint = endpoint
        self.db_session = db_session
        self.fail = fail
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.updated = []

    def query(self, model):
        if self.fail:
            raise RuntimeError("db down")
        return Query(self, model)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class RequestLike:
    def __init__(self, user="alice", auth_manager=None):
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
        self.state = SimpleNamespace(current_user=user)


class Session:
    def __init__(self):
        self.id = "s1"
        self.name = "Chat"
        self.model = "model"
        self.endpoint_url = "http://endpoint/v1/chat/completions"
        self.headers = {}
        self.history = []

    def add_message(self, msg):
        msg.metadata.setdefault("_db_id", f"db-{len(self.history)}")
        self.history.append(msg)

    def get_context_messages(self):
        return [{"role": m.role, "content": m.content} for m in self.history]


def test_privilege_gates_needs_auto_name_and_add_user(monkeypatch):
    monkeypatch.setattr(chat_helpers, "ChatMessage", FakeChatMessage)
    monkeypatch.setattr(chat_helpers, "DBSession", FakeDBSession)
    monkeypatch.setattr(chat_helpers, "get_current_user", lambda request: request.state.current_user)

    class Auth:
        def __init__(self, privs):
            self.privs = privs

        def get_privileges(self, user):
            return self.privs

    sess = Session()
    chat_helpers._enforce_chat_privileges(RequestLike(user=None, auth_manager=Auth({})), sess)
    chat_helpers._enforce_chat_privileges(RequestLike(user="alice", auth_manager=None), sess)

    monkeypatch.setattr(chat_helpers, "get_current_user", lambda request: (_ for _ in ()).throw(RuntimeError("no auth")))
    chat_helpers._enforce_chat_privileges(RequestLike(auth_manager=Auth({"max_messages_per_day": 1})), sess)
    monkeypatch.setattr(chat_helpers, "get_current_user", lambda request: request.state.current_user)
    chat_helpers._enforce_chat_privileges(
        RequestLike(auth_manager=Auth({"allowed_models": ["model"], "max_messages_per_day": 0})),
        sess,
    )

    with pytest.raises(HTTPException) as model_denied:
        chat_helpers._enforce_chat_privileges(
            RequestLike(auth_manager=Auth({"allowed_models": ["other"]})),
            sess,
        )
    assert model_denied.value.status_code == 403

    db = DB(count=3)
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: db)
    with pytest.raises(HTTPException) as capped:
        chat_helpers._enforce_chat_privileges(
            RequestLike(auth_manager=Auth({"allowed_models": ["model"], "max_messages_per_day": 3})),
            sess,
        )
    assert capped.value.status_code == 429
    assert db.closed == 1

    assert chat_helpers.needs_auto_name("") is True
    assert chat_helpers.needs_auto_name("Chat") is True
    assert chat_helpers.needs_auto_name("llama 10:22:33 PM") is True
    assert chat_helpers.needs_auto_name("Real Project Name") is False

    pre = chat_helpers.PreprocessedMessage("enhanced", "user content", "text ctx", [], [{"id": "a"}])
    class Handler:
        called = False

        def update_session_name_if_needed(self, session, text):
            self.called = True
            assert text == "text ctx"

    handler = Handler()
    chat_helpers.add_user_message(sess, handler, pre, incognito=False)
    assert sess.history[-1].metadata == {"attachments": [{"id": "a"}], "_db_id": "db-0"}
    assert handler.called is True
    handler.called = False
    chat_helpers.add_user_message(sess, handler, pre, incognito=True)
    assert handler.called is False


def test_auto_name_session_and_fallback_endpoint(monkeypatch):
    sess = Session()
    sess.history = [SimpleNamespace(role="assistant", content="ignored"), SimpleNamespace(role="user", content=[{"type": "text", "text": "please summarize this"}])]
    updates = []

    class Manager:
        def update_session_name(self, session_id, title):
            updates.append((session_id, title))

    llm_mod = types.ModuleType("src.llm_core")
    async def fake_llm(*args, **kwargs):
        return '<think>reason</think> Project Summary "'
    llm_mod.llm_call_async = fake_llm
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_mod)
    task_endpoint = types.ModuleType("src.task_endpoint")
    task_endpoint.resolve_task_endpoint = lambda url, model, headers: ("task-url", "task-model", {"H": "1"})
    monkeypatch.setitem(sys.modules, "src.task_endpoint", task_endpoint)

    asyncio.run(chat_helpers.auto_name_session(Manager(), sess))
    assert updates == [("s1", "Project Summary")]

    sess.history = []
    asyncio.run(chat_helpers.auto_name_session(Manager(), sess))
    assert updates == [("s1", "Project Summary")]

    async def failing_llm(*args, **kwargs):
        raise RuntimeError("offline")

    llm_mod.llm_call_async = failing_llm
    sess.history = [SimpleNamespace(role="user", content="hello")]
    asyncio.run(chat_helpers.auto_name_session(Manager(), sess))
    assert updates == [("s1", "Project Summary")]
    llm_mod.llm_call_async = fake_llm

    endpoint = FakeEndpoint(name="Fallback", base_url="http://new/v1", api_key="sk")
    primary_db = DB(endpoints=[FakeEndpoint(base_url="http://endpoint/v1"), endpoint])
    persist_db = DB()
    calls = [primary_db, persist_db]
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: calls.pop(0))
    monkeypatch.setattr(chat_helpers, "ModelEndpoint", FakeEndpoint)
    monkeypatch.setattr(chat_helpers, "DBSession", FakeDBSession)

    requests_mod = types.SimpleNamespace()
    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "new-model"}]}

    requests_mod.get = lambda *args, **kwargs: Resp()
    monkeypatch.setitem(sys.modules, "requests", requests_mod)

    resolver = types.ModuleType("src.endpoint_resolver")
    resolver.normalize_base = lambda base: base.rstrip("/")
    resolver.build_models_url = lambda base: base + "/models"
    resolver.build_chat_url = lambda base: base + "/chat/completions"
    resolver.build_headers = lambda key, base: {"Authorization": f"Bearer {key}", "Base": base}
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", resolver)

    result = chat_helpers.try_fallback_endpoint(sess, "s1")
    assert result == {"model": "new-model", "endpoint_url": "http://new/v1/chat/completions", "endpoint_name": "Fallback"}
    assert sess.model == "new-model"
    assert persist_db.updated[0]["model"] == "new-model"
    assert persist_db.commits == 1

    empty_db = DB(endpoints=[FakeEndpoint(base_url="http://empty/v1")])
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: empty_db)
    requests_mod.get = lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [], "models": []},
    )
    assert chat_helpers.try_fallback_endpoint(sess, "s1") is None

    failing_ep_db = DB(endpoints=[FakeEndpoint(base_url="http://broken/v1")])
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: failing_ep_db)
    requests_mod.get = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network disabled"))
    assert chat_helpers.try_fallback_endpoint(sess, "s1") is None

    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: DB(endpoints=[]))
    assert chat_helpers.try_fallback_endpoint(sess, "s1") is None


def test_extract_preprocess_events_auth_and_context(monkeypatch):
    monkeypatch.setattr(chat_helpers, "ChatMessage", FakeChatMessage)
    monkeypatch.setattr(chat_helpers, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda user: {"memory_enabled": True, "skills_enabled": False})
    monkeypatch.setattr(chat_helpers, "normalize_model_id", lambda url, model: "normalized-model")
    monkeypatch.setattr(chat_helpers, "maybe_compact", lambda sess, url, model, messages, headers: asyncio.sleep(0, result=(messages + [{"role": "system", "content": "compacted"}], 999, True)))
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda messages, context_length: messages)
    monkeypatch.setattr(chat_helpers, "untrusted_context_message", lambda label, text: {"role": "system", "content": f"{label}:{text}", "metadata": {"trusted": False}})

    class Handler:
        def validate_and_extract_preset(self, preset_id):
            assert preset_id == "p1"
            return 0.4, 123, "system preset", "Ada"

        async def preprocess_message(self, message, att_ids, sess, auto_opened_docs=None):
            auto_opened_docs.append({"id": "doc1"})
            return "enhanced", [{"type": "text", "text": message}], "text ctx", ["yt"], [{"id": "att"}]

        def update_session_name_if_needed(self, sess, text):
            sess.name_updated = text

    class Processor:
        _last_used_memories = [{"id": "mem"}]

        def build_context_preface(self, **kwargs):
            assert kwargs["message"] == "enhanced"
            assert kwargs["use_web"] is False
            assert kwargs["use_memory"] is True
            assert kwargs["use_skills"] is False
            assert kwargs["character_name"] == "Ada"
            return ([{"role": "system", "content": kwargs["preset_system_prompt"]}], ["rag"], ["web"])

    fired = []
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda name, user: fired.append((name, user))
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)
    created = []
    monkeypatch.setattr(chat_helpers.asyncio, "create_task", lambda coro: created.append(coro) or SimpleNamespace())

    sess = Session()
    ctx = asyncio.run(chat_helpers.build_chat_context(
        sess,
        RequestLike(),
        Handler(),
        Processor(),
        "hello",
        "s1",
        preset_id="p1",
        att_ids=["a"],
        use_web=True,
        use_rag="false",
        search_context="prefetched",
        use_enhanced_message=True,
        webhook_manager=SimpleNamespace(fire=lambda *args: asyncio.sleep(0)),
    ))

    assert ctx.context_length == 999
    assert ctx.was_compacted is True
    assert ctx.rag_sources == ["rag"]
    assert ctx.web_sources == ["web"]
    assert ctx.used_memories == [{"id": "mem"}]
    assert ctx.auto_opened_docs == [{"id": "doc1"}]
    assert sess.model == "normalized-model"
    assert fired == [("message_sent", "alice")]
    for coro in created:
        coro.close()

    handler = Handler()
    preset = chat_helpers.extract_preset(handler, "p1")
    assert preset.temperature == 0.4
    preprocessed = asyncio.run(chat_helpers.preprocess(handler, "msg", [], sess, []))
    assert preprocessed.text_for_context == "text ctx"

    class IncognitoProcessor(Processor):
        def build_context_preface(self, **kwargs):
            assert kwargs["use_memory"] is False
            assert kwargs["use_skills"] is False
            assert kwargs["use_rag"] is False
            return ([], [], [])

    sess2 = Session()
    ctx2 = asyncio.run(chat_helpers.build_chat_context(
        sess2,
        RequestLike(),
        Handler(),
        IncognitoProcessor(),
        "private",
        "s2",
        preset_id="p1",
        use_rag=True,
        incognito=True,
    ))
    assert ctx2.uprefs == {"memory_enabled": True, "skills_enabled": False}


def test_token_usage_headers_thinking_and_save(monkeypatch):
    monkeypatch.setattr(chat_helpers, "ChatMessage", FakeChatMessage)
    row = SimpleNamespace(total_input_tokens=2, total_output_tokens=3)
    db = DB(db_session=row)
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat_helpers, "DBSession", FakeDBSession)
    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 5, "output_tokens": 7})
    assert row.total_input_tokens == 7
    assert row.total_output_tokens == 10
    assert db.commits == 1
    chat_helpers.accumulate_token_usage("s1", {})

    failing = DB(fail=True)
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: failing)
    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 1})
    assert failing.rollbacks == 1

    auth_db = DB(endpoint=FakeEndpoint(name="Local", base_url="http://endpoint/v1", api_key="sk"), db_session=row)
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: auth_db)
    monkeypatch.setattr(chat_helpers, "ModelEndpoint", FakeEndpoint)
    resolver = types.ModuleType("src.endpoint_resolver")
    resolver.build_headers = lambda key, base: {"Authorization": f"Bearer {key}", "Base": base}
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", resolver)
    sess = Session()
    chat_helpers.resolve_session_auth(sess, "s1")
    assert sess.headers["Authorization"] == "Bearer sk"
    assert auth_db.updated and auth_db.commits == 1
    chat_helpers.resolve_session_auth(sess, "s1")
    sess.headers = {}
    monkeypatch.setattr(chat_helpers, "SessionLocal", lambda: DB(fail=True))
    chat_helpers.resolve_session_auth(sess, "s1")

    assert chat_helpers._normalize_thinking("") == ""
    assert chat_helpers._normalize_thinking("Thinking: reason\n\nHi there").startswith("<think>reason</think>")
    assert chat_helpers._normalize_thinking("The user asked a detailed question about setup. Hi there") == (
        "<think>The user asked a detailed question about setup.</think>\nHi there"
    )
    assert chat_helpers._normalize_thinking("Thinking: a long plan\n\nfinal answer") == (
        "<think>a long plan</think>\n\nfinal answer"
    )
    quoted = chat_helpers._normalize_thinking('Thinking: plan only "This is the final answer."')
    assert quoted.endswith("\n\nThis is the final answer.")
    assert chat_helpers._normalize_thinking("Thinking: plan only") == "<think>plan only</think>"
    assert chat_helpers._normalize_thinking("The user asked\n\nSure, here it is") == (
        "<think>The user asked\n</think>\nSure, here it is"
    )
    assert chat_helpers._normalize_thinking("The user asked\n* reason\nFinal answer") == (
        "<think>The user asked\n* reason</think>\nFinal answer"
    )
    assert chat_helpers._normalize_thinking("The user asked\n<think>Hi there") == "<think>The user asked</think>\nHi there"
    assert chat_helpers._normalize_thinking("<think>ok</think> reply") == "<think>ok</think> reply"

    meta = chat_helpers._extract_thinking_meta('<think time="1.2">reason</think> reply')
    assert meta == {"thinking": "reason", "reply": "reply", "time": "1.2"}
    assert chat_helpers._extract_thinking_meta("<think>reason</think>") is None
    assert chat_helpers._extract_thinking_meta("") is None
    assert chat_helpers._extract_thinking_meta("Thinking: reason\n\nFinal reply") == {
        "thinking": "reason",
        "reply": "Final reply",
        "time": None,
    }
    content, md = chat_helpers.clean_thinking_for_save("<think>why</think> answer", {"x": 1})
    assert content == "answer"
    assert md["thinking"] == "why"
    timed_content, timed_md = chat_helpers.clean_thinking_for_save('<think time="3.5">why</think> answer')
    assert timed_content == "answer"
    assert timed_md["thinking_time"] == "3.5"
    unchanged_content, unchanged_md = chat_helpers.clean_thinking_for_save("plain")
    assert unchanged_content == "plain"
    assert unchanged_md == {}

    touched = []
    core_db = types.ModuleType("core.database")
    core_db.update_session_last_accessed = lambda session_id: touched.append(session_id)
    monkeypatch.setitem(sys.modules, "core.database", core_db)
    class Manager:
        saved = 0

        def save_sessions(self):
            self.saved += 1

    manager = Manager()
    sess = Session()
    db_id = chat_helpers.save_assistant_response(
        sess,
        manager,
        "s1",
        '<think time="2">plan</think> final',
        {"input_tokens": 1},
        character_name="Ada",
        web_sources=["web"],
        rag_sources=["rag"],
        research_sources=["research"],
        used_memories=["mem"],
        do_research=True,
        tool_events=[{"tool": "x"}],
    )
    assert db_id == "db-0"
    assert sess.history[-1].content == "final"
    assert sess.history[-1].metadata["thinking"] == "plan"
    assert touched == ["s1"]
    assert manager.saved == 1

    assert chat_helpers.save_assistant_response(sess, manager, "s1", "hidden", None, incognito=True) is None

    class NoDbSession(Session):
        def add_message(self, msg):
            self.history.append(msg)

    assert chat_helpers.save_assistant_response(
        NoDbSession(), manager, "s1", "plain", {"input_tokens": 2}, do_research=True
    ) is None


def test_post_response_tasks(monkeypatch):
    monkeypatch.setattr(chat_helpers, "ChatMessage", FakeChatMessage)
    tasks = []
    monkeypatch.setattr(chat_helpers.asyncio, "create_task", lambda coro: tasks.append(coro) or SimpleNamespace())
    calls = []
    monkeypatch.setattr(chat_helpers, "accumulate_token_usage", lambda sid, metrics: calls.append(("tokens", sid, metrics)))

    extractor = types.ModuleType("services.memory.memory_extractor")
    extractor.extract_and_store = lambda *args: asyncio.sleep(0, result="memory")
    monkeypatch.setitem(sys.modules, "services.memory.memory_extractor", extractor)
    skill = types.ModuleType("services.memory.skill_extractor")
    skill.maybe_extract_skill = lambda *args, **kwargs: asyncio.sleep(0, result="skill")
    monkeypatch.setitem(sys.modules, "services.memory.skill_extractor", skill)
    task_endpoint = types.ModuleType("src.task_endpoint")
    task_endpoint.resolve_task_endpoint = lambda url, model, headers: ("task-url", "task-model", {"H": "1"})
    monkeypatch.setitem(sys.modules, "src.task_endpoint", task_endpoint)

    class Webhook:
        def fire(self, name, payload):
            calls.append(("webhook", name, payload))
            return asyncio.sleep(0, result=None)

    class Manager:
        def __init__(self):
            self.named = []

        def update_session_name(self, session_id, title):
            self.named.append((session_id, title))

    sess = Session()
    sess.history = [FakeChatMessage("user", "u"), FakeChatMessage("assistant", "a"), FakeChatMessage("user", "u2"), FakeChatMessage("assistant", "a2")]
    chat_helpers.run_post_response_tasks(
        sess,
        Manager(),
        "s1",
        "msg",
        "response",
        {"input_tokens": 1, "output_tokens": 2},
        {"auto_memory": True, "auto_skills": True},
        memory_manager=object(),
        memory_vector=object(),
        webhook_manager=Webhook(),
        agent_rounds=2,
        agent_tool_calls=0,
        skills_manager=object(),
        owner="alice",
    )

    assert ("tokens", "s1", {"input_tokens": 1, "output_tokens": 2}) in calls
    assert any(call[0] == "webhook" and call[1] == "chat.completed" for call in calls)
    assert len(tasks) >= 3
    for coro in tasks:
        coro.close()

    tasks.clear()
    sess.name = "Real Name"
    chat_helpers.run_post_response_tasks(
        sess,
        Manager(),
        "s1",
        "msg",
        "response",
        None,
        {"auto_memory": False, "auto_skills": True},
        None,
        None,
        None,
        agent_rounds=2,
        agent_tool_calls=2,
        skills_manager=None,
    )
    assert tasks == []

    chat_helpers.run_post_response_tasks(
        sess,
        Manager(),
        "s1",
        "msg",
        "response",
        None,
        {"auto_memory": True, "auto_skills": True},
        None,
        None,
        Webhook(),
        incognito=True,
        compare_mode=True,
        agent_rounds=3,
        agent_tool_calls=3,
        skills_manager=None,
    )
    assert tasks == []


def test_remaining_thinking_and_save_response_defensive_paths(monkeypatch):
    normalized = chat_helpers._normalize_thinking("Thinking: plan details\n\n_final reply text")
    assert normalized == "<think>plan details</think>\n\n_final reply text"

    class SessionWithBrokenHistory:
        model = "local-model"

        def __init__(self):
            self.messages = []

        def add_message(self, message):
            self.messages.append(message)

        @property
        def history(self):
            raise AttributeError("history unavailable")

    class Manager:
        def save_sessions(self):
            self.saved = True

    updates = []
    core_database = types.ModuleType("core.database")
    core_database.update_session_last_accessed = lambda session_id: updates.append(session_id)
    monkeypatch.setitem(sys.modules, "core.database", core_database)

    assert chat_helpers.save_assistant_response(
        SessionWithBrokenHistory(),
        Manager(),
        "s1",
        "plain response",
        {},
    ) is None
    assert updates == ["s1"]
