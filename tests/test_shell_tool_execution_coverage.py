import asyncio
import datetime as dt
import sys
import types
from types import SimpleNamespace

import pytest


class FakeStream:
    def __init__(self, *lines):
        self.lines = [line if isinstance(line, bytes) else line.encode() for line in lines]

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return b""


class HangingStream:
    async def readline(self):
        await asyncio.sleep(1)
        return b""


class FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.stdout = FakeStream()
        self.stderr = FakeStream()
        self.killed = False
        self.waited = False

    async def communicate(self):
        return self._stdout, self._stderr

    async def wait(self):
        self.waited = True
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_shell_execute_success_truncates_and_preserves_exit_code(monkeypatch):
    from services.shell.service import ShellService

    proc = FakeProc(stdout=b"hello world", stderr=b"warning text", returncode=7)

    async def create_shell(command, stdout=None, stderr=None, cwd=None):
        assert command == "demo"
        assert cwd == "C:/work"
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_shell)

    result = await ShellService(max_output=5).execute("demo", cwd="C:/work")

    assert result.stdout == "hello"
    assert result.stderr == "warni"
    assert result.exit_code == 7
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_shell_execute_timeout_kills_process(monkeypatch):
    from services.shell.service import ShellService

    class SlowProc(FakeProc):
        async def communicate(self):
            await asyncio.sleep(1)
            return b"", b""

    proc = SlowProc()

    async def create_shell(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_shell)

    result = await ShellService().execute("slow", timeout=0.001)

    assert result.timed_out is True
    assert result.exit_code == -1
    assert "timed out" in result.stderr
    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_shell_execute_timeout_ignores_process_lookup(monkeypatch):
    from services.shell.service import ShellService

    class GoneProc(FakeProc):
        async def communicate(self):
            await asyncio.sleep(1)
            return b"", b""

        def kill(self):
            raise ProcessLookupError()

    async def create_shell(*args, **kwargs):
        return GoneProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_shell)

    result = await ShellService().execute("gone", timeout=0.001)

    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_shell_execute_reports_spawn_errors(monkeypatch):
    from services.shell.service import ShellService

    async def fail_spawn(*args, **kwargs):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_spawn)

    result = await ShellService().execute("bad")

    assert result.exit_code == -1
    assert result.stderr == "spawn failed"


@pytest.mark.asyncio
async def test_shell_stream_yields_stdout_stderr_and_exit(monkeypatch):
    from services.shell.service import ShellService

    proc = FakeProc(returncode=3)
    proc.stdout = FakeStream(b"out1\n", b"out2\n")
    proc.stderr = FakeStream(b"err1\n")

    async def create_shell(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_shell)

    events = [event async for event in ShellService().stream("stream")]

    assert {"stream": "stdout", "data": "out1"} in events
    assert {"stream": "stdout", "data": "out2"} in events
    assert {"stream": "stderr", "data": "err1"} in events
    assert events[-1] == {"exit_code": 3}


@pytest.mark.asyncio
async def test_shell_stream_timeout_kills_process(monkeypatch):
    from services.shell.service import ShellService

    proc = FakeProc()
    proc.stdout = HangingStream()
    proc.stderr = HangingStream()

    async def create_shell(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_shell)

    events = [event async for event in ShellService().stream("slow", timeout=0.001)]

    assert events[-2]["stream"] == "stderr"
    assert "timed out" in events[-2]["data"]
    assert events[-1] == {"exit_code": -1}
    assert proc.killed is True


@pytest.mark.asyncio
async def test_shell_stream_timeout_ignores_gone_process_and_reports_spawn_errors(monkeypatch):
    from services.shell.service import ShellService

    class GoneProc(FakeProc):
        def kill(self):
            raise ProcessLookupError()

    gone = GoneProc()
    gone.stdout = HangingStream()
    gone.stderr = HangingStream()

    async def create_gone(*args, **kwargs):
        return gone

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_gone)
    timeout_events = [event async for event in ShellService().stream("gone", timeout=0.001)]
    assert timeout_events[-1] == {"exit_code": -1}
    assert "timed out" in timeout_events[-2]["data"]

    async def fail_spawn(*args, **kwargs):
        raise RuntimeError("stream spawn failed")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_spawn)
    error_events = [event async for event in ShellService().stream("bad")]
    assert error_events == [
        {"stream": "stderr", "data": "stream spawn failed"},
        {"exit_code": -1},
    ]


