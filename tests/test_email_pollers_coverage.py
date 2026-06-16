import asyncio
import email
import json
import sqlite3
from email.mime.text import MIMEText
from types import SimpleNamespace

import pytest


class FakeResponse:
    ok = True
    status_code = 200
    text = "ok"

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeIMAP:
    def __init__(self, raw_message=None, search=b"1"):
        self.raw_message = raw_message
        self.search = search
        self.selected = []
        self.logged_out = False
        self.appended = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def select(self, folder, readonly=False):
        self.selected.append((folder, readonly))
        return "OK", [b""]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [self.search]
        if command == "FETCH" and self.raw_message:
            return "OK", [(b"1", self.raw_message)]
        return "NO", []

    def append(self, folder, flags, date_time, message):
        self.appended.append((folder, flags, message))
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


def _make_email(*, message_id="<m1@example.com>", subject="Status", sender="Sender <sender@example.com>"):
    msg = MIMEText("Long body " * 40, "plain", "utf-8")
    if message_id:
        msg["Message-ID"] = message_id
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = email.utils.formatdate()
    return msg.as_bytes()


def _create_mail_db(path, *, scheduled=False):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE email_summaries (message_id TEXT PRIMARY KEY, uid TEXT, folder TEXT, subject TEXT, sender TEXT, summary TEXT, model_used TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE email_ai_replies (message_id TEXT PRIMARY KEY, uid TEXT, folder TEXT, reply TEXT, model_used TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE email_tags (message_id TEXT PRIMARY KEY, uid TEXT, folder TEXT, subject TEXT, sender TEXT, tags TEXT, spam_verdict INTEGER, spam_reason TEXT, moved_to TEXT, model_used TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE email_calendar_extractions (message_id TEXT PRIMARY KEY, uid TEXT, events_created INTEGER, created_at TEXT)"
    )
    if scheduled:
        conn.execute(
            "CREATE TABLE scheduled_emails (id TEXT PRIMARY KEY, to_addr TEXT, cc TEXT, bcc TEXT, subject TEXT, body TEXT, in_reply_to TEXT, references_hdr TEXT, attachments TEXT, account_id TEXT, cleverly_kind TEXT, status TEXT, send_at TEXT, error TEXT)"
        )
    conn.commit()
    conn.close()


def test_emit_progress_and_run_once_restore_settings(monkeypatch):
    import routes.email_pollers as pollers

    events = []

    async def async_progress(message):
        events.append(f"async:{message}")

    asyncio.run(pollers._emit_progress(lambda message: events.append(message), "sync"))
    asyncio.run(pollers._emit_progress(async_progress, "awaited"))
    asyncio.run(pollers._emit_progress(lambda _message: (_ for _ in ()).throw(RuntimeError("ignore")), "bad"))
    asyncio.run(pollers._emit_progress(None, "ignored"))
    assert events == ["sync", "async:awaited"]

    settings = {
        "email_auto_summarize": False,
        "email_auto_reply": True,
        "email_auto_tag": False,
        "email_auto_spam": True,
        "email_auto_calendar": False,
    }
    saved = []
    monkeypatch.setattr(pollers, "_load_settings", lambda: dict(settings if not saved else saved[-1]))
    monkeypatch.setattr(pollers, "_save_settings", lambda data: saved.append(dict(data)))

    async def fake_pass(days_back=1, account_id=None, progress_cb=None):
        assert days_back == 3
        assert saved[-1]["email_auto_summarize"] is True
        assert saved[-1]["email_auto_reply"] is False
        assert saved[-1]["email_auto_tag"] is True
        return "ran"

    monkeypatch.setattr(pollers, "_auto_summarize_pass", fake_pass)
    assert asyncio.run(
        pollers._run_auto_summarize_once(
            do_summary=True,
            do_reply=False,
            do_tag=True,
            do_spam=False,
            do_calendar=True,
            days_back=3,
        )
    ) == "ran"
    assert saved[-1]["email_auto_summarize"] is False
    assert saved[-1]["email_auto_reply"] is True
    assert saved[-1]["email_auto_spam"] is True


def test_auto_summarize_fanout_single_noop_and_no_recent(monkeypatch):
    import core.database as database
    import routes.email_pollers as pollers

    class Column:
        def __eq__(self, other):
            return other

        def desc(self):
            return self

        def asc(self):
            return self

    class EmailAccount:
        enabled = Column()
        is_default = Column()
        created_at = Column()
        id = Column()

    rows = [
        SimpleNamespace(id="acct1", name="Primary", owner="alice"),
        SimpleNamespace(id="acct2", name="Backup", owner="bob"),
    ]

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return rows

        def first(self):
            return rows[0]

    class DB:
        def query(self, _model):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: DB())
    monkeypatch.setattr(database, "EmailAccount", EmailAccount, raising=False)

    calls = []
    original_single = pollers._auto_summarize_pass_single

    async def fake_single(days_back=1, account_id=None, progress_cb=None):
        calls.append((days_back, account_id))
        if account_id == "acct2":
            raise RuntimeError("offline")
        return f"done:{account_id}"

    monkeypatch.setattr(pollers, "_auto_summarize_pass_single", fake_single)
    progress = []
    result = asyncio.run(pollers._auto_summarize_pass(days_back=2, progress_cb=lambda msg: progress.append(msg)))
    assert "[Primary] done:acct1" in result
    assert "[Backup] error: offline" in result
    assert calls == [(2, "acct1"), (2, "acct2")]
    assert progress[0].startswith("Primary: starting")
    monkeypatch.setattr(pollers, "_auto_summarize_pass_single", original_single)

    monkeypatch.setattr(
        pollers,
        "_load_settings",
        lambda: {
            "email_auto_summarize": False,
            "email_auto_reply": False,
            "email_auto_tag": False,
            "email_auto_spam": False,
            "email_auto_calendar": False,
        },
    )
    assert asyncio.run(pollers._auto_summarize_pass_single()) == "Nothing to do"

    monkeypatch.setattr(
        pollers,
        "_load_settings",
        lambda: {"email_auto_summarize": True, "email_auto_reply": False},
    )
    fake_imap = FakeIMAP(search=b"")
    monkeypatch.setattr(pollers, "_imap_connect", lambda account_id=None: fake_imap)
    assert asyncio.run(pollers._auto_summarize_pass_single(account_id="acct1")) == "No recent emails"
    assert fake_imap.logged_out is True


def test_auto_summarize_single_summary_reply_and_no_model(monkeypatch, tmp_path):
    import asyncio as asyncio_module
    import core.database as database
    import requests
    import routes.email_pollers as pollers
    import src.endpoint_resolver as endpoint_resolver
    import src.llm_core as llm_core

    db_path = tmp_path / "mail.sqlite"
    _create_mail_db(db_path)
    monkeypatch.setattr(pollers, "SCHEDULED_DB", str(db_path))
    monkeypatch.setattr(
        pollers,
        "_load_settings",
        lambda: {
            "email_auto_summarize": True,
            "email_auto_reply": True,
            "email_auto_tag": False,
            "email_auto_spam": False,
            "email_auto_calendar": False,
            "email_writing_style": "brief",
        },
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return SimpleNamespace(owner="alice")

    class DB:
        def query(self, _model):
            return Query()

        def close(self):
            pass

    class EmailAccount:
        id = SimpleNamespace(__eq__=lambda self, other: other)

    monkeypatch.setattr(database, "SessionLocal", lambda: DB())
    monkeypatch.setattr(database, "EmailAccount", EmailAccount, raising=False)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda _name: ("", "", {}))
    fake_imap = FakeIMAP(raw_message=_make_email())
    monkeypatch.setattr(pollers, "_imap_connect", lambda account_id=None: fake_imap)
    assert asyncio.run(pollers._auto_summarize_pass_single(account_id="acct1")) == "No model configured"

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda _name: ("http://llm", "model", {"X": "1"}))
    monkeypatch.setattr(llm_core, "_uses_max_completion_tokens", lambda model: False)
    monkeypatch.setattr(pollers, "_extract_text", lambda msg: "Long body " * 40)
    monkeypatch.setattr(pollers, "_extract_attachment_text", lambda msg, max_chars=6000: "")
    monkeypatch.setattr(pollers, "_decode_header", lambda value: value or "")
    monkeypatch.setattr(pollers, "_extract_reply", lambda value: value.replace("<<<SUMMARY>>>", "").replace("<<<END>>>", "").strip())
    monkeypatch.setattr(pollers, "_apply_email_style_mechanics", lambda value: f"styled:{value}")
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse("<<<SUMMARY>>>\n- summarized\n<<<END>>>"))

    async def fake_llm_call_async(**kwargs):
        assert kwargs["url"] == "http://llm"
        return "Thanks for the update."

    monkeypatch.setattr(pollers, "llm_call_async", fake_llm_call_async)
    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio_module, "sleep", no_sleep)

    result = asyncio.run(pollers._auto_summarize_pass_single(account_id="acct1", progress_cb=lambda _m: None))
    assert "summarized 1" in result
    assert "drafted 1 reply" in result
    assert "summary" in result and "reply" in result

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT summary FROM email_summaries").fetchone()[0] == "- summarized"
    assert conn.execute("SELECT reply FROM email_ai_replies").fetchone()[0] == "styled:Thanks for the update."
    conn.close()


