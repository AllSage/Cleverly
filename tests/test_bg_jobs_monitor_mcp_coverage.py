import asyncio
import importlib
import json
import sys
import types
from types import SimpleNamespace

import pytest


@pytest.fixture()
def bg_jobs_env(monkeypatch, tmp_path):
    import src.bg_jobs as bg_jobs

    monkeypatch.setattr(bg_jobs, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", tmp_path / "bg_jobs")
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "bg_jobs.json")
    monkeypatch.setattr(bg_jobs, "_RETENTION_S", 10)
    monkeypatch.setattr(bg_jobs.time, "time", lambda: 100.0)
    return bg_jobs


def test_bg_jobs_launch_refresh_get_prune_and_result_text(bg_jobs_env, monkeypatch):
    bg_jobs = bg_jobs_env
    popen_calls = []

    class Proc:
        pid = 4321

    monkeypatch.setattr(bg_jobs, "find_bash", lambda: "bash")
    monkeypatch.setattr(bg_jobs, "detached_popen_kwargs", lambda: {"start_new_session": True})
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)) or Proc())

    rec = bg_jobs.launch("echo hello", session_id="s1", cwd="C:/work", max_runtime_s=5)
    assert rec["status"] == "running"
    assert rec["pid"] == 4321
    assert popen_calls[0][1]["cwd"] == "C:/work"
    assert popen_calls[0][1]["start_new_session"] is True
    assert bg_jobs._load()[rec["id"]]["command"] == "echo hello"

    log_path = bg_jobs.Path(rec["log_path"])
    exit_path = bg_jobs.Path(rec["exit_path"])
    log_path.write_text("hello\n", encoding="utf-8")
    exit_path.write_text("0\n", encoding="utf-8")
    refreshed = bg_jobs.refresh()
    done = refreshed[rec["id"]]
    assert done["status"] == "done"
    assert done["exit_code"] == 0
    assert done["ended_at"] == 100.0
    assert bg_jobs.pending_followups()[0]["id"] == rec["id"]

    got = bg_jobs.get(rec["id"])
    assert got["output"] == "hello\n"
    assert "finished with exit code 0" in bg_jobs.result_text(got)

    bg_jobs.mark_followed_up(rec["id"])
    real_unlink = bg_jobs.Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self.parent == bg_jobs._JOBS_DIR and self.name.startswith(f"{rec['id']}."):
            raise OSError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(bg_jobs.Path, "unlink", flaky_unlink)
    monkeypatch.setattr(bg_jobs.time, "time", lambda: 200.0)
    assert rec["id"] not in bg_jobs.refresh()
    assert bg_jobs.get("missing") is None


def test_bg_jobs_cmd_launch_timeout_died_truncate_and_bad_store(bg_jobs_env, monkeypatch):
    bg_jobs = bg_jobs_env

    monkeypatch.setattr(bg_jobs, "pid_alive", lambda pid: pid == 123)
    assert bg_jobs._pid_alive(123) is True

    monkeypatch.setattr(bg_jobs, "find_bash", lambda: None)
    monkeypatch.setattr(bg_jobs.os, "environ", {"ComSpec": "cmd.exe"})
    monkeypatch.setattr(bg_jobs, "detached_popen_kwargs", lambda: {})

    class Proc:
        pid = 88

    monkeypatch.setattr(bg_jobs.subprocess, "Popen", lambda *args, **kwargs: Proc())
    rec = bg_jobs.launch("bad command", session_id="s2", max_runtime_s=1)
    assert bg_jobs.Path(rec["exit_path"]).name.endswith(".exit")

    kills = []
    monkeypatch.setattr(bg_jobs, "kill_process_tree", lambda pid: kills.append(pid))
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(bg_jobs.time, "time", lambda: 200.0)
    timed_out = bg_jobs.refresh()[rec["id"]]
    assert timed_out["status"] == "failed"
    assert timed_out["timed_out"] is True
    assert kills == [88]
    assert "timed out" in bg_jobs.result_text(timed_out)

    rec2 = dict(rec, id="died", pid=99, started_at=199.0, exit_path=str(bg_jobs._JOBS_DIR / "died.exit"), log_path=str(bg_jobs._JOBS_DIR / "died.log"))
    bg_jobs._save({"died": rec2})
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: False)
    died = bg_jobs.refresh()["died"]
    assert died["died"] is True
    assert "died unexpectedly" in bg_jobs.result_text(died)

    huge = "A" * (bg_jobs._MAX_OUTPUT_CHARS + 20)
    bg_jobs.Path(died["log_path"]).write_text(huge, encoding="utf-8")
    output = bg_jobs.get("died")["output"]
    assert "truncated" in output
    assert len(output) < len(huge)

    bad_exit = dict(
        rec,
        id="bad-exit",
        pid=100,
        started_at=199.0,
        session_id="s2",
        exit_path=str(bg_jobs._JOBS_DIR / "bad-exit.exit"),
        log_path=str(bg_jobs._JOBS_DIR / "bad-exit.log"),
    )
    other_session = dict(bad_exit, id="other", session_id="other", exit_path=str(bg_jobs._JOBS_DIR / "other.exit"))
    bg_jobs.Path(bad_exit["exit_path"]).write_text("not-an-int", encoding="utf-8")
    bg_jobs._save({"bad-exit": bad_exit, "other": other_session})
    bad_exit_refreshed = bg_jobs.refresh()["bad-exit"]
    assert bad_exit_refreshed["exit_code"] == 1
    assert bad_exit_refreshed["status"] == "failed"
    assert [job["id"] for job in bg_jobs.list_for_session("s2")] == ["bad-exit"]

    bg_jobs._STORE.write_text("{", encoding="utf-8")
    assert bg_jobs._load() == {}


