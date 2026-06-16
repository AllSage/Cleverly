import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src import chat_helpers


def assert_http_error(excinfo, status_code, error=None):
    assert excinfo.value.status_code == status_code
    if error is not None:
        assert excinfo.value.detail["error"] == error


def test_extract_urls_strips_trailing_sentence_punctuation():
    text = (
        "See https://example.com/path?q=1, then http://host.test/thing). "
        "Ignore <https://blocked.invalid> and keep https://ok.test/a-b!"
    )

    assert chat_helpers.extract_urls(text) == [
        "https://example.com/path?q=1",
        "http://host.test/thing",
        "https://blocked.invalid",
        "https://ok.test/a-b",
    ]


def test_vision_model_detection_keywords_and_vl_tokens():
    assert chat_helpers.is_vision_model("GPT-4o-mini") is True
    assert chat_helpers.is_vision_model("Qwen2.5-VL-7B") is True
    assert chat_helpers.is_vision_model("custom-vlm") is True
    assert chat_helpers.is_vision_model("plain-llama") is False
    assert chat_helpers.is_vision_model(None) is False


def test_validate_message_success_and_errors():
    assert chat_helpers.validate_message("  hello  ") == "hello"

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_message(None)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Message is required"

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_message("   ")
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Message cannot be empty"

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_message("x" * 50001)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Message exceeds maximum length"


def make_upload(filename, data=b"hello"):
    return SimpleNamespace(filename=filename, file=io.BytesIO(data))


class BrokenFile:
    def seek(self, *args):
        raise IOError("cannot seek")


def test_validate_file_upload_success_and_failures():
    upload = make_upload("note.md", b"content")
    assert chat_helpers.validate_file_upload(upload) is upload

    for bad in [None, SimpleNamespace(filename="", file=io.BytesIO(b"x"))]:
        with pytest.raises(HTTPException) as excinfo:
            chat_helpers.validate_file_upload(bad)
        assert_http_error(excinfo, 400, "INVALID_FILE")

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_file_upload(make_upload("empty.txt", b""))
    assert_http_error(excinfo, 400, "EMPTY_FILE")

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_file_upload(make_upload("huge.txt", b"x" * (10 * 1024 * 1024 + 1)))
    assert_http_error(excinfo, 400, "FILE_TOO_LARGE")

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_file_upload(make_upload("script.exe", b"x"))
    assert_http_error(excinfo, 400, "UNSUPPORTED_FILE_TYPE")
    assert ".exe" in excinfo.value.detail["message"]

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.validate_file_upload(SimpleNamespace(filename="bad.txt", file=BrokenFile()))
    assert_http_error(excinfo, 500, "FILE_READ_ERROR")


class SessionManager:
    def __init__(self, behavior=None):
        self.behavior = behavior
        self.calls = []

    def get_session(self, session):
        self.calls.append(session)
        if self.behavior == "missing":
            raise KeyError(session)
        if self.behavior == "json":
            raise json.JSONDecodeError("bad", "{}", 1)
        if self.behavior == "boom":
            raise RuntimeError("boom")
        return {"id": session}


def test_coerce_message_and_session_from_args_and_json_allow_empty():
    manager = SessionManager()

    assert chat_helpers.coerce_message_and_session(None, " hello ", "s1", manager) == ("hello", "s1")
    assert manager.calls == ["s1"]

    assert chat_helpers.coerce_message_and_session(
        {"message": " from json ", "session": "s2"}, None, None, manager
    ) == ("from json", "s2")

    assert chat_helpers.coerce_message_and_session(
        {"message": "ignored", "session": "s3"}, "   ", None, manager, allow_empty=True
    ) == ("", "s3")


def test_coerce_message_and_session_validation_errors():
    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.coerce_message_and_session(None, None, None, SessionManager())
    assert_http_error(excinfo, 400, "MISSING_PARAMETERS")

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.coerce_message_and_session({"message": "hello"}, None, None, SessionManager())
    assert_http_error(excinfo, 400, "VALIDATION_ERROR")

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.coerce_message_and_session(None, "hello", "missing", SessionManager("missing"))
    assert_http_error(excinfo, 404, "SESSION_NOT_FOUND")


def test_coerce_message_and_session_json_and_generic_errors():
    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.coerce_message_and_session(None, "hello", "s1", SessionManager("json"))
    assert_http_error(excinfo, 400, "INVALID_JSON")

    with pytest.raises(HTTPException) as excinfo:
        chat_helpers.coerce_message_and_session(None, "hello", "s1", SessionManager("boom"))
    assert_http_error(excinfo, 400, "REQUEST_PROCESSING_ERROR")
