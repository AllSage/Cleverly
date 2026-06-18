import asyncio
import importlib
import json
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


class Column:
    def __set_name__(self, owner, name):
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def __ne__(self, other):
        return ("ne", self.name, other)

    def desc(self):
        return ("desc",)

    def asc(self):
        return ("asc",)

    def ilike(self, pattern):
        return ("ilike", self.name, pattern)

    def is_(self, value):
        return ("is", self.name, value)

    def startswith(self, prefix):
        return ("startswith", self.name, prefix)

    def in_(self, values):
        return ("in", self.name, values)


class FakeModel(SimpleNamespace):
    id = Column()
    name = Column()
    title = Column()
    is_enabled = Column()
    is_active = Column()
    updated_at = Column()
    created_at = Column()
    owner = Column()
    archived = Column()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class FakeQuery:
    def __init__(self, items):
        self.items = list(items)

    def all(self):
        return list(self.items)

    def first(self):
        return self.items[0] if self.items else None

    def filter(self, *_args, **_kwargs):
        for expr in _args:
            if not isinstance(expr, tuple) or len(expr) < 3:
                continue
            op, name, value = expr[:3]
            if op == "eq":
                self.items = [item for item in self.items if getattr(item, name, None) == value]
            elif op == "ne":
                self.items = [item for item in self.items if getattr(item, name, None) != value]
            elif op == "is":
                self.items = [item for item in self.items if getattr(item, name, None) is value]
            elif op == "startswith":
                self.items = [item for item in self.items if str(getattr(item, name, "")).startswith(str(value))]
            elif op == "in":
                self.items = [item for item in self.items if getattr(item, name, None) in value]
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def join(self, *_args, **_kwargs):
        return self


class FakeDB:
    def __init__(self, data=None):
        self.data = data or {}
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def query(self, model, *_args):
        return FakeQuery(self.data.get(model, []))

    def add(self, item):
        self.added.append(item)
        self.data.setdefault(type(item), []).append(item)

    def delete(self, item):
        self.deleted.append(item)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _install_core_database(monkeypatch, db, **models):
    module = types.ModuleType("core.database")
    module.SessionLocal = lambda: db
    for name, model in models.items():
        setattr(module, name, model)
    monkeypatch.setitem(sys.modules, "core.database", module)
    return module


def _tool_module():
    return importlib.import_module("src.tool_implementations")


def test_search_chats_excludes_legacy_ownerless_sessions_for_authenticated_user(monkeypatch):
    tools = _tool_module()

    class SearchColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return ("eq", self.name, other)

        def desc(self):
            return ("desc", self.name)

        def ilike(self, pattern, **_kwargs):
            return ("ilike", self.name, pattern)

        def in_(self, values):
            return ("in", self.name, values)

    class DbSession(SimpleNamespace):
        id = SearchColumn("id")
        name = SearchColumn("name")
        owner = SearchColumn("owner")
        archived = SearchColumn("archived")

    class DbChatMessage(SimpleNamespace):
        session_id = SearchColumn("session_id")
        content = SearchColumn("content")
        role = SearchColumn("role")
        timestamp = SearchColumn("timestamp")

    class ChatSearchQuery:
        def __init__(self, rows):
            self.rows = list(rows)

        def join(self, *_args, **_kwargs):
            return self

        def filter(self, *exprs, **_kwargs):
            for expr in exprs:
                if not isinstance(expr, tuple) or len(expr) < 3:
                    continue
                op, name, value = expr[:3]
                if op == "eq":
                    self.rows = [
                        row for row in self.rows
                        if getattr(row[0], name, getattr(row[1], name, None)) == value
                    ]
                elif op == "ilike":
                    needle = str(value).strip("%").replace("\\%", "%").replace("\\_", "_").lower()
                    self.rows = [row for row in self.rows if needle in str(getattr(row[0], name, "")).lower()]
                elif op == "in":
                    self.rows = [row for row in self.rows if getattr(row[0], name, None) in value]
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def limit(self, limit):
            self.rows = self.rows[:limit]
            return self

        def all(self):
            return [(msg, sess.id, sess.name) for msg, sess in self.rows]

    class SearchDb:
        def __init__(self, rows):
            self.rows = rows
            self.closed = False

        def query(self, *_args):
            return ChatSearchQuery(self.rows)

        def close(self):
            self.closed = True

    now = datetime.utcnow()
    alice = DbSession(id="alice-chat", name="Alice", owner="alice", archived=False)
    legacy = DbSession(id="legacy-chat", name="Legacy", owner=None, archived=False)
    bob = DbSession(id="bob-chat", name="Bob", owner="bob", archived=False)
    rows = [
        (DbChatMessage(session_id="alice-chat", role="user", content="needle belongs to alice", timestamp=now), alice),
        (DbChatMessage(session_id="legacy-chat", role="user", content="needle legacy secret", timestamp=now), legacy),
        (DbChatMessage(session_id="bob-chat", role="user", content="needle bob secret", timestamp=now), bob),
    ]
    db = SearchDb(rows)
    database = types.ModuleType("src.database")
    database.SessionLocal = lambda: db
    database.ChatMessage = DbChatMessage
    database.Session = DbSession
    monkeypatch.setitem(sys.modules, "src.database", database)

    result = asyncio.run(tools.do_search_chats("needle", owner="alice"))["results"]

    assert "Alice" in result
    assert "Legacy" not in result
    assert "Bob" not in result
    assert db.closed is True