def test_scheduled_poll_once_success_failure_and_flags(monkeypatch, tmp_path):
    import routes.email_pollers as pollers

    db_path = tmp_path / "scheduled.sqlite"
    _create_mail_db(db_path, scheduled=True)
    conn = sqlite3.connect(db_path)
    rows = [
        (
            "ok1",
            "to@example.com",
            "cc@example.com",
            "",
            "Send OK",
            "Body <unsafe>",
            "<in>",
            "<refs>",
            json.dumps([]),
            "acct1",
            "follow/up",
            "pending",
            "2000-01-01T00:00:00",
            "",
        ),
        (
            "bad1",
            "fail@example.com",
            "",
            "bcc@example.com",
            "Send Fail",
            "Body",
            "",
            "",
            json.dumps(["token"]),
            "acct1",
            "scheduled",
            "pending",
            "2000-01-01T00:00:00",
            "",
        ),
    ]
    conn.executemany(
        "INSERT INTO scheduled_emails VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    sent_messages = []
    cleaned = []
    fake_imap = FakeIMAP()
    monkeypatch.setattr(pollers, "SCHEDULED_DB", str(db_path))
    monkeypatch.setattr(
        pollers,
        "_get_email_config",
        lambda account_id=None: {"from_address": "me@example.com"},
    )

    def fake_send(cfg, from_addr, recipients, message):
        if "Send Fail" in message:
            raise RuntimeError("smtp failed")
        sent_messages.append((from_addr, recipients, message))

    monkeypatch.setattr(pollers, "_send_smtp_message", fake_send)
    monkeypatch.setattr(pollers, "_imap", lambda account_id=None: fake_imap)
    monkeypatch.setattr(pollers, "_detect_sent_folder", lambda imap: "Sent Items")
    monkeypatch.setattr(pollers, "_attach_compose_uploads", lambda outer, attachments: outer.attach(MIMEText("attached")))
    monkeypatch.setattr(pollers, "_cleanup_compose_uploads", lambda attachments: cleaned.extend(attachments))

    result = pollers._scheduled_poll_once()
    assert result["sent"] == ["ok1"]
    assert result["failed"][0]["id"] == "bad1"
    assert sent_messages[0][1] == ["to@example.com", "cc@example.com"]
    assert "X-Cleverly-Kind: follow-up" in sent_messages[0][2]
    assert fake_imap.appended and fake_imap.appended[0][0] == "Sent Items"
    assert cleaned == []

    conn = sqlite3.connect(db_path)
    statuses = dict(conn.execute("SELECT id, status FROM scheduled_emails").fetchall())
    error = conn.execute("SELECT error FROM scheduled_emails WHERE id='bad1'").fetchone()[0]
    conn.close()
    assert statuses == {"ok1": "sent", "bad1": "failed"}
    assert "smtp failed" in error

    monkeypatch.setattr(pollers, "SCHEDULED_DB", str(tmp_path / "missing" / "no.sqlite"))
    errored = pollers._scheduled_poll_once()
    assert "error" in errored


def test_inprocess_poller_flag_values(monkeypatch):
    import routes.email_pollers as pollers

    monkeypatch.setenv("CLEVERLY_INPROCESS_POLLERS", "0")
    assert pollers._inprocess_pollers_enabled() is False
    pollers._start_poller()

    monkeypatch.setenv("CLEVERLY_INPROCESS_POLLERS", "yes")
    assert pollers._inprocess_pollers_enabled() is True
