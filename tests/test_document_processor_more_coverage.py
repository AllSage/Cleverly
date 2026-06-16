import base64
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