def test_tool_execution_parsers_and_format_branches():
    import src.tool_execution as tool_execution

    assert tool_execution._truncate("abcdef", 3) == "abc\n... (truncated, 6 chars total)"
    assert tool_execution._build_mcp_args("generate_image", "draw\nmodel\n1024x1024\nhigh") == {
        "prompt": "draw",
        "model": "model",
        "size": "1024x1024",
        "quality": "high",
    }
    assert tool_execution._build_mcp_args("write_file", "a.txt\nbody") == {
        "path": "a.txt",
        "content": "body",
    }
    assert tool_execution._build_mcp_args("manage_memory", "add\nRemember this\nFact") == {
        "action": "add",
        "text": "Remember this",
        "category": "fact",
    }
    assert tool_execution._build_mcp_args("manage_memory", "edit\nm1\nnew text") == {
        "action": "edit",
        "memory_id": "m1",
        "text": "new text",
    }
    assert tool_execution._build_mcp_args("manage_memory", "delete\nm1") == {
        "action": "delete",
        "memory_id": "m1",
    }
    assert tool_execution._build_mcp_args("manage_memory", "search\nneedle") == {
        "action": "search",
        "text": "needle",
    }
    assert tool_execution._build_mcp_args("manage_memory", "list\nfacts") == {
        "action": "list",
        "category": "facts",
    }
    assert tool_execution._split_bg_marker("\n# bg\npython job.py") == (True, "python job.py")
    assert tool_execution._split_bg_marker("python job.py") == (False, "python job.py")

    assert "**stdout:**" in tool_execution.format_tool_result(
        "cmd",
        {"stdout": "out", "stderr": "err", "exit_code": 2},
    )
    assert "File written: p" in tool_execution.format_tool_result(
        "write",
        {"success": True, "path": "p", "size": 4},
    )
    assert "Error: bad" in tool_execution.format_tool_result(
        "write",
        {"success": False, "error": "bad"},
    )
    assert "Document created" in tool_execution.format_tool_result(
        "doc",
        {"action": "create", "title": "T", "doc_id": "d1", "version": 1},
    )
    assert "Document updated" in tool_execution.format_tool_result(
        "doc",
        {"action": "update", "title": "T", "version": 2},
    )
    assert "Document edited" in tool_execution.format_tool_result(
        "doc",
        {"action": "edit", "title": "T", "version": 3, "applied": 1},
    )
    assert "**Error:** failed" in tool_execution.format_tool_result(
        "bad",
        {"error": "failed"},
    )
    assert '"extra": 1' in tool_execution.format_tool_result(
        "extra",
        {"response": "ok", "extra": 1},
    )


@pytest.mark.asyncio
async def test_execute_tool_block_misformatted_disabled_unknown_and_mcp(monkeypatch):
    import src.tool_execution as tool_execution

    bad_block = SimpleNamespace(tool_type="python", content='{"tool":"bash"}')
    desc, result = await tool_execution.execute_tool_block(bad_block)
    assert desc == "python: misformatted tool call"
    assert result["exit_code"] == 1

    disabled_block = SimpleNamespace(tool_type="search_chats", content="anything")
    desc, result = await tool_execution.execute_tool_block(disabled_block, disabled_tools={"search_chats"})
    assert desc == "search_chats: BLOCKED"
    assert result["exit_code"] == 1

    unknown_block = SimpleNamespace(tool_type="does_not_exist", content="")
    desc, result = await tool_execution.execute_tool_block(unknown_block)
    assert desc == "unknown: does_not_exist"
    assert result["error"] == "Unknown tool type: does_not_exist"

    class Manager:
        async def call_tool(self, tool, args):
            assert tool == "mcp__demo__echo"
            assert args == {"x": 1}
            return {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: Manager())
    monkeypatch.setattr(tool_execution, "is_public_blocked_tool", lambda tool: False)
    mcp_block = SimpleNamespace(tool_type="mcp__demo__echo", content='{"x":1}')
    desc, result = await tool_execution.execute_tool_block(mcp_block)
    assert desc == "mcp: mcp__demo__echo"
    assert result == {"output": "ok", "exit_code": 0}


