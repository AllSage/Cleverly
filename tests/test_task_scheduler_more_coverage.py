import asyncio
import json
import sys
import types
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, "eq", other)

    def __le__(self, other):
        return (self.name, "le", other)

    def __ne__(self, other):
        return (self.name, "ne", other)

    def in_(self, values):
        return (self.name, "in", tuple(values))

    def notin_(self, values):
        return (self.name, "notin", tuple(values))

    def isnot(self, value):
        return (self.name, "isnot", value)

    def desc(self):
        return (self.name, "desc")

    def asc(self):
        return (self.name, "asc")


class FakeScheduledTask:
    id = Field("id")
    owner = Field("owner")
    status = Field("status")
    next_run = Field("next_run")
    trigger_type = Field("trigger_type")
    action = Field("action")
    task_type = Field("task_type")
    name = Field("name")
    created_at = Field("created_at")
    crew_member_id = Field("crew_member_id")


class FakeTaskRun:
    id = Field("id")
    task_id = Field("task_id")
    status = Field("status")
    started_at = Field("started_at")


class FakeCrewMember:
    id = Field("id")
    owner = Field("owner")
    is_default_assistant = Field("is_default_assistant")
    created_at = Field("created_at")


class FakeNote:
    owner = Field("owner")
    due_date = Field("due_date")
    archived = Field("archived")


def _install_core_database(monkeypatch, session_factory):
    class FakeDbSession:
        endpoint_url = Field("endpoint_url")
        model = Field("model")
        owner = Field("owner")
        created_at = Field("created_at")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDbChatMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_db = types.ModuleType("core.database")
    fake_db.SessionLocal = session_factory
    fake_db.ScheduledTask = FakeScheduledTask
    fake_db.TaskRun = FakeTaskRun
    fake_db.CrewMember = FakeCrewMember
    fake_db.Note = FakeNote
    fake_db.ModelEndpoint = SimpleNamespace(is_enabled=Field("is_enabled"), base_url="", api_key="")
    fake_db.Session = FakeDbSession
    fake_db.ChatMessage = FakeDbChatMessage
    monkeypatch.setitem(sys.modules, "core.database", fake_db)
    return fake_db


def _scheduler():
    from src.task_scheduler import TaskScheduler

    return TaskScheduler(None)


@pytest.mark.asyncio
async def test_checkin_skips_network_integrations_offline(monkeypatch):
    import src.task_scheduler as ts

    scheduler = _scheduler()
    captured = {}

    async def fake_run_agent_loop(*args, **kwargs):
        captured["context"] = kwargs.get("override_user_message", "")
        return "offline check-in"

    fake_tools = types.ModuleType("src.tool_implementations")
    fake_tools.do_manage_notes = lambda payload, owner="": asyncio.sleep(0, result={"results": "local notes"})
    fake_agent_tools = types.ModuleType("src.agent_tools")
    fake_agent_tools.get_mcp_manager = lambda: None
    fake_integrations = types.ModuleType("src.integrations")

    def load_integrations():
        raise AssertionError("offline check-in should not load network integrations")

    fake_integrations.load_integrations = load_integrations
    monkeypatch.setitem(sys.modules, "src.tool_implementations", fake_tools)
    monkeypatch.setitem(sys.modules, "src.agent_tools", fake_agent_tools)
    monkeypatch.setitem(sys.modules, "src.integrations", fake_integrations)
    monkeypatch.setattr(ts, "offline_mode", lambda: True)
    monkeypatch.setattr(ts, "load_features", lambda: {"network_integrations": True})
    monkeypatch.setattr(scheduler, "_run_agent_loop", fake_run_agent_loop)

    result = await scheduler._execute_checkin(
        SimpleNamespace(owner="alice", prompt="Summarize the day", crew_member_id=None),
        SimpleNamespace(personality=""),
        SimpleNamespace(),
        "session-id",
        "http://localhost:11434/v1/chat/completions",
        "local-model",
    )

    assert result == "offline check-in"
    assert "local notes" in captured["context"]
    assert "rss_miniflux_unread" not in captured["context"]