def test_tool_helpers_document_parsing_and_active_state():
    tools = _tool_module()

    assert tools._truncate("abcdef", 3) == "abc\n... (truncated, 6 chars total)"
    assert tools._parse_tool_args('{"body":{"action":"list"}}') == {"action": "list"}
    assert tools._parse_tool_args({"x": 1}) == {"x": 1}
    assert tools._parse_tool_args(None) == {}
    with pytest.raises(ValueError):
        tools._parse_tool_args("{")

    tools.set_active_document("doc-1")
    tools.set_active_model("model-1")
    assert tools.get_active_document() == "doc-1"

    assert tools._sniff_doc_language("<svg></svg>") == "svg"
    assert tools._sniff_doc_language('{"a": 1}') == "json"
    assert tools._sniff_doc_language("#!/usr/bin/env python") == "python"
    assert tools._sniff_doc_language("select * from table") == "sql"
    assert tools._looks_like_email_document("To: a@example.com\nSubject: Hi\n---\nBody")
    assert tools._coerce_email_document_content("To: a\nSubject: s\n---\nold", "new body").endswith("---\nnew body")

    edit = "<<<FIND>>>\nold\n<<<REPLACE>>>\nnew\n<<<END>>>"
    assert tools.parse_edit_blocks(edit) == [{"find": "old", "replace": "new"}]
    suggest = "<<<FIND>>>\nold\n<<<SUGGEST>>>\nnew\n<<<REASON>>>\nbetter\n<<<END>>>"
    assert tools.parse_suggest_blocks(suggest)[0]["id"] == "sugg-1"
    noop = "<<<FIND>>>\nsame\n<<<SUGGEST>>>\nsame\n<<<REASON>>>\nno change\n<<<END>>>"
    assert tools.parse_suggest_blocks(noop) == []