@pytest.mark.asyncio
async def test_direct_fallback_read_and_write_file(tmp_path):
    import src.tool_execution as tool_execution

    missing = await tool_execution._direct_fallback("read_file", str(tmp_path / "missing.txt"))
    assert missing["exit_code"] == 1
    assert "not found" in missing["error"]

    target = tmp_path / "nested" / "out.txt"
    written = await tool_execution._direct_fallback("write_file", f"{target}\nhello")
    assert written["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == "hello"

    read = await tool_execution._direct_fallback("read_file", str(target))
    assert read == {"output": "hello", "exit_code": 0}


@pytest.mark.asyncio
async def test_direct_fallback_bash_python_and_mcp_not_connected(monkeypatch, tmp_path):
    import src.tool_execution as tool_execution

    class StreamingProc(FakeProc):
        def __init__(self, stdout_lines=(), stderr_lines=(), returncode=0):
            super().__init__(returncode=returncode)
            self.stdout = FakeStream(*stdout_lines)
            self.stderr = FakeStream(*stderr_lines)

    bash_proc = StreamingProc([b"hello\n"], [b"warn\n"], returncode=5)
    python_proc = StreamingProc([], [], returncode=0)

    async def create_shell(*args, **kwargs):
        return bash_proc

    async def create_exec(*args, **kwargs):
        return python_proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", create_shell)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_exec)

    bash = await tool_execution._direct_fallback("bash", "echo hello")
    assert bash == {"output": "hello\nSTDERR: warn", "exit_code": 5}

    python = await tool_execution._direct_fallback("python", "print('ignored by fake')")
    assert python == {"output": "(no output)", "exit_code": 0}

    target = tmp_path / "mcp-fallback.txt"
    target.write_text("fallback", encoding="utf-8")

    class Manager:
        async def call_tool(self, tool, args):
            assert tool == "mcp__filesystem__read_file"
            assert args == {"path": str(target)}
            return {"error": "server not connected", "exit_code": 1}

    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: Manager())
    read = await tool_execution._call_mcp_tool("read_file", str(target))
    assert read == {"output": "fallback", "exit_code": 0}


@pytest.mark.asyncio
async def test_direct_fallback_web_search_and_fetch_are_no_network(monkeypatch):
    import src.search as search_module
    import src.search.content as search_content
    import src.tool_execution as tool_execution

    monkeypatch.setattr(tool_execution, "offline_mode", lambda: False)

    def fake_search(query, max_pages=5, time_filter=None, return_sources=False):
        assert query == "latest local ai"
        assert max_pages == 3
        assert time_filter == "day"
        assert return_sources is True
        return "result text", [{"title": "Source"}]

    monkeypatch.setattr(search_module, "comprehensive_web_search", fake_search)

    search = await tool_execution._direct_fallback(
        "web_search",
        '{"query":"latest local ai","time_filter":"day","max_pages":3}',
    )
    assert search["exit_code"] == 0
    assert "result text" in search["output"]
    assert "<!-- SOURCES:" in search["output"]

    assert (await tool_execution._direct_fallback("web_fetch", "ftp://example.com"))["exit_code"] == 1
    assert (await tool_execution._direct_fallback("web_fetch", "bad url"))["exit_code"] == 1

    def fake_fetch(url, timeout=10):
        assert url == "https://example.com"
        assert timeout == 10
        return {"title": "Demo", "content": "body", "error": None}

    monkeypatch.setattr(search_content, "fetch_webpage_content", fake_fetch)
    fetched = await tool_execution._direct_fallback("web_fetch", "example.com")
    assert fetched["exit_code"] == 0
    assert fetched["output"].startswith("# Demo\nSource: https://example.com")

    monkeypatch.setattr(tool_execution, "offline_mode", lambda: True)
    blocked_search = await tool_execution._direct_fallback("web_search", "latest local ai")
    assert blocked_search == {"error": "web_search is disabled in offline mode", "exit_code": 1}
    blocked_fetch = await tool_execution._direct_fallback("web_fetch", "example.com")
    assert blocked_fetch == {"error": "web_fetch is disabled in offline mode", "exit_code": 1}


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)

    def __lt__(self, value):
        return ("lt", self.name, value)

    def desc(self):
        return ("desc", self.name)

    def in_(self, values):
        return ("in", self.name, tuple(values))