@pytest.mark.asyncio
async def test_cached_singleflight_hit_and_exception(monkeypatch):
    import src.task_scheduler as ts

    ts._shared_cache.clear()
    ts._shared_cache_pending.clear()

    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"value": calls}

    first, second = await asyncio.gather(
        ts._cached(("k",), 60, fetch),
        ts._cached(("k",), 60, fetch),
    )
    assert first == second == {"value": 1}
    assert calls == 1
    assert await ts._cached(("k",), 60, fetch) == {"value": 1}

    async def fail():
        raise RuntimeError("boom")

    failures = await asyncio.gather(
        ts._cached(("bad",), 60, fail),
        ts._cached(("bad",), 60, fail),
        return_exceptions=True,
    )
    assert all(isinstance(item, RuntimeError) for item in failures)
    assert ("bad",) not in ts._shared_cache_pending


def test_compute_next_run_variants_and_timezone(monkeypatch):
    import src.task_scheduler as ts

    after = datetime(2026, 1, 5, 10, 30)  # Monday
    assert ts.compute_next_run("once", "", scheduled_date=after + timedelta(hours=1), after=after) == after + timedelta(hours=1)
    assert ts.compute_next_run("once", "", scheduled_date=after - timedelta(hours=1), after=after) is None
    assert ts.compute_next_run("daily", "", after=after) is None
    assert ts.compute_next_run("daily", "11:00", after=after) == datetime(2026, 1, 5, 11, 0)
    assert ts.compute_next_run("daily", "09:00", after=after) == datetime(2026, 1, 6, 9, 0)
    assert ts.compute_next_run("weekly", "09:00", scheduled_day=0, after=after) == datetime(2026, 1, 12, 9, 0)
    assert ts.compute_next_run("weekly", "09:00", scheduled_day=2, after=after) == datetime(2026, 1, 7, 9, 0)
    assert ts.compute_next_run("monthly", "08:00", scheduled_day=31, after=datetime(2026, 2, 1, 9, 0)) == datetime(2026, 3, 31, 8, 0)
    assert ts.compute_next_run("monthly", "08:00", scheduled_day=31, after=datetime(2026, 1, 31, 9, 0)) == datetime(2026, 2, 28, 8, 0)
    assert ts.compute_next_run("unknown", "08:00", after=after) is None
    assert ts.compute_next_run("cron", "", cron_expression="not cron", after=after) is None

    local_after = datetime(2026, 6, 1, 12, 0)
    tz_run = ts.compute_next_run("daily", "08:00", after=local_after, tz_name="America/Chicago")
    assert tz_run == datetime(2026, 6, 1, 13, 0)
    invalid_tz = ts.compute_next_run("daily", "08:00", after=local_after, tz_name="Bad/Zone")
    assert invalid_tz == datetime(2026, 6, 2, 8, 0)