def test_manage_endpoints_mcp_webhooks_and_tokens(monkeypatch):
    tools = _tool_module()

    class ModelEndpoint(FakeModel):
        base_url = Column()

    class McpServer(FakeModel):
        transport = Column()

    class Webhook(FakeModel):
        url = Column()
        events = Column()

    class ApiToken(FakeModel):
        token_prefix = Column()

    endpoint = ModelEndpoint(id="ep1", name="Local", base_url="http://localhost", is_enabled=False)
    server = McpServer(
        id="srv1",
        name="Files",
        transport="stdio",
        command="python",
        args='["-m","fileserver"]',
        env='{"ACCOUNT":"demo"}',
        url=None,
        is_enabled=True,
    )
    hook = Webhook(id="wh1", name="Hook", url="http://localhost/hook", events="chat.completed", is_active=True)
    token = ApiToken(id="tok1", name="Token", token_prefix="abcdef12", is_active=True)
    db = FakeDB({ModelEndpoint: [endpoint], McpServer: [server], Webhook: [hook], ApiToken: [token]})
    _install_core_database(
        monkeypatch,
        db,
        ModelEndpoint=ModelEndpoint,
        McpServer=McpServer,
        Webhook=Webhook,
        ApiToken=ApiToken,
    )

    assert asyncio.run(tools.do_manage_endpoints('{"action":"list"}'))["endpoints"][0]["id"] == "ep1"
    assert asyncio.run(tools.do_manage_endpoints('{"action":"add","name":"New"}'))["error"] == "base_url is required"
    assert "Added endpoint" in asyncio.run(tools.do_manage_endpoints('{"action":"add","base_url":"http://new"}'))["response"]
    assert "Deleted endpoint" in asyncio.run(tools.do_manage_endpoints('{"action":"delete","endpoint_id":"ep1"}'))["response"]
    assert "disabled" in asyncio.run(tools.do_manage_endpoints('{"action":"disable","endpoint_id":"ep1"}'))["response"]
    assert asyncio.run(tools.do_manage_endpoints('{"action":"other"}'))["exit_code"] == 1

    class Manager:
        def __init__(self):
            self.connected = []
            self.disconnected = []

        def get_server_status(self, server_id):
            return {"status": "connected", "tool_count": 3 if server_id else 0}

        async def connect_server(self, *args, **kwargs):
            self.connected.append((args, kwargs))

        async def disconnect_server(self, server_id):
            self.disconnected.append(server_id)

        def get_all_tools(self):
            return [{"name": "read_file", "server_name": "Files", "description": "Read a file"}]

    manager = Manager()
    monkeypatch.setattr(tools, "get_mcp_manager", lambda: manager)
    assert "1 MCP servers" in asyncio.run(tools.do_manage_mcp('{"action":"list"}'))["response"]
    assert "Added MCP server" in asyncio.run(tools.do_manage_mcp('{"action":"add","name":"New","command":"python","args":["-m","x"],"env":{"A":"B"}}'))["response"]
    assert manager.connected
    assert "Deleted MCP server" in asyncio.run(tools.do_manage_mcp('{"action":"delete","server_id":"srv1"}'))["response"]
    assert "Reconnected" in asyncio.run(tools.do_manage_mcp('{"action":"reconnect","server_id":"srv1"}'))["response"]
    assert manager.connected[-1][1] == {
        "server_id": "srv1",
        "name": "Files",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "fileserver"],
        "env": {"ACCOUNT": "demo"},
        "url": None,
    }
    assert "enabled" in asyncio.run(tools.do_manage_mcp('{"action":"enable","server_id":"srv1"}'))["response"]
    assert manager.connected[-1][1]["server_id"] == "srv1"
    assert "disabled" in asyncio.run(tools.do_manage_mcp('{"action":"disable","server_id":"srv1"}'))["response"]
    assert manager.disconnected[-1] == "srv1"
    assert asyncio.run(tools.do_manage_mcp('{"action":"list_tools"}'))["tools"][0]["name"] == "read_file"
    assert asyncio.run(tools.do_manage_mcp('{"action":"bogus"}'))["exit_code"] == 1
    monkeypatch.setattr(tools, "get_mcp_manager", lambda: None)
    assert asyncio.run(tools.do_manage_mcp('{"action":"list"}'))["servers"] == []

    webhook_manager = types.ModuleType("src.webhook_manager")
    webhook_manager.validate_webhook_url = lambda url: url.rstrip("/")
    webhook_manager.validate_events = lambda events: events
    monkeypatch.setitem(sys.modules, "src.webhook_manager", webhook_manager)
    assert asyncio.run(tools.do_manage_webhooks('{"action":"list"}'))["webhooks"][0]["id"] == "wh1"
    assert asyncio.run(tools.do_manage_webhooks('{"action":"add"}'))["error"] == "url is required"
    assert "Added webhook" in asyncio.run(tools.do_manage_webhooks('{"action":"add","url":"http://localhost/hook/"}'))["response"]
    assert "Deleted webhook" in asyncio.run(tools.do_manage_webhooks('{"action":"delete","webhook_id":"wh1"}'))["response"]
    assert "disabled" in asyncio.run(tools.do_manage_webhooks('{"action":"disable","webhook_id":"wh1"}'))["response"]

    monkeypatch.setattr(tools, "offline_mode", lambda: True)
    assert asyncio.run(tools.do_manage_mcp('{"action":"list"}'))["servers"] == []
    assert asyncio.run(tools.do_manage_mcp('{"action":"list_tools"}'))["tools"] == []
    assert asyncio.run(tools.do_manage_mcp('{"action":"add","name":"Blocked","command":"python"}'))["exit_code"] == 1
    assert asyncio.run(tools.do_manage_mcp('{"action":"reconnect","server_id":"srv1"}'))["exit_code"] == 1
    assert asyncio.run(tools.do_manage_mcp('{"action":"enable","server_id":"srv1"}'))["exit_code"] == 1
    assert asyncio.run(tools.do_manage_webhooks('{"action":"list"}'))["webhooks"] == []
    assert asyncio.run(tools.do_manage_webhooks('{"action":"add","url":"https://example.test/hook"}'))["exit_code"] == 1
    assert "External model endpoints are disabled" in asyncio.run(
        tools.do_manage_endpoints('{"action":"add","base_url":"https://api.openai.com/v1"}')
    )["error"]
    assert "Added endpoint" in asyncio.run(
        tools.do_manage_endpoints('{"action":"add","base_url":"http://localhost:11434/v1"}')
    )["response"]
    monkeypatch.setattr(tools, "offline_mode", lambda: False)
    monkeypatch.setattr(tools, "load_features", lambda: (_ for _ in ()).throw(RuntimeError("settings unavailable")))
    assert asyncio.run(tools.do_manage_webhooks('{"action":"list"}'))["webhooks"] == []
    assert asyncio.run(tools.do_manage_webhooks('{"action":"add","url":"https://example.test/hook"}'))["exit_code"] == 1
    assert "External model endpoints are disabled" in asyncio.run(
        tools.do_manage_endpoints('{"action":"add","base_url":"https://api.openai.com/v1"}')
    )["error"]
    assert "Added endpoint" in asyncio.run(
        tools.do_manage_endpoints('{"action":"add","base_url":"http://localhost:11434/v1"}')
    )["response"]
    monkeypatch.setattr(tools, "load_features", lambda: {})

    bcrypt = types.ModuleType("bcrypt")
    bcrypt.gensalt = lambda: b"salt"
    bcrypt.hashpw = lambda value, salt: b"hashed-" + value[:4]
    monkeypatch.setitem(sys.modules, "bcrypt", bcrypt)
    assert asyncio.run(tools.do_manage_tokens('{"action":"list"}'))["tokens"][0]["token_prefix"] == "abcdef12..."
    created = asyncio.run(tools.do_manage_tokens('{"action":"create","name":"Build"}'))
    assert created["response"] == "Created token 'Build'"
    assert created["token"]
    assert "Deleted token" in asyncio.run(tools.do_manage_tokens('{"action":"delete","token_id":"tok1"}'))["response"]


