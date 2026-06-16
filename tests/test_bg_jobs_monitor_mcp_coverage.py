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
    monkeypatch.setattr(bg_jobs.time, "time", lambda: 200.0)
    assert rec["id"] not in bg_jobs.refresh()
    assert bg_jobs.get("missing") is None


def test_bg_jobs_cmd_launch_timeout_died_truncate_and_bad_store(bg_jobs_env, monkeypatch):
    bg_jobs = bg_jobs_env

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

    bg_jobs._STORE.write_text("{", encoding="utf-8")
    assert bg_jobs._load() == {}


@pytest.mark.asyncio
async def test_bg_monitor_drain_run_followup_loop_and_start(monkeypatch):
    import src.bg_monitor as monitor

    async def stream_agent_loop(*args, **kwargs):
        yield "event: ping\n\n"
        yield "data: {}\n\n"
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
    monkeypatch.setattr(agent_runs, "is_active", lambda sid: True)
    assert await monitor._run_followup({"id": "job4", "session_id": "s1"}) is False

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
