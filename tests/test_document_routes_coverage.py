import asyncio
import datetime as dt
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import Response


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class Expr:
    def __init__(self, field=None, value=None, op="eq"):
        self.field = field
        self.value = value
        self.op = op

    def __or__(self, other):
        return self

    def __and__(self, other):
        return self


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return Expr(self.name, other)

    def in_(self, values):
        return Expr(self.name, set(values), "in")

    def ilike(self, value):
        return Expr(self.name, value, "ilike")

    def is_(self, value):
        return Expr(self.name, value, "is")

    def asc(self):
        return self

    def desc(self):
        return self


class FakeSessionModel:
    id = Column("id")
    name = Column("name")
    owner = Column("owner")

    def __init__(self, id="s1", name="Session", owner="alice"):
        self.id = id
        self.name = name
        self.owner = owner


class FakeDocument:
    id = Column("id")
    session_id = Column("session_id")
    title = Column("title")
    language = Column("language")
    current_content = Column("current_content")
    version_count = Column("version_count")
    is_active = Column("is_active")
    archived = Column("archived")
    owner = Column("owner")
    created_at = Column("created_at")
    updated_at = Column("updated_at")

    def __init__(self, **kwargs):
        now = dt.datetime(2026, 1, 1, 12, 0, 0)
        self.id = kwargs.get("id", "doc-new")
        self.session_id = kwargs.get("session_id")
        self.title = kwargs.get("title", "Untitled")
        self.language = kwargs.get("language", "markdown")
        self.current_content = kwargs.get("current_content", "")
        self.version_count = kwargs.get("version_count", 1)
        self.is_active = kwargs.get("is_active", True)
        self.archived = kwargs.get("archived", False)
        self.owner = kwargs.get("owner", "alice")
        self.tidy_verdict = kwargs.get("tidy_verdict")
        self.created_at = kwargs.get("created_at", now)
        self.updated_at = kwargs.get("updated_at", now)
        self.source_email_uid = kwargs.get("source_email_uid")
        self.source_email_folder = kwargs.get("source_email_folder")
        self.source_email_account_id = kwargs.get("source_email_account_id")
        self.source_email_message_id = kwargs.get("source_email_message_id")


class FakeDocumentVersion:
    id = Column("id")
    document_id = Column("document_id")
    version_number = Column("version_number")
    created_at = Column("created_at")

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "v-new")
        self.document_id = kwargs.get("document_id", "doc1")
        self.version_number = kwargs.get("version_number", 1)
        self.content = kwargs.get("content", "")
        self.summary = kwargs.get("summary", "")
        self.source = kwargs.get("source", "user")
        self.created_at = kwargs.get("created_at", dt.datetime.now(dt.timezone.utc))


class FakeQuery:
    def __init__(self, db, items=None, *, row_mode=False, scalar_value=None):
        self.db = db
        self.items = list(items or [])
        self.row_mode = row_mode
        self.scalar_value = scalar_value

    def _value(self, item, field):
        target = item[0] if isinstance(item, tuple) else item
        return getattr(target, field, None)

    def filter(self, *conditions):
        for condition in conditions:
            if condition is False:
                self.items = []
            if isinstance(condition, Expr):
                if self.items and isinstance(self.items[0], tuple) and not hasattr(self.items[0][0], condition.field):
                    continue
                if condition.op == "eq":
                    self.items = [
                        item for item in self.items if self._value(item, condition.field) == condition.value
                    ]
                elif condition.op == "in":
                    self.items = [
                        item for item in self.items if self._value(item, condition.field) in condition.value
                    ]
                elif condition.op == "ilike":
                    needle = str(condition.value).strip("%").lower()
                    self.items = [
                        item for item in self.items if needle in str(self._value(item, condition.field) or "").lower()
                    ]
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, offset):
        self.items = self.items[offset:]
        return self

    def limit(self, limit):
        self.items = self.items[:limit]
        return self

    def all(self):
        if self.row_mode:
            return [(doc, self.db.session_name(doc.session_id)) for doc in self.items]
        return self.items

    def first(self):
        return self.items[0] if self.items else None

    def count(self):
        return len(self.items)

    def scalar(self):
        if self.scalar_value is not None:
            return self.scalar_value
        return len({doc.session_id for doc in self.db.docs if doc.session_id})


