import asyncio
import importlib
import sqlite3
import sys
import types
from email.message import EmailMessage

import pytest


def _server(monkeypatch, tmp_path):
    mod = importlib.import_module("mcp_servers.email_server")
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    mod._ACCOUNT_CACHE.clear()
    return mod


def _install_secret_stub(monkeypatch):
    stub = types.ModuleType("src.secret_storage")
    stub.decrypt = lambda value: f"dec:{value}"
    monkeypatch.setitem(sys.modules, "src.secret_storage", stub)


def _create_accounts_db(data_dir, rows):
    conn = sqlite3.connect(data_dir / "app.db")
    conn.execute(
        """
        CREATE TABLE email_accounts (
            id TEXT, name TEXT, is_default INTEGER, enabled INTEGER,
            imap_host TEXT, imap_port INTEGER, imap_user TEXT, imap_password TEXT, imap_starttls INTEGER,
            smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT, from_address TEXT,
            created_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO email_accounts VALUES (
            :id, :name, :is_default, :enabled,
            :imap_host, :imap_port, :imap_user, :imap_password, :imap_starttls,
            :smtp_host, :smtp_port, :smtp_user, :smtp_password, :from_address,
            :created_at
        )
        """,
        rows,
    )
    conn.commit()
    conn.close()


def _raw_email(subject="Hello", sender="Sender <sender@example.com>", to="me@example.com"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Cc"] = "copy@example.com"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg["Message-ID"] = "<msg-1@example.com>"
    msg.set_content("Plain body")
    msg.add_attachment(b"payload", maintype="application", subtype="pdf", filename="bad/name?.pdf")
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, raw=None, move_ok=True):
        self.raw = raw or _raw_email()
        self.move_ok = move_ok
        self.calls = []
        self.sock = types.SimpleNamespace(settimeout=lambda timeout: self.calls.append(("timeout", timeout)))

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def list(self):
        return (
            "OK",
            [
                b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
                b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
                b'(\\HasNoChildren \\Archive) "/" "Archive"',
            ],
        )

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "SEARCH":
            criteria = args[-1]
            if "missing" in str(criteria):
                return "OK", [b""]
            return "OK", [b"101 102"]
        if command == "FETCH":
            query = args[-1]
            if query == "(UID)":
                return "OK", [b"1 (UID 101)", b"2 (UID 102)"]
            return "OK", [(b"101", self.raw)]
        if command == "STORE":
            return "OK", [b"stored"]
        if command == "MOVE":
            return ("OK" if self.move_ok else "NO"), [b"moved"]
        if command == "COPY":
            return "OK", [b"copied"]
        return "NO", []

    def append(self, folder, flags, date_time, message):
        self.calls.append(("append", folder, flags, bool(message)))
        return "OK", [b"[APPENDUID 9 777]"]

    def expunge(self):
        self.calls.append(("expunge",))

    def logout(self):
        self.calls.append(("logout",))


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls = []
        self.message = None
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.message = msg
        self.calls.append(("send_message", from_addr, tuple(to_addrs)))

    def quit(self):
        self.calls.append(("quit",))


