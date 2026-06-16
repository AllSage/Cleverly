import datetime as dt
import importlib
import json
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def session_manager_db(monkeypatch, tmp_path):
    import core

    for child in ("database", "session_manager"):
        sys.modules.pop(f"core.{child}", None)
        if hasattr(core, child):
            delattr(core, child)

    database = importlib.import_module("core.database")
    sm = importlib.import_module("core.session_manager")

    db_path = tmp_path / "sessions.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(sm, "SessionLocal", testing_session_local)
    return sm, database, testing_session_local


def test_session_manager_full_session_lifecycle(session_manager_db):
    sm, database, SessionLocal = session_manager_db

    assert sm._message_timestamp_iso(None) is None
    assert sm._message_timestamp_iso(dt.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"
    assert sm._message_timestamp_iso(dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)) == "2026-01-02T03:04:05Z"

    manager = sm.SessionManager()
    assert manager.sessions == {}

    created = manager.create_session("s1", "First", "http://localhost/v1", "model-a", rag=True, owner="alice")
    assert created.id == "s1"
    assert created.owner == "alice"
    assert manager.get_sessions_for_user("alice") == {"s1": created}
    assert manager.get_sessions_for_user("bob") == {}
    assert manager.get_sessions_for_user() is manager.sessions
    assert manager.save_sessions() is None

    first = sm.ChatMessage("user", "hello")
    second = sm.ChatMessage("assistant", "hi", metadata={"token_count": 3})
    manager.add_message("s1", first)
    manager.add_message("s1", second)
    assert first.metadata["_db_id"]
    assert second.metadata["_db_id"]
    assert first.metadata["timestamp"].endswith("Z")

    manager.sessions.clear()
    reloaded = sm.SessionManager()
    assert reloaded.sessions["s1"].history == []
    assert reloaded.sessions["s1"].message_count == 2

    hydrated = reloaded.get_session("s1")
    assert [message.content for message in hydrated.history] == ["hello", "hi"]
    assert hydrated.history[0].metadata["_db_id"]
    assert hydrated.headers == {}

    assert reloaded.truncate_messages("s1", -1) is False
    assert reloaded.truncate_messages("s1", 1) is True
    assert [message.content for message in reloaded.get_session("s1").history] == ["hello"]

    replacements = [
        sm.ChatMessage("system", "be precise"),
        sm.ChatMessage("user", "new question", metadata={"source": "test"}),
    ]
    assert reloaded.replace_messages("s1", replacements) is True
    assert [message.content for message in reloaded.get_session("s1").history] == ["be precise", "new question"]
    assert replacements[0].metadata["_db_id"]
    assert replacements[1].metadata["_db_id"]

    reloaded.update_session_name("s1", "Renamed")
    assert reloaded.sessions["s1"].name == "Renamed"
    reloaded.archive_session("s1")
    assert reloaded.sessions["s1"].archived is True
    reloaded.mark_important("s1", True)
    assert reloaded.sessions["s1"].is_important is True

    with pytest.raises(KeyError):
        reloaded.mark_important("missing", True)

    reloaded.update_session_name("missing", "ignored")
    reloaded.archive_session("missing")
    assert reloaded.sync_session_metadata("missing") is False
    assert reloaded.delete_session("missing") is False

    with SessionLocal() as db:
        db.add(database.Document(id="doc1", session_id="s1", title="Doc", current_content="content"))
        db.commit()

    assert reloaded.delete_session("s1") is True
    assert "s1" not in reloaded.sessions
    with SessionLocal() as db:
        assert db.query(database.Session).filter(database.Session.id == "s1").first() is None
        assert db.query(database.ChatMessage).filter(database.ChatMessage.session_id == "s1").count() == 0
        assert db.query(database.Document).filter(database.Document.id == "doc1").first().session_id is None


