import asyncio
import importlib
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def __ne__(self, other):
        return ("ne", self.name, other)

    def __lt__(self, other):
        return ("lt", self.name, other)

    def __gt__(self, other):
        return ("gt", self.name, other)

    def __or__(self, other):
        return ("or", self, other)

    def is_(self, other):
        return ("is", self.name, other)

    def isnot(self, other):
        return ("isnot", self.name, other)


class FakeCalendarCal:
    id = Column("id")
    owner = Column("owner")
    name = Column("name")

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeCalendarEvent:
    uid = Column("uid")
    status = Column("status")
    rrule = Column("rrule")
    dtstart = Column("dtstart")
    dtend = Column("dtend")
    calendar_id = Column("calendar_id")

    def __init__(self, **kwargs):
        defaults = {
            "status": "",
            "summary": "",
            "description": "",
            "location": "",
            "all_day": False,
            "is_utc": False,
            "rrule": "",
            "color": None,
            "event_type": None,
            "importance": "normal",
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.deleted = False

    def join(self, *args):
        return self

    def filter(self, *args):
        for arg in args:
            if isinstance(arg, tuple) and len(arg) == 3 and arg[0] == "eq":
                _op, name, expected = arg
                if name in {"id", "uid", "calendar_id", "owner", "name"}:
                    self.rows = [
                        row for row in self.rows
                        if not hasattr(row, name) or getattr(row, name, None) == expected
                    ]
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)

    def delete(self):
        self.deleted = True
        count = len(self.rows)
        self.rows.clear()
        return count


class FakeDB:
    def __init__(self, calendars=None, events=None, *, fail_commit=False):
        self.calendars = list(calendars or [])
        self.events = list(events or [])
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_commit = fail_commit

    def query(self, model):
        if model is FakeCalendarCal:
            return FakeQuery(self.calendars)
        if model is FakeCalendarEvent:
            return FakeQuery(self.events)
        return FakeQuery([])

    def add(self, row):
        self.added.append(row)
        if isinstance(row, FakeCalendarCal):
            self.calendars.append(row)
        if isinstance(row, FakeCalendarEvent):
            if row.calendar_id:
                row.calendar = next((cal for cal in self.calendars if cal.id == row.calendar_id), None)
            self.events.append(row)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, row):
        return None

    def delete(self, row):
        self.deleted.append(row)
        if row in self.calendars:
            self.calendars.remove(row)
        if row in self.events:
            self.events.remove(row)

    def close(self):
        self.closed = True


class RequestLike:
    def __init__(self, body=None):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body or {}


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = cls(2026, 6, 16, 12, 0, 0)
        if tz is not None:
            return base.replace(tzinfo=timezone.utc).astimezone(tz)
        return base


def _calendar(monkeypatch):
    cal = importlib.import_module("routes.calendar_routes")
    monkeypatch.setattr(cal, "CalendarCal", FakeCalendarCal)
    monkeypatch.setattr(cal, "CalendarEvent", FakeCalendarEvent)
    monkeypatch.setattr(cal, "or_", lambda *args: ("or", args))
    monkeypatch.setattr(cal, "and_", lambda *args: ("and", args))
    monkeypatch.setattr(cal, "_require_user", lambda request: "alice")
    return cal


def _endpoint(router, path, method):
    method = method.upper()
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_calendar_auth_default_and_datetime_parsing(monkeypatch):
    cal = importlib.import_module("routes.calendar_routes")
    monkeypatch.setattr(cal, "datetime", FixedDateTime)

    req = types.SimpleNamespace()
    monkeypatch.setattr(cal, "get_current_user", lambda request: "alice")
    assert cal._require_user(req) == "alice"
    monkeypatch.setattr(cal, "get_current_user", lambda request: "")
    monkeypatch.setattr(cal, "_SINGLE_USER_MODE", True)
    assert cal._require_user(req) == cal.FALLBACK_OWNER
    monkeypatch.setattr(cal, "_SINGLE_USER_MODE", False)
    with pytest.raises(HTTPException) as exc:
        cal._require_user(req)
    assert exc.value.status_code == 401

    assert cal._parse_dt("2026-06-16").date().isoformat() == "2026-06-16"
    assert cal._parse_dt("2026-06-16T09:30:00Z") == datetime(2026, 6, 16, 9, 30)
    assert cal._parse_dt("tomorrow at 2:15pm") == datetime(2026, 6, 17, 14, 15)
    assert cal._parse_dt("next monday 9am").hour == 9
    assert cal._parse_dt("in 2 hours") == datetime(2026, 6, 16, 14, 0)
    with pytest.raises(ValueError):
        cal._parse_dt("")

    parsed, is_utc = cal._parse_dt_pair("2026-06-16T09:00:00-05:00")
    assert parsed == datetime(2026, 6, 16, 14, 0)
    assert is_utc is True
    assert cal._parse_dt_pair("2026-06-16")[1] is False
    with pytest.raises(ValueError):
        cal._parse_dt_pair("")


