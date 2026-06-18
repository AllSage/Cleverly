import asyncio
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


def test_embedding_routes_remaining_error_reset_and_cache_paths(monkeypatch, tmp_path):
    import routes.embedding_routes as embedding_routes

    cache = tmp_path / "cache"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(cache))
    assert embedding_routes._cache_dir() == str(cache)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    assert embedding_routes._cache_dir().endswith(os.path.join("data", "fastembed_cache"))
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(cache))

    hf_source = "org/model"
    model_dir = cache / embedding_routes._model_cache_name(hf_source)
    snapshots = model_dir / "snapshots" / "rev"
    snapshots.mkdir(parents=True)
    (snapshots / "model.onnx").write_bytes(b"onnx")
    assert embedding_routes._is_downloaded(hf_source) is True
    for child in snapshots.iterdir():
        child.unlink()
    snapshots.rmdir()
    (model_dir / "snapshots").rmdir()
    blobs = model_dir / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "weights.bin").write_bytes(b"123")
    assert embedding_routes._is_downloaded(hf_source) is True
    monkeypatch.setattr(embedding_routes.os.path, "getsize", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    assert embedding_routes._dir_size_mb(str(cache)) == 0.0

    endpoint_file = tmp_path / "endpoint.json"
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(endpoint_file))
    assert embedding_routes._load_custom_endpoint() == {}
    endpoint_file.parent.mkdir(parents=True, exist_ok=True)
    endpoint_file.write_text("{", encoding="utf-8")
    assert embedding_routes._load_custom_endpoint() == {}

    router = embedding_routes.setup_embedding_routes()

    monkeypatch.setitem(sys.modules, "fastembed", None)
    for path, method, args in [
        ("/api/embeddings/models", None, ()),
        ("/api/embeddings/models/{model_name:path}/download", "POST", ("m",)),
        ("/api/embeddings/models/{model_name:path}/status", None, ("m",)),
        ("/api/embeddings/models/{model_name:path}", "DELETE", ("m",)),
    ]:
        with pytest.raises(HTTPException) as exc:
            result = _endpoint(router, path, method)(*args)
            if asyncio.iscoroutine(result):
                asyncio.run(result)
        assert exc.value.status_code == 503

    class TextEmbedding:
        created = []

        def __init__(self, **kwargs):
            self.__class__.created.append(kwargs)
            if kwargs.get("model_name") == "broken":
                raise RuntimeError("download failed")

        @staticmethod
        def list_supported_models():
            return [
                {"model": "active", "sources": {"hf": hf_source}, "size_in_GB": 1, "dim": 384},
                {"model": "empty-source", "sources": {}, "size_in_GB": 2},
                {"model": "broken", "sources": {"hf": "org/broken"}, "size_in_GB": 3},
            ]

    fastembed = types.ModuleType("fastembed")
    fastembed.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)
    monkeypatch.setenv("FASTEMBED_MODEL", "active")
    monkeypatch.setattr(embedding_routes, "_downloading", {})
    models = _endpoint(router, "/api/embeddings/models")()
    assert models[0]["model"] == "active"
    assert models[0]["downloaded"] is True
    assert models[0]["cached_size_mb"] == 0.0

    assert asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("active")) == {
        "status": "already_downloaded",
        "model": "active",
    }
    embedding_routes._downloading["broken"] = True
    assert asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("broken")) == {
        "status": "already_downloading",
        "model": "broken",
    }
    embedding_routes._downloading.clear()
    with pytest.raises(HTTPException) as unknown_download:
        asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("missing"))
    assert unknown_download.value.status_code == 404
    monkeypatch.setattr(embedding_routes, "offline_mode", lambda: True)
    with pytest.raises(HTTPException) as offline_download:
        asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("broken"))
    assert offline_download.value.status_code == 403
    monkeypatch.setattr(embedding_routes, "offline_mode", lambda: False)
    with pytest.raises(HTTPException) as broken_download:
        asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("broken"))
    assert broken_download.value.status_code == 500
    assert "broken" not in embedding_routes._downloading
    assert asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("empty-source")) == {
        "status": "downloaded",
        "model": "empty-source",
    }

    with pytest.raises(HTTPException) as unknown_status:
        _endpoint(router, "/api/embeddings/models/{model_name:path}/status")("missing")
    assert unknown_status.value.status_code == 404
    assert _endpoint(router, "/api/embeddings/models/{model_name:path}/status")("empty-source") == {
        "model": "empty-source",
        "downloaded": False,
        "downloading": False,
    }

    with pytest.raises(HTTPException) as active_delete:
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("active")
    assert active_delete.value.status_code == 400
    embedding_routes._downloading["broken"] = True
    with pytest.raises(HTTPException) as downloading_delete:
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("broken")
    assert downloading_delete.value.status_code == 400
    embedding_routes._downloading.clear()
    with pytest.raises(HTTPException) as unknown_delete:
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("missing")
    assert unknown_delete.value.status_code == 404
    with pytest.raises(HTTPException) as no_source_delete:
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("empty-source")
    assert no_source_delete.value.status_code == 400
    assert _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("broken")["deleted"] is False
    broken_path = cache / embedding_routes._model_cache_name("org/broken")
    broken_path.mkdir(parents=True)
    assert _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("broken") == {
        "deleted": True,
        "model": "broken",
    }

    monkeypatch.setenv("EMBEDDING_URL", "http://env")
    assert _endpoint(router, "/api/embeddings/endpoint")()["url"] == "http://env"
    with pytest.raises(HTTPException) as empty_url:
        _endpoint(router, "/api/embeddings/endpoint", "POST")("")
    assert empty_url.value.status_code == 400
    monkeypatch.setattr(embedding_routes, "offline_mode", lambda: True)
    monkeypatch.setattr(embedding_routes, "is_local_model_url", lambda _url: False)
    with pytest.raises(HTTPException) as offline_endpoint:
        _endpoint(router, "/api/embeddings/endpoint", "POST")("https://remote.example/embed")
    assert offline_endpoint.value.status_code == 403
    monkeypatch.setattr(embedding_routes, "offline_mode", lambda: False)

    httpx_failed = types.ModuleType("httpx")
    httpx_failed.post = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    monkeypatch.setitem(sys.modules, "httpx", httpx_failed)
    with pytest.raises(HTTPException) as endpoint_down:
        _endpoint(router, "/api/embeddings/endpoint", "POST")("http://down")
    assert endpoint_down.value.status_code == 400

    httpx = types.ModuleType("httpx")

    class Response:
        def raise_for_status(self):
            return None

    httpx.post = lambda *args, **kwargs: Response()
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    rag_singleton = types.ModuleType("src.rag_singleton")
    rag_singleton.rag_instance = object()
    rag_singleton._last_attempt = 99
    monkeypatch.setitem(sys.modules, "src.rag_singleton", rag_singleton)
    reset_calls = []
    embeddings = types.ModuleType("src.embeddings")
    embeddings.reset_http_embed_state = lambda: reset_calls.append("embedding")
    chroma_client = types.ModuleType("src.chroma_client")
    chroma_client.reset_client = lambda: reset_calls.append("chroma")
    monkeypatch.setitem(sys.modules, "src.embeddings", embeddings)
    monkeypatch.setitem(sys.modules, "src.chroma_client", chroma_client)
    assert _endpoint(router, "/api/embeddings/endpoint", "POST")(" http://embed ", "m") == {
        "success": True,
        "url": "http://embed",
        "model": "m",
    }
    assert reset_calls == ["embedding", "chroma"]

    embeddings.reset_http_embed_state = lambda: (_ for _ in ()).throw(RuntimeError("reset"))
    chroma_client.reset_client = lambda: (_ for _ in ()).throw(RuntimeError("reset"))
    assert _endpoint(router, "/api/embeddings/endpoint", "POST")("http://embed2", "") == {
        "success": True,
        "url": "http://embed2",
        "model": "",
    }
    endpoint_file.write_text("{}", encoding="utf-8")
    assert _endpoint(router, "/api/embeddings/endpoint", "DELETE")() == {"success": True}
    assert os.environ.get("EMBEDDING_URL") is None