class FakeDB:
    def __init__(self):
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        self.sessions = [FakeSessionModel("s1", "Main", "alice")]
        self.docs = [
            FakeDocument(id="doc1", session_id="s1", title="Alpha", current_content="# Alpha\nBody", owner="alice"),
            FakeDocument(
                id="doc2",
                session_id="s1",
                title="Beta",
                language="python",
                current_content="print('hello world')\nprint('this is a realistic script body')",
                owner="alice",
            ),
            FakeDocument(id="junk", session_id="s1", title="Untitled", current_content="", owner="alice"),
            FakeDocument(id="inactive", session_id="s1", title="Dead", current_content="", owner="alice", is_active=False),
        ]
        self.versions = [
            FakeDocumentVersion(id="v1", document_id="doc1", version_number=1, content="old", created_at=old),
            FakeDocumentVersion(id="v2", document_id="doc1", version_number=2, content="# Alpha\nBody", created_at=old),
        ]
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def session_name(self, session_id):
        session = next((s for s in self.sessions if s.id == session_id), None)
        return session.name if session else None

    def query(self, *models):
        first = models[0]
        if first is FakeSessionModel:
            return FakeQuery(self, self.sessions)
        if first is FakeDocumentVersion:
            return FakeQuery(self, self.versions)
        if first is FakeDocument:
            return FakeQuery(self, self.docs, row_mode=len(models) > 1)
        if isinstance(first, Column) and first.name == "language":
            rows = {}
            for doc in self.docs:
                if doc.is_active:
                    rows[doc.language] = rows.get(doc.language, 0) + 1
            return FakeQuery(self, list(rows.items()))
        return FakeQuery(self, scalar_value=1)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, FakeDocument):
            self.docs.append(obj)
        elif isinstance(obj, FakeDocumentVersion):
            self.versions.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.docs:
            self.docs.remove(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _obj):
        return None

    def close(self):
        self.closed = True


class RequestLike:
    def __init__(self, *, user="alice", body=None, content_type="application/json"):
        self.state = SimpleNamespace(current_user=user, user=user)
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self.client = SimpleNamespace(host="127.0.0.1")
        self.headers = {"content-type": content_type}
        self._body = body or {}

    async def json(self):
        return self._body