class DbSessionModel:
    id = Column("id")
    owner = Column("owner")
    archived = Column("archived")
    last_accessed = Column("last_accessed")
    created_at = Column("created_at")
    is_important = Column("is_important")
    message_count = Column("message_count")


class DbChatMessageModel:
    session_id = Column("session_id")


class CleanupQuery:
    def __init__(self, db, model, rows):
        self.db = db
        self.model = model
        self.rows = rows
        self.filters = []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        self.db.filters.extend(conditions)
        return self

    def order_by(self, *conditions):
        self.db.orderings.extend(conditions)
        return self

    def all(self):
        return list(self.rows)

    def count(self):
        return self.db.message_count

    def delete(self, synchronize_session=False):
        ids = []
        for condition in self.filters:
            if isinstance(condition, tuple) and condition[:2] == ("in", "id"):
                ids = list(condition[2])
        self.db.deleted_ids.extend(ids)
        return len(ids)


class CleanupDb:
    def __init__(self, session_rows_by_call, message_count=0):
        self.session_rows_by_call = list(session_rows_by_call)
        self.message_count = message_count
        self.filters = []
        self.orderings = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.deleted_ids = []

    def query(self, model):
        if model is DbSessionModel:
            rows = self.session_rows_by_call.pop(0) if self.session_rows_by_call else []
            return CleanupQuery(self, model, rows)
        return CleanupQuery(self, model, [])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def install_cleanup_db(monkeypatch, db):
    fake_database = types.ModuleType("src.database")
    fake_database.SessionLocal = lambda: db
    fake_database.Session = DbSessionModel
    fake_database.ChatMessage = DbChatMessageModel
    monkeypatch.setitem(sys.modules, "src.database", fake_database)


@pytest.mark.asyncio
async def test_cleanup_service_archives_inactive_sessions(monkeypatch):
    from src import cleanup_service

    old = SimpleNamespace(
        id="old",
        archived=False,
        updated_at=None,
        last_accessed=dt.datetime.utcnow() - dt.timedelta(days=30),
    )
    db = CleanupDb([[old]])
    install_cleanup_db(monkeypatch, db)

    archived = await cleanup_service.archive_inactive_sessions(SimpleNamespace(), owner="alice")

    assert archived == 1
    assert old.archived is True
    assert old.updated_at is not None
    assert db.commits == 1
    assert ("eq", "owner", "alice") in db.filters
    assert db.closed is True


@pytest.mark.asyncio
async def test_cleanup_service_deletes_only_unprotected_candidates(monkeypatch):
    from src import cleanup_service

    recent_rows = [SimpleNamespace(id=f"recent-{i}", created_at=dt.datetime.utcnow()) for i in range(10)]
    recent_candidate = SimpleNamespace(id="recent-0", name="recent old chat", message_count=2)
    delete_me = SimpleNamespace(id="delete-me", name="old chat", message_count=2)
    protected = SimpleNamespace(id="protected", name="important notes", message_count=2)
    enough_messages = SimpleNamespace(id="long", name="long chat", message_count=20)
    db = CleanupDb([recent_rows, [recent_candidate, delete_me, protected, enough_messages], []], message_count=5)
    install_cleanup_db(monkeypatch, db)
    manager = SimpleNamespace(sessions={"delete-me": object(), "protected": object()})

    deleted, freed_mb = await cleanup_service.cleanup_old_sessions(manager, owner="bob")

    assert deleted == 1
    assert freed_mb == pytest.approx((5 * cleanup_service.CleanupConfig.ESTIMATED_MESSAGE_SIZE_BYTES) / (1024 * 1024))
    assert db.deleted_ids == ["delete-me"]
    assert "delete-me" not in manager.sessions
    assert "protected" in manager.sessions
    assert db.commits == 1
    assert ("eq", "owner", "bob") in db.filters


