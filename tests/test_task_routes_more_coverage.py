import asyncio
import json
import sys
import types
from datetime import datetime
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


class RequestLike:
    def __init__(self, user="alice", body=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, "eq", other)

    def in_(self, values):
        return (self.name, "in", tuple(values))

    def isnot(self, value):
        return (self.name, "isnot", value)

    def desc(self):
        return (self.name, "desc")


class FakeScheduledTask:
    id = Field("id")
    owner = Field("owner")
    task_type = Field("task_type")
    action = Field("action")
    status = Field("status")
    webhook_token = Field("webhook_token")
    created_at = Field("created_at")

    def __init__(self, **kwargs):
        now = datetime(2026, 1, 1, 9, 0)
        self.id = kwargs.pop("id", "task")
        self.owner = kwargs.pop("owner", "alice")
        self.name = kwargs.pop("name", "Task")
        self.prompt = kwargs.pop("prompt", "Do work")
        self.task_type = kwargs.pop("task_type", "llm")
        self.action = kwargs.pop("action", None)
        self.schedule = kwargs.pop("schedule", "daily")
        self.scheduled_time = kwargs.pop("scheduled_time", "09:00")
        self.scheduled_day = kwargs.pop("scheduled_day", None)
        self.scheduled_date = kwargs.pop("scheduled_date", None)
        self.cron_expression = kwargs.pop("cron_expression", None)
        self.trigger_type = kwargs.pop("trigger_type", "schedule")
        self.trigger_event = kwargs.pop("trigger_event", None)
        self.trigger_count = kwargs.pop("trigger_count", None)
        self.trigger_counter = kwargs.pop("trigger_counter", 0)
        self.next_run = kwargs.pop("next_run", now)
        self.last_run = kwargs.pop("last_run", None)
        self.status = kwargs.pop("status", "active")
        self.output_target = kwargs.pop("output_target", "session")
        self.session_id = kwargs.pop("session_id", "")
        self.crew_member_id = kwargs.pop("crew_member_id", None)
        self.model = kwargs.pop("model", "")
        self.endpoint_url = kwargs.pop("endpoint_url", "")
        self.run_count = kwargs.pop("run_count", 0)
        self.then_task_id = kwargs.pop("then_task_id", None)
        self.notifications_enabled = kwargs.pop("notifications_enabled", True)
        self.webhook_token = kwargs.pop("webhook_token", None)
        self.created_at = kwargs.pop("created_at", now)
        self.updated_at = kwargs.pop("updated_at", now)
        self.runs = kwargs.pop("runs", [])
        self.__dict__.update(kwargs)


class FakeTaskRun:
    id = Field("id")
    task_id = Field("task_id")
    started_at = Field("started_at")

    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", "run")
        self.task_id = kwargs.pop("task_id", "task")
        self.started_at = kwargs.pop("started_at", datetime(2026, 1, 1, 10, 0))
        self.finished_at = kwargs.pop("finished_at", None)
        self.status = kwargs.pop("status", "success")
        self.result = kwargs.pop("result", "done")
        self.error = kwargs.pop("error", None)
        self.tokens_used = kwargs.pop("tokens_used", 7)
        self.model = kwargs.pop("model", "run-model")
        self.__dict__.update(kwargs)


class Query:
    def __init__(self, db, models):
        self.db = db
        self.models = models
        self.filters = []
        self._limit = None
        self._offset = 0

    def filter(self, *exprs):
        self.filters.extend(exprs)
        return self

    def join(self, *args):
        return self

    def order_by(self, *args):
        return self

    def distinct(self):
        return self

    def limit(self, limit):
        self._limit = limit
        return self

    def offset(self, offset):
        self._offset = offset
        return self

    def all(self):
        rows = self._base_rows()
        rows = [row for row in rows if self._matches(row)]
        if self._offset:
            rows = rows[self._offset:]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        return len(self.all())

    def _base_rows(self):
        if len(self.models) == 2:
            return [
                (run, self.db.tasks.get(run.task_id))
                for run in self.db.runs
                if self.db.tasks.get(run.task_id)
            ]
        model = self.models[0]
        if model is FakeScheduledTask:
            return list(self.db.tasks.values())
        if model is FakeTaskRun:
            return list(self.db.runs)
        if isinstance(model, Field) and model.name == "owner":
            return [(task.owner,) for task in self.db.tasks.values()]
        return [self.db.recent_session] if self.db.recent_session else []

    def _matches(self, row):
        obj = row
        task = None
        run = None
        if isinstance(row, tuple):
            run, task = row
            obj = task
        elif isinstance(row, FakeTaskRun):
            run = row
        elif isinstance(row, FakeScheduledTask):
            task = row
        for expr in self.filters:
            if expr is True:
                continue
            if not isinstance(expr, tuple):
                continue
            name, op, expected = expr
            target = run if name == "task_id" and run is not None else obj
            value = getattr(target, name, None)
            if op == "eq" and value != expected:
                return False
            if op == "in" and value not in expected:
                return False
        return True