@pytest.fixture
def document_routes_env(monkeypatch):
    import sqlalchemy
    import routes.document_helpers as helpers
    import routes.document_routes as document_routes
    import src.auth_helpers as auth_helpers

    db = FakeDB()
    monkeypatch.setattr(sqlalchemy, "or_", lambda *args: Expr("or", None, "or"))
    monkeypatch.setattr(
        document_routes,
        "func",
        SimpleNamespace(count=lambda *args: Expr("count", None, "count"), distinct=lambda arg: arg),
    )
    monkeypatch.setattr(document_routes, "Document", FakeDocument)
    monkeypatch.setattr(document_routes, "DocumentVersion", FakeDocumentVersion)
    monkeypatch.setattr(document_routes, "DbSession", FakeSessionModel)
    monkeypatch.setattr(helpers, "Document", FakeDocument)
    monkeypatch.setattr(helpers, "DocumentVersion", FakeDocumentVersion)
    monkeypatch.setattr(helpers, "DbSession", FakeSessionModel)
    monkeypatch.setattr(document_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(document_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(auth_helpers, "require_privilege", lambda request, privilege: request.state.current_user)
    return document_routes, db


def test_document_helper_owner_and_slug_branches(monkeypatch):
    import routes.document_helpers as helpers

    db = FakeDB()
    monkeypatch.setattr(helpers, "Document", FakeDocument)
    monkeypatch.setattr(helpers, "DbSession", FakeSessionModel)

    with pytest.raises(HTTPException) as auth_required:
        helpers._verify_doc_owner(db, FakeDocument(owner="alice"), None)
    assert auth_required.value.status_code == 403

    with pytest.raises(HTTPException) as missing_owner:
        helpers._verify_doc_owner(db, FakeDocument(owner=None, session_id=None), "alice")
    assert missing_owner.value.status_code == 404

    helpers._verify_doc_owner(db, FakeDocument(owner=None, session_id="s1"), "alice")

    with pytest.raises(HTTPException) as wrong_legacy_owner:
        helpers._verify_doc_owner(db, FakeDocument(owner=None, session_id="s1"), "bob")
    assert wrong_legacy_owner.value.status_code == 404

    assert helpers._slug(" Report Draft.pdf ") == "Report_Draft"
    assert helpers._slug("bad / name ***") == "bad_name"
    assert helpers._slug("___") == "form"

    query = FakeQuery(db, db.docs)
    assert helpers._owner_session_filter(query, None).items == []


def test_document_helper_upload_resolution_and_marker_paths(monkeypatch, tmp_path):
    import src.upload_handler as upload_module
    import routes.document_helpers as helpers

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    inside = upload_dir / "owned.pdf"
    outside = tmp_path / "outside.pdf"
    inside.write_text("owned", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")
    upload_dir_s = str(upload_dir)

    assert helpers._resolve_user_upload_path(None, "missing", "alice") is None

    class EmptyHandler:
        def resolve_upload(self, *_args, **_kwargs):
            self.upload_dir = upload_dir_s
            return None

    assert helpers._resolve_user_upload_path(EmptyHandler(), "missing", "alice") is None

    class OutsideHandler:
        def resolve_upload(self, *_args, **_kwargs):
            self.upload_dir = upload_dir_s
            return {"path": str(outside)}

    assert helpers._resolve_user_upload_path(OutsideHandler(), "bad", "alice") is None

    class InsideHandler:
        upload_dir = upload_dir_s

        def resolve_upload(self, upload_id, *, owner=None, auth_manager=None):
            self.args = (upload_id, owner, auth_manager)
            return {"path": str(inside)}

    handler = InsideHandler()
    assert helpers._resolve_user_upload_path(handler, "good", "alice", "auth") == str(inside)
    assert handler.args == ("good", "alice", "auth")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            helpers.os.path,
            "commonpath",
            lambda _paths: (_ for _ in ()).throw(ValueError("mixed drives")),
        )
        assert helpers._upload_path_inside(str(upload_dir), str(inside)) is False

    created_handlers = []

    class CreatedHandler(InsideHandler):
        def __init__(self, base_dir, resolved_upload_dir):
            created_handlers.append((base_dir, resolved_upload_dir))
            self.upload_dir = resolved_upload_dir

    monkeypatch.setattr(upload_module, "UploadHandler", CreatedHandler)
    assert helpers._locate_upload(str(upload_dir), "good", owner="alice", auth_manager="auth") == str(inside)
    assert created_handlers == [(str(tmp_path), str(upload_dir))]

    valid_upload_id = ("a" * 32) + ".pdf"
    denied_upload_id = ("b" * 32) + ".pdf"
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_manager="auth")))
    helpers._assert_pdf_marker_upload_owned(request, "# No marker", "alice", handler)
    helpers._assert_pdf_marker_upload_owned(
        request,
        f'<!-- pdf_source upload_id="{valid_upload_id}" -->',
        "alice",
        handler,
    )
    helpers._assert_pdf_marker_upload_owned(request, '<!-- pdf_source upload_id="ignored" -->', "alice", None)

    class DenyHandler(InsideHandler):
        def resolve_upload(self, *_args, **_kwargs):
            return None

    with pytest.raises(HTTPException) as rejected_marker:
        helpers._assert_pdf_marker_upload_owned(
            request,
            f'<!-- pdf_source upload_id="{denied_upload_id}" -->',
            "alice",
            DenyHandler(),
        )
    assert rejected_marker.value.status_code == 400


def test_document_helper_derive_title_branches():
    import routes.document_helpers as helpers

    assert helpers._derive_title("   \n\t") == "Untitled"

    markdown_title = helpers._derive_title("## " + ("A" * 80))
    assert markdown_title.startswith("A" * 48)
    assert markdown_title != "A" * 80

    html_title = helpers._derive_title("<h2>" + ("B" * 80) + "</h2>")
    assert html_title.startswith("B" * 48)
    assert html_title != "B" * 80

    assert helpers._derive_title("\nPlain Title:***\nBody") == "Plain Title"
    plain_long = helpers._derive_title("C" * 55)
    assert plain_long.startswith("C" * 48)
    assert plain_long != "C" * 55
    assert helpers._derive_title("x" * 61) == "Untitled"