def test_notifications_format_email_timezone_and_logging(monkeypatch):
    import src.task_scheduler as ts

    scheduler = _scheduler()
    for i in range(55):
        scheduler.add_notification("task", "success", f"id{i}", owner="alice", body="x" * 600)
    assert len(scheduler._pending_notifications) == 50
    assert scheduler._pending_notifications[-1]["body"].endswith("\u2026")
    assert len(scheduler.pop_notifications("alice")) == 50
    assert scheduler.pop_notifications("alice") == []

    formatted = ts.TaskScheduler._format_email_output(
        "Page 1/2\n[12] Reply Subject From: Sender | Today\n- loose item\nNo emails here"
    )
    assert "- Sender" in formatted
    assert "loose item" in formatted
    assert ts.TaskScheduler._format_email_output("Page 1/2\nNo emails") == "No unread emails"

    assert scheduler._is_email_output_target("email")
    assert scheduler._is_email_output_target("email:self")
    assert scheduler._is_email_output_target("email:a@example.com")
    assert scheduler._is_email_output_target("a@example.com")
    assert not scheduler._is_email_output_target("session")

    class Query:
        def __init__(self, result):
            self.result = result

        def filter(self, *args):
            return self

        def first(self):
            return self.result

    class DB:
        def query(self, model):
            return Query(SimpleNamespace(timezone="America/Chicago"))

    assert ts._resolve_task_timezone(DB(), SimpleNamespace(crew_member_id="crew")) == "America/Chicago"
    assert ts._resolve_task_timezone(DB(), SimpleNamespace(crew_member_id=None)) is None

    logged = []
    fake_log = types.ModuleType("src.assistant_log")
    fake_log.log_to_assistant = lambda owner, text, category=None: logged.append((owner, text, category))
    monkeypatch.setitem(sys.modules, "src.assistant_log", fake_log)
    scheduler._log_to_assistant(None, SimpleNamespace(name="Check-in", action="", owner="alice"), "skip")
    scheduler._log_to_assistant(None, SimpleNamespace(name="Task", action="tidy_sessions", owner="alice"), "skip")
    scheduler._log_to_assistant(None, SimpleNamespace(name="Task", action="", owner="alice"), "result" * 400)
    assert logged == [("alice", ("result" * 400)[:1000], "Task")]


@pytest.mark.asyncio
async def test_execute_action_run_task_now_stop_task_and_model_slot(monkeypatch):
    import src.task_scheduler as ts

    scheduler = _scheduler()
    progress = []
    monkeypatch.setattr(scheduler, "_set_run_progress", lambda run_id, msg: progress.append((run_id, msg)))

    class TaskNoop(Exception):
        pass

    async def ok_action(**kwargs):
        kwargs["progress_cb"]("working")
        return f"{kwargs['owner']}:{kwargs.get('script') or kwargs.get('command')}", True

    async def bad_action(**kwargs):
        raise RuntimeError("action bad")

    fake_actions = types.ModuleType("src.builtin_actions")
    fake_actions.TaskNoop = TaskNoop
    fake_actions.BUILTIN_ACTIONS = {
        "run_local": ok_action,
        "ssh_command": ok_action,
        "bad": bad_action,
        "noop": lambda **kwargs: (_ for _ in ()).throw(TaskNoop("quiet")),
    }
    monkeypatch.setitem(sys.modules, "src.builtin_actions", fake_actions)

    task = SimpleNamespace(action="run_local", prompt="echo hi", owner="alice", name="Run")
    assert await scheduler._execute_action(task, run_id="run1") == ("alice:echo hi", True)
    assert progress == [("run1", "working")]
    task.action = "missing"
    assert await scheduler._execute_action(task) == ("Unknown action: missing", False)
    task.action = "bad"
    assert await scheduler._execute_action(task) == ("action bad", False)
    task.action = "noop"
    with pytest.raises(TaskNoop):
        await scheduler._execute_action(task)

    task_row = SimpleNamespace(task_type="action", action="summarize_emails")
    closed = []

    class Query:
        def filter(self, *args):
            return self

        def first(self):
            return task_row

    class DB:
        def query(self, model):
            return Query()

        def close(self):
            closed.append(True)

    _install_core_database(monkeypatch, DB)
    assert scheduler._task_needs_model_slot("task1") is True
    task_row.action = "tidy_documents"
    assert scheduler._task_needs_model_slot("task1") is False
    task_row.task_type = "research"
    assert scheduler._task_needs_model_slot("task1") is True
    assert closed

    created = []

    def fake_create_task(coro):
        created.append(coro)
        coro.close()
        return SimpleNamespace(done=lambda: True)

    monkeypatch.setattr(ts.asyncio, "create_task", fake_create_task)
    assert await scheduler.run_task_now("manual") is True
    assert await scheduler.run_task_now("manual") is False
    assert await scheduler.run_task_now("force", force=True) is True
    assert len(created) == 2

    run = SimpleNamespace(status="running", error=None, result="", finished_at=None)

    class StopQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return run

    class StopDB:
        def query(self, model):
            return StopQuery()

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    _install_core_database(monkeypatch, StopDB)
    cancelled = []
    scheduler._task_handles["manual"] = SimpleNamespace(done=lambda: False, cancel=lambda: cancelled.append(True))
    assert await scheduler.stop_task("manual") is True
    assert cancelled == [True]
    assert run.status == "aborted"


