import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace


class Column:
    def __eq__(self, other):
        return ("eq", other)

    def ilike(self, pattern):
        return ("ilike", pattern)

    def desc(self):
        return ("desc",)


class FakeQuery:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.items)

    def first(self):
        return self.items[0] if self.items else None

    def order_by(self, *_args, **_kwargs):
        return self


class FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.commits = 0
        self.closed = False

    def query(self, model, *_args):
        return FakeQuery(self.rows.get(model, []))

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class ModelEndpoint(SimpleNamespace):
    is_enabled = Column()
    name = Column()


class DbSession(SimpleNamespace):
    id = Column()
    owner = Column()


class ChatMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeSession:
    def __init__(self, session_id="s1", name="Chat", model="main-model", owner="alice"):
        self.session_id = session_id
        self.name = name
        self.model = model
        self.owner = owner
        self.endpoint_url = "http://main/chat"
        self.headers = {"x": "1"}
        self.message_count = 2
        self.messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]
        self.added = []

    def get_context_messages(self):
        return list(self.messages)

    def add_message(self, message):
        self.added.append(message)


class FakeSessionManager:
    def __init__(self):
        self.sessions = {
            "s1": FakeSession("s1", "Chat", owner="alice"),
            "bob-session": FakeSession("bob-session", "Private", owner="bob"),
        }
        self.created = []
        self.deleted = []
        self.renamed = []

    def create_session(self, **kwargs):
        self.created.append(kwargs)
        sid = kwargs["session_id"]
        self.sessions[sid] = FakeSession(
            sid,
            kwargs["name"],
            kwargs["model"],
            owner=kwargs.get("owner"),
        )
        self.sessions[sid].endpoint_url = kwargs["endpoint_url"]

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def get_sessions_for_user(self, owner):
        if owner is None:
            return dict(self.sessions)
        return {
            sid: sess
            for sid, sess in self.sessions.items()
            if getattr(sess, "owner", None) == owner
        }

    def update_session_name(self, session_id, name):
        self.renamed.append((session_id, name))
        if session_id in self.sessions:
            self.sessions[session_id].name = name

    def delete_session(self, session_id):
        self.deleted.append(session_id)
        return True

    def truncate_messages(self, session_id, keep_count):
        return session_id in self.sessions and keep_count >= 0


def _ai():
    return importlib.import_module("src.ai_interaction")


def _install_llm(monkeypatch, responses=None):
    calls = []
    responses = list(responses or ["ok"])
    llm = types.ModuleType("src.llm_core")
    llm.ANTHROPIC_MODELS = ["claude-3-opus"]
    llm._detect_provider = lambda base: "anthropic" if "anthropic" in base else "openai"

    async def llm_call_async(url, model, messages, headers=None, timeout=None):
        calls.append((url, model, messages, headers, timeout))
        if responses:
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return "ok"

    llm.llm_call_async = llm_call_async
    monkeypatch.setitem(sys.modules, "src.llm_core", llm)
    return calls


def _install_database(monkeypatch, rows):
    database = types.ModuleType("src.database")
    database.SessionLocal = lambda: FakeDB(rows)
    database.ModelEndpoint = ModelEndpoint
    database.Session = DbSession
    monkeypatch.setitem(sys.modules, "src.database", database)

    core_database = types.ModuleType("core.database")
    core_database.SessionLocal = database.SessionLocal
    core_database.Session = DbSession
    monkeypatch.setitem(sys.modules, "core.database", core_database)


def _install_core_models(monkeypatch):
    core_models = types.ModuleType("core.models")
    core_models.ChatMessage = ChatMessage
    monkeypatch.setitem(sys.modules, "core.models", core_models)


def test_ai_model_resolution_and_direct_llm_tools(monkeypatch):
    ai = _ai()
    _install_llm(monkeypatch, responses=["x" * 10020, "teacher", RuntimeError("down")])
    endpoints = [
        ModelEndpoint(name="Anthropic", base_url="http://anthropic/v1", api_key="a", is_enabled=True),
        ModelEndpoint(name="Local", base_url="http://local/v1", api_key="", is_enabled=True),
    ]
    _install_database(monkeypatch, {ModelEndpoint: endpoints})
    monkeypatch.setattr(ai, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(ai, "build_chat_url", lambda base: base + "/chat/completions")
    monkeypatch.setattr(ai, "build_models_url", lambda base: base + "/models")
    monkeypatch.setattr(ai, "build_headers", lambda key, base: {"Authorization": key} if key else {})

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "GLM-5.2"}]}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    assert ai._resolve_model("claude@Anthropic")[1] == "claude-3-opus"
    assert ai._resolve_model("glm@Local")[1] == "GLM-5.2"

    assert asyncio.run(ai.do_chat_with_model(""))["error"] == "First line must be the model name"
    chat = asyncio.run(ai.do_chat_with_model("glm\nhello"))
    assert chat["model"] == "GLM-5.2"
    assert chat["response"].endswith("... (truncated)")

    settings = types.ModuleType("src.settings")
    settings.get_setting = lambda key, default=None: "glm@Local" if key == "teacher_model" else default
    monkeypatch.setitem(sys.modules, "src.settings", settings)
    teacher = asyncio.run(ai.do_ask_teacher("auto\nproblem"))
    assert teacher["teacher"] is True
    failed = asyncio.run(ai.do_ask_teacher("glm@Local\nproblem"))
    assert "Teacher call failed" in failed["error"]