def test_parse_due_for_user_timezone_branches(monkeypatch):
    cal = _calendar(monkeypatch)
    monkeypatch.setattr(cal, "datetime", FixedDateTime)

    cal.set_user_tz_offset(None)
    assert cal.get_user_tz_offset() is None
    assert cal.parse_due_for_user("2026-06-16T09:00:00Z") == "2026-06-16T09:00:00+00:00"
    assert cal.parse_due_for_user("tomorrow 9pm") == "2026-06-17T21:00:00"

    cal.set_user_tz_offset(-300)
    assert cal.get_user_tz_offset() == -300
    assert cal.parse_due_for_user("2026-06-16T09:00:00").endswith("-05:00")
    assert "T21:00:00-05:00" in cal.parse_due_for_user("tonight 9pm")
    assert cal.parse_due_for_user("in 30 minutes").endswith("-05:00")
    assert cal.parse_due_for_user("7:45am").endswith("-05:00")
    assert cal.parse_due_for_user("June 20, 2026").endswith("-05:00")

    cal.set_user_tz_offset("bad")
    assert cal.get_user_tz_offset() == -300
    assert cal.parse_due_for_user("") == ""


def test_calendar_routes_config_calendar_and_event_crud(monkeypatch):
    cal = _calendar(monkeypatch)
    prefs = {}
    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load_for_user = lambda owner: prefs.get(owner, {})
    prefs_mod._save_for_user = lambda owner, data: prefs.__setitem__(owner, data)
    monkeypatch.setitem(__import__("sys").modules, "routes.prefs_routes", prefs_mod)
    monkeypatch.setattr(cal, "offline_mode", lambda: False)

    calendar = FakeCalendarCal(id="cal-1", owner="alice", name="Personal", color="#123", source="local")
    event = FakeCalendarEvent(
        uid="ev-1",
        calendar_id="cal-1",
        calendar=calendar,
        summary="Standup",
        dtstart=datetime(2026, 6, 16, 9),
        dtend=datetime(2026, 6, 16, 10),
    )
    db = FakeDB([calendar], [event])
    monkeypatch.setattr(cal, "SessionLocal", lambda: db)
    router = cal.setup_calendar_routes()

    get_config = _endpoint(router, "/api/calendar/config", "GET")
    save_config = _endpoint(router, "/api/calendar/config", "POST")
    list_cals = _endpoint(router, "/api/calendar/calendars", "GET")
    create_cal = _endpoint(router, "/api/calendar/calendars", "POST")
    update_cal = _endpoint(router, "/api/calendar/calendars/{cal_id}", "PUT")
    delete_cal = _endpoint(router, "/api/calendar/calendars/{cal_id}", "DELETE")
    list_events = _endpoint(router, "/api/calendar/events", "GET")
    create_event = _endpoint(router, "/api/calendar/events", "POST")
    update_event = _endpoint(router, "/api/calendar/events/{uid}", "PUT")
    delete_event = _endpoint(router, "/api/calendar/events/{uid}", "DELETE")
    export_ics = _endpoint(router, "/api/calendar/export/{cal_id}", "GET")

    assert asyncio.run(get_config(RequestLike()))["local"] is True
    assert asyncio.run(save_config(RequestLike({"url": ""}))) == {"ok": True, "cleared": True}
    assert asyncio.run(save_config(RequestLike({"url": "https://cal.local", "username": "u", "password": "p"}))) == {"ok": True}
    assert asyncio.run(get_config(RequestLike())) == {
        "url": "https://cal.local",
        "username": "u",
        "password": "",
        "has_password": True,
        "local": False,
    }

    assert asyncio.run(list_cals(RequestLike()))["calendars"][0]["href"] == "cal-1"
    created_cal = asyncio.run(create_cal(RequestLike(), name="Work", color="#abc"))
    assert created_cal["name"] == "Work"
    assert asyncio.run(update_cal(RequestLike(), "cal-1", name="Home", color="#456")) == {"ok": True}
    assert calendar.name == "Home" and calendar.color == "#456"

    listed = asyncio.run(list_events(RequestLike(), "2026-06-01", "2026-07-01"))
    assert listed["events"][0]["summary"] == "Standup"
    assert asyncio.run(list_events(RequestLike(), "bad", "range")) == {"events": []}

    made = asyncio.run(create_event(
        RequestLike(),
        cal.EventCreate(summary="Call", dtstart="2026-06-16T15:00:00Z", calendar_href="cal-1"),
    ))
    assert made["ok"] is True
    assert db.events[-1].is_utc is True

    assert asyncio.run(update_event(
        RequestLike(),
        "ev-1::2026-06-16T09:00",
        cal.EventUpdate(summary="Updated", all_day=True, color=""),
    )) == {"ok": True}
    assert event.summary == "Updated"
    assert event.is_utc is False
    assert event.color is None

    calendar.name = "Home/Work\r\nX-Bad: yes"
    event.uid = "ev-1\r\nATTENDEE:bad"
    event.summary = "Board, review; Q3\nLOCATION:Injected"
    event.description = "Line 1\nLine 2; with, chars\\trail"
    event.location = "HQ\nSTATUS:CANCELLED"
    event.rrule = "FREQ=DAILY\nX-BAD:YES"
    exported = asyncio.run(export_ics(RequestLike(), "cal-1"))
    ics = exported.body.decode()
    assert 'filename="Home_Work_X-Bad_yes.ics"' in exported.headers["content-disposition"]
    assert "X-WR-CALNAME:Home/Work\\nX-Bad: yes" in ics
    assert "UID:ev-1\\nATTENDEE:bad" in ics
    assert "SUMMARY:Board\\, review\\; Q3\\nLOCATION:Injected" in ics
    assert "DESCRIPTION:Line 1\\nLine 2\\; with\\, chars\\\\trail" in ics
    assert "LOCATION:HQ\\nSTATUS:CANCELLED" in ics
    assert "RRULE:FREQ=DAILYX-BAD:YES" in ics
    assert "\r\nATTENDEE:bad" not in ics
    assert "\r\nSTATUS:CANCELLED" not in ics
    event.uid = "ev-1"

    assert asyncio.run(delete_event(RequestLike(), "ev-1")) == {"ok": True}
    assert event in db.deleted
    assert asyncio.run(delete_cal(RequestLike(), "cal-1")) == {"ok": True}
    assert calendar in db.deleted


