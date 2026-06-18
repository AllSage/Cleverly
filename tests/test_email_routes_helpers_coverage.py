import importlib
import sqlite3
import sys
import types
from email.message import EmailMessage

import pytest
from fastapi import BackgroundTasks, HTTPException


class Column:
    def __eq__(self, other):
        return ("eq", other)

    def desc(self):
        return ("desc",)

    def asc(self):
        return ("asc",)


class FakeEmailAccount:
    enabled = Column()
    owner = Column()
    imap_user = Column()
    from_address = Column()
    is_default = Column()
    created_at = Column()

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def filter(self, *args):
        self.filters.extend(args)
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return list(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def get(self, model, account_id):
        return next((row for row in self.rows if row.id == account_id), None)

    def query(self, model):
        return FakeQuery(self.rows)

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, *, folders=None, uid_exists=True, uid_move_status="OK", copy_status="OK", store_status="OK"):
        self.folders = folders if folders is not None else [
            b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Bin"',
            b'(\\HasNoChildren \\Archive) "/" "[Gmail]/All Mail"',
            b'(\\HasNoChildren \\Junk) "/" "Spam"',
        ]
        self.uid_exists = uid_exists
        self.uid_move_status = uid_move_status
        self.copy_status = copy_status
        self.store_status = store_status
        self.calls = []
        self.expunged = False

    def list(self):
        return "OK", self.folders

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "FETCH":
            return ("OK", [b"1 (UID 42)"]) if self.uid_exists else ("OK", [])
        if command == "SEARCH":
            return "OK", [b"42"]
        if command == "MOVE":
            return self.uid_move_status, [b"moved"]
        if command == "COPY":
            return self.copy_status, [b"copied"]
        if command == "STORE":
            return self.store_status, [b"stored"]
        return "NO", []

    def store(self, uid, op, flag):
        self.calls.append(("store", uid, op, flag))
        return self.store_status, [b"stored"]

    def copy(self, uid, dest):
        self.calls.append(("copy", uid, dest))
        return self.copy_status, [b"copied"]

    def expunge(self):
        self.expunged = True


def _email_routes():
    return importlib.import_module("routes.email_routes")


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


def _install_core_database(monkeypatch, rows):
    core_db = types.ModuleType("core.database")
    db = FakeDB(rows)
    core_db.SessionLocal = lambda: db
    core_db.EmailAccount = FakeEmailAccount
    monkeypatch.setitem(sys.modules, "core.database", core_db)

    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.and_ = lambda *args: ("and", args)
    sqlalchemy.or_ = lambda *args: ("or", args)
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy)
    return db


def test_email_owner_aliases_and_received_events(monkeypatch, tmp_path):
    routes = _email_routes()
    rows = [
        FakeEmailAccount(
            id="acct-1",
            owner="alice",
            imap_user="alice@example.com",
            from_address="Alice <alice@example.com>",
        )
    ]
    db = _install_core_database(monkeypatch, rows)
    monkeypatch.setattr(routes, "_get_email_config", lambda *args, **kwargs: {
        "account_id": "acct-1",
        "imap_user": "alice@example.com",
        "smtp_user": "smtp@example.com",
        "from_address": "Alice <alice@example.com>",
    })

    aliases = routes._email_tag_owner_aliases(None, owner="alice")
    assert aliases == ["alice", "alice@example.com", "smtp@example.com", "Alice <alice@example.com>"]
    assert db.closed is True

    fired = []
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda name, owner: fired.append((name, owner))
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)
    monkeypatch.setattr(routes, "SCHEDULED_DB", str(tmp_path / "scheduled.sqlite3"))

    emails = [{"uid": "1", "message_id": "<one>"}, {"uid": "2", "message_id": "<two>"}]
    routes._record_email_received_events("alice", None, "INBOX", emails)
    assert fired == []

    routes._record_email_received_events("alice", None, "INBOX", emails + [{"uid": "3", "message_id": "<three>"}])
    assert fired == [("email_received", "alice")]

    conn = sqlite3.connect(tmp_path / "scheduled.sqlite3")
    try:
        assert conn.execute("SELECT COUNT(*) FROM email_event_seen").fetchone()[0] == 3
    finally:
        conn.close()

    routes._record_email_received_events("", None, "INBOX", emails)
    routes._record_email_received_events("alice", None, "Sent", emails)
    routes._record_email_received_events("alice", None, "INBOX", [])
    assert fired == [("email_received", "alice")]