def test_ai_session_tools_pipeline_memory_and_dispatch(monkeypatch):
    ai = _ai()
    _install_core_models(monkeypatch)
    calls = _install_llm(monkeypatch, responses=["review", "unified", "sent", "step1", "step2"])
    manager = FakeSessionManager()
    ai.set_session_manager(manager)
    assert ai.get_session_manager() is manager
    monkeypatch.setattr(ai, "_resolve_model", lambda spec: ("http://review/chat", spec, {"h": "v"}))

    now = datetime.utcnow()
    db_row = DbSession(
        id="s1",
        name="Chat",
        owner="alice",
        archived=False,
        is_important=False,
        last_accessed=now - timedelta(minutes=3),
        updated_at=now,
        created_at=now,
    )
    _install_database(monkeypatch, {DbSession: [db_row]})

    created = asyncio.run(ai.do_create_session("New\nreviewer", owner="alice"))
    assert created["name"] == "New"
    assert manager.created

    listed = asyncio.run(ai.do_list_sessions("", owner="alice"))
    assert "Found" in listed["results"]

    second = asyncio.run(ai.do_second_opinion("reviewer\nfocus", session_id="s1", owner="alice"))
    assert "Second Opinion" in second["response"]
    assert calls[0][1] == "reviewer"
    denied_second = asyncio.run(
        ai.do_second_opinion("reviewer\nfocus", session_id="bob-session", owner="alice")
    )
    assert denied_second["error"] == "No conversation context found to review"

    sent = asyncio.run(ai.do_send_to_session("s1\nhello", owner="alice"))
    assert sent["response"] == "sent"
    assert len(manager.get_session("s1").added) == 2
    denied_send = asyncio.run(ai.do_send_to_session("bob-session\nhello", owner="alice"))
    assert "not found" in denied_send["error"]
    assert manager.get_session("bob-session").added == []

    pipeline = asyncio.run(ai.do_pipeline("m1 | draft\nm2 | revise"))
    assert len(pipeline["steps"]) == 2
    assert asyncio.run(ai.do_pipeline("bad line"))["error"].startswith("Each line")

    switched = asyncio.run(ai.do_manage_session('{"action":"switch","session_id":"s1"}', owner="alice"))
    assert switched["session_id"] == "s1"
    renamed = asyncio.run(ai.do_manage_session('{"action":"rename","session_id":"s1","name":"Renamed"}', owner="alice"))
    assert renamed["name"] == "Renamed"
    assert "archived" in asyncio.run(ai.do_manage_session('{"action":"archive","session_id":"s1"}', owner="alice"))["results"]
    assert "marked as important" in asyncio.run(ai.do_manage_session('{"action":"important","session_id":"s1"}', owner="alice"))["results"]
    assert "truncated" in asyncio.run(ai.do_manage_session('{"action":"truncate","session_id":"s1","keep_count":2}', owner="alice"))["results"]
    forked = asyncio.run(ai.do_manage_session('{"action":"fork","session_id":"s1","keep_count":1}', owner="alice"))
    assert forked["messages_copied"] == 1

    class MemoryManager:
        def __init__(self):
            self.items = [{"id": "mem123", "text": "alpha memory", "category": "fact", "owner": "alice"}]

        def load(self, owner=None):
            return [m for m in self.items if owner is None or m.get("owner") == owner]

        def load_all(self):
            return list(self.items)

        def save(self, items):
            self.items = list(items)

        def add_entry(self, text, source, category, owner=None):
            return {"id": "mem999", "text": text, "category": category, "owner": owner}

        def get_relevant_memories(self, query, memories, threshold=0.05, max_items=20):
            return [m for m in memories if query in m.get("text", "")]

    vector = SimpleNamespace(healthy=True, added=[], removed=[])
    vector.add = lambda mid, text: vector.added.append((mid, text))
    vector.remove = lambda mid: vector.removed.append(mid)
    ai.set_memory_manager(MemoryManager(), vector)
    assert "memory entries" in asyncio.run(ai.do_manage_memory("list", owner="alice"))["results"]
    assert asyncio.run(ai.do_manage_memory("add\nnew fact\nfact", owner="alice"))["memory_id"] == "mem999"
    assert "updated" in asyncio.run(ai.do_manage_memory("edit\nmem123\nbeta", owner="alice"))["results"]
    assert "matching memories" in asyncio.run(ai.do_manage_memory("search\nbeta", owner="alice"))["results"]
    assert "deleted" in asyncio.run(ai.do_manage_memory("delete\nmem123", owner="alice"))["results"]

    desc, result = asyncio.run(ai.dispatch_ai_tool("ui_control", "toggle shell on", session_id="s1", owner="alice"))
    assert desc.startswith("ui_control")
    assert result["toggle_name"] == "bash"
    desc, denied_dispatch = asyncio.run(
        ai.dispatch_ai_tool("send_to_session", "bob-session\nhello", owner="alice")
    )
    assert desc == "send_to_session: bob-session"
    assert "not found" in denied_dispatch["error"]
    streamed = []

    async def collect():
        async for event in ai.stream_ai_tool("ui_control", "set_mode agent", session_id="s1", owner="alice"):
            streamed.append(event)

    asyncio.run(collect())
    assert streamed[0]["_final"] is True


