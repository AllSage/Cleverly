import asyncio
import importlib
import json
import sqlite3
import sys
import types
from contextlib import contextmanager
from email.message import EmailMessage

import pytest


def _endpoint(router, path, method="GET"):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"endpoint {method} {path} not found")


def _raw_message(*, uid="101", subject="Hello", sender="Alice <alice@example.com>", date="Mon, 01 Jan 2024 12:00:00 +0000", multipart=False):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "Bob <bob@example.com>"
    msg["Cc"] = "Team <team@example.com>"
    msg["Date"] = date
    msg["Message-ID"] = f"<msg-{uid}@example.com>"
    msg["In-Reply-To"] = "<old@example.com>"
    msg["References"] = "<old@example.com>"
    msg.set_content(f"Plain body {uid}")
    if multipart:
        msg.add_attachment(b"payload", maintype="application", subtype="pdf", filename="file.pdf")
    return msg.as_bytes()


class FakeListConn:
    def __init__(self):
        self.logged_out = False
        self.searches = []
        self.fetches = []

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return "OK", [b"2"]

    def uid(self, command, *args):
        if command == "SEARCH":
            criteria = args[-1]
            self.searches.append(criteria)
            if "HEADER Message-ID" in str(criteria):
                return "OK", [b"101"]
            return "OK", [b"101 102"]
        if command == "FETCH":
            self.fetches.append(args)
            requested = args[0]
            requested_text = requested.decode() if isinstance(requested, bytes) else str(requested)
            include_101 = "101" in requested_text
            include_102 = "102" in requested_text
            rows = []
            if include_101:
                rows.append(
                    (
                        b"1 (UID 101 FLAGS (\\Seen \\Flagged) RFC822.SIZE 321)",
                        _raw_message(uid="101", subject="First", multipart=True),
                    )
                )
            if include_102:
                rows.append(
                    (
                        b"2 (UID 102 FLAGS (\\Answered) RFC822.SIZE 123)",
                        _raw_message(uid="102", subject="Second", date="Tue, 02 Jan 2024 12:00:00 +0000"),
                    )
                )
            if rows:
                return "OK", rows
            return "OK", [
                (
                    b"1 (UID 101 FLAGS (\\Seen \\Flagged) RFC822.SIZE 321)",
                    _raw_message(uid="101", subject="First", multipart=True),
                ),
                (
                    b"2 (UID 102 FLAGS (\\Answered) RFC822.SIZE 123)",
                    _raw_message(uid="102", subject="Second", date="Tue, 02 Jan 2024 12:00:00 +0000"),
                ),
            ]
        return "NO", []

    def logout(self):
        self.logged_out = True


class FakeReadConn:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "FETCH":
            return "OK", [(b"1 (UID 101)", self.raw)]
        if command == "STORE":
            return "OK", [b"stored"]
        return "NO", []


def _setup_email_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE email_tags (owner TEXT, folder TEXT, uid TEXT, message_id TEXT, tags TEXT, spam_verdict INTEGER, spam_reason TEXT)")
    conn.execute("CREATE TABLE email_summaries (message_id TEXT, summary TEXT)")
    conn.execute("CREATE TABLE email_ai_replies (message_id TEXT, reply TEXT)")
    conn.execute("CREATE TABLE email_boundaries (message_id TEXT, sig_start INTEGER, quote_start INTEGER, turns_json TEXT)")
    conn.execute("CREATE TABLE sender_signatures (from_address TEXT, signature_text TEXT)")
    conn.execute(
        "INSERT INTO email_tags VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("alice", "INBOX", "101", "<msg-101@example.com>", json.dumps(["promo"]), 1, "spam"),
    )
    conn.execute("INSERT INTO email_summaries VALUES (?, ?)", ("<msg-101@example.com>", "cached summary"))
    conn.execute("INSERT INTO email_ai_replies VALUES (?, ?)", ("<msg-101@example.com>", "cached reply"))
    conn.execute(
        "INSERT INTO email_boundaries VALUES (?, ?, ?, ?)",
        ("<msg-101@example.com>", 10, 20, json.dumps({"v": 7, "turns": [{"speaker": "alice"}]})),
    )
    conn.execute("INSERT INTO sender_signatures VALUES (?, ?)", ("alice@example.com", "Best,\nAlice"))
    conn.commit()
    conn.close()


def _email_routes(monkeypatch, tmp_path):
    routes = importlib.import_module("routes.email_routes")
    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_email_tag_owner_aliases", lambda account_id, owner: [owner or ""])
    monkeypatch.setattr(routes, "SCHEDULED_DB", str(tmp_path / "email.sqlite3"))
    _setup_email_db(tmp_path / "email.sqlite3")
    thread_parser = types.ModuleType("src.email_thread_parser")
    thread_parser.THREAD_PARSER_VERSION = 7
    thread_parser.parse_thread = lambda html, text: [{"speaker": "parsed"}]
    monkeypatch.setitem(sys.modules, "src.email_thread_parser", thread_parser)
    return routes


