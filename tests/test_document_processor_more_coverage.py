import base64
import builtins
import inspect
import os
import sys
import types
from types import SimpleNamespace


def test_text_file_detection_and_processing_fallback(tmp_path, monkeypatch):
    import src.document_processor as dp

    text_file = tmp_path / "script.py"
    text_file.write_text("print('hello')\n", encoding="utf-8")
    assert dp._is_text_file(str(text_file)) is True
    assert dp._is_text_file(str(tmp_path / "image.png")) is False

    personal_docs = types.ModuleType("src.personal_docs")
    personal_docs.read_text_file = lambda path: (_ for _ in ()).throw(RuntimeError("fallback"))
    monkeypatch.setitem(sys.modules, "src.personal_docs", personal_docs)

    processed = dp._process_text_file(str(text_file))
    assert "=== File: script.py ===" in processed
    assert "[Type: python" in processed
    assert "```python" in processed
    assert "print('hello')" in processed

    log_file = tmp_path / "long.log"
    log_file.write_text(("line\n" * 3000), encoding="utf-8")
    processed_log = dp._process_text_file(str(log_file))
    assert "[Type: log" in processed_log
    assert "[Truncated]" in processed_log

    missing = dp._process_text_file(str(tmp_path / "missing.txt"))
    assert "[Failed to read attached file]" in missing


def test_pdf_processing_text_images_empty_and_error(monkeypatch, tmp_path):
    import src.document_processor as dp

    pdf = tmp_path / "demo.pdf"
    pdf.write_bytes(b"%PDF")

    class Image:
        def save(self, path, fmt):
            assert fmt == "PNG"
            with open(path, "wb") as f:
                f.write(b"png")

    class Page:
        def __init__(self, text, images=()):
            self._text = text
            self.images = list(images)

        def extract_text(self):
            return self._text

    class Reader:
        def __init__(self, path):
            self.pages = [
                Page("Page text", []),
                Page("", [SimpleNamespace(image=Image())]),
            ]

    pypdf = types.ModuleType("pypdf")
    pypdf.PdfReader = Reader
    monkeypatch.setitem(sys.modules, "pypdf", pypdf)
    monkeypatch.setattr(dp, "analyze_image_with_vl", lambda path: "OCR text")

    processed = dp._process_pdf(str(pdf))
    assert "[Page 1 text]" in processed
    assert "[Page 2 image 1 text]: OCR text" in processed

    class EmptyReader:
        def __init__(self, path):
            self.pages = [Page("", [])]

    pypdf.PdfReader = EmptyReader
    assert "no readable content" in dp._process_pdf(str(pdf))

    class BrokenReader:
        def __init__(self, path):
            raise RuntimeError("bad pdf")

    pypdf.PdfReader = BrokenReader
    assert "PDF processing failed: bad pdf" in dp._process_pdf(str(pdf))