def test_ai_ui_control_and_list_models(monkeypatch):
    ai = _ai()
    _install_llm(monkeypatch)
    endpoint = ModelEndpoint(name="Local", base_url="http://local/v1", api_key="", is_enabled=True)
    _install_database(monkeypatch, {ModelEndpoint: [endpoint]})

    monkeypatch.setattr(ai, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(ai, "build_models_url", lambda base: base + "/models")
    monkeypatch.setattr(ai, "build_headers", lambda key, base: {})

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "local-model"}]}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    assert "local-model" in asyncio.run(ai.do_list_models(""))["results"]
    assert asyncio.run(ai.do_ui_control(""))["error"] == "No action specified"
    assert asyncio.run(ai.do_ui_control("toggle shell on"))["state"] is True
    assert asyncio.run(ai.do_ui_control("set_mode chat"))["mode"] == "chat"
    assert asyncio.run(ai.do_ui_control("set_theme dark"))["theme_name"] == "dark"
    theme = asyncio.run(ai.do_ui_control("create_theme mine #000000 #ffffff #111111 #222222 #ff00ff bgPattern=dots frosted=true"))
    assert theme["theme_name"] == "mine"
    assert asyncio.run(ai.do_ui_control("highlight #app Label"))["selector"] == "#app"
    assert asyncio.run(ai.do_ui_control("clear_highlight"))["ui_event"] == "clear_highlight"
    assert asyncio.run(ai.do_ui_control("open_panel notes"))["panel"] == "notes"
    assert asyncio.run(ai.do_ui_control("open_email_reply 101 INBOX reply-all"))["mode"] == "reply-all"
    assert "Toggle states" in asyncio.run(ai.do_ui_control("get_toggles"))["results"]
    assert "Unknown action" in asyncio.run(ai.do_ui_control("unknown"))["error"]


def test_generate_image_blocks_external_endpoint_offline(monkeypatch):
    ai = _ai()
    monkeypatch.setattr(ai, "offline_mode", lambda: True)
    monkeypatch.setattr(ai, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(
        ai,
        "_resolve_model",
        lambda _model: ("https://api.openai.com/v1/chat/completions", "gpt-image-1", {}),
    )

    import httpx

    class BlockedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network")

    monkeypatch.setattr(httpx, "AsyncClient", BlockedClient)

    result = asyncio.run(ai.do_generate_image("Draw a sealed computer\ngpt-image-1"))
    assert result == {"error": "External image generation endpoint is disabled in offline mode."}


def test_list_models_marks_external_endpoint_disabled_offline(monkeypatch):
    ai = _ai()
    _install_llm(monkeypatch)
    endpoint = ModelEndpoint(name="Cloud", base_url="https://api.openai.com/v1", api_key="sk", is_enabled=True)
    _install_database(monkeypatch, {ModelEndpoint: [endpoint]})
    monkeypatch.setattr(ai, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(ai, "offline_mode", lambda: True)
    monkeypatch.setattr(ai, "is_local_model_url", lambda url: False)
    monkeypatch.setattr(ai, "build_headers", lambda key, base: {})

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    result = asyncio.run(ai.do_list_models(""))
    assert "external endpoint disabled in offline mode" in result["results"]