def test_email_server_config_resolution_and_connectors(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    _install_secret_stub(monkeypatch)

    rows = [
        {
            "id": "a1",
            "name": "Personal",
            "is_default": 1,
            "enabled": 1,
            "imap_host": "imap.local",
            "imap_port": 993,
            "imap_user": "me@example.com",
            "imap_password": "imap-secret",
            "imap_starttls": 0,
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "from_address": "",
            "created_at": "2024-01-01",
        },
        {
            "id": "a2",
            "name": "Work",
            "is_default": 0,
            "enabled": 1,
            "imap_host": "imap.work",
            "imap_port": 143,
            "imap_user": "work@example.com",
            "imap_password": "work-secret",
            "imap_starttls": 1,
            "smtp_host": "smtp.work",
            "smtp_port": 587,
            "smtp_user": "work@example.com",
            "smtp_password": "smtp-secret",
            "from_address": "Work User <work@example.com>",
            "created_at": "2024-01-02",
        },
    ]
    _create_accounts_db(tmp_path, rows)

    assert server._list_accounts_raw()[0]["name"] == "Personal"
    assert server._resolve_account(None)["id"] == "a1"
    assert server._resolve_account("work")["id"] == "a2"
    assert server._resolve_account("wrok")["id"] == "a2"
    with pytest.raises(ValueError):
        server._load_config("unknown")

    cfg = server._load_config("work@example.com")
    assert cfg["account_name"] == "Work"
    assert cfg["imap_password"] == "dec:work-secret"
    assert cfg["smtp_password"] == "dec:smtp-secret"
    assert cfg["imap_ssl"] is False

    monkeypatch.setattr(server.imaplib, "IMAP4", lambda *args, **kwargs: FakeIMAP())
    imap = server._imap_connect("work")
    assert ("starttls",) in imap.calls
    assert ("login", "work@example.com", "dec:work-secret") in imap.calls

    FakeSMTP.instances.clear()
    monkeypatch.setattr(server.smtplib, "SMTP", FakeSMTP)
    smtp = server._smtp_connect("work", cfg=cfg)
    assert smtp.port == 587
    assert ("starttls",) in smtp.calls
    assert ("login", "work@example.com", "dec:smtp-secret") in smtp.calls

    with pytest.raises(ValueError):
        server._smtp_connect(cfg={"account_name": "empty"})


def test_email_server_settings_fallback_header_text_and_attachments(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    module_path = tmp_path / "mcp_servers" / "email_server.py"
    module_path.parent.mkdir()
    module_path.write_text("# test module path", encoding="utf-8")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(server, "__file__", str(module_path))
    (tmp_path / "data" / "settings.json").write_text(
        '{"imap_host":"legacy.local","imap_port":"1143","imap_user":"legacy@example.com",'
        '"imap_password":"pw","smtp_host":"smtp.legacy","smtp_port":"2525",'
        '"smtp_user":"legacy@example.com","smtp_password":"spw","from_address":"legacy@example.com"}',
        encoding="utf-8",
    )
    cfg = server._load_config()
    assert cfg["imap_host"] == "legacy.local"
    assert cfg["imap_port"] == 1143
    assert cfg["from_address"] == "legacy@example.com"

    assert server._clean_header_value("A\r\n B") == "A B"
    assert server._b(123) == b"123"
    assert server._uid_fetch_rows([b"1 (UID 101)", (b"skip", b"x"), b"no uid"]) == [b"1 (UID 101)"]
    assert server._folder_name_from_list_line(b'(\\Trash) "/" "[Gmail]/Trash"') == "[Gmail]/Trash"
    assert server._folder_role_from_name("[Gmail]/All Mail") == "archive"

    raw = _raw_email(subject="=?utf-8?q?Hello_=E2=9C=93?=")
    msg = server.email.message_from_bytes(raw)
    assert server._decode_header(msg["Subject"]) == "Hello  \u2713"
    assert "Plain body" in server._extract_text(msg)
    attachments = server._list_attachments_from_msg(msg)
    assert attachments[0]["filename"] == "bad/name?.pdf"
    saved = server._extract_attachment_to_disk(msg, 0, tmp_path / "attachments")
    assert saved.endswith("bad_name_.pdf")
    assert (tmp_path / "attachments" / "bad_name_.pdf").read_bytes() == b"payload"
    assert server._extract_attachment_to_disk(msg, 99, tmp_path / "attachments") is None

    html_msg = EmailMessage()
    html_msg["Subject"] = "HTML"
    html_msg.set_content("<b>plain marker</b>")
    html_msg.add_alternative("<p>Hello<br>there</p>", subtype="html")
    assert "plain marker" in server._extract_text(html_msg)


def test_email_server_imap_listing_read_search_and_mutations(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        server,
        "_load_config",
        lambda account=None: {
            "cache_db": str(tmp_path / "missing-cache.db"),
            "account_name": account or "default",
            "imap_user": "me@example.com",
            "from_address": "me@example.com",
            "archive_folder": "Archive",
            "trash_folder": "Trash",
        },
    )
    fake = FakeIMAP(move_ok=False)
    monkeypatch.setattr(server, "_imap_connect", lambda account=None: fake)

    listed = server._list_emails(max_results=1, unread_only=True, unresponded_only=True)
    assert listed[0]["uid"] == "102"
    assert listed[0]["from_address"] == "sender@example.com"
    assert server._search_emails("sender", folders=["INBOX"], max_results=1)[0]["_folder"] == "INBOX"

    read = server._read_email(uid="101", account="default")
    assert read["subject"] == "Hello"
    assert read["attachments"][0]["filename"] == "bad/name?.pdf"
    assert server._read_email(uid=None)["error"] == "No UID or Message-ID provided"

    assert server._set_flag("101", "INBOX", "\\Seen", add=False) is True
    assert server._bulk_set_flag(["101", "102"], "INBOX", "\\Deleted") == 2
    assert server._bulk_move(["101", "102"], "INBOX", "Trash", role="trash") == 2
    assert server._move_message("101", "INBOX", "Trash", role="trash") is True
    assert server._search_uids("INBOX", "UNSEEN") == [b"101", b"102"]
    assert server._delete_email("101", permanent=True) is True
    assert server._archive_email("101") is True

    downloaded = server._download_attachment("101", 0)
    assert downloaded["filename"] == "bad_name_.pdf"
    assert downloaded["size"] == len(b"payload")


def test_email_server_send_reply_and_cross_account(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    cfg = {
        "smtp_host": "smtp.local",
        "smtp_port": 465,
        "smtp_user": "me@example.com",
        "smtp_password": "pw",
        "smtp_ssl": True,
        "smtp_starttls": False,
        "from_address": "me@example.com",
        "account_name": "Personal",
        "account_id": "a1",
        "imap_user": "me@example.com",
        "archive_folder": "Archive",
        "trash_folder": "Trash",
        "cache_db": str(tmp_path / "missing.db"),
    }
    sent_imap = FakeIMAP()
    monkeypatch.setattr(server, "_load_config", lambda account=None: cfg)
    monkeypatch.setattr(server, "_list_accounts_raw", lambda: [{"id": "a1", "name": "Personal", "imap_user": "me@example.com"}])
    monkeypatch.setattr(server, "_imap_connect", lambda account=None: sent_imap)
    monkeypatch.setattr(server.smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.instances.clear()

    result = server._send_email(
        to="you@example.com, other@example.com",
        subject="Hi\r\n Inject",
        body="Body",
        cc="cc@example.com",
        bcc="bcc@example.com",
    )
    assert result["sent"] is True
    assert result["sent_folder"] == "[Gmail]/Sent Mail"
    assert result["sent_uid"] == "777"
    smtp = FakeSMTP.instances[-1]
    assert ("send_message", "me@example.com", ("you@example.com", "other@example.com", "cc@example.com", "bcc@example.com")) in smtp.calls
    assert smtp.message["Subject"] == "Hi Inject"

    reply = server._reply_to_email("101", "Reply body", reply_all=True)
    assert reply["subject"] == "Re: Hello"
    assert "sender@example.com" in reply["to"]

    monkeypatch.setattr(
        server,
        "_list_accounts_raw",
        lambda: [
            {"id": "a1", "name": "Personal", "imap_user": "one@example.com"},
            {"id": "a2", "name": "Work", "imap_user": "two@example.com"},
        ],
    )
    monkeypatch.setattr(server, "_list_emails", lambda **kwargs: [{"uid": kwargs["account"], "date": "Mon, 01 Jan 2024 12:00:00 +0000"}])
    combined, errors = server._list_emails_across_accounts(max_results=5)
    assert [item["_account"] for item in combined] == ["Personal", "Work"]
    assert errors == []

    monkeypatch.setattr(server, "_read_email", lambda **kwargs: {"error": "missing"} if kwargs["account"] == "a1" else {"subject": "Found", "account": "Work"})
    assert server._read_email_across_accounts(uid="101")["subject"] == "Found"
    monkeypatch.setattr(server, "_read_email", lambda **kwargs: {"subject": "Found", "account": kwargs["account"], "account_email": kwargs["account"]})
    assert "multiple accounts" in server._read_email_across_accounts(uid="101")["error"]


def test_email_server_mcp_tools_and_dispatch(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert {"list_emails", "read_email", "bulk_email", "send_email"} <= names

    def text(result):
        return result[0].text

    assert "Legacy single-account" in text(asyncio.run(server.call_tool("list_email_accounts", {})))

    accounts = [
        {"id": "a1", "name": "Personal", "is_default": 1, "imap_user": "me@example.com", "from_address": "me@example.com"},
        {"id": "a2", "name": "Work", "is_default": 0, "imap_user": "work@example.com", "from_address": "work@example.com"},
    ]
    monkeypatch.setattr(server, "_list_accounts_raw", lambda: accounts)
    assert "Found 2 email account" in text(asyncio.run(server.call_tool("list_email_accounts", {})))

    monkeypatch.setattr(server, "_list_emails_across_accounts", lambda **kwargs: ([{"subject": "Sub", "from": "Sender", "from_address": "sender@example.com", "date": "Today", "uid": "101", "_account": "Personal", "_account_email": "me@example.com", "summary": "Short"}], ["Work: offline"]))
    assert "EMAIL ACCOUNT CONTEXT" in text(asyncio.run(server.call_tool("list_emails", {})))

    monkeypatch.setattr(server, "_search_emails", lambda *args, **kwargs: [{"subject": "Hit", "from": "A", "from_address": "a@example.com", "date": "Today", "_folder": "INBOX", "uid": "1", "to": "b@example.com", "summary": "S"}])
    assert 'matching "Hit"' in text(asyncio.run(server.call_tool("search_emails", {"query": "Hit"})))

    monkeypatch.setattr(server, "_read_email_across_accounts", lambda **kwargs: {"subject": "Read", "from": "A", "from_address": "a@example.com", "date": "Today", "uid": "1", "account": "Personal", "account_email": "me@example.com", "message_id": "<m>", "attachments": [{"index": 0, "filename": "a.pdf", "content_type": "application/pdf", "size": 2048}], "body": "Body"})
    assert "Attachments" in text(asyncio.run(server.call_tool("read_email", {"uid": "1"})))

    assert "required" in text(asyncio.run(server.call_tool("send_email", {"to": "a"})))
    monkeypatch.setattr(server, "_send_email", lambda **kwargs: {"to": ["a@example.com"], "subject": kwargs["subject"], "account": "Personal"})
    assert "Sent email" in text(asyncio.run(server.call_tool("send_email", {"to": "a@example.com", "subject": "S", "body": "B"})))

    monkeypatch.setattr(server, "_reply_to_email", lambda **kwargs: {"subject": "Re: S", "to": "a@example.com"})
    monkeypatch.setattr(server, "_set_flag", lambda *args, **kwargs: True)
    assert "Replied to UID 1" in text(asyncio.run(server.call_tool("reply_to_email", {"uid": "1", "body": "B"})))

    monkeypatch.setattr(server, "_archive_email", lambda *args, **kwargs: True)
    monkeypatch.setattr(server, "_delete_email", lambda *args, **kwargs: False)
    assert "Archived UID 1" in text(asyncio.run(server.call_tool("archive_email", {"uid": "1"})))
    assert "Failed to delete UID 1" in text(asyncio.run(server.call_tool("delete_email", {"uid": "1"})))
    assert "Marked UID 1 as unread" in text(asyncio.run(server.call_tool("mark_email_read", {"uid": "1", "read": False})))

    monkeypatch.setattr(server, "_search_uids", lambda *args, **kwargs: [b"1", b"2"])
    monkeypatch.setattr(server, "_bulk_set_flag", lambda *args, **kwargs: 2)
    assert "2 email(s) marked read" in text(asyncio.run(server.call_tool("bulk_email", {"action": "mark_read", "all_unread": True})))
    assert "Unknown bulk action" in text(asyncio.run(server.call_tool("bulk_email", {"action": "noop", "uids": ["1"]})))
    assert "Unknown tool" in text(asyncio.run(server.call_tool("missing", {})))
