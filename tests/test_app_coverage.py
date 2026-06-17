import asyncio
import importlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from core.exceptions import InvalidFileUploadError, LLMServiceError, SessionNotFoundError, WebSearchError


ROOT = Path(__file__).resolve().parent.parent


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

    assert asyncio.run(app_module.serve_tutorials(RequestLike())).status_code == 200
    assert asyncio.run(app_module.serve_offline(RequestLike())).status_code == 200
    assert asyncio.run(app_module.serve_setup(RequestLike())).status_code == 200

    backgrounds = asyncio.run(app_module.serve_backgrounds(RequestLike()))
    assert backgrounds.status_code == 200
    assert b"Cleverly" in backgrounds.body

    missing = tmp_path / "missing.html"
    with pytest.raises(FileNotFoundError):
        app_module._serve_html_with_nonce(RequestLike(), str(missing))


def test_feature_deeplinks_serve_cleverly_shell(monkeypatch):
    app_module = _fresh_app(monkeypatch)

    feature_routes = [
        ("/", app_module.serve_index),
        ("/notes", app_module.serve_notes),
        ("/calendar", app_module.serve_calendar),
        ("/cookbook", app_module.serve_cookbook),
        ("/training", app_module.serve_training),
        ("/tutorials", app_module.serve_tutorials),
        ("/loops", app_module.serve_loops),
        ("/code", app_module.serve_code),
        ("/offline", app_module.serve_offline),
        ("/setup", app_module.serve_setup),
        ("/email", app_module.serve_email),
        ("/memory", app_module.serve_memory),
        ("/gallery", app_module.serve_gallery),
        ("/tasks", app_module.serve_tasks),
        ("/library", app_module.serve_library),
        ("/backgrounds", app_module.serve_backgrounds),
    ]

    for path, route in feature_routes:
        response = asyncio.run(route(RequestLike(path)))
        body = response.body.decode("utf-8", errors="ignore")
        assert response.status_code == 200, path
        assert "Cleverly" in body, path
        assert 'id="sidebar"' in body, path
        assert not re.search(r"Odysseus|odysseus|(?<![bB])ody-", body), path


def test_login_page_serves_cleverly_brand_without_legacy_tokens(monkeypatch):
    app_module = _fresh_app(monkeypatch)

    response = asyncio.run(app_module.serve_login(RequestLike("/login")))
    body = response.body.decode("utf-8", errors="ignore")

    assert response.status_code == 200
    assert "Cleverly" in body
    assert not re.search(r"Odysseus|odysseus|(?<![bB])ody-", body)


def test_app_registers_major_feature_api_routes(monkeypatch):
    app_module = _fresh_app(monkeypatch)
    registered = {
        (route.path, method)
        for route in app_module.app.routes
        for method in getattr(route, "methods", set())
    }

    expected = {
        # Chat, sessions, search, settings.
        ("/api/chat", "POST"),
        ("/api/chat_stream", "POST"),
        ("/api/sessions", "GET"),
        ("/api/session", "POST"),
        ("/api/search/config", "GET"),
        ("/api/search/query", "POST"),
        ("/api/prefs", "GET"),
        # Memory, documents, notes, tasks.
        ("/api/memory", "GET"),
        ("/api/memory/add", "POST"),
        ("/api/documents/library", "GET"),
        ("/api/document", "POST"),
        ("/api/notes", "GET"),
        ("/api/notes", "POST"),
        ("/api/tasks", "GET"),
        ("/api/tasks", "POST"),
        # Calendar, cookbook, training, code workspace.
        ("/api/calendar/config", "GET"),
        ("/api/calendar/events", "GET"),
        ("/api/cookbook/state", "GET"),
        ("/api/cookbook/gpus", "GET"),
        ("/api/model/cached", "GET"),
        ("/api/training/status", "GET"),
        ("/api/training/finetune/status", "GET"),
        ("/api/code-workspaces", "GET"),
        ("/api/code-workspaces", "POST"),
        ("/api/code-workspaces/{workspace_id}/agent", "POST"),
        # Offline control, model setup, gallery, email, contacts.
        ("/api/offline-control/status", "GET"),
        ("/api/offline-control/models/local", "GET"),
        ("/api/offline-control/models/primary", "GET"),
        ("/api/offline-control/models/recommendations", "GET"),
        ("/api/models", "GET"),
        ("/api/model-endpoints", "GET"),
        ("/api/gallery/library", "GET"),
        ("/api/gallery/upload", "POST"),
        ("/api/email/list", "GET"),
        ("/api/email/folders", "GET"),
        ("/api/contacts/list", "GET"),
        # Compare, skills, research, backup, operator.
        ("/api/compare/start", "POST"),
        ("/api/compare/history", "GET"),
        ("/api/skills", "GET"),
        ("/api/research/active", "GET"),
        ("/api/research/library", "GET"),
        ("/api/export", "GET"),
        ("/api/operator/checks", "GET"),
    }

    missing = sorted(expected - registered)
    assert missing == []


def test_direct_frontend_api_calls_match_registered_routes(monkeypatch):
    app_module = _fresh_app(monkeypatch)
    route_paths = [
        route.path
        for route in app_module.app.routes
        if getattr(route, "path", "").startswith("/api/")
    ]
    route_patterns = []
    for path in route_paths:
        pattern = re.escape(path)
        pattern = re.sub(r"\\\{[^}:]+:path\\\}", r".+", pattern)
        pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/?#]+", pattern)
        route_patterns.append(re.compile(rf"^{pattern}(?:[?#].*)?$"))

    direct_call = re.compile(
        r"""\b(?:fetch|_api|api|apiFetch)\s*\(\s*([`'"])((?:\$\{[^}]+\})?/api/[^`'"\s,)]*)"""
    )
    allowed_external_examples = {
        "/api/v1",
        "/api/paas/v4",
    }
    leftovers = []
    for path in sorted(ROOT.joinpath("static").rglob("*")):
        if path.suffix not in {".html", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in direct_call.finditer(text):
            raw = match.group(2)
            remaining_template = raw
            for prefix in ("${API_BASE}", "${state.API_BASE}", "${apiBase}"):
                remaining_template = remaining_template.replace(prefix, "")
            if "${" in remaining_template:
                continue
            url = re.sub(r"^\$\{[^}]+\}", "", raw)
            api_path = url.split("?", 1)[0].split("#", 1)[0]
            if api_path in allowed_external_examples:
                continue
            matches_registered_route = any(pattern.match(api_path) for pattern in route_patterns)
            matches_dynamic_prefix = api_path.endswith("/") and any(
                route_path.startswith(api_path) for route_path in route_paths
            )
            if not matches_registered_route and not matches_dynamic_prefix:
                line = text.count("\n", 0, match.start()) + 1
                leftovers.append(f"{path.relative_to(ROOT)}:{line}:{api_path}")

    assert leftovers == []


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
