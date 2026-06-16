import io
import json
import os
import sys
import time
import base64
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.upload_handler import UploadHandler, is_valid_upload_id, secure_filename


def _upload(name, data=b"hello"):
    return SimpleNamespace(filename=name, file=io.BytesIO(data))


def test_upload_helpers_detection_cleanup_and_resolution(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))

    assert secure_filename("../.bad name?.txt") == "_.bad_name.txt"
    assert is_valid_upload_id("a" * 32 + ".png")
    assert not is_valid_upload_id("../bad.png")
    assert handler.inside_base_dir(str(tmp_path / "file.txt")) is True
    assert handler.inside_base_dir(str(tmp_path.parent / "file.txt")) is False
    assert handler._inside_upload_dir(str(upload_dir / "file.txt")) is True
    assert handler._inside_upload_dir(str(tmp_path / "other.txt")) is False
    assert os.path.isdir(handler.get_upload_dir())

    data = io.BytesIO(b"abc")
    digest = handler.calculate_file_hash(data)
    assert digest == handler.calculate_file_hash(io.BytesIO(b"abc"))
    assert data.tell() == 0

    class Detector:
        def __init__(self, value=None, exc=None):
            self.value = value
            self.exc = exc

        def from_buffer(self, _buf):
            if self.exc:
                raise self.exc
            return self.value

    handler.file_detector = Detector("image/png")
    assert handler.detect_content_type(io.BytesIO(b"png"), "x.bin") == "image/png"
    handler.file_detector = Detector("application/octet-stream")
    assert handler.detect_content_type(io.BytesIO(b"text"), "note.txt") == "text/plain"
    handler.file_detector = Detector(exc=RuntimeError("magic failed"))
    assert handler.detect_content_type(io.BytesIO(b"text"), "note.txt") == "text/plain"

    assert handler.is_image_file("photo.webp")
    assert handler.is_image_file("blob.bin", "image/jpeg")
    assert not handler.is_image_file("blob.bin", "text/plain")
    assert handler.is_document_file("script.py")
    assert handler.is_document_file("blob.bin", "application/pdf")
    assert handler.is_audio_file("voice.ogg")
    assert handler.is_audio_file("blob.bin", "audio/mpeg")
    assert not handler.is_safe_file_type("application/javascript", "safe.txt")
    assert not handler.is_safe_file_type("text/plain", "run.ps1")
    assert handler.is_safe_file_type("text/plain", "note.txt")

    old = upload_dir / (datetime.now() - timedelta(days=40)).strftime("%Y/%m/%d")
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old", encoding="utf-8")
    bad = upload_dir / "not" / "a" / "date"
    bad.mkdir(parents=True)
    assert handler.cleanup_old_uploads() == 1
    assert not (old / "old.txt").exists()

    assert handler._load_upload_index() == {}
    (upload_dir / "uploads.json").write_text("[]", encoding="utf-8")
    assert handler._load_upload_index() == {}
    file_id = "b" * 32 + ".txt"
    stored = upload_dir / file_id
    stored.write_text("body", encoding="utf-8")
    metadata = {
        "hash": {"id": file_id, "path": str(stored), "owner": "alice", "mime": "text/plain", "size": 4}
    }
    (upload_dir / "uploads.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert handler.get_upload_info(file_id)["owner"] == "alice"
    assert handler.get_upload_info("bad") is None
    assert handler._find_upload_path(file_id) == str(stored)
    assert handler._find_upload_path("bad") is None

    class Auth:
        is_configured = True

        def is_admin(self, user):
            if user == "broken":
                raise RuntimeError("auth down")
            return user == "admin"

    assert handler.resolve_upload(file_id, owner=None, auth_manager=Auth()) is None
    assert handler.resolve_upload(file_id, owner="bob", auth_manager=Auth()) is None
    assert handler.resolve_upload(file_id, owner="admin", auth_manager=Auth())["path"] == str(stored)
    assert handler.resolve_upload(file_id, owner="broken", auth_manager=Auth()) is None
    metadata["hash"]["path"] = str(tmp_path / "missing.txt")
    (upload_dir / "uploads.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert handler.resolve_upload(file_id, owner="alice", auth_manager=Auth())["path"] == str(stored)

    now = time.time()
    handler.upload_rate_log = {"old": [now - 120], "mixed": [now - 120, now]}
    handler.cleanup_rate_limits()
    assert "old" not in handler.upload_rate_log
    assert len(handler.upload_rate_log["mixed"]) == 1

    (upload_dir / "uploads.json").write_text(
        json.dumps({"a": {"size": 1024, "mime": "text/plain"}, "b": {"size": 2048, "mime": "image/png"}}),
        encoding="utf-8",
    )
    stats = handler.get_upload_stats()
    assert stats["total_files"] == 2
    assert stats["file_types"] == {"text/plain": 1, "image/png": 1}
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no read")))
    assert "error" in handler.get_upload_stats()


def test_save_upload_new_duplicate_limits_and_error_paths(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    handler.file_detector = None
    handler.max_upload_size = 20
    handler.upload_rate_limit = 2

    with pytest.raises(HTTPException) as empty:
        handler.save_upload(_upload("empty.txt", b""), "1.1.1.1", owner="alice")
    assert empty.value.status_code == 400
    with pytest.raises(HTTPException) as large:
        handler.save_upload(_upload("large.txt", b"x" * 21), "1.1.1.2", owner="alice")
    assert large.value.status_code == 400
    with pytest.raises(HTTPException) as dangerous:
        handler.save_upload(_upload("run.exe", b"binary"), "1.1.1.3", owner="alice")
    assert dangerous.value.status_code == 400

    first = handler.save_upload(_upload("note.txt", b"hello"), "1.1.1.4", owner="alice")
    assert first["owner"] == "alice"
    assert first["name"] == "note.txt"
    assert os.path.exists(first["path"])

    duplicate = handler.save_upload(_upload("copy.txt", b"hello"), "1.1.1.5", owner="alice")
    assert duplicate["is_duplicate"] is True
    assert duplicate["id"] == first["id"]

    other_owner = handler.save_upload(_upload("copy.txt", b"hello"), "1.1.1.6", owner="bob")
    assert other_owner["id"] != first["id"]

    with pytest.raises(HTTPException) as limited:
        handler.save_upload(_upload("one.txt", b"1"), "9.9.9.9", owner="alice")
        handler.save_upload(_upload("two.txt", b"2"), "9.9.9.9", owner="alice")
        handler.save_upload(_upload("three.txt", b"3"), "9.9.9.9", owner="alice")
    assert limited.value.status_code == 429

    handler.upload_rate_limit = 99
    monkeypatch.setattr(handler, "get_upload_dir", lambda: str(upload_dir / "missing" / "subdir"))
    with pytest.raises(HTTPException) as save_failed:
        handler.save_upload(_upload("bad.txt", b"bad"), "2.2.2.2", owner="alice")
    assert save_failed.value.status_code == 500


def test_upload_handler_fallback_and_error_branches(monkeypatch, tmp_path):
    class MagicModule:
        class Magic:
            def __init__(self, mime=False):
                self.mime = mime

    monkeypatch.setitem(sys.modules, "magic", MagicModule)
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    assert isinstance(handler.file_detector, MagicModule.Magic)

    monkeypatch.setattr("src.upload_handler.os.path.commonpath", lambda _paths: (_ for _ in ()).throw(ValueError("bad path")))
    assert handler.inside_base_dir(str(tmp_path / "file.txt")) is False
    assert handler._inside_upload_dir(str(upload_dir / "file.txt")) is False
    monkeypatch.undo()

    handler = UploadHandler(str(tmp_path), str(upload_dir))
    assert handler.is_document_file("blob.bin") is False
    assert handler.is_audio_file("blob.bin") is False

    file_id = "c" * 32 + ".txt"
    nested = upload_dir / "2026" / "06" / "16"
    nested.mkdir(parents=True)
    (nested / file_id).write_text("nested", encoding="utf-8")
    assert handler._find_upload_path(file_id) == str(nested / file_id)
    assert handler.resolve_upload(file_id) == {
        "id": file_id,
        "path": str(nested / file_id),
        "name": file_id,
        "original_name": file_id,
        "mime": "text/plain",
    }
    assert handler.resolve_upload("bad") is None
    assert handler.resolve_upload("d" * 32 + ".txt") is None

    owned = {"owned": {"id": file_id, "path": str(nested / file_id), "owner": "alice"}}
    (upload_dir / "uploads.json").write_text(json.dumps(owned), encoding="utf-8")
    assert handler.resolve_upload(file_id) is None

    real_inside = handler._inside_upload_dir
    calls = iter([True, False])

    def inside_then_outside(path):
        try:
            return next(calls)
        except StopIteration:
            return real_inside(path)

    monkeypatch.setattr(handler, "_inside_upload_dir", inside_then_outside)
    assert handler.resolve_upload(file_id, owner="alice") is None

    (upload_dir / "uploads.json").write_text("{", encoding="utf-8")
    assert handler._load_upload_index() == {}
    assert handler.get_upload_info(file_id) is None

    monkeypatch.setattr("src.upload_handler.os.walk", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("walk failed")))
    assert handler.cleanup_old_uploads() == 0


def test_cleanup_old_uploads_logs_remove_failures(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    old = upload_dir / (datetime.now() - timedelta(days=40)).strftime("%Y/%m/%d")
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr("src.upload_handler.os.remove", lambda _path: (_ for _ in ()).throw(OSError("locked")))
    monkeypatch.setattr("src.upload_handler.os.rmdir", lambda _path: (_ for _ in ()).throw(OSError("not empty")))
    assert handler.cleanup_old_uploads() == 0


def test_rate_limit_pruning_and_periodic_cleanup(monkeypatch, tmp_path):
    handler = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))
    now = time.time()
    handler.upload_rate_log = {
        "recent": [now],
        "empty": [],
        "older": [now - 1],
        "oldest": [now - 2],
    }
    handler._upload_rate_max_entries = 2
    handler.cleanup_rate_limits()
    assert set(handler.upload_rate_log) == {"recent", "older"}

    called = {"cleanup": 0}
    handler.file_detector = None
    handler._upload_rate_counter = 99
    monkeypatch.setattr(handler, "cleanup_rate_limits", lambda: called.__setitem__("cleanup", called["cleanup"] + 1))
    saved = handler.save_upload(_upload("periodic.txt", b"body"), "3.3.3.3")
    assert saved["name"] == "periodic.txt"
    assert called["cleanup"] == 1


def test_save_upload_index_and_image_metadata_fallbacks(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    handler.file_detector = None

    uploads_db = upload_dir / "uploads.json"
    uploads_db.write_text("{", encoding="utf-8")
    corrupt_index = handler.save_upload(_upload("fresh.txt", b"fresh"), "4.4.4.4", owner="alice")
    assert corrupt_index["name"] == "fresh.txt"

    first = handler.save_upload(_upload("dupe.txt", b"dupe"), "4.4.4.5", owner="alice")
    real_dump = json.dump

    def fail_duplicate_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("src.upload_handler.json.dump", fail_duplicate_dump)
    duplicate = handler.save_upload(_upload("dupe-copy.txt", b"dupe"), "4.4.4.6", owner="alice")
    assert duplicate["is_duplicate"] is True
    assert duplicate["id"] == first["id"]
    monkeypatch.setattr("src.upload_handler.json.dump", real_dump)

    handler.detect_content_type = lambda _file_obj, _name: "image/png"
    bad_image = handler.save_upload(_upload("bad.png", b"not a png"), "4.4.4.7", owner="alice")
    assert bad_image["mime"] == "image/png"
    assert "width" not in bad_image

    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    good_image = handler.save_upload(_upload("good.png", png_1x1), "4.4.4.8", owner="alice")
    assert good_image["width"] == 1
    assert good_image["height"] == 1


def test_save_upload_database_update_failures(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    handler.file_detector = None
    uploads_db = upload_dir / "uploads.json"
    uploads_db.write_text(json.dumps({"stale": {"size": 1}}), encoding="utf-8")
    real_open = open

    def fail_database_read(path, mode="r", *args, **kwargs):
        if os.fspath(path) == os.fspath(uploads_db) and "r" in mode:
            raise OSError("cannot read index")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fail_database_read)
    saved_after_read_failure = handler.save_upload(_upload("read-failure.txt", b"read failure"), "5.5.5.5")
    assert saved_after_read_failure["name"] == "read-failure.txt"

    def fail_database_write(path, mode="r", *args, **kwargs):
        if os.fspath(path) == os.fspath(uploads_db) and "w" in mode:
            raise OSError("cannot write index")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fail_database_write)
    saved_after_write_failure = handler.save_upload(_upload("write-failure.txt", b"write failure"), "5.5.5.6")
    assert saved_after_write_failure["name"] == "write-failure.txt"