class FakeDB:
    def __init__(self):
        self.tasks = {}
        self.runs = []
        self.recent_session = None
        self.added = []
        self.deleted = []
        self.commits = 0
        self.closed = 0

    def query(self, *models):
        return Query(self, models)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, FakeScheduledTask):
            self.tasks[obj.id] = obj

    def delete(self, obj):
        self.deleted.append(obj.id)
        self.tasks.pop(obj.id, None)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        obj.refreshed = True

    def close(self):
        self.closed += 1


class Scheduler:
    def __init__(self):
        self.seeded = []
        self.runs = []
        self.stops = []
        self.notifications = [{"task_name": "Done"}]
        self.run_result = True
        self.stop_result = True

    async def ensure_defaults(self, owner):
        self.seeded.append(owner)

    def pop_notifications(self, owner=None):
        return list(self.notifications) if owner else []

    async def run_task_now(self, task_id, force=False):
        self.runs.append((task_id, force))
        return self.run_result

    async def stop_task(self, task_id):
        self.stops.append(task_id)
        return self.stop_result


def _install_task_route_fakes(monkeypatch, task_routes, db):
    monkeypatch.setattr(task_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(task_routes, "ScheduledTask", FakeScheduledTask)
    monkeypatch.setattr(task_routes, "TaskRun", FakeTaskRun)
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(task_routes, "compute_next_run", lambda *args, **kwargs: datetime(2026, 1, 2, 9, 0))
    monkeypatch.setattr(
        task_routes,
        "feature_flags",
        lambda: {"deep_research": True, "email": True, "webhooks": True, "mcp": True},
    )

    saved_prefs = []
    prefs = {"tasks_opened": False, "tasks_enabled": False}
    monkeypatch.setattr(task_routes, "_load_for_user", lambda user: dict(prefs))
    monkeypatch.setattr(task_routes, "_save_for_user", lambda user, data: saved_prefs.append((user, data)))

    fake_builtin = types.ModuleType("src.builtin_actions")
    fake_builtin.BUILTIN_ACTION_INFO = {
        "tidy_sessions": "Tidy Sessions",
        "summarize_emails": "Summarize Emails",
        "tidy_research": "Tidy Research",
        "run_local": "Run Local",
        "send_digest": "Send Digest",
    }
    monkeypatch.setitem(sys.modules, "src.builtin_actions", fake_builtin)

    fake_auth = types.ModuleType("core.auth")

    class AuthManager:
        is_configured = True

        def is_admin(self, user):
            return user == "admin"

    fake_auth.AuthManager = AuthManager
    monkeypatch.setitem(sys.modules, "core.auth", fake_auth)
    return saved_prefs


def test_task_route_helpers_and_onboarding(monkeypatch):
    import routes.task_routes as task_routes

    task = FakeScheduledTask(
        id="builtin",
        name="Tidy Chat Sessions",
        task_type="action",
        action="tidy_sessions",
        status="paused",
        runs=[FakeTaskRun(status="error", error="bad")],
    )
    as_dict = task_routes._task_to_dict(task, include_last_run_result=True)
    assert as_dict["name"] == "Chat Sessions Tidy"
    assert as_dict["is_builtin"] is True
    assert as_dict["is_modified"] is True
    assert as_dict["last_run_status"] == "error"
    assert task_routes._run_to_dict(FakeTaskRun(id="r1"))["id"] == "r1"
    assert task_routes._run_research_id(FakeScheduledTask(task_type="research", session_id="research-session")) == "research-session"
    assert task_routes._run_research_id(FakeScheduledTask(task_type="llm", session_id="chat")) == ""

    db = FakeDB()
    db.tasks["t1"] = task
    scheduler = Scheduler()
    saved_prefs = _install_task_route_fakes(monkeypatch, task_routes, db)
    router = task_routes.setup_task_routes(scheduler)
    request = RequestLike()

    listed = asyncio.run(_endpoint(router, "/api/tasks")(request, include_last_run=True))
    assert listed["tasks"][0]["id"] == "builtin"
    assert scheduler.seeded == ["alice"]

    assert asyncio.run(_endpoint(router, "/api/tasks/onboarding")(request)) == {
        "opened": False,
        "enabled": False,
    }
    updated = asyncio.run(_endpoint(router, "/api/tasks/onboarding", "POST")(request, {"enabled": True}))
    assert updated["resumed"] == 1
    assert saved_prefs[-1] == ("alice", {"tasks_opened": True, "tasks_enabled": True})


def test_task_create_update_lifecycle_and_controls(monkeypatch):
    import routes.task_routes as task_routes

    db = FakeDB()
    db.recent_session = SimpleNamespace(endpoint_url="http://llm", model="model")
    existing = FakeScheduledTask(id="existing", owner="alice", action="tidy_sessions", name="Custom")
    db.tasks[existing.id] = existing
    scheduler = Scheduler()
    _install_task_route_fakes(monkeypatch, task_routes, db)
    monkeypatch.setattr(task_routes.uuid, "uuid4", lambda: "new-task-id")
    monkeypatch.setattr(task_routes.secrets, "token_urlsafe", lambda n=32: "webhook-token")

    fake_llm = types.ModuleType("src.llm_core")
    fake_llm.llm_call_async = lambda **kwargs: asyncio.sleep(0, result="Generated Name")
    monkeypatch.setitem(sys.modules, "src.llm_core", fake_llm)

    router = task_routes.setup_task_routes(scheduler)
    request = RequestLike()
    create = _endpoint(router, "/api/tasks", "POST")

    for req, message in [
        (task_routes.TaskCreate(task_type="llm", prompt=""), "Prompt"),
        (task_routes.TaskCreate(task_type="action"), "Action"),
        (task_routes.TaskCreate(task_type="llm", prompt="x", trigger_type="schedule", schedule=None), "Schedule"),
        (task_routes.TaskCreate(task_type="llm", prompt="x", schedule="cron"), "Cron"),
        (task_routes.TaskCreate(task_type="llm", prompt="x", trigger_type="event"), "Event"),
        (task_routes.TaskCreate(task_type="llm", prompt="x", trigger_type="event", trigger_event="memory_added"), "Trigger count"),
    ]:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(create(request, req))
        assert message in exc.value.detail

    with pytest.raises(HTTPException) as admin_only:
        asyncio.run(create(request, task_routes.TaskCreate(task_type="action", action="run_local", schedule="daily")))
    assert admin_only.value.status_code == 403

    created = asyncio.run(create(request, task_routes.TaskCreate(prompt="Summarize logs", schedule="daily")))
    assert created["id"] == "new-task-id"
    assert created["name"] == "Generated Name"
    assert db.tasks["new-task-id"].notifications_enabled is True

    webhook = asyncio.run(
        create(
            request,
            task_routes.TaskCreate(name="Hook", prompt="Ping", trigger_type="webhook", schedule=None),
        )
    )
    assert webhook["webhook_token"] == "webhook-token"

    action = asyncio.run(
        create(
            request,
            task_routes.TaskCreate(task_type="action", action="tidy_sessions", schedule="daily"),
        )
    )
    assert action["name"] == "Tidy Sessions"
    assert db.tasks["new-task-id"].notifications_enabled is False

    get_task = _endpoint(router, "/api/tasks/{task_id}")
    assert asyncio.run(get_task(request, "existing"))["id"] == "existing"
    with pytest.raises(HTTPException) as denied:
        asyncio.run(get_task(RequestLike(user="bob"), "existing"))
    assert denied.value.status_code == 403
    db.tasks["legacy"] = FakeScheduledTask(id="legacy", owner=None)
    with pytest.raises(HTTPException) as legacy_read:
        asyncio.run(get_task(request, "legacy"))
    assert legacy_read.value.status_code == 403
    with pytest.raises(HTTPException) as legacy_run:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}/run", "POST")(request, "legacy"))
    assert legacy_run.value.status_code == 403

    update = _endpoint(router, "/api/tasks/{task_id}", "PUT")
    updated = asyncio.run(
        update(
            request,
            "existing",
            task_routes.TaskUpdate(
                name="Updated",
                prompt="new prompt",
                task_type="research",
                action="tidy_sessions",
                output_target="email",
                model="m",
                endpoint_url="http://e",
                trigger_type="webhook",
                trigger_event="memory_added",
                trigger_count=2,
                then_task_id="next",
                notifications_enabled=False,
                schedule="daily",
                scheduled_time="10:30",
                scheduled_day=2,
                scheduled_date="2026-02-03T04:05",
            ),
        )
    )
    assert updated["name"] == "Updated"
    assert updated["webhook_token"] == "webhook-token"
    assert updated["notifications_enabled"] is False

    with pytest.raises(HTTPException) as update_admin:
        asyncio.run(update(request, "existing", task_routes.TaskUpdate(action="run_local")))
    assert update_admin.value.status_code == 403
    with pytest.raises(HTTPException) as bad_date:
        asyncio.run(update(request, "existing", task_routes.TaskUpdate(scheduled_date="bad")))
    assert bad_date.value.status_code == 400

    assert asyncio.run(_endpoint(router, "/api/tasks/{task_id}/pause", "POST")(request, "existing"))["status"] == "paused"
    resumed = asyncio.run(_endpoint(router, "/api/tasks/{task_id}/resume", "POST")(request, "existing"))
    assert resumed["status"] == "active"
    reverted = asyncio.run(_endpoint(router, "/api/tasks/{task_id}/revert", "POST")(request, "existing"))
    assert reverted["task"]["name"] == "Chat Sessions Tidy"
    assert asyncio.run(_endpoint(router, "/api/tasks/{task_id}/run", "POST")(request, "existing", force=True))["ok"] is True
    assert scheduler.runs[-1] == ("existing", True)
    scheduler.run_result = False
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}/run", "POST")(request, "existing"))
    assert conflict.value.status_code == 409
    assert asyncio.run(_endpoint(router, "/api/tasks/{task_id}/stop", "POST")(request, "existing"))["ok"] is True
    scheduler.stop_result = False
    with pytest.raises(HTTPException) as not_running:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}/stop", "POST")(request, "existing"))
    assert not_running.value.status_code == 404

    scheduler.run_result = True
    assert asyncio.run(_endpoint(router, "/api/tasks/{task_id}/webhook/{token}", "POST")("existing", "webhook-token"))["ok"] is True
    with pytest.raises(HTTPException) as bad_hook:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}/webhook/{token}", "POST")("existing", "bad"))
    assert bad_hook.value.status_code == 404
    assert asyncio.run(_endpoint(router, "/api/tasks/{task_id}/webhook-regenerate", "POST")(request, "existing"))["webhook_token"] == "webhook-token"

    assert asyncio.run(_endpoint(router, "/api/tasks/{task_id}", "DELETE")(request, "existing")) == {"ok": True}
    with pytest.raises(HTTPException) as missing_delete:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}", "DELETE")(request, "missing"))
    assert missing_delete.value.status_code == 404