@pytest.mark.asyncio
async def test_email_account_connection_test_blocks_offline_sockets(monkeypatch, tmp_path):
    routes = _email_routes(monkeypatch, tmp_path)

    class RequestLike:
        async def json(self):
            return {
                "imap_host": "imap.example.test",
                "imap_port": 993,
                "imap_user": "alice",
                "imap_password": "secret",
                "smtp_host": "smtp.example.test",
                "smtp_port": 465,
                "smtp_user": "alice",
                "smtp_password": "secret",
            }

    class BlockedSocket:
        def __init__(self, *args, **kwargs):
            raise AssertionError("offline email connection test should not open sockets")

    monkeypatch.setattr(routes.imaplib, "IMAP4", BlockedSocket)
    monkeypatch.setattr(routes.imaplib, "IMAP4_SSL", BlockedSocket)
    monkeypatch.setattr(routes.smtplib, "SMTP", BlockedSocket)
    monkeypatch.setattr(routes.smtplib, "SMTP_SSL", BlockedSocket)

    endpoint = _endpoint(routes.setup_email_routes(), "/api/email/accounts/test", "POST")

    monkeypatch.setattr(routes, "offline_mode", lambda: True)
    with pytest.raises(routes.HTTPException) as exc:
        await endpoint(RequestLike(), owner="alice")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Email connection tests are disabled"

    monkeypatch.setattr(routes, "offline_mode", lambda: False)
    monkeypatch.setattr(routes, "_email_feature_enabled", lambda: False)
    with pytest.raises(routes.HTTPException) as disabled_exc:
        await endpoint(RequestLike(), owner="alice")

    assert disabled_exc.value.status_code == 403
    assert disabled_exc.value.detail == "Email connection tests are disabled"


def test_email_list_endpoint_uses_fake_imap_cache_tags_and_attachment_filter(monkeypatch, tmp_path):
    routes = _email_routes(monkeypatch, tmp_path)
    conn = FakeListConn()
    monkeypatch.setattr(routes, "_imap_connect", lambda account_id=None, owner="": conn)
    monkeypatch.setattr(routes, "_record_email_received_events", lambda *args, **kwargs: None)

    router = routes.setup_email_routes()
    list_endpoint = _endpoint(router, "/api/email/list")

    first = asyncio.run(
        list_endpoint(
            folder="INBOX",
            limit=5,
            offset=0,
            filter="all",
            from_addr=None,
            account_id=None,
            has_attachments=0,
            cache_bust=None,
            owner="alice",
        )
    )
    assert first["total"] == 2
    assert first["emails"][0]["uid"] == "102"
    assert first["emails"][1]["tags"] == ["marketing"]
    assert first["emails"][1]["is_spam_verdict"] is True
    assert conn.logged_out is True

    cached = asyncio.run(
        list_endpoint(
            folder="INBOX",
            limit=5,
            offset=0,
            filter="all",
            from_addr=None,
            account_id=None,
            has_attachments=0,
            cache_bust=None,
            owner="alice",
        )
    )
    assert cached == first

    attached = asyncio.run(
        list_endpoint(
            folder="INBOX",
            limit=5,
            offset=0,
            filter="all",
            from_addr=None,
            account_id=None,
            has_attachments=1,
            cache_bust="x",
            owner="alice",
        )
    )
    assert [email["uid"] for email in attached["emails"]] == ["101"]

    tagged = asyncio.run(
        list_endpoint(
            folder="INBOX",
            limit=5,
            offset=0,
            filter="tag:marketing",
            from_addr=None,
            account_id=None,
            has_attachments=0,
            cache_bust="y",
            owner="alice",
        )
    )
    assert tagged["emails"][0]["uid"] == "101"
    assert any("HEADER Message-ID" in str(criteria) for criteria in conn.searches)


def test_email_read_endpoint_populates_cache_and_cached_metadata(monkeypatch, tmp_path):
    routes = _email_routes(monkeypatch, tmp_path)
    raw = _raw_message(uid="101", subject="Read me", multipart=True)
    read_conns = []

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        conn = FakeReadConn(raw)
        read_conns.append(conn)
        yield conn

    monkeypatch.setattr(routes, "_imap", fake_imap)
    monkeypatch.setattr(routes, "_extract_text", lambda msg: "Plain body")
    monkeypatch.setattr(routes, "_extract_html", lambda msg: "<p>Plain body</p>")
    monkeypatch.setattr(routes, "_list_attachments_from_msg", lambda msg: [{"filename": "file.pdf"}])
    monkeypatch.setattr(routes, "_extract_reply", lambda text: text)
    monkeypatch.setattr(routes, "_apply_email_style_mechanics", lambda text: f"styled:{text}")

    router = routes.setup_email_routes()
    read_endpoint = _endpoint(router, "/api/email/read/{uid}")

    result = asyncio.run(read_endpoint("101", folder="INBOX", account_id=None, mark_seen=True, owner="alice"))
    assert result["subject"] == "Read me"
    assert result["cached_summary"] == "cached summary"
    assert result["cached_ai_reply"] == "styled:cached reply"
    assert result["boundaries"] == {"sig_start": 10, "quote_start": 20}
    assert result["thread_turns"] == [{"speaker": "alice"}]
    assert result["sender_signature"] == "Best,\nAlice"
    assert any(call[0] == "uid" and call[1] == "STORE" for conn in read_conns for call in conn.calls)

    cached = asyncio.run(read_endpoint("101", folder="INBOX", account_id=None, mark_seen=False, owner="alice"))
    assert cached == result