def test_vision_analysis_result_paths(monkeypatch, tmp_path):
    import src.document_processor as dp

    image = tmp_path / "image.png"
    image.write_bytes(b"img")

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": False})
    disabled = dp.analyze_image_with_vl_result(str(image))
    assert "Vision is disabled" in disabled["text"]

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": ""})
    monkeypatch.setattr(dp, "_resolve_vl_model", lambda model: (_ for _ in ()).throw(ValueError("none")))
    missing = dp.analyze_image_with_vl_result(str(image))
    assert "No vision model configured" in missing["text"]

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "primary"})
    monkeypatch.setattr(dp, "_resolve_vl_model", lambda model: ("http://primary", "primary-model", {"H": "1"}))
    endpoint_resolver = types.ModuleType("src.endpoint_resolver")
    endpoint_resolver.resolve_vision_fallback_candidates = lambda: [("http://fallback", "fallback-model", {})]
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", endpoint_resolver)

    calls = []

    def fake_llm_call(url, model, messages, headers=None, timeout=None):
        calls.append((url, model, headers, timeout, messages))
        if model == "primary-model":
            raise RuntimeError("down")
        return "description"

    monkeypatch.setattr(dp, "llm_call", fake_llm_call)
    result = dp.analyze_image_with_vl_result(str(image))
    assert result == {"text": "description", "model": "fallback-model"}
    assert calls[0][1] == "primary-model"
    assert calls[1][1] == "fallback-model"
    encoded = calls[1][4][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"img"
    assert dp.analyze_image_with_vl(str(image)) == "description"


class UploadHandler:
    def __init__(self, uploads, inside=True):
        self.uploads = uploads
        self.inside = inside

    def resolve_upload(self, fid, owner=None):
        return self.uploads.get(fid)

    def _inside_upload_dir(self, path):
        return self.inside

    def inside_base_dir(self, path):
        return self.inside

    def is_image_file(self, name, mime):
        return mime.startswith("image/")

    def is_audio_file(self, name, mime):
        return mime.startswith("audio/")

    def is_document_file(self, name, mime):
        return mime.startswith("text/") or mime == "application/pdf" or name.endswith(".bin")


def test_build_user_content_handles_media_documents_and_missing(tmp_path, monkeypatch):
    import src.document_processor as dp

    image = tmp_path / "pic.png"
    image.write_bytes(b"img")
    audio = tmp_path / "sound.wav"
    audio.write_bytes(b"aud")
    note = tmp_path / "note.txt"
    note.write_text("note body", encoding="utf-8")
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"pdf")
    binary = tmp_path / "file.bin"
    binary.write_bytes(b"bin")

    uploads = {
        "missing-path": {"path": str(tmp_path / "missing.txt"), "mime": "text/plain", "name": "missing.txt"},
        "image": {"path": str(image), "mime": "image/png", "name": "pic.png"},
        "audio": {"path": str(audio), "mime": "audio/wav", "name": "sound.wav"},
        "note": {"path": str(note), "mime": "text/plain", "name": "note.txt"},
        "pdf": {"path": str(pdf), "mime": "application/pdf", "name": "form.pdf"},
        "binary": {"path": str(binary), "mime": "application/octet-stream", "name": "file.bin"},
    }
    handler = UploadHandler(uploads)
    monkeypatch.setattr(dp, "_process_pdf", lambda path: "\n\n[PDF content]: pdf text")

    content = dp.build_user_content("hello", ["missing", "missing-path", "image", "audio"], str(tmp_path), handler, owner="alice")
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "hello"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["audio"]["url"].startswith("data:audio/wav;base64,")

    text_content = dp.build_user_content("hello", ["note", "pdf", "binary"], str(tmp_path), handler)
    assert isinstance(text_content, str)
    assert "note body" in text_content
    assert "pdf text" in text_content
    assert "[Attached document file]" in text_content

    outside = dp.build_user_content("hello", ["note"], str(tmp_path), UploadHandler(uploads, inside=False))
    assert outside == "hello"

    class NoPrivateInside(UploadHandler):
        def __getattribute__(self, name):
            if name == "_inside_upload_dir":
                raise AttributeError(name)
            return super().__getattribute__(name)

    outside_base = dp.build_user_content("hello", ["note"], str(tmp_path), NoPrivateInside(uploads, inside=False))
    assert outside_base == "hello"


def test_api_key_manager_encrypts_persists_and_handles_empty_values(tmp_path):
    from src.api_key_manager import APIKeyManager

    manager = APIKeyManager(str(tmp_path))
    assert manager.encrypt_api_key("") == ""
    assert manager.decrypt_api_key("") == ""

    encrypted = manager.encrypt_api_key("secret")
    assert encrypted != "secret"
    assert manager.decrypt_api_key(encrypted) == "secret"
    assert (tmp_path / ".key").exists()

    manager.save("openai", "sk-test")
    raw = (tmp_path / "api_keys.json").read_text(encoding="utf-8")
    assert "sk-test" not in raw
    assert manager.load() == {"openai": "sk-test"}

    reloaded = APIKeyManager(str(tmp_path))
    assert reloaded.get_or_create_key() == manager.get_or_create_key()
    assert reloaded.load() == {"openai": "sk-test"}

    assert APIKeyManager(str(tmp_path / "empty")).load() == {}


