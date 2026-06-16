import asyncio
import base64
import datetime as dt
import json
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class RequestLike:
    def __init__(self, *, query=None, headers=None, json_body=None, form_body=None, user="alice"):
        self.query_params = query or {}
        if headers is None and json_body is not None and form_body is None:
            headers = {"content-type": "application/json"}
        self.headers = headers or {}
        self._json_body = json_body
        self._form_body = form_body
        self.state = SimpleNamespace(current_user=user)
        self.app = SimpleNamespace(state=SimpleNamespace())

    async def json(self):
        if isinstance(self._json_body, Exception):
            raise self._json_body
        return self._json_body

    async def form(self):
        if isinstance(self._form_body, Exception):
            raise self._form_body
        return self._form_body or {}


def test_search_routes_request_formats_provider_listing_and_errors(monkeypatch):
    import routes.search_routes as search_routes

    json_request = RequestLike(
        query={"q": "query-param"},
        headers={"content-type": "application/json; charset=utf-8"},
        json_body={"query": "json query", "time_filter": "day"},
    )
    assert asyncio.run(search_routes._request_values(json_request)) == {
        "q": "query-param",
        "query": "json query",
        "time_filter": "day",
    }

    broken_form = RequestLike(query={"q": "fallback"}, form_body=RuntimeError("bad form"))
    assert asyncio.run(search_routes._request_values(broken_form)) == {"q": "fallback"}

    form_request = RequestLike(query={"q": "query-param"}, form_body={"query": "form query"})
    assert asyncio.run(search_routes._request_values(form_request)) == {
        "q": "query-param",
        "query": "form query",
    }

    monkeypatch.setattr(search_routes, "get_search_config", lambda: {"provider": "searxng"})
    search_calls = []

    def fake_comprehensive(query, return_sources=False, time_filter=None):
        search_calls.append((query, return_sources, time_filter))
        return "context", [{"title": "Source"}]

    monkeypatch.setattr(search_routes, "comprehensive_web_search", fake_comprehensive)
    monkeypatch.setattr(
        search_routes,
        "PROVIDER_INFO",
        {
            "disabled": ("Disabled", False, False),
            "duck": ("Duck", False, False),
            "keyed": ("Keyed", True, False),
            "searxng": ("SearXNG", False, True),
        },
    )
    monkeypatch.setattr(search_routes, "_get_provider_key", lambda provider: "secret" if provider == "keyed" else "")
    monkeypatch.setattr(search_routes, "_get_search_instance", lambda: None)
    monkeypatch.setattr(search_routes.time, "time", lambda: 100.0)

    router = search_routes.setup_search_routes(config=None)
    assert asyncio.run(_endpoint(router, "/api/search/config")()) == {"provider": "searxng"}
    assert asyncio.run(_endpoint(router, "/api/search")(RequestLike(json_body={"query": "   "}))) == {
        "context": "",
        "sources": [],
        "error": "query is required",
    }
    searched = asyncio.run(_endpoint(router, "/api/search")(json_request))
    assert searched == {"context": "context", "sources": [{"title": "Source"}]}
    assert search_calls == [("json query", True, "day")]

    monkeypatch.setattr(search_routes, "comprehensive_web_search", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    failed = asyncio.run(_endpoint(router, "/api/search")(RequestLike(json_body={"query": "x"})))
    assert failed["error"] == "offline"

    providers = asyncio.run(_endpoint(router, "/api/search/providers")())
    assert providers == [
        {"id": "duck", "label": "Duck", "available": True},
        {"id": "keyed", "label": "Keyed", "available": True},
        {"id": "searxng", "label": "SearXNG", "available": False},
    ]
    monkeypatch.setattr(search_routes, "_get_provider_key", lambda provider: "")
    providers_without_key = asyncio.run(_endpoint(router, "/api/search/providers")())
    assert providers_without_key[1] == {"id": "keyed", "label": "Keyed", "available": False}

    assert asyncio.run(_endpoint(router, "/api/search/query")(RequestLike(json_body={"provider": "duck"}))) == {
        "results": [],
        "provider": "duck",
        "error": "query is required",
    }
    assert asyncio.run(_endpoint(router, "/api/search/query")(RequestLike(json_body={"query": "x", "provider": "missing"}))) == {
        "results": [],
        "provider": "missing",
        "error": "Unknown provider",
    }
    monkeypatch.setattr(search_routes, "_call_provider", lambda provider, query, count: [{"provider": provider, "query": query, "count": count}])
    monkeypatch.setattr(search_routes.time, "time", lambda: 101.234)
    queried = asyncio.run(_endpoint(router, "/api/search/query")(RequestLike(json_body={"query": "x", "provider": "duck", "count": "bad"})))
    assert queried == {
        "results": [{"provider": "duck", "query": "x", "count": 10}],
        "provider": "duck",
        "time": 0.0,
    }
    monkeypatch.setattr(search_routes, "_call_provider", lambda *args: (_ for _ in ()).throw(RuntimeError("provider down")))
    failed_query = asyncio.run(_endpoint(router, "/api/search/query")(RequestLike(json_body={"query": "x", "provider": "duck", "limit": 99})))
    assert failed_query["results"] == []
    assert failed_query["error"] == "provider down"


def test_preset_routes_crud_expand_and_group_paths(monkeypatch):
    import routes.preset_routes as preset_routes

    class Manager:
        def __init__(self):
            self.presets = {"custom": {"name": "Custom"}}
            self.templates = [{"id": "t1"}]
            self.groups = [{"id": "g1"}]
            self.saved_groups = None

        def update_custom(self, *args):
            self.updated = args
            return True

        def get_user_templates(self):
            return self.templates

        def save_user_template(self, template):
            self.template = template
            return template["name"] != "bad"

        def delete_user_template(self, template_id):
            return template_id == "t1"

        def get_group_presets(self):
            return self.groups

        def save_group_presets(self, groups):
            self.saved_groups = groups

    manager = Manager()
    router = preset_routes.setup_preset_routes(manager)

    assert asyncio.run(_endpoint(router, "/api/presets")()) == {"custom": {"name": "Custom"}}
    update_req = SimpleNamespace(
        temperature=0.5,
        max_tokens=128,
        system_prompt="system",
        name="Name",
        enabled=True,
        inject_prefix="pre",
        inject_suffix="post",
    )
    assert asyncio.run(_endpoint(router, "/api/presets/custom", "POST")(update_req, _admin=None)) == {
        "success": True,
        "message": "Custom preset updated",
    }
    assert manager.updated == (0.5, 128, "system", "Name", True, "pre", "post")

    manager.update_custom = lambda *args: False
    assert asyncio.run(_endpoint(router, "/api/presets/custom", "POST")(update_req, _admin=None)) == {
        "success": False,
        "message": "Failed to save preset",
    }
    manager.update_custom = lambda *args: (_ for _ in ()).throw(RuntimeError("save bad"))
    with pytest.raises(HTTPException) as update_failed:
        asyncio.run(_endpoint(router, "/api/presets/custom", "POST")(update_req, _admin=None))
    assert update_failed.value.status_code == 500

    assert asyncio.run(_endpoint(router, "/api/presets/templates")()) == [{"id": "t1"}]
    monkeypatch.setattr(preset_routes.uuid, "uuid4", lambda: SimpleNamespace(hex="abcdef123456"))
    saved = asyncio.run(
        _endpoint(router, "/api/presets/templates", "POST")(
            preset_routes.UserTemplateRequest(name="Template", system_prompt="sys"),
            _admin=None,
        )
    )
    assert saved["success"] is True
    assert saved["template"]["id"] == "user-abcdef12"
    assert asyncio.run(
        _endpoint(router, "/api/presets/templates", "POST")(
            preset_routes.UserTemplateRequest(id="keep", name="bad"),
            _admin=None,
        )
    ) == {"success": False, "message": "Failed to save template"}
    assert asyncio.run(_endpoint(router, "/api/presets/templates/{template_id}", "DELETE")("t1", _admin=None)) == {"success": True}
    assert asyncio.run(_endpoint(router, "/api/presets/templates/{template_id}", "DELETE")("missing", _admin=None)) == {
        "success": False,
        "message": "Failed to delete template",
    }

    ai_interaction = types.ModuleType("src.ai_interaction")
    ai_interaction._resolve_model = lambda model_spec: ("http://local", model_spec or "model", {"X": "1"})
    llm_core = types.ModuleType("src.llm_core")

    async def fake_llm(url, model, messages, temperature=0.0, max_tokens=0, headers=None):
        assert url == "http://local"
        assert model == "model"
        assert "Character name: Ada" in messages[-1]["content"]
        assert temperature == 0.8
        assert max_tokens == 500
        assert headers == {"X": "1"}
        return " expanded prompt "

    llm_core.llm_call_async = fake_llm
    monkeypatch.setitem(sys.modules, "src.ai_interaction", ai_interaction)
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)

    assert asyncio.run(_endpoint(router, "/api/presets/expand")(RequestLike(json_body={}))) == {
        "success": False,
        "message": "Nothing to expand",
    }

    expanded = asyncio.run(_endpoint(router, "/api/presets/expand")(RequestLike(json_body={"name": "Ada", "prompt": "helpful"})))
    assert expanded == {"success": True, "prompt": "expanded prompt"}

    llm_core.llm_call_async = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("llm down"))
    failed_expand = asyncio.run(_endpoint(router, "/api/presets/expand")(RequestLike(json_body={"prompt": "x"})))
    assert failed_expand == {"success": False, "message": "llm down"}

    assert asyncio.run(_endpoint(router, "/api/presets/groups")()) == {"groups": [{"id": "g1"}]}
    assert asyncio.run(_endpoint(router, "/api/presets/groups", "POST")(RequestLike(json_body={"groups": [{"id": "g2"}]}), _admin=None)) == {
        "ok": True
    }
    assert manager.saved_groups == [{"id": "g2"}]


