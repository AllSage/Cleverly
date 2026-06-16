import builtins
import json
import sys
import types

import pytest

from src import pdf_form_doc as pdfdoc


VALID_UPLOAD_ID = "0123456789abcdef0123456789abcdef.pdf"


def test_annotation_parser_unescapes_defaults_and_skips_bad(caplog):
    content = "\n".join(
        [
            r"- Line\nTwo\\Done <!-- annotation id=a-1 page=2 x=10.5 y=20 w=30 h=40 kind=signature lh=1.8 -->",
            r"- _(empty)_ <!-- annotation id=empty page=1 x=1 y=2 w=3 h=4 -->",
            r"- Bad <!-- annotation id=bad page=1 x=12.3.4 y=2 w=3 h=4 -->",
        ]
    )

    annotations = pdfdoc.parse_markdown_annotations(content)

    assert annotations == [
        {
            "id": "a-1",
            "page": 2,
            "x": 10.5,
            "y": 20.0,
            "w": 30.0,
            "h": 40.0,
            "kind": "signature",
            "line_height": 1.8,
            "value": "Line\nTwo\\Done",
        },
        {
            "id": "empty",
            "page": 1,
            "x": 1.0,
            "y": 2.0,
            "w": 3.0,
            "h": 4.0,
            "kind": "text",
            "line_height": 1.3,
            "value": "",
        },
    ]
    assert "Skipping malformed annotation bullet" in caplog.text
    assert pdfdoc.parse_markdown_annotations(None) == []
    assert pdfdoc._unescape_annotation_value(r"\q") == "q"


def test_sidecar_save_load_success_missing_and_errors(tmp_path, monkeypatch, caplog):
    pdf_path = str(tmp_path / "form.pdf")
    fields = [{"name": "full name", "type": "text"}]

    assert pdfdoc.sidecar_path(pdf_path).endswith("form.pdf.fields.json")
    assert pdfdoc.load_field_sidecar(pdf_path) is None
    saved_path = pdfdoc.save_field_sidecar(pdf_path, fields)
    assert json.loads((tmp_path / "form.pdf.fields.json").read_text(encoding="utf-8")) == fields
    assert pdfdoc.load_field_sidecar(pdf_path) == fields
    assert saved_path == pdfdoc.sidecar_path(pdf_path)

    (tmp_path / "broken.pdf.fields.json").write_text("{", encoding="utf-8")
    assert pdfdoc.load_field_sidecar(str(tmp_path / "broken.pdf")) is None
    assert "Failed to read field sidecar" in caplog.text

    real_open = builtins.open

    def failing_open(path, *args, **kwargs):
        if str(path).endswith("fail.pdf.fields.json"):
            raise OSError("no write")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    assert pdfdoc.save_field_sidecar(str(tmp_path / "fail.pdf"), fields).endswith(
        "fail.pdf.fields.json"
    )
    assert "Failed to write field sidecar" in caplog.text


def test_source_upload_id_accepts_form_and_plain_markers_and_rejects_bad(caplog):
    assert (
        pdfdoc.find_source_upload_id(
            f'<!-- pdf_form_source upload_id="{VALID_UPLOAD_ID}" fields="2" -->'
        )
        == VALID_UPLOAD_ID
    )
    assert (
        pdfdoc.find_source_upload_id(f'<!-- pdf_source upload_id="{VALID_UPLOAD_ID}" -->')
        == VALID_UPLOAD_ID
    )
    assert pdfdoc.find_source_upload_id("no marker") is None
    assert pdfdoc.find_source_upload_id('<!-- pdf_source upload_id="../secret.pdf" -->') is None
    assert "Ignoring invalid pdf_source upload_id" in caplog.text


def test_plain_pdf_markdown_includes_optional_body():
    with_body = pdfdoc.render_plain_pdf_markdown(VALID_UPLOAD_ID, "Form Title", " body text \n")
    assert with_body.startswith(f'<!-- pdf_source upload_id="{VALID_UPLOAD_ID}" -->')
    assert "# Form Title" in with_body
    assert "body text" in with_body
    assert with_body.endswith("\n")

    without_body = pdfdoc.render_plain_pdf_markdown(VALID_UPLOAD_ID, "No Body", "   ")
    assert "No Body" in without_body
    assert "   " not in without_body


