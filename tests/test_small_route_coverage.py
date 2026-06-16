import asyncio
import datetime as dt
import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


def _request(user="alice"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(state=SimpleNamespace(invalidate_token_cache=lambda: None)),
    )


def test_cleanup_routes_success_and_failure(monkeypatch):
    import routes.cleanup_routes as cleanup_routes

    async def preview(owner=None):
        return {"owner": owner, "archive": ["s1"]}

    async def cleanup(session_manager, owner=None):
        assert session_manager == "sessions"
        assert owner == "alice"
        return 2, 3, 4.567

    monkeypatch.setattr(cleanup_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(cleanup_routes, "get_cleanup_preview", preview)
    monkeypatch.setattr(cleanup_routes, "cleanup_sessions", cleanup)

    router = cleanup_routes.setup_cleanup_routes("sessions")

    assert asyncio.run(_endpoint(router, "/api/cleanup/preview")(_request())) == {
        "owner": "alice",
        "archive": ["s1"],
    }
    assert asyncio.run(_endpoint(router, "/api/cleanup")(_request())) == {
        "archived_count": 2,
        "deleted_count": 3,
        "space_freed_mb": 4.57,
    }

    async def boom_preview(owner=None):
        raise RuntimeError("preview failed")

    async def boom_cleanup(session_manager, owner=None):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(cleanup_routes, "get_cleanup_preview", boom_preview)
    with pytest.raises(HTTPException) as preview_exc:
        asyncio.run(_endpoint(router, "/api/cleanup/preview")(_request()))
    assert preview_exc.value.status_code == 500

    monkeypatch.setattr(cleanup_routes, "cleanup_sessions", boom_cleanup)
    with pytest.raises(HTTPException) as cleanup_exc:
        asyncio.run(_endpoint(router, "/api/cleanup")(_request()))
    assert cleanup_exc.value.status_code == 500


def test_font_routes_group_custom_fonts(monkeypatch, tmp_path):
    import routes.font_routes as font_routes

    custom = tmp_path / "fonts"
    custom.mkdir()
    (custom / "JetBrainsMono-Regular.woff2").write_text("font", encoding="utf-8")
    (custom / "GohuFont.ttf").write_text("font", encoding="utf-8")
    (custom / "notes.txt").write_text("skip", encoding="utf-8")
    monkeypatch.setattr(font_routes, "CUSTOM_FONTS_DIR", str(custom))

    assert font_routes._derive_family("JetBrainsMono-Regular.woff2") == "Jet Brains Mono"
    assert font_routes._derive_family("____.ttf") == "____.ttf"

    result = asyncio.run(_endpoint(font_routes.setup_font_routes(), "/api/fonts/custom")())

    assert sorted(result["fonts"]) == ["Gohu Font", "Jet Brains Mono"]
    assert result["fonts"]["Jet Brains Mono"][0] == {
        "file": "JetBrainsMono-Regular.woff2",
        "url": "/static/fonts/custom/JetBrainsMono-Regular.woff2",
        "format": "woff2",
    }


def test_emoji_routes_invalid_cached_fetch_and_failure(monkeypatch, tmp_path):
    import routes.emoji_routes as emoji_routes

    cache_dir = tmp_path / "emoji"
    monkeypatch.setattr(emoji_routes, "_CACHE_DIR", cache_dir)
    router = emoji_routes.setup_emoji_routes()
    emoji_svg = _endpoint(router, "/api/emoji/{code}.svg")

    invalid = asyncio.run(emoji_svg("../bad"))
    assert isinstance(invalid, Response)
    assert invalid.body == emoji_routes._BLANK_SVG

    cache_dir.mkdir()
    cached = cache_dir / "1f600.svg"
    cached.write_bytes(b"<svg>cached</svg>")
    cached_response = asyncio.run(emoji_svg("1F600"))
    assert isinstance(cached_response, FileResponse)
    assert str(cached_response.path).endswith("1f600.svg")

    cached.unlink()

    class GoodResponse:
        status_code = 200
        content = b"<svg>downloaded</svg>"

    class GoodClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            assert url.endswith("/1F600.svg")
            return GoodResponse()

    monkeypatch.setattr(emoji_routes.httpx, "AsyncClient", GoodClient)
    fetched = asyncio.run(emoji_svg("1f600"))
    assert fetched.body == b"<svg>downloaded</svg>"
    assert cached.read_bytes() == b"<svg>downloaded</svg>"

    cached.unlink()

    class UnwritableFile:
        def exists(self):
            return False

        def write_bytes(self, _content):
            raise OSError("read only")

    class UnwritableCacheDir:
        def mkdir(self, parents=False, exist_ok=False):
            return None

        def __truediv__(self, _name):
            return UnwritableFile()

    monkeypatch.setattr(emoji_routes, "_CACHE_DIR", UnwritableCacheDir())
    write_failed_but_served = asyncio.run(emoji_svg("1f600"))
    assert write_failed_but_served.body == b"<svg>downloaded</svg>"

    monkeypatch.setattr(emoji_routes, "_CACHE_DIR", cache_dir)

    class BadClient(GoodClient):
        async def get(self, url):
            raise RuntimeError("offline")

    monkeypatch.setattr(emoji_routes.httpx, "AsyncClient", BadClient)
    failed = asyncio.run(emoji_svg("1f601"))
    assert failed.body == emoji_routes._BLANK_SVG


def test_stt_routes_stats_transcribe_and_errors():
    import routes.stt_routes as stt_routes

    class Upload:
        def __init__(self, data):
            self.data = data

        async def read(self):
            return self.data

    class Service:
        available = True

        def __init__(self):
            self.transcribed = []

        def get_stats(self):
            return {"provider": "local"}

        def transcribe(self, audio_bytes):
            self.transcribed.append(audio_bytes)
            return "hello"

    service = Service()
    router = stt_routes.setup_stt_routes(service)

    assert asyncio.run(_endpoint(router, "/api/stt/stats")()) == {"provider": "local"}
    assert asyncio.run(_endpoint(router, "/api/stt/transcribe")(Upload(b"audio"))) == {"text": "hello"}
    assert service.transcribed == [b"audio"]

    service.available = False
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(_endpoint(router, "/api/stt/transcribe")(Upload(b"audio")))
    assert unavailable.value.status_code == 503

    service.available = True
    with pytest.raises(HTTPException) as empty:
        asyncio.run(_endpoint(router, "/api/stt/transcribe")(Upload(b"")))
    assert empty.value.status_code == 400

    service.transcribe = lambda _data: None
    with pytest.raises(HTTPException) as failed:
        asyncio.run(_endpoint(router, "/api/stt/transcribe")(Upload(b"audio")))
    assert failed.value.status_code == 500

    class BrokenUpload:
        async def read(self):
            raise RuntimeError("read failed")

    with pytest.raises(HTTPException) as broken_upload:
        asyncio.run(_endpoint(router, "/api/stt/transcribe")(BrokenUpload()))
    assert broken_upload.value.status_code == 500
    assert "read failed" in broken_upload.value.detail["message"]

    service.get_stats = lambda: (_ for _ in ()).throw(RuntimeError("stats bad"))
    with pytest.raises(HTTPException) as stats_bad:
        asyncio.run(_endpoint(router, "/api/stt/stats")())
    assert stats_bad.value.status_code == 500


def test_tts_routes_audio_base64_cache_and_errors():
    import routes.tts_routes as tts_routes

    class Service:
        available = True

        def __init__(self):
            self.cache_cleared = False

        def get_stats(self):
            return {"provider": "endpoint"}

        def synthesize_to_base64(self, text):
            return "YWJj" if text == "ok" else ""

        def synthesize(self, text):
            if text == "mp3":
                return b"ID3abc"
            if text == "wav":
                return b"RIFFabc"
            return b""

        def clear_cache(self):
            self.cache_cleared = True

    service = Service()
    router = tts_routes.setup_tts_routes(service)

    assert asyncio.run(_endpoint(router, "/api/tts/stats")()) == {"provider": "endpoint"}
    assert asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="ok", format="base64"))) == {"audio": "YWJj"}

    mp3 = asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="mp3")))
    assert mp3.media_type == "audio/mpeg"
    assert mp3.body == b"ID3abc"

    wav = asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="wav")))
    assert wav.media_type == "audio/wav"
    assert wav.body == b"RIFFabc"

    assert asyncio.run(_endpoint(router, "/api/tts/clear-cache")()) == {
        "success": True,
        "message": "Cache cleared",
    }
    assert service.cache_cleared is True

    service.available = False
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="ok")))
    assert unavailable.value.status_code == 503

    service.available = True
    with pytest.raises(HTTPException) as bad_b64:
        asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="bad", format="base64")))
    assert bad_b64.value.status_code == 500

    with pytest.raises(HTTPException) as bad_audio:
        asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="bad")))
    assert bad_audio.value.status_code == 500

    service.synthesize = lambda _text: (_ for _ in ()).throw(RuntimeError("synth crash"))
    with pytest.raises(HTTPException) as synth_crash:
        asyncio.run(_endpoint(router, "/api/tts/synthesize")(tts_routes.TTSRequest(text="mp3")))
    assert synth_crash.value.status_code == 500
    assert "synth crash" in synth_crash.value.detail["message"]

    service.get_stats = lambda: (_ for _ in ()).throw(RuntimeError("stats bad"))
    with pytest.raises(HTTPException) as stats_bad:
        asyncio.run(_endpoint(router, "/api/tts/stats")())
    assert stats_bad.value.status_code == 500

    service.clear_cache = lambda: (_ for _ in ()).throw(RuntimeError("cache bad"))
    with pytest.raises(HTTPException) as cache_bad:
        asyncio.run(_endpoint(router, "/api/tts/clear-cache")())
    assert cache_bad.value.status_code == 500