def test_task_activity_metadata_notifications_and_parse(monkeypatch):
    import routes.task_routes as task_routes

    db = FakeDB()
    task = FakeScheduledTask(id="task1", owner="alice", endpoint_url="http://endpoint", model="task-model")
    db.tasks[task.id] = task
    db.runs.extend(
        [
            FakeTaskRun(id="run1", task_id="task1", started_at=datetime(2026, 1, 1, 10, 1), result="done"),
            FakeTaskRun(id="run2", task_id="task1", started_at=datetime(2026, 1, 1, 10, 2), result="done"),
        ]
    )
    scheduler = Scheduler()
    _install_task_route_fakes(monkeypatch, task_routes, db)

    fake_agent_tools = types.ModuleType("src.agent_tools")
    fake_agent_tools.get_mcp_manager = lambda: SimpleNamespace(
        get_all_tools=lambda: [
            {"qualified_name": "mcp__mail__send", "server_name": "Mail", "name": "send_email", "description": "Send"},
            {"qualified_name": "mcp__mail__search", "server_name": "Mail", "name": "search_email", "description": "Search"},
        ]
    )
    monkeypatch.setitem(sys.modules, "src.agent_tools", fake_agent_tools)

    fake_endpoint = types.ModuleType("src.endpoint_resolver")
    fake_endpoint.resolve_endpoint = lambda kind: ("http://llm", "model", {"H": "1"})
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", fake_endpoint)
    fake_llm = types.ModuleType("src.llm_core")
    fake_llm.llm_call_async = lambda **kwargs: asyncio.sleep(
        0,
        result='```json\n{"task_type":"research","name":"AI News","prompt":"Research AI news","schedule":"weekly","scheduled_time":"07:30","scheduled_day":1,"output_target":"email"}\n```',
    )
    monkeypatch.setitem(sys.modules, "src.llm_core", fake_llm)

    router = task_routes.setup_task_routes(scheduler)
    request = RequestLike()

    assert asyncio.run(_endpoint(router, "/api/tasks/notifications")(request)) == {
        "notifications": [{"task_name": "Done"}]
    }
    assert asyncio.run(_endpoint(router, "/api/tasks/notifications")(RequestLike(user=None))) == {"notifications": []}

    recent = asyncio.run(_endpoint(router, "/api/tasks/runs/recent")(request, limit=500))
    assert len(recent["runs"]) == 2
    assert recent["runs"][0]["endpoint_url"] == "http://endpoint"
    runs = asyncio.run(_endpoint(router, "/api/tasks/{task_id}/runs")(request, "task1", limit=1, offset=0))
    assert runs["total"] == 2
    assert runs["runs"][0]["id"] == "run1"

    targets = asyncio.run(_endpoint(router, "/api/tasks/meta/output-targets")(request))
    assert "mcp__mail__send" in {item["value"] for item in targets["targets"]}
    assert "mcp__mail__search" not in {item["value"] for item in targets["targets"]}
    actions = asyncio.run(_endpoint(router, "/api/tasks/meta/actions")(request))
    assert "run_local" not in {item["name"] for item in actions["actions"]}
    admin_actions = asyncio.run(_endpoint(router, "/api/tasks/meta/actions")(RequestLike(user="admin")))
    assert "run_local" in {item["name"] for item in admin_actions["actions"]}
    assert any(item["name"] == "memory_added" for item in asyncio.run(_endpoint(router, "/api/tasks/meta/events")(request))["events"])

    empty_parse = asyncio.run(_endpoint(router, "/api/tasks/parse", "POST")(RequestLike(body={"description": " "})))
    assert empty_parse["success"] is False
    parsed = asyncio.run(
        _endpoint(router, "/api/tasks/parse", "POST")(
            RequestLike(body={"description": "Every Tuesday research AI news and email me"})
        )
    )
    assert parsed["success"] is True
    assert parsed["draft"]["task_type"] == "research"
    assert parsed["draft"]["output_target"] == "email"

    fake_endpoint.resolve_endpoint = lambda kind: ("", "", {})
    no_model = asyncio.run(_endpoint(router, "/api/tasks/parse", "POST")(RequestLike(body={"description": "daily task"})))
    assert no_model["success"] is False