def test_stt_service_dispatch_local_api_stats_and_singleton(monkeypatch, tmp_path):
    import services.stt.stt_service as stt_module

    service = stt_module.STTService()
    settings = {
        "stt_enabled": True,
        "stt_provider": "disabled",
        "stt_model": "base",
        "stt_language": "",
    }
    monkeypatch.setattr(service, "_load_settings", lambda: dict(settings))
    assert service.available is False
    assert service.transcribe(b"audio") is None

    settings["stt_provider"] = "browser"
    assert service.available is True
    assert service.transcribe(b"audio") is None

    settings["stt_provider"] = "local"
    monkeypatch.setattr(service, "_get_whisper", lambda: None)
    assert service.available is False
    assert service._transcribe_local(b"audio") is None

    class Segment:
        def __init__(self, text):
            self.text = text

    class Model:
        def transcribe(self, path, **kwargs):
            assert kwargs == {"language": "en"}
            assert path.endswith(".webm")
            return [Segment(" hello "), Segment("world")], SimpleNamespace(language="en", language_probability=0.98)

    monkeypatch.setattr(service, "_get_whisper", lambda: Model())
    assert service._transcribe_local(b"audio", language="en") == "hello world"

    class BrokenModel:
        def transcribe(self, path, **kwargs):
            raise RuntimeError("bad audio")

    monkeypatch.setattr(service, "_get_whisper", lambda: BrokenModel())
    assert service._transcribe_local(b"audio") is None

    class Endpoint:
        base_url = "http://endpoint/"
        api_key = "secret"

    class Query:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def filter(self, _condition):
            return self

        def first(self):
            return self.endpoint

    class DB:
        def __init__(self, endpoint):
            self.endpoint = endpoint
            self.closed = False

        def query(self, _model):
            return Query(self.endpoint)

        def close(self):
            self.closed = True

    db = DB(Endpoint())
    fake_database = types.ModuleType("src.database")
    fake_database.SessionLocal = lambda: db
    fake_database.ModelEndpoint = SimpleNamespace(id=object())
    monkeypatch.setitem(sys.modules, "src.database", fake_database)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "api text"}

    post_calls = []

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        post_calls.append((url, headers, data, timeout))
        return Response()

    monkeypatch.setattr(stt_module.httpx, "post", fake_post)
    assert service._transcribe_api(b"audio", "ep1", "whisper", "en") == "api text"
    assert post_calls[-1] == (
        "http://endpoint/audio/transcriptions",
        {"Authorization": "Bearer secret"},
        {"model": "whisper", "language": "en"},
        60,
    )
    assert db.closed is True

    db_missing = DB(None)
    fake_database.SessionLocal = lambda: db_missing
    assert service._transcribe_api(b"audio", "missing", "whisper") is None

    db_error = DB(Endpoint())
    fake_database.SessionLocal = lambda: db_error
    monkeypatch.setattr(stt_module.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")))
    assert service._transcribe_api(b"audio", "ep1", "whisper") is None

    settings.update({"stt_provider": "endpoint:ep1", "stt_model": "whisper", "stt_language": "en"})
    monkeypatch.setattr(service, "_transcribe_api", lambda audio, endpoint_id, model, language: f"{endpoint_id}:{model}:{language}:{len(audio)}")
    assert service.available is True
    assert service.transcribe(b"abc") == "ep1:whisper:en:3"
    stats = service.get_stats()
    assert stats["endpoint_id"] == "ep1"
    assert stats["available"] is True

    settings["stt_enabled"] = False
    assert service.get_stats()["provider"] == "disabled"

    settings["stt_enabled"] = True
    settings["stt_provider"] = "unknown"
    assert service.available is False
    assert service.transcribe(b"abc") is None

    stt_module._stt_service = None
    assert stt_module.get_stt_service() is stt_module.get_stt_service()


def test_tts_service_cache_dispatch_stats_kokoro_and_singleton(monkeypatch, tmp_path):
    import services.tts.tts_service as tts_module

    service = tts_module.TTSService(cache_dir=str(tmp_path / "tts"))
    settings = {
        "tts_enabled": True,
        "tts_provider": "disabled",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1.5",
    }
    monkeypatch.setattr(service, "_load_settings", lambda: dict(settings))

    key = service._cache_key("text", "endpoint:one", "tts-1", "alloy", 1.5)
    assert len(key) == 64
    assert service._get_cached(key) is None
    service._put_cache("mp3", b"ID3data")
    service._put_cache("wav", b"RIFFdata")
    assert service._get_cached("mp3") == b"ID3data"
    assert service._get_cached("wav") == b"RIFFdata"
    service.clear_cache()
    assert list(service.cache_dir.glob("*.*")) == []

    assert service.available is False
    assert service.synthesize("hello") is None
    settings["tts_provider"] = "browser"
    assert service.available is True
    assert service.synthesize("hello") is None

    class Kokoro:
        available = True

        def synthesize_raw(self, text, voice):
            assert voice == "alloy"
            return b"RIFFlocal"

    settings["tts_provider"] = "local"
    monkeypatch.setattr(service, "_get_kokoro", lambda: Kokoro())
    assert service.available is True
    assert service.synthesize("local text", use_cache=False) == b"RIFFlocal"

    class MissingKokoro:
        available = False

    monkeypatch.setattr(service, "_get_kokoro", lambda: MissingKokoro())
    assert service.synthesize("local text", use_cache=False) is None

    class Endpoint:
        base_url = "http://endpoint/"
        api_key = ""

    class Query:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def filter(self, _condition):
            return self

        def first(self):
            return self.endpoint

    class DB:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def query(self, _model):
            return Query(self.endpoint)

        def close(self):
            self.closed = True

    fake_database = types.ModuleType("src.database")
    fake_database.SessionLocal = lambda: DB(Endpoint())
    fake_database.ModelEndpoint = SimpleNamespace(id=object())
    monkeypatch.setitem(sys.modules, "src.database", fake_database)

    class Response:
        content = b"ID3remote"

        def raise_for_status(self):
            return None

    post_calls = []
    monkeypatch.setattr(
        tts_module.httpx,
        "post",
        lambda url, json=None, headers=None, timeout=None: post_calls.append((url, json, headers, timeout)) or Response(),
    )
    assert service._synthesize_api("hello", "ep1", "tts-1", "alloy", 1.25) == b"ID3remote"
    assert post_calls[-1][0] == "http://endpoint/audio/speech"
    assert post_calls[-1][1]["speed"] == 1.25
    assert post_calls[-1][2] == {"Content-Type": "application/json"}

    fake_database.SessionLocal = lambda: DB(None)
    assert service._synthesize_api("hello", "missing", "tts-1", "alloy") is None
    fake_database.SessionLocal = lambda: DB(Endpoint())
    monkeypatch.setattr(tts_module.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")))
    assert service._synthesize_api("hello", "ep1", "tts-1", "alloy") is None

    settings["tts_provider"] = "endpoint:ep1"
    monkeypatch.setattr(service, "_synthesize_api", lambda text, endpoint_id, model, voice, speed: b"ID3" + text.encode()[:3])
    long_text = "x" * 5100
    assert service.synthesize(long_text) == b"ID3xxx"
    assert service.synthesize(long_text) == b"ID3xxx"
    assert service.synthesize_to_base64("abc") == base64.b64encode(b"ID3abc").decode("utf-8")
    monkeypatch.setattr(service, "synthesize", lambda text: None)
    assert service.synthesize_to_base64("abc") is None
    service.set_voice("other")

    stats = service.get_stats()
    assert stats["endpoint_id"] == "ep1"
    assert stats["available"] is True
    assert stats["ready"] is True
    settings["tts_enabled"] = False
    assert service.get_stats()["available"] is False
    settings["tts_enabled"] = True
    settings["tts_provider"] = "unknown"
    assert service.available is False
    assert service.synthesize("hello") is None

    class KokoroPipeline(tts_module._KokoroPipeline):
        def _init(self):
            self.available = False

    kokoro = KokoroPipeline()
    assert kokoro.synthesize_raw("text") is None

    tts_module._tts_service = None
    first = tts_module.get_tts_service()
    assert first is tts_module.get_tts_service()


def test_hwfit_routes_manual_overrides_model_errors_and_image_models(monkeypatch):
    import routes.hwfit_routes as hwfit_routes
    import services.hwfit.hardware as hardware
    import services.hwfit.fit as fit
    import services.hwfit.models as llm_models
    import services.hwfit.image_models as image_models

    detected = {
        "has_gpu": True,
        "gpu_name": "GPU",
        "gpu_vram_gb": 24,
        "gpu_count": 2,
        "gpus": [{"index": 0, "name": "GPU", "vram_gb": 12}, {"index": 1, "name": "GPU", "vram_gb": 12}],
        "gpu_groups": [{"name": "GPU", "vram_each": 12, "count": 2, "indices": [0, 1], "vram_total": 24}],
        "available_ram_gb": 64,
        "total_ram_gb": 64,
        "backend": "cuda",
    }
    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: dict(detected))
    monkeypatch.setattr(llm_models, "get_models", lambda: [{"id": "m"}])
    monkeypatch.setattr(llm_models, "model_catalog_path", lambda: "catalog.json")
    monkeypatch.setattr(fit, "rank_models", lambda system, **kwargs: [{"system": system, "kwargs": kwargs}])
    original_rank_image_models = image_models.rank_image_models
    monkeypatch.setattr(image_models, "rank_image_models", lambda system, **kwargs: [{"system": system, "kwargs": kwargs}])

    router = hwfit_routes.setup_hwfit_routes()
    assert _endpoint(router, "/api/hwfit/system")(host="box", fresh=True)["gpu_count"] == 2

    gpu_models = _endpoint(router, "/api/hwfit/models")(
        gpu_count="1",
        manual_mode="gpu",
        manual_gpu_count="3",
        manual_vram_gb="40",
        manual_ram_gb="128",
        manual_backend="bad",
        limit=3,
        search="coder",
        quant="Q4",
    )
    model_system = gpu_models["models"][0]["system"]
    assert model_system["gpu_count"] == 1
    assert model_system["gpu_vram_gb"] == 40
    assert model_system["total_ram_gb"] == 128
    assert model_system["backend"] == "cuda"
    assert gpu_models["models"][0]["kwargs"]["limit"] == 3

    cpu_models = _endpoint(router, "/api/hwfit/models")(gpu_count="0", ignore_detected_gpu=True, ignore_detected_ram=True)
    assert cpu_models["models"][0]["system"]["has_gpu"] is False
    assert cpu_models["models"][0]["system"]["total_ram_gb"] == 0

    ram_models = _endpoint(router, "/api/hwfit/models")(manual_mode="ram", manual_ram_gb="32")
    assert ram_models["models"][0]["system"]["backend"] == "cpu_x86"
    assert ram_models["models"][0]["system"]["gpu_count"] == 0

    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: {"error": "no ssh"})
    assert _endpoint(router, "/api/hwfit/models")()["error"] == "no ssh"
    assert _endpoint(router, "/api/hwfit/image-models")()["error"] == "no ssh"

    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: dict(detected))
    monkeypatch.setattr(llm_models, "get_models", lambda: [])
    missing_catalog = _endpoint(router, "/api/hwfit/models")()
    assert missing_catalog["models"] == []
    assert "catalog.json" in missing_catalog["error"]

    image_ranked = _endpoint(router, "/api/hwfit/image-models")(gpu_count="1", ignore_detected_ram=True)
    assert image_ranked["models"][0]["system"]["gpu_count"] == 1
    assert image_ranked["models"][0]["system"]["gpu_vram_gb"] == 12

    monkeypatch.setattr(image_models, "rank_image_models", original_rank_image_models)
    original_registry = image_models.IMAGE_MODEL_REGISTRY
    monkeypatch.setattr(
        image_models,
        "IMAGE_MODEL_REGISTRY",
        [
            {
                "id": "big/model",
                "name": "Big Model",
                "provider": "A",
                "params_b": 10,
                "vram_bf16": 30,
                "vram_fp8": 10,
                "vram_q4": 5,
                "default_quant": "BF16",
                "quant_repos": {"FP8": "big/fp8"},
                "capabilities": ["text-to-image"],
                "description": "quality",
                "quality": 90,
                "speed": 50,
                "released": "2026",
            },
            {
                "id": "fast/model",
                "name": "Fast Model",
                "provider": "B",
                "params_b": 2,
                "vram_bf16": 4,
                "vram_fp8": 2,
                "vram_q4": None,
                "default_quant": "BF16",
                "quant_repos": {},
                "capabilities": ["text-to-image"],
                "description": "fast",
                "quality": 70,
                "speed": 95,
            },
        ],
    )
    assert image_models.get_image_models()[0]["id"] == "big/model"
    no_gpu = image_models.rank_image_models({"has_gpu": False, "gpu_vram_gb": 0})
    assert {item["fit"] for item in no_gpu} == {"no_gpu"}
    filtered = image_models.rank_image_models({"has_gpu": True, "gpu_vram_gb": 12}, search="big", sort="vram")
    assert filtered[0]["id"] == "big/model"
    assert filtered[0]["quant"] == "FP8"
    assert filtered[0]["quant_repo"] == "big/fp8"
    quality_sorted = image_models.rank_image_models({"has_gpu": True, "gpu_vram_gb": 99}, sort="quality")
    assert quality_sorted[0]["id"] == "big/model"
    speed_sorted = image_models.rank_image_models({"has_gpu": True, "gpu_vram_gb": 99}, sort="speed")
    assert speed_sorted[0]["id"] == "fast/model"
    too_small = image_models.rank_image_models({"has_gpu": True, "gpu_vram_gb": 1})
    assert {item["fit"] for item in too_small} == {"no_fit"}
    monkeypatch.setattr(image_models, "IMAGE_MODEL_REGISTRY", original_registry)


