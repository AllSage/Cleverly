import importlib
import json
import sys
import types
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest
from fastapi import HTTPException


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def desc(self):
        return ("desc",)

    def asc(self):
        return ("asc",)


class FakeEmailAccount:
    id = Column("id")
    enabled = Column("enabled")
    owner = Column("owner")
    imap_user = Column("imap_user")
    from_address = Column("from_address")
    is_default = Column("is_default")
    created_at = Column("created_at")

    def __init__(self, **kwargs):
        defaults = {
            "name": "Mail",
            "smtp_host": "smtp.local",
            "smtp_port": 465,
            "smtp_user": "smtp-user",
            "smtp_password": "enc-smtp",
            "imap_host": "imap.local",
            "imap_port": 993,
            "imap_user": "imap-user",
            "imap_password": "enc-imap",
            "imap_starttls": False,
            "from_address": "me@example.com",
            "owner": "",
            "enabled": True,
            "is_default": False,
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []

    def filter(self, *args):
        self.filters.extend(args)
        for arg in args:
            if isinstance(arg, tuple) and len(arg) == 3 and arg[0] == "eq":
                _op, name, expected = arg
                self.rows = [row for row in self.rows if getattr(row, name, None) == expected]
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = list(rows)
        self.closed = False

    def query(self, model):
        return FakeQuery(self.rows)

    def close(self):
        self.closed = True


def _helpers():
    return importlib.import_module("routes.email_helpers")


def _install_core_database(monkeypatch, rows, *, raise_on_query=False):
    core_db = types.ModuleType("core.database")

    class RaisingDB(FakeDB):
        def query(self, model):
            if raise_on_query:
                raise RuntimeError("db down")
            return super().query(model)

    db = RaisingDB(rows)
    core_db.SessionLocal = lambda: db
    core_db.EmailAccount = FakeEmailAccount
    monkeypatch.setitem(sys.modules, "core.database", core_db)

    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.and_ = lambda *args: ("and", args)
    sqlalchemy.or_ = lambda *args: ("or", args)
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy)
    return db


def test_smtp_message_tls_modes_and_fallback(monkeypatch):
    helpers = _helpers()
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            sent.append(("open", "plain", self.host, self.port, self.timeout))
            return self

        def __exit__(self, *exc):
            sent.append(("close", self.port))

        def starttls(self):
            sent.append(("starttls", self.port))

        def login(self, user, password):
            sent.append(("login", user, password))

        def sendmail(self, from_addr, recipients, message):
            sent.append(("send", from_addr, tuple(recipients), message))

    class FailingSSL(FakeSMTP):
        def __enter__(self):
            raise TimeoutError("slow")

    monkeypatch.setattr(helpers.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(helpers.smtplib, "SMTP_SSL", FailingSSL)

    cfg = {"smtp_host": "smtp.local", "smtp_port": 587, "smtp_user": "u", "smtp_password": "p"}
    helpers._send_smtp_message(cfg, "me@example.com", ["you@example.com"], "body", timeout=7)
    assert ("starttls", 587) in sent
    assert ("login", "u", "p") in sent

    sent.clear()
    cfg["smtp_port"] = 465
    helpers._send_smtp_message(cfg, "me@example.com", ["you@example.com"], b"body")
    assert sent[0] == ("open", "plain", "smtp.local", 587, 30)


def test_text_reply_style_and_auth(monkeypatch):
    helpers = _helpers()
    assert helpers._strip_think("<think>secret</think>Visible").strip() == "Visible"
    assert helpers._extract_reply("noise <<<SUMMARY>>>Final\n<<<END>>> tail") == "Final"
    assert helpers._extract_reply("plain <think>x</think>answer") == "plain answer"
    assert helpers._apply_email_style_mechanics("plain text") == "plain text"

    auth_helpers = importlib.import_module("src.auth_helpers")
    monkeypatch.setattr(auth_helpers, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(helpers, "get_current_user", lambda request: "alice")
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()), client=None)
    assert helpers._require_auth(req) == "alice"

    monkeypatch.setattr(helpers, "get_current_user", lambda request: "")
    configured = types.SimpleNamespace(is_configured=True)
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(auth_manager=configured)), client=None)
    with pytest.raises(HTTPException) as exc:
        helpers._require_auth(req)
    assert exc.value.status_code == 401

    req = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(auth_manager=types.SimpleNamespace(is_configured=False))),
        client=types.SimpleNamespace(host="127.0.0.1"),
    )
    assert helpers._require_auth(req) == ""

    req.client.host = "10.0.0.5"
    with pytest.raises(HTTPException):
        helpers._require_auth(req)