def test_task_routes_hide_and_deny_disabled_online_features(monkeypatch):
    import routes.task_routes as task_routes

    db = FakeDB()
    db.tasks["existing"] = FakeScheduledTask(id="existing", owner="alice")
    db.tasks["hook"] = FakeScheduledTask(
        id="hook",
        owner="alice",
        trigger_type="webhook",
        webhook_token="webhook-token",
        status="active",
    )
    scheduler = Scheduler()
    _install_task_route_fakes(monkeypatch, task_routes, db)
    monkeypatch.setattr(
        task_routes,
        "feature_flags",
        lambda: {"deep_research": False, "email": False, "webhooks": False, "mcp": False},
    )

    fake_agent_tools = types.ModuleType("src.agent_tools")
    fake_agent_tools.get_mcp_manager = lambda: SimpleNamespace(
        get_all_tools=lambda: [
            {"qualified_name": "mcp__mail__send", "server_name": "Mail", "name": "send_email", "description": "Send"},
        ]
    )
    monkeypatch.setitem(sys.modules, "src.agent_tools", fake_agent_tools)

    fake_endpoint = types.ModuleType("src.endpoint_resolver")
    fake_endpoint.resolve_endpoint = lambda kind: ("http://llm", "model", {})
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", fake_endpoint)
    fake_llm = types.ModuleType("src.llm_core")
    fake_llm.llm_call_async = lambda **kwargs: asyncio.sleep(
        0,
        result='{"task_type":"research","name":"AI News","prompt":"Research AI news","schedule":"daily","output_target":"email"}',
    )
    monkeypatch.setitem(sys.modules, "src.llm_core", fake_llm)

    router = task_routes.setup_task_routes(scheduler)
    request = RequestLike()

    targets = asyncio.run(_endpoint(router, "/api/tasks/meta/output-targets")(request))["targets"]
    assert {item["value"] for item in targets} == {"session", "notification"}

    actions = {item["name"] for item in asyncio.run(_endpoint(router, "/api/tasks/meta/actions")(request))["actions"]}
    assert "tidy_sessions" in actions
    assert "summarize_emails" not in actions
    assert "tidy_research" not in actions

    events = {item["name"] for item in asyncio.run(_endpoint(router, "/api/tasks/meta/events")(request))["events"]}
    assert "memory_added" in events
    assert "email_received" not in events
    assert "research_completed" not in events

    create = _endpoint(router, "/api/tasks", "POST")
    disabled_creates = [
        task_routes.TaskCreate(task_type="research", prompt="Find news", schedule="daily"),
        task_routes.TaskCreate(prompt="Ping", trigger_type="webhook", schedule=None),
        task_routes.TaskCreate(prompt="Ping", schedule="daily", output_target="email"),
        task_routes.TaskCreate(task_type="action", action="summarize_emails", schedule="daily"),
    ]
    for req in disabled_creates:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(create(request, req))
        assert exc.value.status_code == 403

    update = _endpoint(router, "/api/tasks/{task_id}", "PUT")
    with pytest.raises(HTTPException) as disabled_update:
        asyncio.run(update(request, "existing", task_routes.TaskUpdate(task_type="research")))
    assert disabled_update.value.status_code == 403

    with pytest.raises(HTTPException) as disabled_hook:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}/webhook/{token}", "POST")("hook", "webhook-token"))
    assert disabled_hook.value.status_code == 403
    with pytest.raises(HTTPException) as disabled_regen:
        asyncio.run(_endpoint(router, "/api/tasks/{task_id}/webhook-regenerate", "POST")(request, "hook"))
    assert disabled_regen.value.status_code == 403

    parsed = asyncio.run(
        _endpoint(router, "/api/tasks/parse", "POST")(
            RequestLike(body={"description": "Every day research AI news and email me"})
        )
    )
    assert parsed["success"] is True
    assert parsed["draft"]["task_type"] == "llm"
    assert parsed["draft"]["output_target"] == "session"


def test_task_feature_flags_fail_closed_for_online_task_features(monkeypatch):
    import src.task_feature_guards as guards

    monkeypatch.setattr(
        guards,
        "load_features",
        lambda: (_ for _ in ()).throw(RuntimeError("settings unavailable")),
    )

    features = guards.feature_flags()
    assert features == {
        "deep_research": False,
        "email": False,
        "webhooks": False,
        "mcp": False,
    }
    assert guards.task_feature_disabled_reason({"task_type": "research"}, features)
    assert guards.task_feature_disabled_reason({"trigger_type": "webhook"}, features)
    assert guards.task_feature_disabled_reason({"output_target": "email"}, features)
    assert guards.task_feature_disabled_reason({"output_target": "mcp__mail__send"}, features)
    assert guards.action_allowed("summarize_emails", features) is False
    assert guards.action_allowed("tidy_research", features) is False
    assert guards.event_allowed("email_received", features) is False
    assert guards.event_allowed("research_completed", features) is False