def test_render_form_markdown_and_parse_values_round_trip():
    fields = [
        {"page": 1, "name": "full name", "label": "Full\nName", "type": "text", "value": "Ada\nLovelace"},
        {"page": 1, "name": "empty", "label": "", "type": "text", "value": ""},
        {"page": 1, "name": "agree?", "label": "Agree", "type": "checkbox", "value": True},
        {"page": 1, "name": "decline", "label": "Decline", "type": "checkbox", "value": False},
        {
            "page": 2,
            "name": "choice field",
            "label": "Plan",
            "type": "choice",
            "options": ["Basic", "Pro"],
            "value": "Pro",
        },
        {"page": 2, "name": "choice empty", "label": "Empty Plan", "type": "choice", "options": [], "value": ""},
        {
            "page": 2,
            "name": "signature",
            "label": "Sign",
            "type": "signature",
            "value": "signature:sig-1",
        },
        {"page": 2, "name": "unsigned", "label": "Unsigned", "type": "signature", "value": "plain"},
    ]

    markdown = pdfdoc.render_form_as_markdown(
        fields, VALID_UPLOAD_ID, "Application", intro_text=" Original OCR text "
    )

    assert f'<!-- pdf_form_source upload_id="{VALID_UPLOAD_ID}" fields="8" -->' in markdown
    assert "## Page 1" in markdown
    assert "## Page 2" in markdown
    assert "Full Name" in markdown
    assert "Ada Lovelace" in markdown
    assert "field=full%20name type=text" in markdown
    assert "field=agree%3F type=checkbox" in markdown
    assert "**Plan** [Basic / Pro]: Pro" in markdown
    assert "**Empty Plan** []: _(not selected)_" in markdown
    assert "**Sign:** signature:sig-1" in markdown
    assert "**Unsigned:** _(unsigned)_" in markdown
    assert "Original OCR text" in markdown

    values = pdfdoc.parse_markdown_to_values(markdown)
    assert values == {
        "full name": "Ada Lovelace",
        "empty": "",
        "agree?": True,
        "decline": False,
        "choice field": "Pro",
        "choice empty": "",
        "signature": "signature:sig-1",
        "unsigned": "",
    }


def test_parse_markdown_to_values_handles_missing_value_patterns():
    content = "\n".join(
        [
            "- no visible value <!-- field=nope type=text -->",
            "- no choice value <!-- field=choice type=choice -->",
            "- not a checkbox <!-- field=cb type=checkbox -->",
            "- **Text:** _(empty)_. <!-- field=placeholder type=text -->",
            "- **Skip:** stripped marker",
        ]
    )

    assert pdfdoc.parse_markdown_to_values(content) == {
        "nope": "",
        "choice": "",
        "cb": False,
        "placeholder": "",
    }
    assert pdfdoc.parse_markdown_to_values(None) == {}
    assert pdfdoc._flatten(None) == ""
    assert pdfdoc._flatten(" a \n b\tc ") == "a b c"
    assert pdfdoc._checkbox_marker("off") == "[ ]"
    assert pdfdoc._checkbox_marker("yes") == "[x]"


class FakeDb:
    def __init__(self, owner="owner-1", fail_commit=False):
        self.owner = owner
        self.fail_commit = fail_commit
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def query(self, model):
        return self

    def filter(self, condition):
        return self

    def first(self):
        return types.SimpleNamespace(owner=self.owner) if self.owner is not None else None

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeModel:
    id = "session-id-column"

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def install_fake_db(monkeypatch, fake_db):
    active_docs = []
    database = sys.modules.get("src.database")
    if database is None:
        database = types.ModuleType("src.database")
        monkeypatch.setitem(sys.modules, "src.database", database)
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(database, "Document", FakeModel, raising=False)
    monkeypatch.setattr(database, "DocumentVersion", FakeModel, raising=False)
    monkeypatch.setattr(database, "Session", FakeModel, raising=False)
    fake_tools = types.ModuleType("src.tool_implementations")
    fake_tools.set_active_document = active_docs.append
    monkeypatch.setitem(sys.modules, "src.tool_implementations", fake_tools)
    return active_docs


def test_create_plain_pdf_document_success_and_failure(monkeypatch, caplog):
    fake_db = FakeDb(owner="alice")
    active_docs = install_fake_db(monkeypatch, fake_db)

    doc_id = pdfdoc.create_plain_pdf_document("session-1", VALID_UPLOAD_ID, "Plain", "text")

    assert doc_id
    assert active_docs == [doc_id]
    assert fake_db.committed is True
    assert fake_db.closed is True
    doc, version = fake_db.added
    assert doc.id == doc_id
    assert doc.session_id == "session-1"
    assert doc.owner == "alice"
    assert doc.language == "markdown"
    assert version.document_id == doc_id
    assert version.summary == "Imported from PDF"

    failing_db = FakeDb(fail_commit=True)
    install_fake_db(monkeypatch, failing_db)
    assert pdfdoc.create_plain_pdf_document("session-1", VALID_UPLOAD_ID, "Plain") is None
    assert failing_db.rolled_back is True
    assert failing_db.closed is True
    assert "Failed to create plain PDF document" in caplog.text


def test_create_form_markdown_document_success_and_failure(monkeypatch, caplog):
    fields = [{"page": 1, "name": "field", "label": "Field", "type": "text", "value": "value"}]
    fake_db = FakeDb(owner=None)
    active_docs = install_fake_db(monkeypatch, fake_db)

    doc_id = pdfdoc.create_form_markdown_document(
        "session-2", fields, VALID_UPLOAD_ID, "Fillable", intro_text="intro"
    )

    assert doc_id
    assert active_docs == [doc_id]
    assert fake_db.committed is True
    assert fake_db.closed is True
    doc, version = fake_db.added
    assert doc.owner is None
    assert "pdf_form_source" in doc.current_content
    assert version.document_id == doc_id
    assert version.summary == "Imported from PDF form"

    failing_db = FakeDb(fail_commit=True)
    install_fake_db(monkeypatch, failing_db)
    assert pdfdoc.create_form_markdown_document("session-2", fields, VALID_UPLOAD_ID, "Fillable") is None
    assert failing_db.rolled_back is True
    assert failing_db.closed is True
    assert "Failed to create form markdown document" in caplog.text
