import asyncio
import datetime as dt
import importlib
import json
import sys
import types
from pathlib import Path
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
    def __init__(self, name, value=None):
        self.name = name
        self.value = value

    def __or__(self, other):
        return Expr("or", (self, other))


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
        self._body = body

    async def json(self):
        return self._body or {}


def test_assistant_routes_settings_session_update_run_and_helpers(monkeypatch):
    import routes.assistant_routes as assistant_routes

    class CrewMember:
        id = Column("crew_id")
        owner = Column("crew_owner")
        is_default_assistant = Column("is_default")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.updated_at = kwargs.get("updated_at")

    class ScheduledTask:
        id = Column("task_id")
        owner = Column("task_owner")
        crew_member_id = Column("task_crew")
        scheduled_time = Column("scheduled_time")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model
            self.ids = []

        def filter(self, *conditions):
            for condition in conditions:
                if isinstance(condition, Expr) and condition.name in {"crew_id", "task_id"}:
                    self.ids.append(condition.value)
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            if self.model is ScheduledTask:
                return self.db.tasks
            return []

        def first(self):
            if self.model is CrewMember:
                return self.db.crew
            if self.model is ScheduledTask:
                if self.ids:
                    return self.db.tasks_by_id.get(self.ids[-1])
                return self.db.tasks[0] if self.db.tasks else None
            return None

    class DB:
        def __init__(self):
            self.crew = CrewMember(
                id="crew1",
                owner="alice",
                name="Clever",
                avatar="A",
                personality="helpful",
                model="m1",
                endpoint_url="http://local",
                greeting="hi",
                enabled_tools=json.dumps(["search", "send_email"]),
                session_id="session1",
                is_default_assistant=True,
                timezone="UTC",
            )
            self.tasks = [
                ScheduledTask(
                    id="task1",
                    name="Morning",
                    scheduled_time="09:00",
                    prompt="Check in",
                    status="active",
                    next_run=dt.datetime(2026, 1, 1, 9),
                    last_run=dt.datetime(2026, 1, 1, 8),
                    run_count=2,
                    schedule="daily",
                    scheduled_day=None,
                    scheduled_date=None,
                    cron_expression=None,
                    owner="alice",
                    crew_member_id="crew1",
                )
            ]
            self.tasks_by_id = {t.id: t for t in self.tasks}
            self.commits = 0
            self.closed = 0

        def query(self, model):
            return Query(self, model)

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed += 1

    class Scheduler:
        def __init__(self):
            self.seeded = []
            self.ran = []

        async def ensure_assistant_defaults(self, owner):
            self.seeded.append(owner)

        async def run_task_now(self, task_id):
            self.ran.append(task_id)
            return True

    db = DB()
    scheduler = Scheduler()
    monkeypatch.setattr(assistant_routes, "CrewMember", CrewMember)
    monkeypatch.setattr(assistant_routes, "ScheduledTask", ScheduledTask)
    monkeypatch.setattr(assistant_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(assistant_routes, "get_current_user", lambda request: request.state.current_user)
    next_runs = []
    monkeypatch.setattr(
        assistant_routes,
        "compute_next_run",
        lambda *args, **kwargs: next_runs.append((args, kwargs)) or dt.datetime(2026, 1, 2, 9),
    )

    crew_dict = assistant_routes._crew_to_dict(db.crew)
    assert crew_dict["allow_autonomous_email"] is True
    db.crew.enabled_tools = "{"
    assert assistant_routes._crew_to_dict(db.crew)["enabled_tools"] == []
    db.crew.enabled_tools = json.dumps(["search", "send_email"])
    task_dict = assistant_routes._task_to_checkin_dict(db.tasks[0])
    assert task_dict["enabled"] is True
    assert task_dict["next_run"] == "2026-01-01T09:00:00Z"

    router = assistant_routes.setup_assistant_routes(scheduler)
    request = RequestLike("alice")

    assert asyncio.run(_endpoint(router, "/api/assistant/session")(request)) == {
        "session_id": "session1",
        "crew_member_id": "crew1",
        "name": "Clever",
    }
    settings = asyncio.run(_endpoint(router, "/api/assistant/settings")(request))
    assert settings["task_ids"] == ["task1"]
    assert settings["crew"]["session_id"] == "session1"

    payload = assistant_routes.AssistantSettingsUpdate(
        name="  New Name  ",
        avatar="B",
        personality="direct",
        model="m2",
        endpoint_url="",
        enabled_tools=["calendar"],
        allow_autonomous_email=True,
        timezone="America/Chicago",
        check_ins=[
            assistant_routes.CheckInUpdate(
                id="task1",
                name="  Midday  ",
                scheduled_time="10:30",
                prompt="Ping",
                enabled=True,
            )
        ],
    )
    updated = asyncio.run(_endpoint(router, "/api/assistant/settings", "PATCH")(payload, request))
    assert updated["crew"]["name"] == "New Name"
    assert "send_email" in updated["crew"]["enabled_tools"]
    assert db.tasks[0].next_run == dt.datetime(2026, 1, 2, 9)
    assert next_runs
    assert db.commits == 1

    assert asyncio.run(_endpoint(router, "/api/assistant/run/{task_id}", "POST")("task1", request)) == {"started": True}
    assert scheduler.ran == ["task1"]
    assert "UTC" in asyncio.run(_endpoint(router, "/api/assistant/available-timezones")())["timezones"]

    with pytest.raises(HTTPException) as unauth:
        asyncio.run(_endpoint(router, "/api/assistant/session")(RequestLike(user=None)))
    assert unauth.value.status_code == 401
    with pytest.raises(HTTPException) as synthetic:
        asyncio.run(_endpoint(router, "/api/assistant/session")(RequestLike(user="api")))
    assert synthetic.value.status_code == 400

    db.crew.session_id = ""
    with pytest.raises(HTTPException) as unresolved:
        asyncio.run(_endpoint(router, "/api/assistant/session")(request))
    assert unresolved.value.status_code == 500


def test_note_routes_crud_toggles_reorder_and_reminder(monkeypatch):
    attrs_module = types.ModuleType("sqlalchemy.orm.attributes")
    attrs_module.flag_modified = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm.attributes", attrs_module)
    orm_module = sys.modules.get("sqlalchemy.orm")
    if orm_module is not None:
        monkeypatch.setattr(orm_module, "__path__", [], raising=False)
        monkeypatch.setattr(orm_module, "attributes", attrs_module, raising=False)

    import routes.note_routes as note_routes

    class Note:
        id = Column("id")
        owner = Column("owner")
        archived = Column("archived")
        label = Column("label")
        updated_at = Column("updated_at")
        pinned = Column("pinned")
        sort_order = Column("sort_order")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.created_at = kwargs.get("created_at", dt.datetime(2026, 1, 1))
            self.updated_at = kwargs.get("updated_at", dt.datetime(2026, 1, 2))
            self.owner = kwargs.get("owner", "alice")
            self.title = kwargs.get("title", "")
            self.content = kwargs.get("content")
            self.items = kwargs.get("items")
            self.note_type = kwargs.get("note_type", "note")
            self.color = kwargs.get("color")
            self.label = kwargs.get("label")
            self.pinned = kwargs.get("pinned", False)
            self.archived = kwargs.get("archived", False)
            self.due_date = kwargs.get("due_date")
            self.source = kwargs.get("source", "user")
            self.session_id = kwargs.get("session_id")
            self.sort_order = kwargs.get("sort_order", 0)
            self.image_url = kwargs.get("image_url")
            self.repeat = kwargs.get("repeat", "none")
            self.ai_classification = kwargs.get("ai_classification")
            self.ai_content_hash = kwargs.get("ai_content_hash")
            self.agent_session_id = kwargs.get("agent_session_id")

    class Query:
        def __init__(self, db):
            self.db = db
            self.id_filter = None
            self.owner_filter = None

        def filter(self, *conditions):
            for condition in conditions:
                if isinstance(condition, Expr) and condition.name == "id":
                    self.id_filter = condition.value
                if isinstance(condition, Expr) and condition.name == "owner":
                    self.owner_filter = condition.value
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            notes = list(self.db.notes.values())
            if self.owner_filter is not None:
                notes = [note for note in notes if note.owner == self.owner_filter]
            return notes

        def first(self):
            if self.id_filter is not None:
                note = self.db.notes.get(self.id_filter)
                if note is not None and self.owner_filter is not None and note.owner != self.owner_filter:
                    return None
                return note
            notes = self.all()
            return notes[0] if notes else None

    class DB:
        def __init__(self):
            self.notes = {
                "n1": Note(
                    id="n1",
                    title="Title",
                    content="Body",
                    items=json.dumps([{"text": "one", "done": False}]),
                    ai_classification=json.dumps({"kind": "todo"}),
                ),
                "bob-note": Note(id="bob-note", owner="bob", title="Private", content="Other"),
            }
            self.added = []
            self.deleted = []
            self.commits = 0

        def query(self, _model):
            return Query(self)

        def add(self, note):
            self.added.append(note)
            self.notes[note.id] = note

        def commit(self):
            self.commits += 1

        def refresh(self, _note):
            return None

        def delete(self, note):
            self.deleted.append(note)
            self.notes.pop(note.id, None)

        def close(self):
            self.closed = True

    db = DB()
    flagged = []
    monkeypatch.setattr(note_routes, "Note", Note)
    monkeypatch.setattr(note_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(note_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(note_routes, "flag_modified", lambda note, field: flagged.append((note.id, field)))
    monkeypatch.setattr(note_routes.uuid, "uuid4", lambda: "note-uuid")

    scheduler = SimpleNamespace(notifications=[])
    router = note_routes.setup_note_routes(scheduler)
    request = RequestLike("alice")

    assert note_routes._note_to_dict(db.notes["n1"])["ai_classification"] == {"kind": "todo"}
    db.notes["n1"].items = "{"
    db.notes["n1"].ai_classification = "{"
    parsed = note_routes._note_to_dict(db.notes["n1"])
    assert parsed["items"] is None
    assert parsed["ai_classification"] is None
    db.notes["n1"].items = json.dumps([{"text": "one", "done": False}])

    assert _endpoint(router, "/api/notes")(request)["notes"][0]["id"] == "n1"
    assert _endpoint(router, "/api/notes")(request, archived=True)["notes"][0]["id"] == "n1"
    created = _endpoint(router, "/api/notes", "POST")(
        request,
        note_routes.NoteCreate(title="New", items=[{"text": "x"}], pinned=True, sort_order=4),
    )
    assert created["id"] == "note-uuid"
    assert json.loads(db.notes["note-uuid"].items) == [{"text": "x"}]

    assert _endpoint(router, "/api/notes/{note_id}")(request, "n1")["title"] == "Title"
    db.notes["n1"].owner = "bob"
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/notes/{note_id}")(request, "n1")
    db.notes["n1"].owner = "alice"
    with pytest.raises(HTTPException) as missing:
        _endpoint(router, "/api/notes/{note_id}")(request, "missing")
    assert missing.value.status_code == 404

    update = note_routes.NoteUpdate(
        title="Updated",
        content="Updated body",
        items=[{"text": "one", "done": True}],
        note_type="checklist",
        color="blue",
        label="work",
        pinned=True,
        archived=True,
        due_date="2026-01-03",
        image_url="/img",
        repeat="daily",
        sort_order=9,
        agent_session_id="agent",
    )
    updated = _endpoint(router, "/api/notes/{note_id}", "PUT")(request, "n1", update)
    assert updated["title"] == "Updated"
    assert ("n1", "items") in flagged

    assert _endpoint(router, "/api/notes/{note_id}/pin", "POST")(request, "n1")["pinned"] is False
    assert _endpoint(router, "/api/notes/{note_id}/archive", "POST")(request, "n1")["archived"] is False
    toggled = _endpoint(router, "/api/notes/{note_id}/items/{index}/toggle", "POST")(request, "n1", 0)
    assert toggled["items"][0]["done"] is False
    with pytest.raises(HTTPException) as out_of_range:
        _endpoint(router, "/api/notes/{note_id}/items/{index}/toggle", "POST")(request, "n1", 99)
    assert out_of_range.value.status_code == 400
    db.notes["n1"].items = ""
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/notes/{note_id}/items/{index}/toggle", "POST")(request, "n1", 0)

    reminders = []

    async def fake_dispatch(**kwargs):
        reminders.append(kwargs)
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", fake_dispatch)
    with pytest.raises(HTTPException) as unauth:
        asyncio.run(_endpoint(router, "/api/notes/fire-reminder", "POST")(RequestLike(None, {"note_id": "n1"})))
    assert unauth.value.status_code == 401
    with pytest.raises(HTTPException) as missing_note_id:
        asyncio.run(_endpoint(router, "/api/notes/fire-reminder", "POST")(RequestLike("alice", {})))
    assert missing_note_id.value.status_code == 400
    with pytest.raises(HTTPException) as cross_owner_note:
        asyncio.run(
            _endpoint(router, "/api/notes/fire-reminder", "POST")(
                RequestLike("alice", {"note_id": "bob-note", "title": " T ", "body": " B "})
            )
        )
    assert cross_owner_note.value.status_code == 404
    assert reminders == []
    assert asyncio.run(
        _endpoint(router, "/api/notes/fire-reminder", "POST")(
            RequestLike("alice", {"note_id": "n1", "title": " T ", "body": " B "})
        )
    ) == {"browser_sent": True}
    assert reminders[-1]["owner"] == "alice"
    assert reminders[-1]["queue_browser"] is False

    auth_module = types.ModuleType("core.auth")
    auth_module.AuthManager = lambda: SimpleNamespace(is_configured=True)
    monkeypatch.setitem(sys.modules, "core.auth", auth_module)
    core_pkg = importlib.import_module("core")
    monkeypatch.setattr(core_pkg, "auth", auth_module, raising=False)
    assert asyncio.run(_endpoint(router, "/api/notes/reorder", "POST")(RequestLike("alice", {"ids": ["n1", "note-uuid"]}))) == {
        "ok": True,
        "count": 2,
    }
    assert db.notes["n1"].sort_order == 0
    with pytest.raises(HTTPException) as bad_ids:
        asyncio.run(_endpoint(router, "/api/notes/reorder", "POST")(RequestLike("alice", {"ids": "bad"})))
    assert bad_ids.value.status_code == 400

    assert _endpoint(router, "/api/notes/{note_id}", "DELETE")(request, "n1") == {"ok": True}
    assert db.deleted[-1].id == "n1"


def test_dispatch_reminder_blocks_external_channels_when_offline(monkeypatch, tmp_path):
    import routes.note_routes as note_routes
    import src.settings as settings

    monkeypatch.chdir(tmp_path)
    notifications = []

    class FakeScheduler:
        def add_notification(self, **kwargs):
            notifications.append(kwargs)

    def unexpected_email_config(*_args, **_kwargs):
        raise AssertionError("disabled email reminders must not load SMTP config")

    def unexpected_integrations():
        raise AssertionError("disabled ntfy reminders must not load integrations")

    email_module = types.ModuleType("routes.email_routes")
    email_module._get_email_config = unexpected_email_config
    integrations_module = types.ModuleType("src.integrations")
    integrations_module.load_integrations = unexpected_integrations

    monkeypatch.setattr(note_routes, "_scheduler_ref", FakeScheduler())
    monkeypatch.setattr(settings, "offline_mode", lambda: True)
    monkeypatch.setattr(settings, "load_features", lambda: {"email": False, "network_notifications": False})
    monkeypatch.setitem(sys.modules, "routes.email_routes", email_module)
    monkeypatch.setitem(sys.modules, "src.integrations", integrations_module)

    monkeypatch.setattr(
        settings,
        "load_settings",
        lambda: {"reminder_channel": "email", "reminder_llm_synthesis": False},
    )
    email_result = asyncio.run(
        note_routes.dispatch_reminder("Pay invoice", "Due today", "email-note", owner="alice")
    )
    assert email_result["email_sent"] is False
    assert "disabled" in email_result["email_error"].lower()
    assert email_result["browser_sent"] is True

    monkeypatch.setattr(
        settings,
        "load_settings",
        lambda: {"reminder_channel": "ntfy", "reminder_llm_synthesis": False},
    )
    ntfy_result = asyncio.run(
        note_routes.dispatch_reminder("Check backup", "Run local check", "ntfy-note", owner="alice")
    )
    assert ntfy_result["ntfy_sent"] is False
    assert "disabled" in ntfy_result["ntfy_error"].lower()
    assert ntfy_result["browser_sent"] is True
    assert [item["task_id"] for item in notifications] == ["reminder-email-note", "reminder-ntfy-note"]


def test_vault_routes_config_login_unlock_lock_logout_and_helpers(monkeypatch, tmp_path):
    import routes.vault_routes as vault_routes

    vault_file = tmp_path / "vault.json"
    monkeypatch.setattr(vault_routes, "VAULT_FILE", vault_file)
    monkeypatch.setattr(vault_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(vault_routes, "safe_chmod", lambda path, mode: None)

    assert vault_routes._load_config() == {}
    vault_file.write_text("{", encoding="utf-8")
    assert vault_routes._load_config() == {}
    vault_routes._save_config({"session": "secret", "email": "a@example.com"})
    assert json.loads(vault_file.read_text(encoding="utf-8"))["session"] == "secret"

    monkeypatch.setattr(vault_routes, "which_tool", lambda name: "C:/bin/bw.exe")
    assert vault_routes._find_bw() == "C:/bin/bw.exe"
    monkeypatch.setattr(vault_routes, "which_tool", lambda name: "")
    monkeypatch.setattr(vault_routes, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    (npm_dir / "bw.cmd").write_text("bw", encoding="utf-8")
    assert vault_routes._find_bw().endswith("bw.cmd")

    monkeypatch.setattr(vault_routes, "_find_bw", lambda: "missing-bw")

    async def missing_exec(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(vault_routes.asyncio, "create_subprocess_exec", missing_exec)
    assert asyncio.run(vault_routes._run_bw(["--version"]))[2] == 127

    async def launch_failed(*args, **kwargs):
        raise RuntimeError("launch bad")

    monkeypatch.setattr(vault_routes.asyncio, "create_subprocess_exec", launch_failed)
    assert asyncio.run(vault_routes._run_bw(["--version"]))[2] == 1

    class Proc:
        def __init__(self, rc=0, out=b"ok", err=b""):
            self.returncode = rc
            self.out = out
            self.err = err

        async def communicate(self, input=None):
            self.input = input
            return self.out, self.err

    proc_calls = []

    async def good_exec(*args, **kwargs):
        proc_calls.append((args, kwargs))
        return Proc(out=b"session-key\n")

    monkeypatch.setattr(vault_routes.asyncio, "create_subprocess_exec", good_exec)
    stdout, stderr, rc = asyncio.run(vault_routes._run_bw(["unlock"], session="old", input_text="pw"))
    assert stdout == "session-key"
    assert rc == 0
    assert proc_calls[-1][1]["env"]["BW_SESSION"] == "old"

    original_check_bw_installed = vault_routes._check_bw_installed
    monkeypatch.setattr(vault_routes, "_check_bw_installed", lambda: asyncio.sleep(0, result=True))
    router = vault_routes.setup_vault_routes()
    request = RequestLike("alice")

    monkeypatch.setattr(vault_routes, "offline_mode", lambda: True)
    assert asyncio.run(_endpoint(router, "/api/vault/config")(request)) == {
        "server_url": "",
        "email": "",
        "unlocked": False,
        "unlocked_at": "",
        "bw_installed": False,
    }
    with pytest.raises(HTTPException) as offline_save:
        asyncio.run(_endpoint(router, "/api/vault/config", "POST")(vault_routes.VaultConfig(server_url="http://v", email="e"), request))
    assert offline_save.value.status_code == 403

    monkeypatch.setattr(vault_routes, "offline_mode", lambda: False)
    vault_routes._save_config({"server_url": "http://old", "email": "old", "session": "s", "unlocked_at": "then"})
    config = asyncio.run(_endpoint(router, "/api/vault/config")(request))
    assert config["unlocked"] is True
    assert config["bw_installed"] is True

    async def bw_config_failed(args, session=None, input_text=None):
        return "", "bad server", 1

    monkeypatch.setattr(vault_routes, "_run_bw", bw_config_failed)
    assert asyncio.run(_endpoint(router, "/api/vault/config", "POST")(vault_routes.VaultConfig(server_url="http://v/", email="e"), request)) == {
        "ok": False,
        "error": "bw config failed: bad server",
    }

    async def bw_success(args, session=None, input_text=None):
        return "new-session", "", 0

    monkeypatch.setattr(vault_routes, "_run_bw", bw_success)
    assert asyncio.run(_endpoint(router, "/api/vault/config", "POST")(vault_routes.VaultConfig(server_url="http://v/", email="e"), request)) == {"ok": True}
    assert vault_routes._load_config()["server_url"] == "http://v"

    async def bw_login_already(args, session=None, input_text=None):
        return "", "Already logged in", 1

    monkeypatch.setattr(vault_routes, "_run_bw", bw_login_already)
    assert asyncio.run(_endpoint(router, "/api/vault/login", "POST")(vault_routes.VaultLoginRequest(email="e", master_password="pw"), request)) == {
        "ok": True,
        "already": True,
    }

    async def bw_login_failed(args, session=None, input_text=None):
        return "", "wrong", 1

    monkeypatch.setattr(vault_routes, "_run_bw", bw_login_failed)
    assert asyncio.run(_endpoint(router, "/api/vault/login", "POST")(vault_routes.VaultLoginRequest(email="e", master_password="pw"), request)) == {
        "ok": False,
        "error": "Login failed: wrong",
    }

    monkeypatch.setattr(vault_routes, "_run_bw", bw_success)
    assert asyncio.run(_endpoint(router, "/api/vault/login", "POST")(vault_routes.VaultLoginRequest(email="e", master_password="pw"), request)) == {"ok": True}
    assert vault_routes._load_config()["session"] == "new-session"

    async def bw_unlock_failed(args, session=None, input_text=None):
        return "", "locked", 1

    monkeypatch.setattr(vault_routes, "_run_bw", bw_unlock_failed)
    assert asyncio.run(_endpoint(router, "/api/vault/unlock", "POST")(vault_routes.VaultUnlockRequest(master_password="pw"), request)) == {
        "ok": False,
        "error": "Unlock failed: locked",
    }

    async def bw_empty(args, session=None, input_text=None):
        return "", "", 0

    monkeypatch.setattr(vault_routes, "_run_bw", bw_empty)
    assert asyncio.run(_endpoint(router, "/api/vault/unlock", "POST")(vault_routes.VaultUnlockRequest(master_password="pw"), request)) == {
        "ok": False,
        "error": "bw returned empty session",
    }
    monkeypatch.setattr(vault_routes, "_run_bw", bw_success)
    assert asyncio.run(_endpoint(router, "/api/vault/unlock", "POST")(vault_routes.VaultUnlockRequest(master_password="pw"), request)) == {
        "ok": True,
        "message": "Vault unlocked",
    }

    assert asyncio.run(_endpoint(router, "/api/vault/lock", "POST")(request)) == {"ok": True, "message": "Vault locked"}
    assert "session" not in vault_routes._load_config()
    assert asyncio.run(_endpoint(router, "/api/vault/logout", "POST")(request)) == {"ok": True}
    assert "email" not in vault_routes._load_config()

    async def version_bad(*args, **kwargs):
        raise RuntimeError("no bw")

    monkeypatch.setattr(vault_routes, "_check_bw_installed", original_check_bw_installed)
    monkeypatch.setattr(vault_routes.asyncio, "create_subprocess_exec", version_bad)
    assert asyncio.run(vault_routes._check_bw_installed()) is False


def test_assistant_routes_remaining_error_and_status_paths(monkeypatch):
    import core.database as core_database
    import routes.assistant_routes as assistant_routes

    class CrewMember:
        id = Column("crew_id")
        owner = Column("crew_owner")
        is_default_assistant = Column("is_default")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.updated_at = None

    class ScheduledTask:
        id = Column("task_id")
        owner = Column("task_owner")
        crew_member_id = Column("task_crew")
        scheduled_time = Column("scheduled_time")

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TaskRun:
        task_id = Column("run_task_id")
        started_at = Column("started_at")

        def __init__(self, status):
            self.status = status

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model
            self.ids = []

        def filter(self, *conditions):
            for condition in conditions:
                if isinstance(condition, Expr) and condition.name in {"crew_id", "task_id"}:
                    self.ids.append(condition.value)
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            if self.model is ScheduledTask or getattr(self.model, "__name__", "") == "ScheduledTask":
                return self.db.tasks
            return []

        def first(self):
            if self.model is CrewMember:
                if self.ids and self.ids[-1] == "missing-crew":
                    return None
                return self.db.crew
            if self.model is ScheduledTask or getattr(self.model, "__name__", "") == "ScheduledTask":
                if self.ids:
                    return self.db.tasks_by_id.get(self.ids[-1])
                return self.db.tasks[0] if self.db.tasks else None
            if self.model is TaskRun or getattr(self.model, "__name__", "") == "TaskRun":
                return self.db.run
            return None

    class DB:
        def __init__(self):
            self.crew = None
            self.tasks = []
            self.tasks_by_id = {}
            self.run = None
            self.commits = 0

        def query(self, model):
            return Query(self, model)

        def commit(self):
            self.commits += 1

        def close(self):
            pass

    class Scheduler:
        def __init__(self, db):
            self.db = db
            self.seeded = []

        async def ensure_assistant_defaults(self, owner):
            self.seeded.append(owner)
            self.db.crew = CrewMember(
                id="crew1",
                owner=owner,
                name="Seeded",
                avatar="A",
                personality="p",
                model=None,
                endpoint_url=None,
                greeting="hi",
                enabled_tools="{",
                session_id="session1",
                is_default_assistant=True,
                timezone=None,
            )
            self.db.tasks = [
                ScheduledTask(
                    id="task1",
                    name="Task",
                    scheduled_time="09:00",
                    prompt="Prompt",
                    status="paused",
                    next_run=None,
                    last_run=None,
                    run_count=0,
                    schedule="daily",
                    scheduled_day=None,
                    scheduled_date=None,
                    cron_expression=None,
                    owner=owner,
                    crew_member_id="crew1",
                )
            ]
            self.db.tasks_by_id = {"task1": self.db.tasks[0]}

        async def run_task_now(self, _task_id):
            return False

    db = DB()
    scheduler = Scheduler(db)
    monkeypatch.setattr(assistant_routes, "CrewMember", CrewMember)
    monkeypatch.setattr(assistant_routes, "ScheduledTask", ScheduledTask)
    monkeypatch.setattr(core_database, "ScheduledTask", ScheduledTask)
    monkeypatch.setattr(core_database, "TaskRun", TaskRun)
    monkeypatch.setattr(assistant_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(assistant_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(assistant_routes, "compute_next_run", lambda *args, **kwargs: dt.datetime(2026, 1, 3, 9))

    router = assistant_routes.setup_assistant_routes(scheduler)
    request = RequestLike("alice")

    session = asyncio.run(_endpoint(router, "/api/assistant/session")(request))
    assert session["session_id"] == "session1"
    assert scheduler.seeded == ["alice"]

    payload = assistant_routes.AssistantSettingsUpdate(
        name="   ",
        allow_autonomous_email=False,
        check_ins=[
            assistant_routes.CheckInUpdate(id="missing", scheduled_time="10:00"),
            assistant_routes.CheckInUpdate(id="task1", name="   ", enabled=False),
        ],
    )
    updated = asyncio.run(_endpoint(router, "/api/assistant/settings", "PATCH")(payload, request))
    assert updated["crew"]["name"] == "Seeded"
    assert updated["crew"]["enabled_tools"] == []
    assert db.tasks[0].status == "paused"
    assert db.commits == 1

    db.crew.id = "missing-crew"
    with pytest.raises(HTTPException) as no_crew_db:
        asyncio.run(_endpoint(router, "/api/assistant/settings", "PATCH")(assistant_routes.AssistantSettingsUpdate(), request))
    assert no_crew_db.value.status_code == 404
    db.crew.id = "crew1"

    with pytest.raises(HTTPException) as missing_task:
        asyncio.run(_endpoint(router, "/api/assistant/run/{task_id}", "POST")("missing", request))
    assert missing_task.value.status_code == 404
    db.tasks[0].crew_member_id = "missing-crew"
    with pytest.raises(HTTPException) as not_assistant:
        asyncio.run(_endpoint(router, "/api/assistant/run/{task_id}", "POST")("task1", request))
    assert not_assistant.value.status_code == 400
    db.tasks[0].crew_member_id = "crew1"
    assert asyncio.run(_endpoint(router, "/api/assistant/run/{task_id}", "POST")("task1", request)) == {"started": False}

    fake_core_database = types.ModuleType("core.database")
    fake_core_database.ScheduledTask = ScheduledTask
    fake_core_database.TaskRun = TaskRun
    monkeypatch.setitem(sys.modules, "core.database", fake_core_database)

    with pytest.raises(HTTPException) as status_missing:
        asyncio.run(_endpoint(router, "/api/assistant/run-status/{task_id}")("missing", request))
    assert status_missing.value.status_code == 404
    db.tasks[0].owner = "bob"
    with pytest.raises(HTTPException) as status_other_owner:
        asyncio.run(_endpoint(router, "/api/assistant/run-status/{task_id}")("task1", request))
    assert status_other_owner.value.status_code == 404
    db.tasks[0].owner = None
    with pytest.raises(HTTPException) as status_legacy_owner:
        asyncio.run(_endpoint(router, "/api/assistant/run-status/{task_id}")("task1", request))
    assert status_legacy_owner.value.status_code == 404
    db.tasks[0].owner = "alice"
    assert asyncio.run(_endpoint(router, "/api/assistant/run-status/{task_id}")("task1", request)) == {"status": "unknown"}
    db.run = TaskRun("running")
    assert asyncio.run(_endpoint(router, "/api/assistant/run-status/{task_id}")("task1", request)) == {"status": "running"}
    db.run = TaskRun("failed")
    assert asyncio.run(_endpoint(router, "/api/assistant/run-status/{task_id}")("task1", request)) == {
        "status": "done",
        "result_status": "failed",
    }

    zoneinfo = types.ModuleType("zoneinfo")
    zoneinfo.available_timezones = lambda: (_ for _ in ()).throw(RuntimeError("no zones"))
    monkeypatch.setitem(sys.modules, "zoneinfo", zoneinfo)
    assert asyncio.run(_endpoint(router, "/api/assistant/available-timezones")()) == {"timezones": ["UTC"]}


def test_assistant_routes_seeded_none_is_reported(monkeypatch):
    import routes.assistant_routes as assistant_routes

    class CrewMember:
        owner = Column("crew_owner")
        is_default_assistant = Column("is_default")

    class DB:
        def query(self, _model):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return None

        def close(self):
            pass

    class Scheduler:
        async def ensure_assistant_defaults(self, _owner):
            return None

    monkeypatch.setattr(assistant_routes, "CrewMember", CrewMember)
    monkeypatch.setattr(assistant_routes, "SessionLocal", lambda: DB())
    monkeypatch.setattr(assistant_routes, "get_current_user", lambda request: request.state.current_user)

    router = assistant_routes.setup_assistant_routes(Scheduler())
    request = RequestLike("alice")
    with pytest.raises(HTTPException) as settings_missing:
        asyncio.run(_endpoint(router, "/api/assistant/settings")(request))
    assert settings_missing.value.status_code == 500
    with pytest.raises(HTTPException) as update_missing:
        asyncio.run(_endpoint(router, "/api/assistant/settings", "PATCH")(assistant_routes.AssistantSettingsUpdate(), request))
    assert update_missing.value.status_code == 500


def test_vault_routes_remaining_cli_and_offline_paths(monkeypatch, tmp_path):
    import routes.vault_routes as vault_routes

    monkeypatch.setattr(vault_routes, "which_tool", lambda _name: "")
    monkeypatch.setattr(vault_routes, "IS_WINDOWS", False)
    monkeypatch.setattr(vault_routes.os.path, "expanduser", lambda _path: str(tmp_path))
    monkeypatch.setattr(
        vault_routes.os.path,
        "isfile",
        lambda path: str(path).replace("\\", "/").endswith("/.nvm/versions/node/v1/bin/bw"),
    )
    monkeypatch.setattr(vault_routes.os, "access", lambda *_args: True)
    glob_module = types.ModuleType("glob")
    glob_module.glob = lambda _pattern: [str(tmp_path / ".nvm/versions/node/v1/bin/bw")]
    monkeypatch.setitem(sys.modules, "glob", glob_module)
    assert vault_routes._find_bw().replace("\\", "/").endswith("/.nvm/versions/node/v1/bin/bw")

    monkeypatch.setattr(vault_routes, "IS_WINDOWS", True)
    monkeypatch.setattr(vault_routes.os.path, "isfile", lambda _path: False)
    assert vault_routes._find_bw() == "bw"

    monkeypatch.setattr(vault_routes, "IS_WINDOWS", False)
    monkeypatch.setattr(vault_routes.os.path, "isfile", lambda path: str(path).replace("\\", "/").endswith("/usr/local/bin/bw"))
    assert vault_routes._find_bw() == "/usr/local/bin/bw"

    monkeypatch.setattr(vault_routes.os.path, "isfile", lambda _path: False)
    assert vault_routes._find_bw() == "bw"

    class BadProc:
        returncode = 0

        async def communicate(self, input=None):
            raise RuntimeError("pipe closed")

    async def bad_communicate_exec(*args, **kwargs):
        return BadProc()

    monkeypatch.setattr(vault_routes, "_find_bw", lambda: "bw")
    monkeypatch.setattr(vault_routes.asyncio, "create_subprocess_exec", bad_communicate_exec)
    assert asyncio.run(vault_routes._run_bw(["unlock"], input_text="pw")) == ("", "bw subprocess error: pipe closed", 1)

    class VersionProc:
        returncode = 0

        async def communicate(self):
            return b"2026", b""

    async def version_exec(*args, **kwargs):
        return VersionProc()

    monkeypatch.setattr(vault_routes.asyncio, "create_subprocess_exec", version_exec)
    assert asyncio.run(vault_routes._check_bw_installed()) is True

    monkeypatch.setattr(vault_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(vault_routes, "offline_mode", lambda: True)
    router = vault_routes.setup_vault_routes()
    request = RequestLike("alice")
    with pytest.raises(HTTPException) as login_offline:
        asyncio.run(_endpoint(router, "/api/vault/login", "POST")(vault_routes.VaultLoginRequest(email="a", master_password="pw"), request))
    assert login_offline.value.status_code == 403
    with pytest.raises(HTTPException) as unlock_offline:
        asyncio.run(_endpoint(router, "/api/vault/unlock", "POST")(vault_routes.VaultUnlockRequest(master_password="pw"), request))
    assert unlock_offline.value.status_code == 403

    vault_routes._save_config({"session": "sealed-session", "email": "a@example.com", "unlocked_at": "then"})

    async def fail_bw(*_args, **_kwargs):
        raise AssertionError("offline lock/logout should not launch bw")

    monkeypatch.setattr(vault_routes, "_run_bw", fail_bw)
    assert asyncio.run(_endpoint(router, "/api/vault/lock", "POST")(request)) == {"ok": True, "message": "Vault locked"}
    locked_cfg = vault_routes._load_config()
    assert "session" not in locked_cfg
    assert "unlocked_at" not in locked_cfg
    assert locked_cfg["email"] == "a@example.com"
    vault_routes._save_config({"session": "sealed-session", "email": "a@example.com", "unlocked_at": "then"})
    assert asyncio.run(_endpoint(router, "/api/vault/logout", "POST")(request)) == {"ok": True}
    logged_out_cfg = vault_routes._load_config()
    assert "session" not in logged_out_cfg
    assert "email" not in logged_out_cfg
    assert "unlocked_at" not in logged_out_cfg