@pytest.mark.asyncio
async def test_delivery_email_mcp_and_agent_loop(monkeypatch):
    import src.task_scheduler as ts

    scheduler = _scheduler()
    sent = []
    fake_email_routes = types.ModuleType("routes.email_routes")
    fake_email_routes._resolve_send_config = lambda owner="": {"from_address": "me@example.com", "smtp_user": "smtp@example.com"}
    fake_email_helpers = types.ModuleType("routes.email_helpers")
    fake_email_helpers._send_smtp_message = lambda cfg, from_addr, to_addrs, body, timeout=30: sent.append(
        (cfg, from_addr, to_addrs, body, timeout)
    )
    fake_email_helpers._get_email_config = lambda: {"from_address": "me@example.com"}
    monkeypatch.setitem(sys.modules, "routes.email_routes", fake_email_routes)
    monkeypatch.setitem(sys.modules, "routes.email_helpers", fake_email_helpers)

    task = SimpleNamespace(id="t1", name="Digest", owner="alice@example.com", prompt="prompt", max_steps=2)
    await scheduler._deliver_via_email("email:you@example.com", task, "body")
    assert sent[0][2] == ["you@example.com"]
    assert "X-Cleverly-Kind: task" in sent[0][3]

    class Mcp:
        def __init__(self):
            self.calls = []

        async def call_tool(self, tool_name, args):
            self.calls.append((tool_name, args))
            return {"exit_code": 0, "stdout": "sent"}

    mcp = Mcp()
    fake_agent_tools = types.ModuleType("src.agent_tools")
    fake_agent_tools.get_mcp_manager = lambda: mcp
    monkeypatch.setitem(sys.modules, "src.agent_tools", fake_agent_tools)
    await scheduler._deliver_via_mcp("mcp__mail__send", task, "result")
    assert mcp.calls[0][1]["to"] == "me@example.com"

    fake_email_helpers._get_email_config = lambda: {}
    task.owner = "owner@example.com"
    await scheduler._deliver_via_mcp("mcp__mail__send", task, "result")
    assert mcp.calls[1][1]["email"] == "owner@example.com"

    async def stream_agent_loop(**kwargs):
        yield "data: " + json.dumps({"delta": "hello "})
        yield "data: " + json.dumps({"type": "tool_output", "tool": "bash", "stdout": "ignored"})
        yield "data: [DONE]"

    fake_loop = types.ModuleType("src.agent_loop")
    fake_loop.stream_agent_loop = stream_agent_loop
    monkeypatch.setitem(sys.modules, "src.agent_loop", fake_loop)

    fake_endpoint = types.ModuleType("src.endpoint_resolver")
    fake_endpoint.resolve_utility_fallback_candidates = lambda: [("fallback", "model", {})]
    fake_endpoint.normalize_base = lambda url: url.rstrip("/")
    fake_endpoint.build_headers = lambda api_key, base_url: {"Authorization": api_key}
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", fake_endpoint)

    result = await scheduler._run_agent_loop("http://llm", "model", task, "session", relevant_tools={"read_file"})
    assert result == "hello "

    async def stream_tool_only(**kwargs):
        yield "data: " + json.dumps({"type": "tool_output", "tool": "bash", "stdout": "tool said ok"})

    fake_loop.stream_agent_loop = stream_tool_only
    fake_llm = types.ModuleType("src.llm_core")
    fake_llm.llm_call_async_with_fallback = lambda candidates, messages, timeout=30: asyncio.sleep(0, result="summary")
    monkeypatch.setitem(sys.modules, "src.llm_core", fake_llm)
    assert await scheduler._run_agent_loop("http://llm", "model", task, "session") == "summary"

    fake_llm.llm_call_async_with_fallback = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no summary"))
    assert "tool said ok" in await scheduler._run_agent_loop("http://llm", "model", task, "session")

    monkeypatch.setattr(ts, "task_feature_disabled_reason", lambda values: "blocked output" if values.get("output_target") == "email" else None)
    stale_task = SimpleNamespace(
        id="stale",
        name="Stale",
        owner="alice",
        prompt="prompt",
        task_type="llm",
        action="",
        trigger_type="schedule",
        trigger_event="",
        output_target="email",
    )
    with pytest.raises(RuntimeError, match="blocked output"):
        await scheduler._deliver_task_result(stale_task, "body", SimpleNamespace())