def test_account_owner_config_list_and_settings(monkeypatch, tmp_path):
    helpers = _helpers()
    rows = [
        FakeEmailAccount(id="acct-1", owner="alice", is_default=True, from_address="alice@example.com"),
        FakeEmailAccount(id="acct-2", owner="bob", from_address="bob@example.com"),
        FakeEmailAccount(id="acct-legacy", owner=None, from_address="legacy@example.com"),
    ]
    db = _install_core_database(monkeypatch, rows)
    monkeypatch.setattr(helpers, "_decrypt", lambda value: f"dec:{value}")

    cfg = helpers._get_email_config("acct-1", owner="alice")
    assert cfg["account_id"] == "acct-1"
    assert cfg["smtp_password"] == "dec:enc-smtp"
    assert cfg["imap_password"] == "dec:enc-imap"
    assert db.closed is True

    helpers._assert_owns_account("acct-1", "alice")
    with pytest.raises(HTTPException) as exc:
        helpers._assert_owns_account("acct-2", "alice")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as legacy_exc:
        helpers._assert_owns_account("acct-legacy", "alice")
    assert legacy_exc.value.status_code == 404

    denied_cfg = helpers._get_email_config("acct-2", owner="alice")
    assert denied_cfg["account_id"] == "acct-2"
    assert denied_cfg["smtp_host"] == ""
    assert denied_cfg["imap_host"] == ""
    missing_cfg = helpers._get_email_config("missing", owner="alice")
    assert missing_cfg["account_id"] == "missing"
    assert missing_cfg["smtp_host"] == ""

    assert [acct["account_id"] for acct in helpers._list_email_accounts()] == ["acct-1", "acct-2", "acct-legacy"]

    _install_core_database(monkeypatch, [], raise_on_query=True)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"smtp_host": "settings-smtp", "smtp_port": "2525"}), encoding="utf-8")
    monkeypatch.setattr(helpers, "SETTINGS_FILE", settings_file)
    monkeypatch.setenv("IMAP_HOST", "env-imap")
    monkeypatch.setenv("SMTP_USER", "env-user")
    fallback = helpers._get_email_config(owner="alice")
    assert fallback["smtp_host"] == "settings-smtp"
    assert fallback["smtp_port"] == 2525
    assert fallback["imap_host"] == "env-imap"
    assert fallback["smtp_user"] == "env-user"

    saved = tmp_path / "saved.json"
    monkeypatch.setattr(helpers, "SETTINGS_FILE", saved)
    helpers._save_settings({"ok": True})
    assert helpers._load_settings() == {"ok": True}


def test_attachments_and_message_extractors(monkeypatch, tmp_path):
    helpers = _helpers()
    monkeypatch.setattr(helpers, "COMPOSE_UPLOADS_DIR", tmp_path)
    token = "tok_report.txt"
    (tmp_path / token).write_text("hello", encoding="utf-8")
    outer = MIMEMultipart()
    helpers._attach_compose_uploads(outer, [f"../{token}", "missing.txt"])
    payload = outer.get_payload()
    assert len(payload) == 1
    assert payload[0].get_filename() == "report.txt"
    helpers._cleanup_compose_uploads([token])
    assert not (tmp_path / token).exists()

    msg = MIMEMultipart()
    msg.attach(MIMEText("plain body", "plain"))
    msg.attach(MIMEText("<p>html body</p>", "html"))
    att = MIMEText("attachment text", "plain")
    att.add_header("Content-Disposition", "attachment", filename="notes.txt")
    msg.attach(att)

    listed = helpers._list_attachments_from_msg(msg)
    assert listed == [{"index": 0, "filename": "notes.txt", "content_type": "text/plain", "size": 15, "is_inline": False}]
    assert "attachment text" in helpers._extract_attachment_text(msg)
    path = helpers._extract_attachment_to_disk(msg, 0, tmp_path / "out")
    assert path.name == "notes.txt"
    assert path.read_text(encoding="utf-8") == "attachment text"
    assert helpers._extract_attachment_to_disk(msg, 9, tmp_path / "out") is None
    assert helpers._extract_text(msg) == "plain body"
    assert helpers._extract_html(msg) == "<p>html body</p>"

    html_only = EmailMessage()
    html_only.set_content("<br>Hello <b>world</b>", subtype="html")
    assert helpers._extract_text(html_only) == "<br>Hello <b>world</b>\n"
    assert helpers._extract_html(html_only).startswith("<br>Hello")