def test_email_folder_uid_flag_and_move_helpers():
    routes = _email_routes()
    folders = [
        b'(\\HasNoChildren \\Trash) "/" "Deleted Items"',
        b'(\\HasNoChildren) "/" Archive',
        b'(\\HasNoChildren \\Junk) "/" "Spam"',
    ]
    conn = FakeConn(folders=folders)

    assert routes._folder_name_from_list_line(folders[0]) == "Deleted Items"
    assert routes._folder_name_from_list_line("Archive") == "Archive"
    assert routes._list_imap_folders(conn)[1] == ["Deleted Items", "Archive", "Spam"]
    assert routes._resolve_mail_folder(conn, "Missing", "trash") == "Deleted Items"
    assert routes._resolve_mail_folder(conn, "Archive", "") == "Archive"
    assert routes._folder_role_from_name("[Gmail]/All Mail") == "archive"
    assert routes._folder_role_from_name("junk mail") == "junk"
    assert routes._folder_role_from_name("Deleted") == "trash"
    assert routes._folder_role_from_name("Inbox") == ""

    assert routes._uid_bytes("42") == b"42"
    assert routes._uid_bytes(b"42") == b"42"
    assert routes._uid_exists(conn, "42") is True
    assert routes._imap_uid_search(conn, "ALL") == ("OK", [b"42"])
    assert routes._imap_uid_fetch(conn, "42", "(RFC822)")[0] == "OK"
    assert routes._uid_from_fetch_meta(b"1 (UID 123 FLAGS ())") == "123"
    assert routes._uid_from_fetch_meta(b"no uid") == ""

    assert routes._store_email_flag(conn, "42", "\\Seen") is True
    assert routes._store_email_flag(conn, "42", "\\Seen", add=False) is True

    move_conn = FakeConn(uid_exists=True, uid_move_status="NO")
    assert routes._move_email_message(move_conn, "42", "Archive") is True
    assert move_conn.expunged is True
    assert any(call[1] == "COPY" for call in move_conn.calls if call[0] == "uid")

    fallback_conn = FakeConn(uid_exists=False)
    assert routes._move_email_message(fallback_conn, "42", "Trash") is True
    assert fallback_conn.expunged is True
    assert any(call[0] == "copy" for call in fallback_conn.calls)

    assert routes._move_email_message(FakeConn(uid_exists=True, uid_move_status="NO", copy_status="NO"), "42", "Archive") is False
    assert routes._list_imap_folders(types.SimpleNamespace(list=lambda: ("NO", []))) == ([], [])
    assert routes._uid_exists(types.SimpleNamespace(uid=lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))), "42") is False


def test_email_send_config_headers_markdown_and_sanitizer(monkeypatch):
    routes = _email_routes()
    rows = [
        FakeEmailAccount(id="acct-1", owner="alice", imap_user="alice@example.com", from_address="alice@example.com"),
        FakeEmailAccount(id="acct-2", owner="alice", imap_user="work@example.com", from_address="work@example.com"),
    ]
    _install_core_database(monkeypatch, rows)

    configs = {
        None: {"account_name": "Default", "account_id": "acct-1", "smtp_host": "", "smtp_user": "", "smtp_password": ""},
        "acct-1": {"account_name": "Default", "account_id": "acct-1", "smtp_host": "", "smtp_user": "", "smtp_password": ""},
        "acct-2": {"account_name": "Work", "account_id": "acct-2", "smtp_host": "smtp.local", "smtp_user": "u", "smtp_password": "p"},
    }
    monkeypatch.setattr(routes, "_get_email_config", lambda account_id=None, owner="": dict(configs[account_id]))

    assert routes._smtp_ready(configs["acct-2"]) is True
    assert routes._smtp_ready(configs[None]) is False
    assert routes._resolve_send_config(None, owner="alice")["account_id"] == "acct-2"
    with pytest.raises(ValueError, match="has no SMTP"):
        routes._resolve_send_config("acct-1", owner="alice")

    msg = EmailMessage()
    routes._apply_cleverly_headers(msg, kind="kind/with spaces", ref_id="ref with spaces and/slash")
    assert msg["X-Cleverly-Origin"] == routes.CLEVERLY_MAIL_ORIGIN
    assert msg["X-Cleverly-Kind"] == "kind-with-spaces"
    assert msg["X-Cleverly-Ref"] == "ref-with-spaces-and-slash"

    html = routes._md_to_email_html(
        "# Title\n"
        "- **bold** item\n"
        "- [link](https://example.com)\n"
        "1. `code`\n"
        "plain <script>alert(1)</script>"
    )
    assert "<h1>Title</h1>" in html
    assert "<ul>" in html and "</ul>" in html
    assert "<ol>" in html and "</ol>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "&lt;script&gt;" in html

    sanitized = routes._sanitize_email_html(
        '<p onclick="bad()">Hi <a href="javascript:bad()">bad</a>'
        '<a href="mailto:a@example.com">mail</a><script>nope()</script><br></p>'
    )
    assert sanitized == (
        '<html><body><p>Hi <a>bad</a>'
        '<a href="mailto:a@example.com" target="_blank" rel="noopener noreferrer">mail</a><br></p></body></html>'
    )
    assert routes._sanitize_email_html("<script>only hidden</script>") is None


def test_email_send_and_schedule_reject_when_feature_disabled(monkeypatch):
    routes = _email_routes()

    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_email_feature_enabled", lambda: False)
    monkeypatch.setattr(
        routes,
        "_resolve_send_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resolve SMTP")),
    )

    router = routes.setup_email_routes()
    send = _endpoint(router, "/api/email/send", "POST")
    schedule = _endpoint(router, "/api/email/schedule", "POST")

    with pytest.raises(HTTPException) as send_exc:
        import asyncio
        asyncio.run(send(routes.SendEmailRequest(to="a@example.com", subject="S", body="B"), BackgroundTasks(), owner="alice"))
    assert send_exc.value.status_code == 403

    with pytest.raises(HTTPException) as schedule_exc:
        import asyncio
        asyncio.run(schedule({"send_at": "2999-01-01T00:00:00"}, owner="alice"))
    assert schedule_exc.value.status_code == 403