def test_manage_documents_settings_api_and_vault(monkeypatch, tmp_path):
    tools = _tool_module()

    class Document(FakeModel):
        language = Column()

    class ModelEndpoint(FakeModel):
        cached_models = Column()

    now = datetime.utcnow() - timedelta(minutes=5)
    doc = Document(
        id="doc1",
        title="Plan",
        language="markdown",
        current_content="hello world",
        is_active=True,
        updated_at=now,
        created_at=now,
    )
    endpoint = ModelEndpoint(id="ep1", name="Local", cached_models=json.dumps(["GLM-5.2"]), is_enabled=True)
    db = FakeDB({Document: [doc], ModelEndpoint: [endpoint]})
    _install_core_database(monkeypatch, db, Document=Document, ModelEndpoint=ModelEndpoint)

    docs = asyncio.run(tools.do_manage_documents('{"action":"list"}'))
    assert "Found 1 document" in docs["response"]
    read = asyncio.run(tools.do_manage_documents('{"action":"read","document_id":"doc1","limit":5}'))
    assert read["document"]["truncated"] is True
    assert "Deleted document" in asyncio.run(tools.do_manage_documents('{"action":"delete","document_id":"doc1"}'))["response"]
    assert asyncio.run(tools.do_manage_documents('{"action":"unknown"}'))["exit_code"] == 1

    settings_store = {
        "tts_enabled": False,
        "tts_voice": "alloy",
        "search_result_count": 5,
        "image_quality": "medium",
        "default_model": "",
        "default_endpoint_id": "",
        "disabled_tools": [],
        "brave_api_key": "secret",
        "keybinds": {"save": "Ctrl+S"},
    }
    settings_module = types.ModuleType("src.settings")
    settings_module.DEFAULT_SETTINGS = {
        "tts_enabled": False,
        "tts_voice": "alloy",
        "search_result_count": 5,
        "image_quality": "medium",
        "default_model": "",
        "default_endpoint_id": "",
        "disabled_tools": [],
        "brave_api_key": "",
        "keybinds": {"save": "Ctrl+S"},
    }
    settings_module.load_settings = lambda: dict(settings_store)

    def save_settings(new_settings):
        settings_store.clear()
        settings_store.update(new_settings)

    settings_module.save_settings = save_settings
    settings_module.get_setting = lambda key, default=None: settings_store.get(key, default)
    monkeypatch.setitem(sys.modules, "src.settings", settings_module)

    listed = asyncio.run(tools.do_manage_settings('{"action":"list"}'))
    assert listed["settings"]["brave_api_key"].startswith("\u2022")
    assert asyncio.run(tools.do_manage_settings('{"action":"get","key":"voice"}'))["value"] == "alloy"
    assert "Set tts_enabled = True" in asyncio.run(tools.do_manage_settings('{"action":"set","key":"tts","value":"yes"}'))["response"]
    assert asyncio.run(tools.do_manage_settings('{"action":"set","key":"image quality","value":"ultra"}'))["exit_code"] == 1
    assert "credential" in asyncio.run(tools.do_manage_settings('{"action":"set","key":"brave_api_key","value":"x"}'))["response"]
    assert "structured setting" in asyncio.run(tools.do_manage_settings('{"action":"set","key":"keybinds","value":"x"}'))["response"]
    assert "endpoint ep1" in asyncio.run(tools.do_manage_settings('{"action":"set","key":"default model","value":"glm 5.2"}'))["response"].lower()
    assert "Reset tts_voice" in asyncio.run(tools.do_manage_settings('{"action":"reset","key":"voice"}'))["response"]
    assert "Disabled shell" in asyncio.run(tools.do_manage_settings('{"action":"disable_tool","tool":"shell"}'))["response"]
    assert "Enabled shell" in asyncio.run(tools.do_manage_settings('{"action":"enable_tool","tool":"shell"}'))["response"]
    assert "Currently disabled" in asyncio.run(tools.do_manage_settings('{"action":"list_tools"}'))["response"]

    integrations = types.ModuleType("src.integrations")
    integrations.load_integrations = lambda: [{"id": "demo", "name": "Demo", "enabled": True}]

    async def execute_api_call(*args, **kwargs):
        return {"called": args, "kwargs": kwargs, "exit_code": 0}

    integrations.execute_api_call = execute_api_call
    monkeypatch.setitem(sys.modules, "src.integrations", integrations)
    api = asyncio.run(tools.do_api_call("Demo\nPOST /items\n{\"x\":1}"))
    assert api["called"][:3] == ("demo", "POST", "/items")
    assert asyncio.run(tools.do_api_call('{"integration":"missing"}'))["exit_code"] == 1
    monkeypatch.setattr(tools, "offline_mode", lambda: True)
    assert asyncio.run(tools.do_api_call("Demo\nGET /items")) == {
        "error": "Network integrations are disabled in offline mode",
        "exit_code": 1,
    }
    monkeypatch.setattr(tools, "offline_mode", lambda: False)

    monkeypatch.chdir(tmp_path)
    assert tools._load_vault_config() == {}
    monkeypatch.setattr(tools, "_run_bw", lambda *args, **kwargs: asyncio.sleep(0, result=('[{"id":"item123456","name":"Demo","login":{"username":"u","uris":[{"uri":"https://x"}]}}]', "", 0)))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "vault.json").write_text('{"session":"sess"}', encoding="utf-8")
    search = asyncio.run(tools.do_vault_search('{"query":"demo"}'))
    assert "item1234" in search["output"]
    monkeypatch.setattr(tools, "_run_bw", lambda *args, **kwargs: asyncio.sleep(0, result=('{"name":"Demo","login":{"username":"u","password":"p","totp":"t","uris":[{"uri":"https://x"}]},"notes":"n"}', "", 0)))
    got = asyncio.run(tools.do_vault_get('{"item_id":"item123456","reason":"testing"}', owner="alice"))
    assert "Password: p" in got["output"]
    monkeypatch.setattr(tools, "_run_bw", lambda *args, **kwargs: asyncio.sleep(0, result=("session-key", "", 0)))
    assert "Vault unlocked" in asyncio.run(tools.do_vault_unlock('{"master_password":"pw"}'))["output"]
    monkeypatch.setattr(tools, "offline_mode", lambda: True)

    async def fail_bw(*_args, **_kwargs):
        raise AssertionError("offline vault tools must not launch bw")

    monkeypatch.setattr(tools, "_run_bw", fail_bw)
    assert asyncio.run(tools.do_vault_search('{"query":"demo"}')) == {
        "error": "Vault integration is disabled in offline mode",
        "exit_code": 1,
    }
    assert asyncio.run(tools.do_vault_get('{"item_id":"item123456","reason":"testing"}')) == {
        "error": "Vault integration is disabled in offline mode",
        "exit_code": 1,
    }
    assert asyncio.run(tools.do_vault_unlock('{"master_password":"pw"}')) == {
        "error": "Vault integration is disabled in offline mode",
        "exit_code": 1,
    }
    monkeypatch.setattr(tools, "offline_mode", lambda: False)