def test_calendar_routes_error_and_offline_branches(monkeypatch):
    cal = _calendar(monkeypatch)
    monkeypatch.setattr(cal, "offline_mode", lambda: True)
    router = cal.setup_calendar_routes()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_endpoint(router, "/api/calendar/config", "POST")(RequestLike({"url": "https://x"})))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_endpoint(router, "/api/calendar/test", "POST")(RequestLike({})))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_endpoint(router, "/api/calendar/sync", "POST")(RequestLike({})))
    assert exc.value.status_code == 403

    monkeypatch.setattr(cal, "offline_mode", lambda: False)
    db = FakeDB([FakeCalendarCal(id="cal-1", owner="alice", name="Personal", color="#123")], fail_commit=True)
    monkeypatch.setattr(cal, "SessionLocal", lambda: db)
    router = cal.setup_calendar_routes()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_endpoint(router, "/api/calendar/events", "POST")(
            RequestLike(), cal.EventCreate(summary="Bad", dtstart="2026-06-16")
        ))
    assert exc.value.status_code == 500
    assert db.rollbacks == 1


def test_caldav_sync_helper_blocks_offline_before_network(monkeypatch):
    from src import caldav_sync

    async def fail_to_thread(*args, **kwargs):
        raise AssertionError("offline CalDAV sync must not enter blocking network path")

    monkeypatch.setattr(caldav_sync, "offline_mode", lambda: True)
    monkeypatch.setattr(caldav_sync.asyncio, "to_thread", fail_to_thread)

    assert asyncio.run(caldav_sync.sync_caldav("alice")) == {
        "calendars": 0,
        "events": 0,
        "deleted": 0,
        "errors": ["CalDAV sync is disabled in offline mode"],
    }