def test_document_crud_version_archive_export_and_tidy_paths(document_routes_env, monkeypatch):
    document_routes, db = document_routes_env
    router = document_routes.setup_document_routes(session_manager=None)
    request = RequestLike()

    import src.tool_implementations as tools

    monkeypatch.setattr(tools, "_looks_like_email_document", lambda content, title: "To:" in content)

    created = asyncio.run(
        _endpoint(router, "/api/document", "POST")(
            request,
            document_routes.DocumentCreate(session_id="s1", title="Created", language="markdown", content="Hello"),
        )
    )
    assert created["title"] == "Created"
    assert any(isinstance(item, FakeDocumentVersion) and item.summary == "Initial version" for item in db.added)

    library = asyncio.run(
        _endpoint(router, "/api/documents/library")(
            request,
            search="Alpha",
            language="markdown",
            sort="alpha",
            offset=0,
            limit=10,
            archived=False,
        )
    )
    assert library["total"] >= 1
    assert library["documents"][0]["session_name"] == "Main"
    assert "markdown" in library["languages"]

    listed = asyncio.run(_endpoint(router, "/api/documents/{session_id}")(request, "s1"))
    assert {doc["id"] for doc in listed} >= {"doc1", "doc2"}

    fetched = asyncio.run(_endpoint(router, "/api/document/{doc_id}")(request, "doc1"))
    assert fetched["title"] == "Alpha"

    archived = asyncio.run(_endpoint(router, "/api/document/{doc_id}/archive", "POST")(request, "doc1", archived=True))
    assert archived == {"ok": True, "id": "doc1", "archived": True}

    unchanged = asyncio.run(
        _endpoint(router, "/api/document/{doc_id}", "PUT")(
            request,
            "doc1",
            document_routes.DocumentUpdate(content="# Alpha\nBody"),
        )
    )
    assert unchanged["current_content"] == "# Alpha\nBody"

    updated = asyncio.run(
        _endpoint(router, "/api/document/{doc_id}", "PUT")(
            request,
            "doc1",
            document_routes.DocumentUpdate(content="# Alpha\nNew", summary="edit"),
        )
    )
    assert updated["current_content"] == "# Alpha\nNew"
    assert db.commits >= 2

    patched = asyncio.run(
        _endpoint(router, "/api/document/{doc_id}", "PATCH")(
            request,
            "doc1",
            document_routes.DocumentPatch(title="Renamed", language="text", session_id=""),
        )
    )
    assert patched["title"] == "Renamed"
    assert patched["session_id"] is None

    versions = asyncio.run(_endpoint(router, "/api/document/{doc_id}/versions")(request, "doc1"))
    assert versions[0]["document_id"] if "document_id" in versions[0] else versions[0]["id"]
    version = asyncio.run(_endpoint(router, "/api/document/{doc_id}/version/{num}")(request, "doc1", 1))
    assert version["content"] == "old"

    restored = asyncio.run(_endpoint(router, "/api/document/{doc_id}/restore/{num}", "POST")(request, "doc1", 1))
    assert restored["current_content"] == "old"
    assert any(getattr(v, "summary", "") == "Restored from v1" for v in db.added)

    zip_response = asyncio.run(
        _endpoint(router, "/api/documents/export-zip", "POST")(
            RequestLike(body={"ids": ["doc1", "doc2", "inactive", "missing"]})
        )
    )
    assert isinstance(zip_response, Response)
    with zipfile.ZipFile(BytesIO(zip_response.body)) as zf:
        names = zf.namelist()
        assert names
        assert not any("Dead" in name for name in names)

    tidy = asyncio.run(_endpoint(router, "/api/documents/tidy", "POST")(request))
    assert tidy["deleted"] >= 1
    assert db.deleted

    deleted = asyncio.run(_endpoint(router, "/api/document/{doc_id}", "DELETE")(request, "doc2"))
    assert deleted == {"status": "deleted", "id": "doc2"}
    assert next(doc for doc in db.docs if doc.id == "doc2").is_active is False