def test_cookbook_download_tools_blocked_in_offline_mode(monkeypatch):
    tools = _tool_module()

    monkeypatch.setattr(tools, "offline_mode", lambda: True)
    monkeypatch.setattr(tools, "_cookbook_servers", lambda: asyncio.sleep(0, result={"default_host": "gpu-box", "hosts": []}))

    search_result = asyncio.run(tools.do_search_hf_models('{"query":"llama"}'))
    download_result = asyncio.run(tools.do_download_model('{"repo_id":"Team/Model-7B"}'))
    cached_result = asyncio.run(tools.do_list_cached_models('{"host":"gpu-box"}'))
    remote_serve_result = asyncio.run(
        tools.do_serve_model('{"repo_id":"Team/Model-7B","cmd":"vllm serve Team/Model-7B","host":"gpu-box"}')
    )
    default_remote_serve_result = asyncio.run(
        tools.do_serve_model('{"repo_id":"Team/Model-7B","cmd":"vllm serve Team/Model-7B"}')
    )
    install_serve_result = asyncio.run(
        tools.do_serve_model('{"repo_id":"playwright","cmd":"python -m pip install playwright","local":true}')
    )
    download_serve_result = asyncio.run(
        tools.do_serve_model('{"repo_id":"Team/Model-7B","cmd":"hf download Team/Model-7B","local":true}')
    )

    assert search_result == {
        "error": "HuggingFace model search is disabled in offline mode",
        "exit_code": 1,
    }
    assert download_result == {
        "error": "Model downloads are disabled in offline mode",
        "exit_code": 1,
    }
    assert cached_result == {
        "error": "Remote Cookbook servers are disabled in offline mode",
        "exit_code": 1,
    }
    assert remote_serve_result == {
        "error": "Remote Cookbook servers are disabled in offline mode",
        "exit_code": 1,
    }
    assert default_remote_serve_result == {
        "error": "Remote Cookbook servers are disabled in offline mode",
        "exit_code": 1,
    }
    assert install_serve_result == {
        "error": "Dependency installs are disabled in offline mode",
        "exit_code": 1,
    }
    assert download_serve_result == {
        "error": "Network download commands are disabled in offline mode",
        "exit_code": 1,
    }