def test_hwfit_routes_remaining_manual_group_and_image_paths(monkeypatch):
    import services.hwfit.fit as fit
    import services.hwfit.hardware as hardware
    import services.hwfit.image_models as image_models
    import services.hwfit.models as models
    import routes.hwfit_routes as hwfit_routes

    base_system = {
        "has_gpu": True,
        "gpu_name": "GPU",
        "gpu_vram_gb": 24,
        "gpu_count": 2,
        "gpus": [{"vram_gb": 12}, {"vram_gb": 12}],
        "gpu_groups": [{"name": "pool", "vram_each": 12, "count": 2, "indices": [0, 1], "vram_total": 24}],
        "available_ram_gb": 64,
        "total_ram_gb": 64,
        "backend": "cuda",
    }

    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: dict(base_system))
    monkeypatch.setattr(models, "get_models", lambda: [{"id": "m"}])
    monkeypatch.setattr(models, "model_catalog_path", lambda: "catalog.json")
    monkeypatch.setattr(fit, "rank_models", lambda system, **kwargs: [{"system_gpu": system.get("gpu_vram_gb"), "kwargs": kwargs}])
    monkeypatch.setattr(image_models, "rank_image_models", lambda system, **kwargs: [{"image_gpu": system.get("gpu_vram_gb"), "kwargs": kwargs}])

    router = hwfit_routes.setup_hwfit_routes()
    assert _endpoint(router, "/api/hwfit/system")(fresh=True)["gpu_count"] == 2

    manual_ram = _endpoint(router, "/api/hwfit/models")(
        manual_mode="ram",
        manual_ram_gb="bad",
        ignore_detected_gpu=True,
        ignore_detected_ram=True,
    )
    assert manual_ram["system"]["has_gpu"] is False
    assert manual_ram["system"]["available_ram_gb"] == 0

    manual_gpu = _endpoint(router, "/api/hwfit/models")(
        manual_mode="gpu",
        manual_gpu_count="bad",
        manual_vram_gb="bad",
        manual_ram_gb="128",
        manual_backend="bad",
    )
    assert manual_gpu["system"]["gpu_count"] == 1
    assert manual_gpu["system"]["gpu_vram_gb"] == 8.0
    assert manual_gpu["system"]["backend"] == "cuda"
    assert manual_gpu["system"]["available_ram_gb"] == 128

    invalid_group = _endpoint(router, "/api/hwfit/models")(gpu_group="bad", gpu_count="1")
    assert invalid_group["system"]["active_group"]["use_count"] == 1
    cpu_only = _endpoint(router, "/api/hwfit/models")(gpu_count="0")
    assert cpu_only["system"]["has_gpu"] is False
    assert cpu_only["system"]["gpu_count"] == 0
    assert cpu_only["system"]["gpu_only"] is False

    no_group_system = dict(base_system)
    no_group_system["gpu_groups"] = []
    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: dict(no_group_system))
    no_group = _endpoint(router, "/api/hwfit/models")(gpu_count="4")
    assert no_group["system"]["gpu_count"] == 4
    assert no_group["system"]["gpu_vram_gb"] == 48.0

    monkeypatch.setattr(models, "get_models", lambda: [])
    missing_catalog = _endpoint(router, "/api/hwfit/models")()
    assert missing_catalog["models"] == []
    assert "catalog.json" in missing_catalog["error"]

    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: {"error": "offline"})
    assert _endpoint(router, "/api/hwfit/models")()["error"] == "offline"
    assert _endpoint(router, "/api/hwfit/image-models")()["error"] == "offline"

    monkeypatch.setattr(hardware, "detect_system", lambda **kwargs: dict(base_system))
    image_result = _endpoint(router, "/api/hwfit/image-models")(
        ignore_detected_gpu=True,
        ignore_detected_ram=True,
        manual_mode="gpu",
        manual_gpu_count="2",
        manual_vram_gb="16",
    )
    assert image_result["system"]["gpu_count"] == 1
    assert image_result["system"]["gpu_vram_gb"] == 16.0
