import asyncio
import json
import sys
import types
from datetime import datetime, timedelta

from src import event_bus


def test_scheduler_set_get_and_fire_event_sync_and_async(monkeypatch):
    scheduler = object()
    event_bus.set_task_scheduler(scheduler)
    assert event_bus.get_task_scheduler() is scheduler

    calls = []

    async def fake_handle(event_name, owner=None):
        calls.append((event_name, owner))

    monkeypatch.setattr(event_bus, "_handle_event", fake_handle)
    event_bus.fire_event("sync_event", "alice")
    assert calls == [("sync_event", "alice")]

    async def run_inside_loop():
        event_bus.fire_event("async_event", "bob")
        await asyncio.sleep(0)

    asyncio.run(run_inside_loop())
    assert calls[-1] == ("async_event", "bob")


def test_resolve_event_owner_explicit_admin_first_user_and_errors(tmp_path, monkeypatch):
    import src.constants as constants

    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"users": {"bob": {"is_admin": False}, "alice": {"is_admin": True}}}),
        encoding="utf-8",
    )

    assert event_bus._resolve_event_owner("  carol  ") == "carol"
    assert event_bus._resolve_event_owner(None) == "alice"

    auth_path.write_text(json.dumps({"users": {"bob": {"is_admin": False}}}), encoding="utf-8")
    assert event_bus._resolve_event_owner("") == "bob"

    auth_path.write_text("{", encoding="utf-8")
    assert event_bus._resolve_event_owner("") is None


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, other)


class ScheduledTaskModel:
    trigger_type = Column("trigger_type")
    trigger_event = Column("trigger_event")
    status = Column("status")
    owner = Column("owner")


class FakeTask:
    def __init__(
        self,
        task_id,
        name,
        *,
        owner="alice",
        trigger_count=1,
        trigger_counter=0,
        next_run=None,
    ):
        self.id = task_id
        self.name = name
        self.owner = owner
        self.trigger_count = trigger_count
        self.trigger_counter = trigger_counter
        self.next_run = next_run


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def filter(self, *filters):
        self.db.filters.append(filters)
        return self

    def all(self):
        if isinstance(self.db.tasks, BaseException):
            raise self.db.tasks
        return self.db.tasks


class FakeDb:
    def __init__(self, tasks):
        self.tasks = tasks
        self.filters = []
        self.commits = 0
        self.closed = False

    def query(self, model):
        self.model = model
        return FakeQuery(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeScheduler:
    def __init__(self):
        self.ran = []

    async def run_task_now(self, task_id):
        self.ran.append(task_id)


def install_database(monkeypatch, db):
    database = types.ModuleType("core.database")
    database.SessionLocal = lambda: db
    database.ScheduledTask = ScheduledTaskModel
    monkeypatch.setitem(sys.modules, "core.database", database)


def test_handle_event_no_tasks_and_ownerless_filter(monkeypatch):
    db = FakeDb([])
    install_database(monkeypatch, db)
    monkeypatch.setattr(event_bus, "_resolve_event_owner", lambda owner: None)

    asyncio.run(event_bus._handle_event("message_sent", None))

    assert db.closed is True
    assert db.commits == 0
    assert ("owner", None) in db.filters[0]


def test_handle_event_increments_counters_runs_due_and_warns_without_scheduler(monkeypatch):
    due = FakeTask("due", "Due", trigger_count=2, trigger_counter=1)
    pending = FakeTask("pending", "Pending", trigger_count=3, trigger_counter=1)
    no_scheduler = FakeTask("no-scheduler", "No Scheduler", trigger_count=1, trigger_counter=0)
    db = FakeDb([due, pending])
    install_database(monkeypatch, db)
    monkeypatch.setattr(event_bus, "_resolve_event_owner", lambda owner: "alice")
    scheduler = FakeScheduler()
    event_bus.set_task_scheduler(scheduler)

    asyncio.run(event_bus._handle_event("message_sent", "alice"))

    assert due.trigger_counter == 0
    assert isinstance(due.next_run, datetime)
    assert pending.trigger_counter == 2
    assert scheduler.ran == ["due"]
    assert db.commits == 2
    assert db.closed is True
    assert ("owner", "alice") in db.filters[0]

    db = FakeDb([no_scheduler])
    install_database(monkeypatch, db)
    event_bus.set_task_scheduler(None)
    asyncio.run(event_bus._handle_event("message_sent", "alice"))
    assert no_scheduler.trigger_counter == 0
    assert db.commits == 1


def test_handle_event_deferred_task_and_database_exception(monkeypatch):
    deferred = FakeTask(
        "deferred",
        "Deferred",
        trigger_count=1,
        trigger_counter=0,
        next_run=datetime.utcnow() + timedelta(minutes=5),
    )
    db = FakeDb([deferred])
    install_database(monkeypatch, db)
    monkeypatch.setattr(event_bus, "_resolve_event_owner", lambda owner: "alice")
    scheduler = FakeScheduler()
    event_bus.set_task_scheduler(scheduler)
    future = datetime.utcnow() + timedelta(minutes=5)
    earlier = datetime.utcnow()

    class FakeDatetime:
        calls = [future, earlier]

        @classmethod
        def utcnow(cls):
            return cls.calls.pop(0)

    monkeypatch.setattr(event_bus, "datetime", FakeDatetime)

    asyncio.run(event_bus._handle_event("message_sent", "alice"))

    assert scheduler.ran == []
    assert db.closed is True

    broken_db = FakeDb(RuntimeError("query failed"))
    install_database(monkeypatch, broken_db)
    asyncio.run(event_bus._handle_event("message_sent", "alice"))
    assert broken_db.closed is True