def test_cookbook_serve_tool_feature_gates(monkeypatch):
    tools = _tool_module()

    monkeypatch.setattr(tools, "offline_mode", lambda: False)
    monkeypatch.setattr(tools, "_cookbook_servers", lambda: asyncio.sleep(0, result={"default_host": "gpu-box", "hosts": []}))
    monkeypatch.setattr(tools, "_cookbook_env_for_host", lambda host: asyncio.sleep(0, result={}))
    monkeypatch.setattr(tools, "_cookbook_register_task", lambda *args, **kwargs: asyncio.sleep(0, result=True))

    monkeypatch.setattr(tools, "load_features", lambda: {"cookbook_remote_servers": False})
    assert asyncio.run(
        tools.do_serve_model('{"repo_id":"Team/Model-7B","cmd":"vllm serve Team/Model-7B","host":"gpu-box"}')
    ) == {"error": "Remote Cookbook servers are disabled in offline mode", "exit_code": 1}

    monkeypatch.setattr(tools, "load_features", lambda: {"cookbook_dependency_installs": False})
    assert asyncio.run(
        tools.do_serve_model('{"repo_id":"playwright","cmd":"python -m pip install playwright","local":true}')
    ) == {"error": "Dependency installs are disabled in offline mode", "exit_code": 1}

    monkeypatch.setattr(tools, "load_features", lambda: {"cookbook_downloads": False})
    assert asyncio.run(
        tools.do_serve_model('{"repo_id":"Team/Model-7B","cmd":"python -c \\"from huggingface_hub import snapshot_download\\"","local":true}')
    ) == {"error": "Network download commands are disabled in offline mode", "exit_code": 1}

    monkeypatch.setattr(tools, "load_features", lambda: (_ for _ in ()).throw(RuntimeError("settings unavailable")))
    assert asyncio.run(
        tools.do_serve_model('{"repo_id":"Team/Model-7B","cmd":"vllm serve Team/Model-7B","host":"gpu-box"}')
    ) == {"error": "Remote Cookbook servers are disabled in offline mode", "exit_code": 1}