def test_signature_routes_list_create_delete_and_db_errors(monkeypatch):
    import routes.signature_routes as signature_routes

    class Column:
        def __eq__(self, other):
            return other

        def desc(self):
            return self

    class Signature:
        id = Column()
        owner = Column()
        created_at = Column()

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.created_at = kwargs.get("created_at", dt.datetime(2026, 1, 2, 3, 4, 5))

    class Query:
        def __init__(self, db):
            self.db = db

        def filter(self, condition):
            self.db.filter_condition = condition
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return self.db.rows

        def first(self):
            return self.db.first

    class DB:
        def __init__(self):
            self.rows = [
                Signature(id="s1", owner="alice", name="Sig", data_png="YWJj", width=10, height=20, created_at=dt.datetime(2026, 1, 1))
            ]
            self.first = self.rows[0]
            self.added = []
            self.deleted = []
            self.committed = False
            self.rolled_back = False
            self.filter_condition = None

        def query(self, _model):
            return Query(self)

        def add(self, sig):
            self.added.append(sig)

        def commit(self):
            self.committed = True

        def refresh(self, _sig):
            return None

        def rollback(self):
            self.rolled_back = True

        def delete(self, sig):
            self.deleted.append(sig)

        def close(self):
            self.closed = True

    db = DB()
    monkeypatch.setattr(signature_routes, "Signature", Signature)
    monkeypatch.setattr(signature_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(signature_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(signature_routes.uuid, "uuid4", lambda: "uuid-1")

    router = signature_routes.setup_signature_routes()
    listed = asyncio.run(_endpoint(router, "/api/signatures")(RequestLike(user="alice")))
    assert listed["signatures"][0]["data_url"] == "data:image/png;base64,YWJj"
    assert db.filter_condition == "alice"

    no_user = asyncio.run(_endpoint(router, "/api/signatures")(RequestLike(user=None)))
    assert no_user["signatures"][0]["id"] == "s1"

    with pytest.raises(HTTPException) as invalid:
        asyncio.run(
            _endpoint(router, "/api/signatures", "POST")(
                RequestLike(user="alice"),
                signature_routes.SignatureCreate(name="x", data="not base64"),
            )
        )
    assert invalid.value.status_code == 400
    with pytest.raises(HTTPException) as empty_payload:
        asyncio.run(
            _endpoint(router, "/api/signatures", "POST")(
                RequestLike(user="alice"),
                signature_routes.SignatureCreate(name="x", data=""),
            )
        )
    assert empty_payload.value.status_code == 400

    created = asyncio.run(
        _endpoint(router, "/api/signatures", "POST")(
            RequestLike(user="alice"),
            signature_routes.SignatureCreate(
                name="   ",
                data="data:image/png;base64," + base64.b64encode(b"png").decode("ascii"),
                width=7,
                height=8,
                svg="<svg />",
            ),
        )
    )
    assert created["name"] == "Signature"
    assert db.added[-1].owner == "alice"
    assert db.added[-1].width == 7

    db.first = None
    with pytest.raises(HTTPException) as missing:
        asyncio.run(_endpoint(router, "/api/signatures/{sig_id}", "DELETE")("missing", RequestLike(user="alice")))
    assert missing.value.status_code == 404

    db.first = Signature(id="s2", owner="bob", name="Other", data_png="YQ==", width=None, height=None)
    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(_endpoint(router, "/api/signatures/{sig_id}", "DELETE")("s2", RequestLike(user="alice")))
    assert forbidden.value.status_code == 403

    db.first = Signature(id="s3", owner="alice", name="Mine", data_png="YQ==", width=None, height=None)
    assert asyncio.run(_endpoint(router, "/api/signatures/{sig_id}", "DELETE")("s3", RequestLike(user="alice"))) == {"deleted": "s3"}
    assert db.deleted[-1].id == "s3"

    class BrokenDB(DB):
        def commit(self):
            raise RuntimeError("commit bad")

    broken_db = BrokenDB()
    monkeypatch.setattr(signature_routes, "SessionLocal", lambda: broken_db)
    with pytest.raises(HTTPException) as create_failed:
        asyncio.run(
            _endpoint(router, "/api/signatures", "POST")(
                RequestLike(user="alice"),
                signature_routes.SignatureCreate(data=base64.b64encode(b"png").decode("ascii")),
            )
        )
    assert create_failed.value.status_code == 500
    assert broken_db.rolled_back is True

    class BrokenDeleteDB(DB):
        def delete(self, sig):
            raise RuntimeError("delete bad")

    broken_delete_db = BrokenDeleteDB()
    broken_delete_db.first = Signature(id="s4", owner="alice", name="Mine", data_png="YQ==", width=None, height=None)
    monkeypatch.setattr(signature_routes, "SessionLocal", lambda: broken_delete_db)
    with pytest.raises(HTTPException) as delete_failed:
        asyncio.run(_endpoint(router, "/api/signatures/{sig_id}", "DELETE")("s4", RequestLike(user="alice")))
    assert delete_failed.value.status_code == 500
    assert broken_delete_db.rolled_back is True