@pytest.mark.asyncio
async def test_bg_monitor_drain_run_followup_loop_and_start(monkeypatch):
    import src.bg_monitor as monitor

    async def stream_agent_loop(*args, **kwargs):
        yield "event: ping\n\n"
        yield "data: {}\n\n"
        yield "data: not json\n\n"
        yield "data: []\n\n"
        yield 'data: {"delta":"hello "}\n\n'
        yield 'data: {"type":"agent_step","round":3}\n\n'
        yield 'data: {"type":"tool_output","tool":"bash","command":"echo","output":"ok","exit_code":0}\n\n'
        yield 'data: {"delta":"world"}\n\n'
        yield "data: [DONE]\n\n"

    agent_loop = types.ModuleType("src.agent_loop")
    agent_loop.stream_agent_loop = stream_agent_loop
    monkeypatch.setitem(sys.modules, "src.agent_loop", agent_loop)

    sess = SimpleNamespace(
        id="s1",
        endpoint_url="http://local",
        model="model",
        headers={"H": "1"},
        context_length=2048,
        owner="alice",
        get_context_messages=lambda: [{"role": "user", "content": "previous"}],
    )

    class Manager:
        def __init__(self):
            self.messages = []
            self.saved = False

        def get_session(self, sid):
            if sid == "gone":
                raise KeyError(sid)
            if sid == "none":
                return None
            return sess

        def add_message(self, sid, message):
            self.messages.append((sid, message))

        def save_sessions(self):
            self.saved = True

    manager = Manager()
    ai = types.ModuleType("src.ai_interaction")
    ai.get_session_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "src.ai_interaction", ai)

    models = types.ModuleType("core.models")
    models.ChatMessage = lambda role, content, metadata=None: SimpleNamespace(role=role, content=content, metadata=metadata)
    monkeypatch.setitem(sys.modules, "core.models", models)
    monkeypatch.setattr(monitor.bg_jobs, "result_text", lambda rec: "job result")

    full, tool_events = await monitor._drain_agent(sess, [{"role": "user", "content": "go"}])
    assert full == "hello world"
    assert tool_events == [{"round": 3, "tool": "bash", "command": "echo", "output": "ok", "exit_code": 0}]

    import src.agent_runs as agent_runs

    monkeypatch.setattr(agent_runs, "is_active", lambda sid: False)
    assert await monitor._run_followup({"id": "job1", "session_id": "s1"}) is True
    assert manager.messages[0][1].content == "hello world"
    assert manager.messages[0][1].metadata["bg_job_id"] == "job1"
    assert manager.saved is True

    ai.get_session_manager = lambda: None
    assert await monitor._run_followup({"id": "job2", "session_id": "s1"}) is False
    ai.get_session_manager = lambda: manager
    assert await monitor._run_followup({"id": "job3", "session_id": "gone"}) is True
    assert await monitor._run_followup({"id": "job3b", "session_id": "none"}) is True
    monkeypatch.setattr(agent_runs, "is_active", lambda sid: True)
    assert await monitor._run_followup({"id": "job4", "session_id": "s1"}) is False
    monkeypatch.setattr(agent_runs, "is_active", lambda sid: (_ for _ in ()).throw(RuntimeError("busy check failed")))
    assert await monitor._run_followup({"id": "job4b", "session_id": "s1"}) is True

    pending = [{"id": "job5", "session_id": "gone"}]
    marked = []
    monkeypatch.setattr(monitor.bg_jobs, "pending_followups", lambda: list(pending))
    monkeypatch.setattr(monitor.bg_jobs, "mark_followed_up", lambda jid: marked.append(jid))

    async def stop_after_tick(_delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(monitor.asyncio, "sleep", stop_after_tick)
    with pytest.raises(asyncio.CancelledError):
        await monitor._loop()
    assert marked == ["job5"]

    async def run_followup_raises(_rec):
        raise RuntimeError("followup failed")

    monkeypatch.setattr(monitor, "_run_followup", run_followup_raises)
    monkeypatch.setattr(monitor.bg_jobs, "pending_followups", lambda: [{"id": "bad", "session_id": "s1"}])
    with pytest.raises(asyncio.CancelledError):
        await monitor._loop()
    assert marked == ["job5"]

    monkeypatch.setattr(monitor.bg_jobs, "pending_followups", lambda: (_ for _ in ()).throw(RuntimeError("store failed")))
    with pytest.raises(asyncio.CancelledError):
        await monitor._loop()

    original_sleep = asyncio.sleep
    monkeypatch.setattr(monitor.asyncio, "sleep", lambda delay: original_sleep(999))
    task = monitor.start_bg_monitor()
    assert monitor.start_bg_monitor() is task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_mcp_manager_call_tools_statuses_and_schemas(monkeypatch):
    import src.mcp_manager as mcp_manager

    mgr = mcp_manager.McpManager()
    assert await mgr.connect_server("s", "Server", "bogus") is False
    assert mgr.get_server_status("s")["status"] == "disconnected"

    class Result:
        isError = False
        content = [
            SimpleNamespace(text="plain"),
            SimpleNamespace(type="image", data="base64", mimeType="image/jpeg"),
            SimpleNamespace(data={"x": 1}),
        ]

    class Session:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            return Result()

    session = Session()
    mgr._sessions["remote"] = session
    mgr._connections["remote"] = {"name": "Remote", "identity": "acct@example.com"}
    mgr._tools["remote"] = [{"name": "search", "description": "Search things", "input_schema": {"type": "object"}}]
    result = await mgr.call_tool("mcp__remote__search", {"q": "x"})
    assert result["stdout"].startswith("plain")
    assert result["exit_code"] == 0
    assert result["images"] == [{"data": "base64", "mimeType": "image/jpeg"}]
    assert session.calls == [("search", {"q": "x"})]

    assert await mgr.call_tool("bad", {}) == {"error": "Invalid MCP tool name: bad", "exit_code": 1}
    assert await mgr.call_tool("mcp__missing__tool", {}) == {"error": "MCP server not connected: missing", "exit_code": 1}

    class ErrorSession:
        async def call_tool(self, name, args):
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="failed")])

    assert (await mgr._do_call(ErrorSession(), "x", {})) == {"stdout": "", "stderr": "failed", "exit_code": 1}

    schemas = mgr.get_all_openai_schemas()
    assert schemas[0]["function"]["name"] == "mcp__remote__search"
    assert "acct@example.com" in schemas[0]["function"]["description"]
    assert mgr.get_all_tools({"remote": {"search"}})[0]["is_disabled"] is True
    desc = mgr.get_tool_descriptions_for_prompt()
    assert "mcp__remote__search" in desc
    assert mgr.get_tool_descriptions_for_prompt() == desc
    assert mgr.get_all_statuses()["remote"]["name"] == "Remote"

    mgr._tools["memory"] = [{"name": "list", "description": "List memories", "input_schema": {}}]
    assert all("memory" not in item["function"]["name"] for item in mgr.get_all_openai_schemas())

    class Stack:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    stack = Stack()
    mgr._sessions["remote"] = session
    mgr._stacks["remote"] = stack
    await mgr.disconnect_server("remote")
    assert stack.closed is True
    assert "remote" not in mgr._sessions