def test_manage_tasks_crud_and_run_paths(monkeypatch):
    tools = _tool_module()

    class ScheduledTask(FakeModel):
        status = Column()
        task_type = Column()
        action = Column()
        trigger_type = Column()
        schedule = Column()
        trigger_event = Column()
        trigger_count = Column()
        next_run = Column()
        last_run = Column()
        run_count = Column()

    now = datetime.utcnow()
    task = ScheduledTask(
        id="task-1",
        owner="alice",
        name="Morning",
        status="active",
        task_type="llm",
        action=None,
        trigger_type="schedule",
        schedule="daily",
        scheduled_time="09:00",
        scheduled_day=None,
        trigger_event=None,
        trigger_count=None,
        next_run=now,
        last_run=now - timedelta(days=1),
        run_count=2,
        output_target="session",
        prompt="Report",
    )
    db = FakeDB({ScheduledTask: [task]})
    _install_core_database(monkeypatch, db, ScheduledTask=ScheduledTask)

    listed = asyncio.run(tools.do_manage_tasks('{"action":"list"}', owner="alice"))
    assert listed["tasks"][0]["id"] == "task-1"

    assert asyncio.run(tools.do_manage_tasks('{"action":"create","task_type":"llm"}', owner="alice"))["exit_code"] == 1
    assert asyncio.run(tools.do_manage_tasks('{"action":"create","task_type":"action"}', owner="alice"))["exit_code"] == 1
    created = asyncio.run(
        tools.do_manage_tasks(
            '{"action":"create","task_type":"action","action_name":"cleanup","trigger_type":"event","trigger_event":"startup"}',
            owner="alice",
        )
    )
    assert created["exit_code"] == 0
    assert any(item.name == "cleanup" for item in db.added)

    edited = asyncio.run(
        tools.do_manage_tasks(
            '{"action":"edit","task_id":"task-1","name":"Later","schedule":"daily","scheduled_time":"10:30"}',
            owner="alice",
        )
    )
    assert "Updated task" in edited["response"]
    assert task.name == "Later"
    assert task.scheduled_time == "10:30"

    paused = asyncio.run(tools.do_manage_tasks('{"action":"pause","task_id":"task-1"}', owner="alice"))
    assert "paused" in paused["response"]
    assert task.status == "paused"
    resumed = asyncio.run(tools.do_manage_tasks('{"action":"resume","task_id":"task-1"}', owner="alice"))
    assert "resumed" in resumed["response"]
    assert task.status == "active"

    class Scheduler:
        def __init__(self, value):
            self.value = value
            self.calls = []

        async def run_task_now(self, task_id):
            self.calls.append(task_id)
            return self.value

    event_bus = types.ModuleType("src.event_bus")
    scheduler = Scheduler(True)
    event_bus.get_task_scheduler = lambda: scheduler
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)
    assert "triggered" in asyncio.run(tools.do_manage_tasks('{"action":"run","task_id":"task-1"}', owner="alice"))["response"]
    scheduler.value = False
    assert asyncio.run(tools.do_manage_tasks('{"action":"run","task_id":"task-1"}', owner="alice"))["exit_code"] == 1
    event_bus.get_task_scheduler = lambda: None
    assert asyncio.run(tools.do_manage_tasks('{"action":"run","task_id":"task-1"}', owner="alice"))["error"] == "Task scheduler not available"

    foreign = ScheduledTask(id="task-2", owner="bob", name="Foreign", status="active")
    db.data[ScheduledTask].insert(0, foreign)
    assert asyncio.run(tools.do_manage_tasks('{"action":"delete","task_id":"task-2"}', owner="alice"))["error"] == "Access denied"
    db.data[ScheduledTask].remove(foreign)
    legacy = ScheduledTask(id="task-legacy", owner=None, name="Legacy", status="active")
    db.data[ScheduledTask].insert(0, legacy)
    assert asyncio.run(tools.do_manage_tasks('{"action":"edit","task_id":"task-legacy","name":"Nope"}', owner="alice"))["error"] == "Access denied"
    assert asyncio.run(tools.do_manage_tasks('{"action":"run","task_id":"task-legacy"}', owner="alice"))["error"] == "Access denied"
    assert legacy.name == "Legacy"
    db.data[ScheduledTask].remove(legacy)
    deleted = asyncio.run(tools.do_manage_tasks('{"action":"delete","task_id":"task-1"}', owner="alice"))
    assert "Deleted task" in deleted["response"]
    assert task in db.deleted

    assert asyncio.run(tools.do_manage_tasks("{"))["error"] == "Invalid JSON arguments"
    assert asyncio.run(tools.do_manage_tasks('{"action":"bogus"}'))["exit_code"] == 1


