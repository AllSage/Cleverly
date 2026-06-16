import builtins
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.chat_handler as chat_handler_module


class UploadHandler:
    def __init__(self, files=None):
        self.files = files or {}
        self.resolved = []

    def resolve_upload(self, att_id, owner=None):
        self.resolved.append((att_id, owner))
        return self.files.get(att_id)

    def is_image_file(self, name, mime):
        return name.lower().endswith((".png", ".jpg", ".jpeg")) or mime.startswith("image/")


class MemoryManager:
    def __init__(self, *, is_command=True, duplicate=False):
        self.is_command = is_command
        self.duplicate = duplicate
        self.saved = None

    def process_inline_memory_command(self, message):
        return self.is_command, "remembered fact" if self.is_command else ""

    def load(self):
        return []

    def find_duplicates(self, _text, _mem):
        return self.duplicate

    def add_entry(self, text):
        return {"text": text}

    def save(self, mem):
        self.saved = list(mem)


class SessionManager:
    def __init__(self):
        self.saves = 0

    def save_sessions(self):
        self.saves += 1


class Session:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "sess1")
        self.owner = kwargs.get("owner", "alice")
        self.model = kwargs.get("model", "")
        self.name = kwargs.get("name")
        self.history = kwargs.get("history", [])
        self.messages = []

    def add_message(self, message):
        self.messages.append(message)


def make_handler(*, presets=None, upload_handler=None, memory_manager=None, session_manager=None):
    return chat_handler_module.ChatHandler(
        session_manager or SessionManager(),
        memory_manager or MemoryManager(is_command=False),
        chat_processor=object(),
        research_handler=object(),
        preset_manager=SimpleNamespace(presets=presets or {}),
        upload_handler=upload_handler or UploadHandler(),
    )


def patch_settings(monkeypatch, *, vision_enabled=True, vision_model="vl-setting"):
    import src.settings as settings_module

    def get_setting(key, default=None):
        if key == "vision_enabled":
            return vision_enabled
        if key == "vision_model":
            return vision_model
        return default

    monkeypatch.setattr(settings_module, "get_setting", get_setting)


def test_validate_preset_variants_and_enhancement():
    handler = make_handler(
        presets={
            "disabled": {"enabled": False, "temperature": 0.1},
            "full": {
                "system_prompt": "Answer carefully.",
                "character_name": "Ada",
                "temperature": 0.2,
                "max_tokens": 123,
            },
            "named": {"character_name": "Clever"},
        }
    )

    with pytest.raises(HTTPException) as invalid:
        handler.validate_and_extract_preset("missing")
    assert invalid.value.status_code == 400

    assert handler.validate_and_extract_preset("disabled") == (
        chat_handler_module.DEFAULT_TEMPERATURE,
        chat_handler_module.DEFAULT_MAX_TOKENS,
        None,
        "",
    )
    assert handler.validate_and_extract_preset("full") == (
        0.2,
        123,
        "Your name is Ada. Answer carefully.",
        "Ada",
    )
    assert handler.validate_and_extract_preset("named")[2] == "Your name is Clever."
    assert handler.validate_and_extract_preset(None)[0] == chat_handler_module.DEFAULT_TEMPERATURE
    assert handler.enhance_message_if_needed("plain") == "plain"


