"""Regression tests for task-result delivery into chat sessions (issue #326)."""
import asyncio
import importlib
import sys
import types as _types
from unittest.mock import MagicMock

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, _types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from src.task_scheduler import TaskScheduler


def _unstub_sqlalchemy():
    for name, mod in list(sys.modules.items()):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            if isinstance(mod, MagicMock):
                sys.modules.pop(name, None)


def _real_database_module():
    _unstub_sqlalchemy()
    mod = sys.modules.get("core.database")
    session_model = getattr(mod, "Session", None)
    if (
        isinstance(mod, MagicMock)
        or isinstance(session_model, MagicMock)
        or not hasattr(getattr(mod, "Base", None), "metadata")
        or not hasattr(session_model, "__tablename__")
    ):
        sys.modules.pop("core.database", None)
        mod = importlib.import_module("core.database")
    return mod


def _make_db():
    _unstub_sqlalchemy()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    Base = _real_database_module().Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_task():
    return _types.SimpleNamespace(
        id="task-1",
        name="Chat Sessions Tidy",
        prompt="tidy",
        output_target="session",
        endpoint_url=None,
        model=None,
        session_id=None,
        owner=None,
        crew_member_id=None,
    )


def test_session_delivery_survives_empty_database():
    """On a fresh/wiped database there is no session to inherit endpoint/model
    from, so _resolve_defaults returns None. The delivery must still persist a
    session instead of crashing on the NOT NULL constraint (issue #326)."""
    db = _make_db()
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._session_manager = None

    asyncio.run(scheduler._deliver_task_result(_make_task(), "done", db))

    DbSession = _real_database_module().Session
    sessions = db.query(DbSession).all()
    assert len(sessions) == 1
    assert sessions[0].endpoint_url == ""
    assert sessions[0].model == ""