@pytest.mark.asyncio
async def test_mcp_manager_reconnect_and_database_connect(monkeypatch):
    import src.mcp_manager as mcp_manager

    mgr = mcp_manager.McpManager()

    class CrashingSession:
        async def call_tool(self, name, args):
            raise RuntimeError("crashed")

    mgr._sessions["builtin_browser"] = CrashingSession()
    reconnects = []

    async def reconnect(server_id):
        reconnects.append(server_id)
        mgr._sessions[server_id] = SimpleNamespace(call_tool=lambda name, args: None)
        return False

    monkeypatch.setattr(mgr, "_reconnect_builtin", reconnect)
    failed = await mgr.call_tool("mcp__builtin_browser__click", {})
    assert failed["exit_code"] == 1
    assert reconnects == ["builtin_browser"]

    class Db:
        def __init__(self):
            self.closed = False

        def query(self, model):
            return SimpleNamespace(filter=lambda *_: SimpleNamespace(all=lambda: [
                SimpleNamespace(
                    id="srv",
                    name="Srv",
                    transport="stdio",
                    command="cmd",
                    args='["--flag"]',
                    env='{"ACCOUNT":"demo"}',
                    url=None,
                )
            ]))

        def close(self):
            self.closed = True

    db = Db()
    database = types.ModuleType("src.database")
    database.SessionLocal = lambda: db
    database.McpServer = SimpleNamespace(is_enabled=True)
    monkeypatch.setitem(sys.modules, "src.database", database)
    connected = []

    async def connect_server(**kwargs):
        connected.append(kwargs)
        return True

    monkeypatch.setattr(mgr, "connect_server", connect_server)
    await mgr.connect_all_enabled()
    assert connected[0]["args"] == ["--flag"]
    assert connected[0]["env"] == {"ACCOUNT": "demo"}
    assert db.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offline", "features"),
    [
        (True, {"mcp": True}),
        (False, {"mcp": False}),
    ],
)
async def test_mcp_manager_connect_all_enabled_skips_when_offline_or_disabled(monkeypatch, offline, features):
    import src.mcp_manager as mcp_manager

    mgr = mcp_manager.McpManager()

    settings = types.ModuleType("src.settings")
    settings.offline_mode = lambda: offline
    settings.load_features = lambda: dict(features)
    monkeypatch.setitem(sys.modules, "src.settings", settings)

    database = types.ModuleType("src.database")

    def fail_session_local():
        raise AssertionError("offline MCP startup should not query configured servers")

    database.SessionLocal = fail_session_local
    database.McpServer = SimpleNamespace(is_enabled=True)
    monkeypatch.setitem(sys.modules, "src.database", database)

    async def fail_connect_server(**_kwargs):
        raise AssertionError("offline MCP startup should not connect configured servers")

    monkeypatch.setattr(mgr, "connect_server", fail_connect_server)

    await mgr.connect_all_enabled()