def test_api_token_routes_list_create_delete_and_errors(monkeypatch):
    import routes.api_token_routes as token_routes

    class Column:
        def __eq__(self, other):
            return other

    class ApiToken:
        id = Column()

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Query:
        def __init__(self, db):
            self.db = db
            self._id = None

        def all(self):
            return self.db.tokens

        def filter(self, condition):
            self._id = condition
            return self

        def delete(self):
            if self._id == "missing":
                return 0
            self.db.deleted.append(self._id)
            return 1

    class DB:
        def __init__(self):
            self.tokens = [
                SimpleNamespace(
                    id="tok1",
                    name="Main",
                    owner="alice",
                    token_prefix="clv_abc",
                    scopes="chat, tools",
                    is_active=True,
                    last_used_at=dt.datetime(2026, 1, 2, 3, 4, 5),
                    created_at=dt.datetime(2026, 1, 1, 1, 2, 3),
                )
            ]
            self.added = []
            self.deleted = []
            self.delete_id = None

        def query(self, _model):
            return Query(self)

        def add(self, token):
            self.added.append(token)

    db = DB()

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(token_routes, "ApiToken", ApiToken)
    monkeypatch.setattr(token_routes, "get_db_session", session)
    monkeypatch.setattr(token_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(token_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(token_routes.secrets, "token_urlsafe", lambda _n: "raw-secret")
    monkeypatch.setattr(token_routes.uuid, "uuid4", lambda: "12345678-uuid")
    monkeypatch.setattr(token_routes.bcrypt, "gensalt", lambda: b"salt")
    monkeypatch.setattr(token_routes.bcrypt, "hashpw", lambda data, salt: b"hashed")

    router = token_routes.setup_api_token_routes()
    request = _request()

    listed = _endpoint(router, "/api/tokens")(request)
    assert listed[0]["scopes"] == ["chat", "tools"]
    assert listed[0]["last_used_at"] == "2026-01-02T03:04:05"

    created = _endpoint(router, "/api/tokens", "POST")(request, name="  token name  ")
    assert created["id"] == "12345678"
    assert created["owner"] == "alice"
    assert created["token"] == "clv_raw-secret"
    assert db.added[0].token_hash == "hashed"

    with pytest.raises(HTTPException) as missing_name:
        _endpoint(router, "/api/tokens", "POST")(request, name="   ")
    assert missing_name.value.status_code == 400

    db.delete_id = "tok1"
    assert _endpoint(router, "/api/tokens/{token_id}", "DELETE")(request, token_id="tok1") == {"status": "deleted"}
    assert db.deleted == ["tok1"]

    db.delete_id = "missing"
    with pytest.raises(HTTPException) as missing:
        _endpoint(router, "/api/tokens/{token_id}", "DELETE")(request, token_id="missing")
    assert missing.value.status_code == 404

    request.app.state.invalidate_token_cache = lambda: (_ for _ in ()).throw(RuntimeError("ignore"))
    db.delete_id = "tok2"
    assert _endpoint(router, "/api/tokens/{token_id}", "DELETE")(request, token_id="tok2") == {"status": "deleted"}


def test_prefs_routes_load_save_and_user_scope(monkeypatch, tmp_path):
    import routes.prefs_routes as prefs_routes

    prefs_file = tmp_path / "prefs.json"
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda request: request.state.current_user)

    assert prefs_routes._load() == {}
    prefs_file.write_text("{", encoding="utf-8")
    assert prefs_routes._load() == {}

    prefs_file.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert prefs_routes._load_for_user("alice") == {"theme": "dark"}

    prefs_file.write_text(json.dumps({"_users": {"bob": {"theme": "blue"}}}), encoding="utf-8")
    assert prefs_routes._load_for_user(None) == {"theme": "blue"}
    assert prefs_routes._load_for_user("alice") == {}

    prefs_routes._save_for_user(None, {"flat": True})
    assert json.loads(prefs_file.read_text(encoding="utf-8")) == {"flat": True}

    router = prefs_routes.setup_prefs_routes()
    request = _request("alice")

    assert asyncio.run(_endpoint(router, "/api/prefs")(request)) == {"flat": True}
    assert asyncio.run(_endpoint(router, "/api/prefs/{key}")(request, key="missing")) == {"key": "missing", "value": None}
    assert asyncio.run(_endpoint(router, "/api/prefs/{key}", "PUT")(request, key="theme", body={"value": "cyan"})) == {
        "key": "theme",
        "value": "cyan",
    }
    assert json.loads(prefs_file.read_text(encoding="utf-8")) == {"_users": {"alice": {"flat": True, "theme": "cyan"}}}


def test_diagnostics_routes_success_and_error_paths(monkeypatch):
    import routes.diagnostics_routes as diagnostics_routes

    class Rag:
        def get_stats(self):
            return {"chunks": 3}

    class Research:
        async def call_research_service(self, query, endpoint, model):
            assert query == "topic"
            assert endpoint.endswith("/v1/chat/completions")
            assert model == "gpt-oss-120b"
            return "x" * 250

    core_database = types.ModuleType("core.database")
    core_database.get_detailed_stats = lambda: {"sessions": 2}
    monkeypatch.setitem(sys.modules, "core.database", core_database)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(diagnostics_routes, "extract_youtube_id", lambda url: "vid" if "watch" in url else None)

    async def transcript(url, video_id):
        return {"success": True, "transcript": "t" * 600}

    monkeypatch.setattr(diagnostics_routes, "extract_transcript_async", transcript)

    router = diagnostics_routes.setup_diagnostics_routes(Rag(), True, Research())
    request = _request()

    assert asyncio.run(_endpoint(router, "/api/db/stats")(request)) == {"sessions": 2}
    assert asyncio.run(_endpoint(router, "/api/rag/stats")(request)) == {"chunks": 3}
    youtube = asyncio.run(_endpoint(router, "/api/test/youtube")(request, url="https://youtube.com/watch?v=vid"))
    assert youtube["video_id"] == "vid"
    assert youtube["transcript_success"] is True
    assert youtube["transcript_length"] == 600
    assert youtube["transcript_preview"].endswith("...")
    research = asyncio.run(_endpoint(router, "/api/test-research")(request, query="topic"))
    assert research["status"] == "success"
    assert research["result_length"] == 250

    no_rag = diagnostics_routes.setup_diagnostics_routes(None, False, Research())
    assert asyncio.run(_endpoint(no_rag, "/api/rag/stats")(request)) == {"error": "RAG system not available"}
    assert asyncio.run(_endpoint(router, "/api/test/youtube")(request, url="bad")) == {"error": "Invalid YouTube URL"}

    async def transcript_fail(url, video_id):
        return {"success": False, "error": "captions off", "transcript": None}

    monkeypatch.setattr(diagnostics_routes, "extract_transcript_async", transcript_fail)
    failed_youtube = asyncio.run(_endpoint(router, "/api/test/youtube")(request, url="https://youtube.com/watch?v=vid"))
    assert failed_youtube["error"] == "captions off"
    assert failed_youtube["transcript_length"] == 0

    monkeypatch.setattr(diagnostics_routes, "extract_youtube_id", lambda url: (_ for _ in ()).throw(RuntimeError("parse bad")))
    assert asyncio.run(_endpoint(router, "/api/test/youtube")(request, url="x")) == {"error": "parse bad"}

    class BrokenResearch:
        async def call_research_service(self, *args, **kwargs):
            raise RuntimeError("research bad")

    broken_research = diagnostics_routes.setup_diagnostics_routes(Rag(), True, BrokenResearch())
    assert asyncio.run(_endpoint(broken_research, "/api/test-research")(request, query="topic")) == {
        "status": "error",
        "error": "research bad",
        "query": "topic",
    }

    core_database.get_detailed_stats = lambda: (_ for _ in ()).throw(RuntimeError("db bad"))
    with pytest.raises(HTTPException) as db_bad:
        asyncio.run(_endpoint(router, "/api/db/stats")(request))
    assert db_bad.value.status_code == 500


def test_training_routes_success_and_bad_request_paths(monkeypatch):
    import routes.training_routes as training_routes
    from src.local_training import LocalTrainingError

    monkeypatch.setattr(training_routes, "ensure_training_dirs", lambda: "training-root")
    monkeypatch.setattr(training_routes, "list_datasets", lambda: [{"id": "ds"}])
    monkeypatch.setattr(training_routes, "list_artifacts", lambda: [{"id": "art"}])
    monkeypatch.setattr(training_routes, "finetune_status", lambda: {"jobs": []})
    monkeypatch.setattr(training_routes, "create_dataset", lambda name, text: {"name": name, "chars": len(text)})
    monkeypatch.setattr(training_routes, "train_ngram", lambda dataset_id, model_name, order: {"dataset_id": dataset_id, "order": order})
    monkeypatch.setattr(training_routes, "generate_text", lambda artifact_id, **kwargs: {"artifact_id": artifact_id, **kwargs})
    monkeypatch.setattr(training_routes, "start_lora_job", lambda **kwargs: {"job": kwargs})
    monkeypatch.setattr(training_routes, "read_job", lambda job_id: {"id": job_id})

    router = training_routes.setup_training_routes()

    status = _endpoint(router, "/api/training/status")()
    assert status["ok"] is True
    assert status["root"] == "training-root"
    assert status["datasets"] == [{"id": "ds"}]

    dataset_body = training_routes.DatasetCreateRequest(name="Corpus", text="x" * 32)
    assert _endpoint(router, "/api/training/datasets")(dataset_body)["dataset"]["name"] == "Corpus"

    train_body = training_routes.TrainRequest(dataset_id="ds", model_name="Model", order=2)
    assert _endpoint(router, "/api/training/train")(train_body)["artifact"]["order"] == 2

    generate_body = training_routes.GenerateRequest(artifact_id="art", prompt="hi", max_chars=3, temperature=0.5, seed=7)
    generated = _endpoint(router, "/api/training/generate")(generate_body)
    assert generated["output"]["artifact_id"] == "art"
    assert generated["output"]["seed"] == 7

    assert _endpoint(router, "/api/training/finetune/status")()["finetune"] == {"jobs": []}

    finetune_body = training_routes.FineTuneRequest(dataset_id="ds", model_id="model")
    assert _endpoint(router, "/api/training/finetune/jobs")(finetune_body)["job"]["job"]["dataset_id"] == "ds"
    assert _endpoint(router, "/api/training/finetune/jobs/{job_id}")(job_id="job1")["job"] == {"id": "job1"}

    for attr, path, body in (
        ("create_dataset", "/api/training/datasets", dataset_body),
        ("train_ngram", "/api/training/train", train_body),
        ("generate_text", "/api/training/generate", generate_body),
        ("start_lora_job", "/api/training/finetune/jobs", finetune_body),
    ):
        monkeypatch.setattr(training_routes, attr, lambda *args, **kwargs: (_ for _ in ()).throw(LocalTrainingError("bad input")))
        with pytest.raises(HTTPException) as exc:
            _endpoint(router, path)(body)
        assert exc.value.status_code == 400

    monkeypatch.setattr(training_routes, "read_job", lambda job_id: (_ for _ in ()).throw(LocalTrainingError("missing job")))
    with pytest.raises(HTTPException) as missing:
        _endpoint(router, "/api/training/finetune/jobs/{job_id}")(job_id="missing")
    assert missing.value.status_code == 400