@pytest.mark.asyncio
async def test_preprocess_youtube_and_text_only_image_generates_vl_cache(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(chat_handler_module, "UPLOAD_DIR", str(upload_dir))
    patch_settings(monkeypatch, vision_enabled=True, vision_model="vl-setting")
    monkeypatch.setattr(chat_handler_module, "extract_urls", lambda _message: ["https://youtu.be/bad", "https://youtu.be/good"])
    monkeypatch.setattr(chat_handler_module, "is_youtube_url", lambda url: "youtu.be" in url)
    monkeypatch.setattr(chat_handler_module, "extract_youtube_id", lambda url: None if url.endswith("bad") else "video1")

    async def transcript(url, video_id):
        return [{"text": f"transcript:{video_id}"}]

    async def comments(video_id):
        return {"title": "Title", "channel": "Channel", "comments": ["nice"]}

    monkeypatch.setattr(chat_handler_module, "extract_transcript_async", transcript)
    monkeypatch.setattr(chat_handler_module, "fetch_youtube_comments", comments)
    monkeypatch.setattr(chat_handler_module, "format_transcript_for_context", lambda data, url, title, channel: f"{title}:{channel}:{data[0]['text']}")
    monkeypatch.setattr(chat_handler_module, "format_comments_for_context", lambda data, url: "comments context")
    monkeypatch.setattr(chat_handler_module, "is_vision_model", lambda model: False)
    monkeypatch.setattr(
        chat_handler_module,
        "analyze_image_with_vl_result",
        lambda path: {"text": f"described {path}", "model": "vl-runtime"},
    )
    monkeypatch.setattr(
        chat_handler_module,
        "build_user_content",
        lambda message, *_args, **_kwargs: [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": "ignored"},
        ],
    )

    image = {"id": "img1", "name": "photo.png", "mime": "image/png", "size": 10, "path": "photo-path"}
    handler = make_handler(upload_handler=UploadHandler({"img1": image}))
    result = await handler.preprocess_message("watch this", ["img1"], Session(model="text-model"), auto_opened_docs=[])
    enhanced, user_content, text_for_context, transcripts, meta = result

    assert transcripts == [chat_handler_module.YOUTUBE_INSTRUCTION_PROMPT, "Title:Channel:transcript:video1", "comments context"]
    assert "[Image: photo.png]" in enhanced
    assert "described photo-path" in user_content
    assert text_for_context == user_content
    assert meta[0]["vision"] == "described photo-path"
    assert meta[0]["vision_model"] == "vl-runtime"
    assert (upload_dir / ".vision" / "img1.txt").read_text(encoding="utf-8") == "described photo-path"
    assert handler.upload_handler.resolved == [("img1", "alice")]


@pytest.mark.asyncio
async def test_preprocess_vision_model_uses_corrected_caption(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    vision_dir = upload_dir / ".vision"
    vision_dir.mkdir(parents=True)
    (vision_dir / "img1.txt").write_text("corrected caption", encoding="utf-8")
    monkeypatch.setattr(chat_handler_module, "UPLOAD_DIR", str(upload_dir))
    patch_settings(monkeypatch, vision_enabled=True)
    monkeypatch.setattr(chat_handler_module, "extract_urls", lambda _message: [])
    monkeypatch.setattr(chat_handler_module, "is_vision_model", lambda model: True)
    monkeypatch.setattr(
        chat_handler_module,
        "build_user_content",
        lambda message, *_args, **_kwargs: [{"type": "text", "text": message}, {"type": "image_url", "image_url": "kept"}],
    )

    image = {"id": "img1", "name": "photo.png", "mime": "image/png", "size": 10, "path": "photo-path"}
    handler = make_handler(upload_handler=UploadHandler({"img1": image}))
    enhanced, user_content, text_for_context, _transcripts, meta = await handler.preprocess_message(
        "look", ["img1"], Session(model="gpt-vision")
    )

    assert "[Image attached: photo.png]" in enhanced
    assert "corrected caption" in enhanced
    assert isinstance(user_content, list)
    assert text_for_context == enhanced
    assert meta[0]["vision"] == "corrected caption"
    assert meta[0]["vision_model"] == "gpt-vision"


@pytest.mark.asyncio
async def test_preprocess_text_only_model_uses_cached_caption(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    vision_dir = upload_dir / ".vision"
    vision_dir.mkdir(parents=True)
    (vision_dir / "img1.txt").write_text("cached text caption", encoding="utf-8")
    monkeypatch.setattr(chat_handler_module, "UPLOAD_DIR", str(upload_dir))
    patch_settings(monkeypatch, vision_enabled=True, vision_model="vl-setting")
    monkeypatch.setattr(chat_handler_module, "extract_urls", lambda _message: [])
    monkeypatch.setattr(chat_handler_module, "is_vision_model", lambda _model: False)
    monkeypatch.setattr(chat_handler_module, "analyze_image_with_vl_result", lambda _path: pytest.fail("cache should be used"))
    monkeypatch.setattr(chat_handler_module, "build_user_content", lambda message, *_args, **_kwargs: message)
    image = {"id": "img1", "name": "photo.png", "mime": "image/png", "size": 10, "path": "photo-path"}
    handler = make_handler(upload_handler=UploadHandler({"img1": image}))

    enhanced, _user_content, _text_for_context, _transcripts, meta = await handler.preprocess_message(
        "look", ["img1"], Session(model="text-model")
    )

    assert "cached text caption" in enhanced
    assert meta[0]["vision"] == "cached text caption"
    assert meta[0]["vision_model"] == "vl-setting"


@pytest.mark.asyncio
async def test_preprocess_handles_caption_read_and_write_errors(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    vision_dir = upload_dir / ".vision"
    vision_dir.mkdir(parents=True)
    (vision_dir / "img1.txt").write_text("cached caption", encoding="utf-8")
    monkeypatch.setattr(chat_handler_module, "UPLOAD_DIR", str(upload_dir))
    patch_settings(monkeypatch, vision_enabled=True)
    monkeypatch.setattr(chat_handler_module, "extract_urls", lambda _message: [])
    monkeypatch.setattr(chat_handler_module, "build_user_content", lambda message, *_args, **_kwargs: message)
    monkeypatch.setattr(chat_handler_module, "analyze_image_with_vl_result", lambda _path: {"text": "fresh caption", "model": "vl"})
    image = {"id": "img1", "name": "photo.png", "mime": "image/png", "size": 10, "path": "photo-path"}

    real_open = builtins.open

    def failing_open(path, *args, **kwargs):
        if str(path).endswith("img1.txt"):
            raise OSError("cannot read or write")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)

    handler = make_handler(upload_handler=UploadHandler({"img1": image}))
    monkeypatch.setattr(chat_handler_module, "is_vision_model", lambda _model: True)
    enhanced, *_rest = await handler.preprocess_message("look", ["img1"], Session(model="vision-model"))
    assert "[Image attached: photo.png]" in enhanced

    monkeypatch.setattr(chat_handler_module, "is_vision_model", lambda _model: False)
    enhanced, *_rest = await handler.preprocess_message("look", ["img1"], Session(model="text-model"))
    assert "fresh caption" in enhanced


@pytest.mark.asyncio
async def test_preprocess_strips_image_parts_when_vision_disabled(monkeypatch):
    patch_settings(monkeypatch, vision_enabled=False)
    monkeypatch.setattr(chat_handler_module, "extract_urls", lambda _message: [])
    monkeypatch.setattr(chat_handler_module, "is_vision_model", lambda _model: False)
    monkeypatch.setattr(
        chat_handler_module,
        "build_user_content",
        lambda *_args, **_kwargs: [
            {"type": "image_url", "image_url": "ignored"},
            {"type": "text", "text": "text only"},
        ],
    )
    handler = make_handler()

    _enhanced, user_content, text_for_context, transcripts, meta = await handler.preprocess_message(
        "hello", [], Session(model="text-model")
    )

    assert user_content == "text only"
    assert text_for_context == "text only"
    assert transcripts == []
    assert meta == []


def test_session_helpers_trim_and_name():
    handler = make_handler()
    session = Session(name=None)
    handler.update_session_name_if_needed(session, "one two three four five six")
    assert session.name == "Chat: one two three four five"

    untouched = Session(name="Existing")
    handler.update_session_name_if_needed(untouched, "new")
    assert untouched.name == "Existing"

    history = list(range(chat_handler_module.MAX_CONTEXT_MESSAGES + 3))
    session = Session(history=history)
    handler.trim_history_if_needed(session)
    assert session.history == history[-chat_handler_module.MAX_CONTEXT_MESSAGES :]


@pytest.mark.asyncio
async def test_handle_memory_command_saves_new_memory(monkeypatch):
    import src.database as database

    touched = []
    monkeypatch.setattr(database, "update_session_last_accessed", lambda session_id: touched.append(session_id), raising=False)
    session_manager = SessionManager()
    memory_manager = MemoryManager(is_command=True, duplicate=False)
    handler = make_handler(memory_manager=memory_manager, session_manager=session_manager)
    session = Session(id="s1")

    assert await handler.handle_memory_command(session, "/remember this") == "Saved to memory: remembered fact"
    assert memory_manager.saved == [{"text": "remembered fact"}]
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert touched == ["s1"]
    assert session_manager.saves == 1


@pytest.mark.asyncio
async def test_handle_memory_command_returns_none_for_non_command():
    handler = make_handler(memory_manager=MemoryManager(is_command=False))

    assert await handler.handle_memory_command(Session(), "normal chat") is None