@pytest.mark.asyncio
async def test_cleanup_service_preview_groups_archive_delete_and_preserved(monkeypatch):
    from src import cleanup_service

    archived_candidate = SimpleNamespace(
        id="archive",
        name="Archive Me",
        last_accessed=dt.datetime(2026, 1, 1),
        message_count=1,
    )
    recent_rows = [SimpleNamespace(id=f"recent-{i}", created_at=dt.datetime.utcnow()) for i in range(10)]
    recent_candidate = SimpleNamespace(
        id="recent-0",
        name="Recent Old",
        last_accessed=None,
        message_count=4,
    )
    delete_candidate = SimpleNamespace(
        id="delete",
        name="Old",
        last_accessed=dt.datetime(2025, 1, 1),
        message_count=4,
    )
    keyword_candidate = SimpleNamespace(
        id="keep",
        name="save this idea",
        last_accessed=dt.datetime(2025, 1, 2),
        message_count=4,
    )
    long_candidate = SimpleNamespace(
        id="long",
        name="Long",
        last_accessed=None,
        message_count=20,
    )
    db = CleanupDb([[archived_candidate], recent_rows, [recent_candidate, delete_candidate, keyword_candidate, long_candidate]])
    install_cleanup_db(monkeypatch, db)

    preview = await cleanup_service.get_cleanup_preview(owner="alice")

    assert preview["sessions_to_archive"][0]["id"] == "archive"
    assert preview["sessions_to_delete"][0]["id"] == "delete"
    assert preview["sessions_to_delete"][0]["estimated_size_kb"] == 2.0
    preserved = {item["id"]: item for item in preview["preserved_sessions"]}
    assert preserved["recent-0"]["reason"] == "part of last 10 sessions"
    assert preserved["long"]["reason"] == "has 20+ messages"
    assert preserved["keep"]["last_accessed"] == "2025-01-02T00:00:00"
    assert "contains keyword" in preserved["keep"]["reason"]
    assert preview["estimated_space_freed_mb"] == 0.0
    assert db.closed is True


@pytest.mark.asyncio
async def test_cleanup_sessions_continues_when_one_phase_fails(monkeypatch):
    from src import cleanup_service

    async def archive_fail(*args, **kwargs):
        raise RuntimeError("archive failed")

    async def delete_ok(*args, **kwargs):
        return 3, 1.25

    monkeypatch.setattr(cleanup_service, "archive_inactive_sessions", archive_fail)
    monkeypatch.setattr(cleanup_service, "cleanup_old_sessions", delete_ok)

    result = await cleanup_service.cleanup_sessions(SimpleNamespace(), owner="alice")

    assert result == (0, 3, 1.25)

    async def archive_ok(*args, **kwargs):
        return 2

    async def delete_fail(*args, **kwargs):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(cleanup_service, "archive_inactive_sessions", archive_ok)
    monkeypatch.setattr(cleanup_service, "cleanup_old_sessions", delete_fail)
    assert await cleanup_service.cleanup_sessions(SimpleNamespace(), owner="alice") == (2, 0, 0.0)


@pytest.mark.asyncio
async def test_cleanup_service_defensive_error_paths(monkeypatch):
    from src import cleanup_service

    query = CleanupQuery(CleanupDb([]), DbSessionModel, [])
    assert cleanup_service._apply_owner_filter(query, DbSessionModel, None) is query

    class RaisingDb(CleanupDb):
        def query(self, _model):
            raise RuntimeError("db failed")

    archive_db = RaisingDb([])
    install_cleanup_db(monkeypatch, archive_db)
    assert await cleanup_service.archive_inactive_sessions(SimpleNamespace()) == 0
    assert archive_db.rollbacks == 1
    assert archive_db.closed is True

    cleanup_db = RaisingDb([])
    install_cleanup_db(monkeypatch, cleanup_db)
    assert await cleanup_service.cleanup_old_sessions(SimpleNamespace(sessions={})) == (0, 0.0)
    assert cleanup_db.rollbacks == 1
    assert cleanup_db.closed is True

    empty_db = CleanupDb([[SimpleNamespace(id="recent", created_at=dt.datetime.utcnow())], [SimpleNamespace(id="recent", name="recent", message_count=1)]])
    install_cleanup_db(monkeypatch, empty_db)
    assert await cleanup_service.cleanup_old_sessions(SimpleNamespace(sessions={})) == (0, 0.0)

    preview_db = RaisingDb([])
    install_cleanup_db(monkeypatch, preview_db)
    assert await cleanup_service.get_cleanup_preview() == {
        "sessions_to_archive": [],
        "sessions_to_delete": [],
        "preserved_sessions": [],
        "estimated_space_freed_mb": 0.0,
    }
    assert preview_db.closed is True
