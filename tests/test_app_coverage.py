import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from core.exceptions import InvalidFileUploadError, LLMServiceError, SessionNotFoundError, WebSearchError


def _fresh_app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("REQUEST_HARD_TIMEOUT", "0.01")
    for name, module in list(sys.modules.items()):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            if isinstance(module, MagicMock) or name == "sqlalchemy.orm":
                monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delitem(sys.modules, "app", raising=False)
    monkeypatch.delitem(sys.modules, "src.database", raising=False)
    core_database = sys.modules.get("core.database")
    if core_database is not None and not hasattr(core_database, "Webhook"):
        monkeypatch.delitem(sys.modules, "core.database", raising=False)
    return importlib.import_module("app")


class RequestLike:
    def __init__(self, path="/", user="alice"):
        self.url = SimpleNamespace(path=path)
        self.state = SimpleNamespace(csp_nonce="nonce-123", current_user=user)
        self.client = SimpleNamespace(host="127.0.0.1")
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace())


def test_app_import_helpers_exception_handlers_and_html(monkeypatch, tmp_path):
    app_module = _fresh_app(monkeypatch)

    assert app_module._truthy("true") is True
    assert app_module._truthy("ON") is True
    assert app_module._truthy("no") is False
    assert app_module.app.title == "Cleverly"

    html = tmp_path / "page.html"
    html.write_text("<script nonce=\"{{CSP_NONCE}}\">x</script>", encoding="utf-8")
    rendered = app_module._serve_html_with_nonce(RequestLike(), str(html))
    assert b"nonce-123" in rendered.body

    assert asyncio.run(app_module.session_not_found_handler(RequestLike(), SessionNotFoundError("missing"))).status_code == 404
    assert asyncio.run(app_module.invalid_file_upload_handler(RequestLike(), InvalidFileUploadError("bad"))).status_code == 400
    assert asyncio.run(app_module.llm_service_error_handler(RequestLike(), LLMServiceError("down"))).status_code == 502
    assert asyncio.run(app_module.web_search_error_handler(RequestLike(), WebSearchError("offline"))).status_code == 502

    assert asyncio.run(app_module.get_version())["version"]
    assert asyncio.run(app_module.health_check())["status"] == "healthy"
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local/v1")
    runtime = asyncio.run(app_module.runtime_info())
    assert runtime["ollama_base_url"] == "http://ollama.local/v1"

    operator = asyncio.run(app_module.serve_operator(RequestLike()))
    assert operator.status_code == 302
    assert operator.headers["location"] == "/api/operator/page"

    missing = tmp_path / "missing.html"
    with pytest.raises(FileNotFoundError):
        app_module._serve_html_with_nonce(RequestLike(), str(missing))


def test_app_timeout_middleware_and_static_headers(monkeypatch, tmp_path):
    app_module = _fresh_app(monkeypatch)

    middleware = app_module._RequestTimeoutMiddleware(app=object())

    async def slow_call(_request):
        await asyncio.sleep(0.05)
        return JSONResponse({"ok": True})

    timed_out = asyncio.run(middleware.dispatch(RequestLike("/api/prefs"), slow_call))
    assert timed_out.status_code == 504

    async def exempt_call(_request):
        return JSONResponse({"stream": True})

    exempt = asyncio.run(middleware.dispatch(RequestLike("/api/chat_stream"), exempt_call))
    assert exempt.status_code == 200

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    static = app_module._RevalidatingStatic(directory=str(static_dir))
    response = asyncio.run(static.get_response("app.js", {"type": "http", "method": "GET", "path": "/static/app.js", "headers": []}))
    assert response.headers["Cache-Control"] == "no-cache"


def test_app_generated_image_route_auth_and_errors(monkeypatch, tmp_path):
    app_module = _fresh_app(monkeypatch)
    monkeypatch.chdir(tmp_path)
    generated = tmp_path / "data" / "generated_images"
    generated.mkdir(parents=True)
    filename = "abcdef123456.png"
    (generated / filename).write_bytes(b"png")

    class Column:
        def __eq__(self, other):
            return ("filename", other)

    class GalleryImage:
        filename = Column()

    class Query:
        def __init__(self, row):
            self.row = row

        def filter(self, *_args):
            return self

        def first(self):
            return self.row

    class DB:
        def __init__(self, row):
            self.row = row

        def query(self, _model):
            return Query(self.row)

        def close(self):
            self.closed = True

    import core.database as core_database

    monkeypatch.setattr(core_database, "GalleryImage", GalleryImage)
    monkeypatch.setattr(core_database, "SessionLocal", lambda: DB(SimpleNamespace(owner="bob")))
    with pytest.raises(HTTPException) as denied:
        asyncio.run(app_module.serve_generated_image(filename, RequestLike()))
    assert denied.value.status_code == 404

    monkeypatch.setattr(core_database, "SessionLocal", lambda: DB(None))
    response = asyncio.run(app_module.serve_generated_image(filename, RequestLike()))
    assert response.media_type == "image/png"
    assert "immutable" in response.headers["Cache-Control"]

    with pytest.raises(HTTPException) as invalid:
        asyncio.run(app_module.serve_generated_image("../bad.png", RequestLike()))
    assert invalid.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        asyncio.run(app_module.serve_generated_image("deadbeef.png", RequestLike()))
    assert missing.value.status_code == 404