def test_document_export_pdf_post_uses_reviewed_modal_values(document_routes_env, monkeypatch, tmp_path):
    document_routes, db = document_routes_env
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    source_pdf = upload_dir / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    class UploadHandler:
        def resolve_upload(self, upload_id, *, owner=None, auth_manager=None):
            assert upload_id == "source.pdf"
            assert owner == "alice"
            return {"path": str(source_pdf)}

    UploadHandler.upload_dir = str(upload_dir)

    recorded = {}

    def fake_fill_fields(pdf_path, out_path, values):
        recorded["pdf_path"] = pdf_path
        recorded["values"] = values
        with open(out_path, "wb") as handle:
            handle.write(b"%PDF-1.4\nfilled\n")

    import src.pdf_form_doc as pdf_form_doc
    import src.pdf_forms as pdf_forms

    monkeypatch.setattr(pdf_form_doc, "find_source_upload_id", lambda _content: "source.pdf")
    monkeypatch.setattr(
        pdf_form_doc,
        "load_field_sidecar",
        lambda _path: [
            {"name": "full_name", "type": "text"},
            {"name": "accepted", "type": "checkbox"},
            {"name": "signature", "type": "signature"},
        ],
    )
    monkeypatch.setattr(pdf_form_doc, "parse_markdown_to_values", lambda _content: {"full_name": "from markdown"})
    monkeypatch.setattr(pdf_form_doc, "parse_markdown_annotations", lambda _content: [])
    monkeypatch.setattr(pdf_forms, "fill_fields", fake_fill_fields)

    router = document_routes.setup_document_routes(session_manager=None, upload_handler=UploadHandler())
    request = RequestLike(
        body={
            "values": {"full_name": "Reviewed Name", "accepted": True},
            "signatures": {"not_a_signature_field": "ignored"},
        }
    )
    request.method = "POST"

    response = asyncio.run(_endpoint(router, "/api/document/{doc_id}/export-pdf", "POST")("doc1", request))

    assert response.media_type == "application/pdf"
    assert recorded["pdf_path"] == str(source_pdf)
    assert recorded["values"] == {"full_name": "Reviewed Name", "accepted": True}


def test_document_route_error_paths(document_routes_env, monkeypatch):
    document_routes, db = document_routes_env
    router = document_routes.setup_document_routes(session_manager=None)
    request = RequestLike()

    with pytest.raises(HTTPException) as missing_session:
        asyncio.run(
            _endpoint(router, "/api/document", "POST")(
                request,
                document_routes.DocumentCreate(session_id="missing", title="Bad", content="x"),
            )
        )
    assert missing_session.value.status_code == 404

    with pytest.raises(HTTPException) as missing_doc:
        asyncio.run(_endpoint(router, "/api/document/{doc_id}")(request, "missing"))
    assert missing_doc.value.status_code == 404

    with pytest.raises(HTTPException) as no_session:
        asyncio.run(_endpoint(router, "/api/documents/{session_id}")(request, "missing"))
    assert no_session.value.status_code == 404

    with pytest.raises(HTTPException) as no_ids:
        asyncio.run(_endpoint(router, "/api/documents/export-zip", "POST")(RequestLike(body={})))
    assert no_ids.value.status_code == 400

    with pytest.raises(HTTPException) as no_versions:
        asyncio.run(_endpoint(router, "/api/document/{doc_id}/version/{num}")(request, "doc1", 99))
    assert no_versions.value.status_code == 404

    with pytest.raises(HTTPException) as no_restore_version:
        asyncio.run(_endpoint(router, "/api/document/{doc_id}/restore/{num}", "POST")(request, "doc1", 99))
    assert no_restore_version.value.status_code == 404

    db.docs[0].owner = "bob"
    with pytest.raises(HTTPException) as wrong_owner:
        asyncio.run(_endpoint(router, "/api/document/{doc_id}")(request, "doc1"))
    assert wrong_owner.value.status_code == 404

    db.docs[0].owner = "alice"
    db.commit = lambda: (_ for _ in ()).throw(RuntimeError("commit bad"))
    with pytest.raises(HTTPException) as update_bad:
        asyncio.run(
            _endpoint(router, "/api/document/{doc_id}", "PUT")(
                request,
                "doc1",
                document_routes.DocumentUpdate(content="different"),
            )
        )
    assert update_bad.value.status_code == 500
    assert db.rollbacks == 1