def test_manage_notes_crud_checklists_and_duplicates(monkeypatch):
    tools = _tool_module()

    class Note(FakeModel):
        content = Column()
        items = Column()
        note_type = Column()
        color = Column()
        label = Column()
        pinned = Column()
        due_date = Column()

    import sqlalchemy.orm.attributes as sa_attrs

    monkeypatch.setattr(sa_attrs, "flag_modified", lambda obj, key: None)

    checklist = Note(
        id="note-check",
        owner="alice",
        title="Groceries",
        content="",
        items=json.dumps([{"text": "Milk", "done": False}]),
        note_type="checklist",
        color=None,
        label="home",
        pinned=True,
        archived=False,
        due_date=None,
        updated_at=datetime.utcnow(),
    )
    plain = Note(
        id="note-plain",
        owner="alice",
        title="Idea",
        content="Line one\nLine two",
        items=None,
        note_type="note",
        color=None,
        label="work",
        pinned=False,
        archived=False,
        due_date="2026-01-01T10:00:00",
        updated_at=datetime.utcnow() - timedelta(hours=1),
    )
    db = FakeDB({Note: [checklist, plain]})
    _install_core_database(monkeypatch, db, Note=Note)

    calendar_routes = types.ModuleType("routes.calendar_routes")
    calendar_routes.parse_due_for_user = lambda raw: "2026-01-01T10:00:00"
    monkeypatch.setitem(sys.modules, "routes.calendar_routes", calendar_routes)

    listed = asyncio.run(tools.do_manage_notes('{"action":"list","label":"home"}', owner="alice"))
    assert "Groceries" in listed["results"]
    assert "[ ] 0: Milk" in listed["results"]

    duplicate = asyncio.run(
        tools.do_manage_notes(
            '{"action":"add","title":"Reminder: Idea","due_date":"tomorrow 10am"}',
            owner="alice",
        )
    )
    assert duplicate["duplicate"] is True

    created = asyncio.run(
        tools.do_manage_notes(
            '{"action":"create","text":"Capture this","items":[{"text":"Step","done":false}],"pinned":true}',
            owner="alice",
        )
    )
    assert "Note created" in created["response"]
    assert any(note.title == "Capture this" for note in db.added)

    updated = asyncio.run(
        tools.do_manage_notes(
            '{"action":"update","id":"note-check","title":"Groceries updated","items":[{"text":"Milk","done":true}],"archived":true}',
            owner="alice",
        )
    )
    assert "Groceries updated" in updated["response"]
    assert checklist.archived is True

    checklist.archived = False
    toggled = asyncio.run(tools.do_manage_notes('{"action":"toggle_item","id":"note-check","index":0}', owner="alice"))
    assert "marked undone" in toggled["response"]
    assert json.loads(checklist.items)[0]["done"] is False
    assert asyncio.run(tools.do_manage_notes('{"action":"toggle_item","id":"note-check","index":99}', owner="alice"))["exit_code"] == 1

    foreign = Note(id="note-foreign", owner="bob", title="Private", content="", items=None, note_type="note", archived=False, pinned=False, updated_at=datetime.utcnow())
    db.data[Note].insert(0, foreign)
    assert asyncio.run(tools.do_manage_notes('{"action":"delete","id":"note-foreign"}', owner="alice"))["error"] == "Note not found"
    db.data[Note].remove(foreign)
    legacy = Note(id="note-legacy", owner=None, title="Legacy", content="", items=None, note_type="note", archived=False, pinned=False, updated_at=datetime.utcnow())
    db.data[Note].insert(0, legacy)
    assert asyncio.run(tools.do_manage_notes('{"action":"update","id":"note-legacy","title":"Nope"}', owner="alice"))["error"] == "Note not found"
    assert asyncio.run(tools.do_manage_notes('{"action":"delete","id":"note-legacy"}', owner="alice"))["error"] == "Note not found"
    assert legacy.title == "Legacy"
    db.data[Note].remove(legacy)
    deleted = asyncio.run(tools.do_manage_notes('{"action":"delete","id":"note-plain"}', owner="alice"))
    assert "Deleted note" in deleted["response"]
    assert plain in db.deleted

    assert asyncio.run(tools.do_manage_notes('{"action":"toggle_item","id":"note-plain"}', owner="alice"))["exit_code"] == 1
    assert asyncio.run(tools.do_manage_notes("{"))["error"] == "Invalid JSON arguments"
    assert asyncio.run(tools.do_manage_notes('{"action":"unknown"}'))["exit_code"] == 1