def test_imap_connect_context_folders_and_move(monkeypatch):
    helpers = _helpers()
    monkeypatch.setattr(helpers, "_get_email_config", lambda account_id=None, owner="": {
        "imap_host": "imap.local",
        "imap_port": 143,
        "imap_user": "user",
        "imap_password": "pass",
        "imap_starttls": True,
    })
    calls = []

    class FakeIMAP:
        def __init__(self, host, port, timeout=15):
            self.host = host
            self.port = port
            self.sock = types.SimpleNamespace(settimeout=lambda timeout: calls.append(("sock", timeout)))

        def starttls(self):
            calls.append(("starttls", self.host, self.port))

        def login(self, user, password):
            calls.append(("login", user, password))

        def logout(self):
            calls.append(("logout",))

    monkeypatch.setattr(helpers.imaplib, "IMAP4", FakeIMAP)
    conn = helpers._imap_connect("acct", owner="alice")
    assert isinstance(conn, FakeIMAP)
    assert ("starttls", "imap.local", 143) in calls
    assert ("login", "user", "pass") in calls

    released = []
    helpers._POOL_HOOKS["connect"] = lambda account_id, owner="": (types.SimpleNamespace(name=owner), False)
    helpers._POOL_HOOKS["release"] = lambda account_id, conn, ok=True, owner="": released.append((account_id, conn.name, ok, owner))
    with helpers._imap("acct", owner="alice") as pooled:
        assert pooled.name == "alice"
    assert released == [("acct", "alice", True, "alice")]
    helpers._POOL_HOOKS["connect"] = None
    helpers._POOL_HOOKS["release"] = None

    class FolderConn:
        def __init__(self, folders):
            self.folders = folders

        def list(self):
            return "OK", self.folders

    folders = [
        b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
        b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
        b'(\\HasNoChildren \\Junk) "/" "Junk Mail"',
    ]
    folder_conn = FolderConn(folders)
    assert helpers._detect_sent_folder(folder_conn) == "[Gmail]/Sent Mail"
    assert helpers._detect_drafts_folder(folder_conn) == "Drafts"
    assert helpers._detect_spam_folder(folder_conn) == "Junk Mail"
    assert helpers._detect_spam_folder(FolderConn([])) is None
    assert helpers._decode_header("=?utf-8?q?Hello_=E2=9C=93?=") == "Hello ✓"

    class MoveConn:
        def __init__(self, copy_status="OK"):
            self.copy_status = copy_status
            self.calls = []

        def select(self, folder):
            self.calls.append(("select", folder))

        def copy(self, uid, dest):
            self.calls.append(("copy", uid, dest))
            return self.copy_status, []

        def store(self, uid, op, flag):
            self.calls.append(("store", uid, op, flag))

        def expunge(self):
            self.calls.append(("expunge",))

        def logout(self):
            self.calls.append(("logout",))

    mover = MoveConn()
    monkeypatch.setattr(helpers, "_imap_connect", lambda: mover)
    assert helpers._imap_move("42", "Archive", src='Bad "Folder') is True
    assert ('select', '"Bad \\"Folder"') in mover.calls

    monkeypatch.setattr(helpers, "_imap_connect", lambda: MoveConn(copy_status="NO"))
    assert helpers._imap_move("42", "Archive") is False


def test_sender_thread_context_and_pre_retrieve(monkeypatch):
    helpers = _helpers()
    message = EmailMessage()
    message["Subject"] = "Project Echo"
    message["From"] = "Sender <sender@example.com>"
    message["Date"] = "Tue, 16 Jun 2026 10:00:00 -0500"
    message.set_content("Body about Project Echo")

    class ContextIMAP:
        def __init__(self):
            self.selected = []
            self.closed = False
            self.logged_out = False

        def select(self, folder, readonly=False):
            self.selected.append(folder)
            return "OK", []

        def search(self, charset, *criteria):
            joined = " ".join(str(c) for c in criteria)
            if "sender@example.com" in joined or "Project Echo" in joined:
                return "OK", [b"1 2"]
            return "OK", [b""]

        def fetch(self, uid, spec):
            return "OK", [(b"1", message.as_bytes())]

        def close(self):
            self.closed = True

        def logout(self):
            self.logged_out = True

    ctx = ContextIMAP()
    monkeypatch.setattr(helpers, "_imap_connect", lambda *args, **kwargs: ctx)
    assert "Project Echo" in helpers._fetch_sender_thread_context("sender@example.com", limit=1)
    assert ctx.closed is True and ctx.logged_out is True

    contacts = types.ModuleType("routes.contacts_routes")
    contacts._fetch_contacts = lambda: [
        {"name": "Project Echo", "email": "sender@example.com", "phone": "555-1212"},
    ]
    monkeypatch.setitem(sys.modules, "routes.contacts_routes", contacts)
    snippets, terms = helpers._pre_retrieve_context(
        "Please review Project Echo and Alice Report for Wednesday.", "Sender <sender@example.com>"
    )
    assert terms[:2] == ["Project Echo", "Alice Report"]
    assert any("Project Echo" in snippet for snippet in snippets)

    snippets, terms = helpers._pre_retrieve_context("Cold Sender Topic", "cold@example.com")
    assert snippets == [] and terms == []