def test_known_owners_chain_cycle_and_defaults(monkeypatch):
    import src.task_scheduler as ts

    scheduler = _scheduler()

    class OwnersQuery:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def distinct(self):
            return self

        def all(self):
            return self.rows

    class OwnersDB:
        def query(self, field):
            if getattr(field, "name", "") == "owner":
                if getattr(self, "called", False):
                    return OwnersQuery([("bob",), ("",)])
                self.called = True
                return OwnersQuery([("alice",), (None,)])
            return OwnersQuery([])

        def close(self):
            self.closed = True

    _install_core_database(monkeypatch, OwnersDB)
    assert scheduler._known_task_owners() == ["alice", "bob"]

    tasks = {
        "a": SimpleNamespace(id="a", then_task_id="b"),
        "b": SimpleNamespace(id="b", then_task_id="a"),
        "c": SimpleNamespace(id="c", then_task_id=None),
    }

    class ChainQuery:
        def __init__(self, task_id=None):
            self.task_id = task_id

        def filter(self, expr):
            self.task_id = expr[2]
            return self

        def first(self):
            return tasks.get(self.task_id)

    class ChainDB:
        def query(self, model):
            return ChainQuery()

    assert scheduler._has_chain_cycle(ChainDB(), "a") is True
    assert scheduler._has_chain_cycle(ChainDB(), "c") is False

    recent = SimpleNamespace(endpoint_url="http://endpoint", model="model")

    class DefaultsQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return recent

    class DefaultsDB:
        def query(self, model):
            return DefaultsQuery()

    assert scheduler._resolve_defaults(DefaultsDB(), "alice") == ("http://endpoint", "model")

    class BrokenDB:
        def query(self, model):
            raise RuntimeError("db bad")

    assert scheduler._resolve_defaults(BrokenDB(), "alice") == (None, None)


@pytest.mark.asyncio
async def test_check_due_and_chained_dispatch(monkeypatch):
    import src.task_scheduler as ts

    scheduler = _scheduler()
    scheduler._executing = {"busy"}
    dispatched = []

    due = [
        SimpleNamespace(id="busy"),
        SimpleNamespace(id="due1"),
        SimpleNamespace(id="due2"),
    ]

    class DueQuery:
        def filter(self, *args):
            return self

        def all(self):
            return due

    class DueDB:
        def query(self, model):
            return DueQuery()

        def close(self):
            self.closed = True

    _install_core_database(monkeypatch, DueDB)

    def fake_create_task(coro):
        dispatched.append(coro)
        coro.close()
        return SimpleNamespace(done=lambda: True)

    monkeypatch.setattr(ts.asyncio, "create_task", fake_create_task)
    await scheduler._check_due_tasks()
    assert len(dispatched) == 2
    assert scheduler._executing == {"busy", "due1", "due2"}

    async def fake_execute_task(task_id):
        dispatched.append(task_id)

    monkeypatch.setattr(scheduler, "_execute_task", fake_execute_task)
    await scheduler._run_chained("due1")
    await scheduler._run_chained("new")
    assert "new" in dispatched