def test_session_manager_metadata_conversion_and_empty_load(session_manager_db):
    sm, database, SessionLocal = session_manager_db

    manager = sm.SessionManager()
    empty = manager.create_session("empty", "Empty", "http://localhost/v1", "model", owner="alice")
    assert manager.get_session("empty") is empty

    with SessionLocal() as db:
        db_session = db.query(database.Session).filter(database.Session.id == "empty").first()
        db_session.headers = "{not-json"
        db_session.endpoint_url = ""
        db_session.model = ""
        db_session.rag = False
        db.commit()

    assert manager.sync_session_metadata("empty") is True
    assert manager.sessions["empty"].headers == {}
    assert manager.sessions["empty"].endpoint_url == ""
    assert manager.sessions["empty"].model == ""

    with SessionLocal() as db:
        db_session = db.query(database.Session).filter(database.Session.id == "empty").first()
        meta = manager._db_to_session_meta(db_session)
        assert meta.id == "empty"
        assert meta.history == []
        assert meta.message_count == 0
        assert manager._db_to_session(db_session, db) is None

    with pytest.raises(KeyError):
        manager.get_session("missing")


def test_session_manager_loads_existing_rows_and_cleans_empty_sessions(session_manager_db, monkeypatch):
    sm, database, SessionLocal = session_manager_db

    now = dt.datetime(2026, 1, 10, 12, 0, 0)
    old = dt.datetime(2025, 11, 1, 12, 0, 0)

    with SessionLocal() as db:
        loaded = database.Session(
            id="loaded",
            name="Loaded",
            endpoint_url="http://localhost/v1",
            model="model",
            headers={"A": "B"},
            owner="alice",
            message_count=1,
            archived=False,
            last_accessed=now,
        )
        db.add(loaded)
        db.add(database.ChatMessage(id="msg1", session_id="loaded", role="user", content="loaded message", meta_data=json.dumps(None), timestamp=old))
        db.add(database.Session(id="empty", name="Empty", endpoint_url="u", model="m", message_count=0, archived=False, last_accessed=now))
        db.add(database.Session(id="old", name="Old", endpoint_url="u", model="m", message_count=2, archived=False, last_accessed=old, is_important=False))
        db.add(database.Session(id="important", name="Important", endpoint_url="u", model="m", message_count=2, archived=False, last_accessed=old, is_important=True))
        db.commit()

    manager = sm.SessionManager()
    assert "loaded" in manager.sessions
    assert manager.sessions["loaded"].history == []
    assert manager.get_session("loaded").history[0].metadata["timestamp"].endswith("Z")

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

        @classmethod
        def utcnow(cls):
            return now

    monkeypatch.setattr(sm, "datetime", FixedDateTime)
    manager.sessions["empty"] = sm.Session("empty", "Empty", "u", "m")
    stats = manager.cleanup_empty_sessions(auto_archive_days=30)
    assert stats == {"deleted_empty": 1, "archived_old": 1, "total_checked": 4}
    assert "empty" not in manager.sessions

    with SessionLocal() as db:
        assert db.query(database.Session).filter(database.Session.id == "empty").first() is None
        assert db.query(database.Session).filter(database.Session.id == "old").first().archived is True
        assert db.query(database.Session).filter(database.Session.id == "important").first().archived is False


def test_session_manager_rollbacks_on_database_errors(monkeypatch):
    import core.session_manager as sm

    class BrokenDb:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def query(self, model):
            raise RuntimeError("db down")

        def add(self, item):
            raise RuntimeError("add down")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    broken = BrokenDb()
    monkeypatch.setattr(sm, "SessionLocal", lambda: broken)

    manager = sm.SessionManager()
    assert manager.sessions == {}
    assert broken.closed is True

    manager.sessions["s"] = sm.Session("s", "S", "u", "m")
    assert manager.sync_session_metadata("s") is False
    assert manager.delete_session("s") is False

    with pytest.raises(RuntimeError):
        manager.create_session("bad", "Bad", "u", "m")
    assert broken.rolled_back is True
