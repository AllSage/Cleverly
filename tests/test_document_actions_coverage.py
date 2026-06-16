import asyncio
import datetime as dt
import sys
import types
from types import SimpleNamespace

import pytest


class Expr:
    def __init__(self, value):
        self.value = value


class Column:
    def __eq__(self, other):
        return Expr(other)


class FakeDocumentModel:
    owner = Column()


class FakeQuery:
    def __init__(self, docs):
        self.docs = list(docs)

    def filter(self, expr):
        if isinstance(expr, Expr):
            self.docs = [doc for doc in self.docs if doc.owner == expr.value]
        return self

    def all(self):
        return self.docs


class FakeDB:
    def __init__(self, docs):
        self.docs = docs
        self.deleted = []
        self.commits = 0
        self.closed = False

    def query(self, _model):
        return FakeQuery(self.docs)

    def delete(self, doc):
        self.deleted.append(doc)
        if doc in self.docs:
            self.docs.remove(doc)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _doc(id, title, content, *, owner="alice", updated_at=None):
    now = dt.datetime(2026, 1, 1, 12, 0, 0)
    return SimpleNamespace(
        id=id,
        title=title,
        current_content=content,
        owner=owner,
        created_at=now,
        updated_at=updated_at or now,
    )


def test_document_action_helpers_normalize_fingerprint_and_length():
    import src.document_actions as actions

    assert actions._norm_title("  A   Mixed\tTitle  ") == "a mixed title"
    assert actions._content_fingerprint('<pdf upload_id="abc"> id=ann-XYZ   Text') == (
        "<pdf upload_id> id=ann text"
    )
    assert actions._real_len("# Heading\n**Bold** --- text") == len("Heading Bold text")


def test_run_document_tidy_deletes_junk_and_duplicate_documents(monkeypatch):
    import core.database as database
    import src.document_actions as actions

    newer = dt.datetime(2026, 1, 2, 12, 0, 0)
    docs = [
        _doc("empty", "Empty", ""),
        _doc("untitled", "Untitled", "# Untitled"),
        _doc("junk-title", "asdf", "real-ish content"),
        _doc("throwaway", "Real", "foo"),
        _doc("quote", "Reply", "On Monday wrote:\n> quoted\n> text\nok"),
        _doc("keep-old", "Report", 'Body upload_id="old" id=ann-abc', updated_at=dt.datetime(2026, 1, 1)),
        _doc("keep-new", "Report", 'Body upload_id="new" id=ann-def', updated_at=newer),
        _doc("other-owner", "Other", "", owner="bob"),
    ]
    db = FakeDB(docs)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(database, "Document", FakeDocumentModel)

    result = asyncio.run(actions.run_document_tidy("alice"))

    assert result.startswith("Removed 6 of 7:")
    assert "(+1 more)" in result
    assert db.commits == 1
    assert db.closed is True
    assert {doc.id for doc in db.deleted} == {
        "empty",
        "untitled",
        "junk-title",
        "throwaway",
        "quote",
        "keep-old",
    }
    assert [doc.id for doc in db.docs] == ["keep-new", "other-owner"]


def test_run_document_tidy_records_quote_and_duplicate_examples(monkeypatch):
    import core.database as database
    import src.document_actions as actions

    newer = dt.datetime(2026, 1, 2, 12, 0, 0)
    docs = [
        _doc("quote", "Thread followup", "On Monday wrote:\n> quoted\n> text"),
        _doc("dupe-old", "Plan", "same body", updated_at=dt.datetime(2026, 1, 1)),
        _doc("dupe-new", "Plan", "same body", updated_at=newer),
    ]
    db = FakeDB(docs)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(database, "Document", FakeDocumentModel)

    result = asyncio.run(actions.run_document_tidy("alice"))

    assert "Thread followup (email quote-chain only)" in result
    assert "Plan (+1 duplicate copies)" in result
    assert {doc.id for doc in db.deleted} == {"quote", "dupe-old"}


def test_run_document_tidy_noop_raises_task_noop_and_closes(monkeypatch):
    import core.database as database
    import src.document_actions as actions

    class TaskNoop(Exception):
        pass

    builtin_actions = types.ModuleType("src.builtin_actions")
    builtin_actions.TaskNoop = TaskNoop
    monkeypatch.setitem(sys.modules, "src.builtin_actions", builtin_actions)

    docs = [_doc("short", "Note", "tiny but valid", owner=None)]
    db = FakeDB(docs)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(database, "Document", FakeDocumentModel)

    with pytest.raises(TaskNoop) as exc:
        asyncio.run(actions.run_document_tidy(None))

    assert "no junk" in str(exc.value)
    assert db.commits == 0
    assert db.closed is True