def test_document_processor_text_pdf_and_vision_remaining_branches(tmp_path, monkeypatch):
    import src.document_processor as dp

    latin_file = tmp_path / "latin.txt"
    latin_file.write_bytes("caf\xe9".encode("latin-1"))
    personal_docs = types.ModuleType("src.personal_docs")
    personal_docs.read_text_file = lambda path: (_ for _ in ()).throw(RuntimeError("fallback"))
    monkeypatch.setitem(sys.modules, "src.personal_docs", personal_docs)
    charset_normalizer = types.ModuleType("charset_normalizer")
    charset_normalizer.detect = lambda _raw: {"encoding": "latin-1"}
    monkeypatch.setitem(sys.modules, "charset_normalizer", charset_normalizer)
    monkeypatch.setattr(dp.os.path, "getsize", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    processed_latin = dp._process_text_file(str(latin_file))
    assert "cafe" not in processed_latin
    assert "caf" in processed_latin
    assert "Size: unknown" in processed_latin

    no_forward_newline = tmp_path / "long.txt"
    no_forward_newline.write_text(("a" * 29_950) + "\n" + ("b" * 200), encoding="utf-8")
    processed_long = dp._process_text_file(str(no_forward_newline))
    assert "[Truncated]" in processed_long
    code_file = tmp_path / "long.py"
    code_file.write_text("print('x')\n" * 4000, encoding="utf-8")
    assert "[Truncated]" in dp._process_text_file(str(code_file))
    boundary_file = tmp_path / "boundary.txt"
    boundary_file.write_text("z" * 30001, encoding="utf-8")
    monkeypatch.setattr(dp, "min", lambda *_args: 2, raising=False)
    assert "[Truncated]" in dp._process_text_file(str(boundary_file))
    monkeypatch.delattr(dp, "min", raising=False)

    class PageImagesError:
        def extract_text(self):
            return "short"

        @property
        def images(self):
            raise RuntimeError("no images")

    class FailingImage:
        def save(self, _path, _fmt):
            raise RuntimeError("save failed")

    class PageWithFailingImage:
        def extract_text(self):
            return ""

        @property
        def images(self):
            return [SimpleNamespace(image=FailingImage())]

    class LongReader:
        def __init__(self, _path):
            self.pages = [PageImagesError(), PageWithFailingImage()] + [
                SimpleNamespace(extract_text=lambda i=i: "x" * 4000, images=[])
                for i in range(5)
            ]

    pypdf = types.ModuleType("pypdf")
    pypdf.PdfReader = LongReader
    monkeypatch.setitem(sys.modules, "pypdf", pypdf)
    monkeypatch.setattr(dp.os, "unlink", lambda _path: (_ for _ in ()).throw(OSError("busy")))
    processed_pdf = dp._process_pdf(str(tmp_path / "long.pdf"))
    assert "[PDF content truncated]" in processed_pdf

    settings = types.ModuleType("src.settings")
    settings.load_settings = lambda: (_ for _ in ()).throw(RuntimeError("settings down"))
    monkeypatch.setitem(sys.modules, "src.settings", settings)
    assert dp._load_vl_settings() == {}

    ai_interaction = types.ModuleType("src.ai_interaction")
    calls = []

    def resolve_model(name):
        calls.append(name)
        if name == "configured":
            return "http://configured", "configured", {"H": "1"}
        if name == "pixtral":
            return "http://vision", "pixtral", {}
        raise ValueError("missing")

    ai_interaction._resolve_model = resolve_model
    monkeypatch.setitem(sys.modules, "src.ai_interaction", ai_interaction)
    assert dp._resolve_vl_model("configured") == ("http://configured", "configured", {"H": "1"})
    assert dp._resolve_vl_model("") == ("http://vision", "pixtral", {})
    assert "pixtral" in calls

    ai_interaction._resolve_model = lambda _name: (_ for _ in ()).throw(ValueError("none"))
    try:
        dp._resolve_vl_model("")
    except ValueError as exc:
        assert "No vision model available" in str(exc)
    else:
        raise AssertionError("expected vision resolution failure")

    image = tmp_path / "image.weird"
    image.write_bytes(b"image")
    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "m"})
    monkeypatch.setattr(dp, "_resolve_vl_model", lambda _model: ("http://primary", "primary", {}))
    endpoint_resolver = types.ModuleType("src.endpoint_resolver")
    endpoint_resolver.resolve_vision_fallback_candidates = lambda: (_ for _ in ()).throw(RuntimeError("fallback config bad"))
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", endpoint_resolver)
    monkeypatch.setattr(dp, "llm_call", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("llm down")))
    assert dp.analyze_image_with_vl_result(str(image)) == {
        "text": "[VL model unavailable - image not analyzed]",
        "model": "",
    }

    endpoint_resolver.resolve_vision_fallback_candidates = lambda: [(None, "missing-url", {}), ("http://x", "", {})]
    monkeypatch.setattr(dp, "llm_call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no candidates")))
    assert dp.analyze_image_with_vl_result(str(image))["text"] == "[VL model unavailable - image not analyzed]"