@pytest.mark.asyncio
async def test_mcp_manager_connect_transports_reconnect_and_prompt_edges(monkeypatch):
    import src.mcp_manager as mcp_manager

    mgr = mcp_manager.McpManager()

    class ToolWithSchema:
        name = "tool"
        description = None
        inputSchema = {"type": "object", "properties": {"x": {"type": "string"}}}

    class ToolWithoutSchema:
        name = "plain"
        description = "Plain tool"

    class Session:
        def __init__(self, read, write):
            self.read = read
            self.write = write
            self.initialized = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def initialize(self):
            self.initialized = True

        async def list_tools(self):
            return SimpleNamespace(tools=[ToolWithSchema(), ToolWithoutSchema()])

    class Params:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class TransportContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_exc):
            return False

    stdio_calls = []
    sse_calls = []
    mcp_module = types.ModuleType("mcp")
    mcp_module.ClientSession = Session
    mcp_module.StdioServerParameters = Params
    stdio_module = types.ModuleType("mcp.client.stdio")
    stdio_module.stdio_client = lambda params: stdio_calls.append(params) or TransportContext(("read", "write"))
    sse_module = types.ModuleType("mcp.client.sse")
    sse_module.sse_client = lambda url: sse_calls.append(url) or TransportContext(("sse-read", "sse-write"))
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_module)

    assert await mgr.connect_server(
        "stdio",
        "Stdio",
        "stdio",
        command="cmd",
        args=["--flag"],
        env={"EMAIL_ADDRESS": "a@example.com"},
    ) is True
    assert stdio_calls[0].kwargs["command"] == "cmd"
    assert stdio_calls[0].kwargs["args"] == ["--flag"]
    assert stdio_calls[0].kwargs["env"]["EMAIL_ADDRESS"] == "a@example.com"
    assert mgr.get_server_status("stdio")["identity"] == "a@example.com"
    assert mgr._tools["stdio"][0]["input_schema"]["type"] == "object"
    assert mgr._tools["stdio"][1]["input_schema"] == {}

    assert await mgr.connect_server("sse", "SSE", "sse", url="http://mcp") is True
    assert sse_calls == ["http://mcp"]
    assert mgr.get_server_status("sse")["transport"] == "sse"

    monkeypatch.setitem(sys.modules, "mcp", None)
    assert await mgr._connect_stdio("no-stdio", "No Stdio", "cmd", [], {}) is False
    assert mgr.get_server_status("no-stdio")["error"] == "mcp package not installed"
    assert await mgr._connect_sse("no-sse", "No SSE", "http://mcp") is False
    assert mgr.get_server_status("no-sse")["error"] == "mcp package not installed"

    async def raises_connect(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mgr, "_connect_stdio", raises_connect)
    assert await mgr.connect_server("bad", "Bad", "stdio", command="cmd") is False
    assert mgr.get_server_status("bad")["error"] == "boom"

    class BadStack:
        async def aclose(self):
            raise RuntimeError("close bad")

    mgr._sessions["badstack"] = object()
    mgr._tools["badstack"] = []
    mgr._connections["badstack"] = {"name": "Bad Stack"}
    mgr._stacks["badstack"] = BadStack()
    await mgr.disconnect_server("badstack")
    assert mgr.get_server_status("badstack") == {"status": "disconnected"}

    mgr._sessions["a"] = object()
    mgr._sessions["b"] = object()
    disconnected = []

    async def disconnect(server_id):
        disconnected.append(server_id)
        mgr._sessions.pop(server_id, None)

    monkeypatch.setattr(mgr, "disconnect_server", disconnect)
    await mgr.disconnect_all()
    assert disconnected == ["stdio", "sse", "a", "b"]

    class FailingSession:
        async def call_tool(self, _name, _args):
            raise RuntimeError("failed")

    mgr._sessions["remote"] = FailingSession()
    assert (await mgr.call_tool("mcp__remote__tool", {})) == {"error": "failed", "exit_code": 1}

    mgr._sessions["builtin_browser"] = FailingSession()

    async def reconnect_no_session(server_id):
        mgr._sessions.pop(server_id, None)
        return True

    monkeypatch.setattr(mgr, "_reconnect_builtin", reconnect_no_session)
    assert await mgr.call_tool("mcp__builtin_browser__tool", {}) == {
        "error": "Reconnected but no session for builtin_browser",
        "exit_code": 1,
    }

    class StillFailingSession:
        async def call_tool(self, _name, _args):
            raise RuntimeError("still failed")

    async def reconnect_still_fails(server_id):
        mgr._sessions[server_id] = StillFailingSession()
        return True

    mgr._sessions["builtin_browser"] = FailingSession()
    monkeypatch.setattr(mgr, "_reconnect_builtin", reconnect_still_fails)
    assert await mgr.call_tool("mcp__builtin_browser__tool", {}) == {"error": "still failed", "exit_code": 1}

    builtin_mcp = types.ModuleType("src.builtin_mcp")
    builtin_mcp._BUILTIN_SERVERS = {"builtin_browser": ("mcp_servers/browser.py", "Browser")}
    monkeypatch.setitem(sys.modules, "src.builtin_mcp", builtin_mcp)
    reconnect_mgr = mcp_manager.McpManager()
    assert await reconnect_mgr._reconnect_builtin("missing") is False
    reconnect_args = []

    async def reconnect_connect_server(**kwargs):
        reconnect_args.append(kwargs)
        return True

    monkeypatch.setattr(reconnect_mgr, "connect_server", reconnect_connect_server)
    assert await reconnect_mgr._reconnect_builtin("builtin_browser") is True
    assert reconnect_args[0]["name"] == "Browser"
    assert reconnect_args[0]["env"]["PYTHONPATH"].endswith("cleverly")

    async def reconnect_connect_raises(**_kwargs):
        raise RuntimeError("connect bad")

    monkeypatch.setattr(reconnect_mgr, "connect_server", reconnect_connect_raises)
    assert await reconnect_mgr._reconnect_builtin("builtin_browser") is False

    schema_mgr = mcp_manager.McpManager()
    assert schema_mgr.get_tool_descriptions_for_prompt() == ""
    schema_mgr._connections["builtin_browser"] = {"name": "Browser", "identity": "local"}
    schema_mgr._connections["memory"] = {"name": "Memory"}
    schema_mgr._tools["builtin_browser"] = [
        {"name": "shot", "description": "x" * 130, "input_schema": {"type": "object"}},
        {"name": "skip", "description": "Skip", "input_schema": {}},
    ]
    schema_mgr._tools["memory"] = [{"name": "list", "description": "List", "input_schema": {}}]
    schemas = schema_mgr.get_all_openai_schemas({"builtin_browser": {"skip"}})
    assert [s["function"]["name"] for s in schemas] == ["mcp__builtin_browser__shot"]
    prompt = schema_mgr.get_tool_descriptions_for_prompt({"builtin_browser": {"skip"}})
    assert "Browser (local)" in prompt
    assert "..." in prompt
    assert schema_mgr.get_tool_descriptions_for_prompt({"builtin_browser": {"shot", "skip"}}) == ""