def test_build_user_content_auto_pdf_docs_and_defensive_insert_paths(tmp_path, monkeypatch):
    import src.document_processor as dp

    image = tmp_path / "bad.png"
    image.write_bytes(b"img")
    audio = tmp_path / "bad.wav"
    audio.write_bytes(b"aud")
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"pdf")
    other = tmp_path / "other.dat"
    other.write_bytes(b"other")

    class Handler(UploadHandler):
        pass

    uploads = {
        "image": {"path": str(image), "mime": "image/png", "name": "bad.png"},
        "audio": {"path": str(audio), "mime": "audio/wav", "name": "bad.wav"},
        "pdf": {"path": str(pdf), "mime": "application/pdf", "name": "form.pdf"},
        "other": {"path": str(other), "mime": "application/octet-stream", "name": "other.dat"},
    }

    real_open = builtins.open

    def mutate_content_to_media():
        frame = inspect.currentframe()
        while frame:
            content = frame.f_locals.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                content[0]["type"] = "image_url"
                return
            frame = frame.f_back

    def failing_open(path, *args, **kwargs):
        if path in {str(image), str(audio)}:
            mutate_content_to_media()
            raise OSError("blocked")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    image_content = dp.build_user_content("", ["image"], str(tmp_path), Handler(uploads))
    assert image_content[0]["text"] == "[Image attached but could not be processed]"
    audio_content = dp.build_user_content("", ["audio"], str(tmp_path), Handler(uploads))
    assert audio_content[0]["text"] == "[Audio attached but could not be processed]"
    monkeypatch.setattr(builtins, "open", real_open)

    def plain_failing_open(path, *args, **kwargs):
        if path in {str(image), str(audio)}:
            raise OSError("blocked")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", plain_failing_open)
    image_text = dp.build_user_content("hello", ["image"], str(tmp_path), Handler(uploads))
    assert image_text.endswith("[Image attached but could not be processed]")
    audio_text = dp.build_user_content("hello", ["audio"], str(tmp_path), Handler(uploads))
    assert audio_text.endswith("[Audio attached but could not be processed]")
    monkeypatch.setattr(builtins, "open", real_open)

    class MutatingNonDocumentHandler(UploadHandler):
        def is_document_file(self, name, mime):
            mutate_content_to_media()
            return False

    assert "[Attached non-text file]" in dp.build_user_content("hello", ["other"], str(tmp_path), Handler(uploads))
    non_text = dp.build_user_content("", ["other"], str(tmp_path), MutatingNonDocumentHandler(uploads))
    assert non_text[0]["text"] == "[Attached non-text file]"

    class MutatingDocumentHandler(UploadHandler):
        def is_document_file(self, name, mime):
            mutate_content_to_media()
            return True

    inserted_document = dp.build_user_content("", ["other"], str(tmp_path), MutatingDocumentHandler(uploads))
    assert inserted_document[0]["text"] == "[Attached document file]"

    pdf_forms = types.ModuleType("src.pdf_forms")
    pdf_forms.has_form_fields = lambda _path: True
    pdf_forms.extract_fields = lambda _path: [{"name": "full_name"}]
    monkeypatch.setitem(sys.modules, "src.pdf_forms", pdf_forms)
    pdf_form_doc = types.ModuleType("src.pdf_form_doc")
    saved = []
    pdf_form_doc.save_field_sidecar = lambda path, fields: saved.append((path, fields))
    pdf_form_doc.create_form_markdown_document = lambda **kwargs: "doc-form"
    pdf_form_doc.create_plain_pdf_document = lambda **kwargs: "doc-plain"
    monkeypatch.setitem(sys.modules, "src.pdf_form_doc", pdf_form_doc)
    monkeypatch.setattr(dp, "_process_pdf", lambda _path: "\n\n[PDF content]:" + ("x" * 15100))

    class Document:
        id = SimpleNamespace(__eq__=lambda self, other: ("id", other))

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return SimpleNamespace(
                id="doc-form",
                title="Form",
                language="markdown",
                current_content="body",
                version_count=3,
            )

    class DB:
        def query(self, _model):
            return Query()

        def close(self):
            self.closed = True

    database = types.ModuleType("src.database")
    database.SessionLocal = lambda: DB()
    database.Document = Document
    monkeypatch.setitem(sys.modules, "src.database", database)

    opened = []
    form_text = dp.build_user_content("hello", ["pdf"], str(tmp_path), Handler(uploads), session_id="s1", auto_opened_docs=opened)
    assert "Form attached: form" in form_text
    assert "truncated for inline context" in form_text
    assert opened[0]["doc_id"] == "doc-form"
    assert saved

    pdf_forms.has_form_fields = lambda _path: False
    opened.clear()
    monkeypatch.setattr(dp, "_process_pdf", lambda _path: (_ for _ in ()).throw(RuntimeError("pdf bad")))
    no_pdf_body = dp.build_user_content("hello", ["pdf"], str(tmp_path), Handler(uploads), session_id="s1")
    assert "PDF attached: form" in no_pdf_body
    monkeypatch.setattr(dp, "_process_pdf", lambda _path: "\n\n[PDF content]:" + ("x" * 15100))
    plain_text = dp.build_user_content("hello", ["pdf"], str(tmp_path), Handler(uploads), session_id="s1", auto_opened_docs=opened)
    assert "PDF attached: form" in plain_text
    assert opened[0]["doc_id"] == "doc-form"

    pdf_forms.has_form_fields = lambda _path: (_ for _ in ()).throw(RuntimeError("detect bad"))
    pdf_form_doc.create_plain_pdf_document = lambda **kwargs: ""
    fallback_text = dp.build_user_content("hello", ["pdf"], str(tmp_path), Handler(uploads), session_id="s1")
    assert "[PDF content]" in fallback_text or "x" in fallback_text

    pdf_forms.has_form_fields = lambda _path: (_ for _ in ()).throw(RuntimeError("outer bad"))
    pdf_form_doc.create_plain_pdf_document = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("create bad"))
    assert "x" in dp.build_user_content("hello", ["pdf"], str(tmp_path), Handler(uploads), session_id="s1")
