import asyncio
import datetime as dt
import importlib
import json
import os
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class Column:
    def __eq__(self, other):
        return ("eq", other)

    def desc(self):
        return self

    def asc(self):
        return self


class RequestLike:
    def __init__(self, user="alice"):
        self.state = SimpleNamespace(current_user=user)


def test_compare_routes_start_vote_record_history_and_delete(monkeypatch):
    import routes.compare_routes as compare_routes

    monkeypatch.setattr(compare_routes, "router", APIRouter(prefix="/api/compare", tags=["compare"]))

    class Comparison:
        id = Column()
        owner = Column()
        created_at = Column()

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.created_at = kwargs.get("created_at", dt.datetime(2026, 1, 1))
            self.voted_at = kwargs.get("voted_at")
            self.winner = kwargs.get("winner")

    class ModelEndpoint:
        base_url = Column()

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model

        def filter(self, *conditions):
            self.conditions = conditions
            return self

        def first(self):
            if self.model is ModelEndpoint:
                return SimpleNamespace(api_key="secret", base_url="http://a")
            return self.db.first

        def order_by(self, *_args):
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def all(self):
            return self.db.rows

    class DB:
        def __init__(self):
            self.rows = []
            self.first = None
            self.added = []
            self.deleted = []
            self.commits = 0
            self.closed = 0

        def query(self, model):
            return Query(self, model)

        def add(self, item):
            self.added.append(item)
            if isinstance(item, Comparison):
                self.rows.append(item)
                self.first = item

        def delete(self, item):
            self.deleted.append(item)

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed += 1

    class SessionManager:
        def __init__(self):
            self.sessions = {}
            self.created = []

        def create_session(self, **kwargs):
            self.created.append(kwargs)
            self.sessions[kwargs["session_id"]] = SimpleNamespace(headers={})

    db = DB()
    monkeypatch.setattr(compare_routes, "Comparison", Comparison)
    monkeypatch.setattr(compare_routes, "SessionLocal", lambda: db)
    ids = iter(["comp-id", "sid-a", "sid-b", "plain-comp", "plain-a", "plain-b", "record-id", "record-two"])
    monkeypatch.setattr(compare_routes.uuid, "uuid4", lambda: next(ids))
    monkeypatch.setattr(compare_routes.random, "random", lambda: 0.7)
    monkeypatch.setattr(compare_routes, "get_current_user", lambda request: request.state.current_user)

    core_database = types.ModuleType("core.database")
    core_database.ModelEndpoint = ModelEndpoint
    monkeypatch.setitem(sys.modules, "core.database", core_database)
    endpoint_resolver = types.ModuleType("src.endpoint_resolver")
    endpoint_resolver.normalize_base = lambda endpoint: endpoint.rstrip("/")
    endpoint_resolver.build_headers = lambda api_key, base_url: {"Authorization": f"Bearer {api_key}", "Base": base_url}
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", endpoint_resolver)

    manager = SessionManager()
    router = compare_routes.setup_compare_routes(manager)
    request = RequestLike()

    started = _endpoint(router, "/api/compare/start", "POST")(
        request,
        prompt="Question?",
        model_a="openai/a",
        model_b="openai/b",
        endpoint_a="http://a/",
        endpoint_b="http://b/",
        is_blind="true",
    )
    assert started["mapping"] == {"left": "b", "right": "a"}
    assert len(manager.created) == 2
    assert all(session.headers["Authorization"] == "Bearer secret" for session in manager.sessions.values())
    assert db.added[-1].owner == "alice"

    started_plain = _endpoint(router, "/api/compare/start", "POST")(
        request,
        prompt="Question?",
        model_a="openai/a",
        model_b="openai/b",
        endpoint_a="",
        endpoint_b="",
        is_blind="false",
    )
    assert started_plain["mapping"] == {"left": "a", "right": "b"}

    db.first = Comparison(
        id="comp",
        prompt="Prompt",
        model_a="A",
        model_b="B",
        endpoint_a="",
        endpoint_b="",
        is_blind=True,
        blind_mapping=json.dumps({"left": "b", "right": "a"}),
        owner="alice",
    )
    voted = _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="comp", winner="left")
    assert voted["winner"] == "b"
    assert voted["revealed"]["left"] == "B"

    db.first.winner = None
    right_voted = _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="comp", winner="right")
    assert right_voted["winner"] == "a"

    db.first.winner = None
    assert _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="comp", winner="tie")["winner"] == "tie"
    db.first.winner = "a"
    with pytest.raises(HTTPException) as already:
        _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="comp", winner="right")
    assert already.value.status_code == 400

    db.first.winner = None
    with pytest.raises(HTTPException) as bad_winner:
        _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="comp", winner="bad")
    assert bad_winner.value.status_code == 400
    db.first = None
    with pytest.raises(HTTPException) as missing:
        _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="missing", winner="left")
    assert missing.value.status_code == 404
    db.first = Comparison(id="other", prompt="", model_a="", model_b="", endpoint_a="", endpoint_b="", owner="bob")
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="other", winner="left")
    db.first = Comparison(id="legacy", prompt="", model_a="", model_b="", endpoint_a="", endpoint_b="", owner=None)
    with pytest.raises(HTTPException) as legacy_vote:
        _endpoint(router, "/api/compare/{comp_id}/vote", "POST")(request, comp_id="legacy", winner="left")
    assert legacy_vote.value.status_code == 404

    record = _endpoint(router, "/api/compare/record", "POST")(
        request,
        compare_routes.RecordVoteRequest(prompt="x" * 600, models=["A", "B", "C"], winner="C", is_blind=False),
    )
    assert record["status"] == "ok"
    assert len(db.added[-1].prompt) == 500
    assert json.loads(db.added[-1].blind_mapping) == {"models": ["A", "B", "C"]}

    two_model_record = _endpoint(router, "/api/compare/record", "POST")(
        request,
        compare_routes.RecordVoteRequest(prompt="short", models=["A", "B"], winner="B", is_blind=True),
    )
    assert two_model_record["status"] == "ok"
    assert db.added[-1].blind_mapping is None

    db.rows = [
        Comparison(id="h1", prompt="p" * 120, model_a="A", model_b="B", endpoint_a="", endpoint_b="", winner="A", is_blind=False, voted_at=dt.datetime(2026, 1, 2), created_at=dt.datetime(2026, 1, 1), owner="alice")
    ]
    history = _endpoint(router, "/api/compare/history")(request)
    assert history[0]["prompt"] == "p" * 100
    assert history[0]["voted_at"] == "2026-01-02T00:00:00"

    db.first = db.rows[0]
    assert _endpoint(router, "/api/compare/{comp_id}", "DELETE")(request, comp_id="h1") == {"status": "deleted"}
    assert db.deleted[-1].id == "h1"
    db.first = None
    with pytest.raises(HTTPException) as delete_missing:
        _endpoint(router, "/api/compare/{comp_id}", "DELETE")(request, comp_id="missing")
    assert delete_missing.value.status_code == 404
    db.first = Comparison(id="not-owned", prompt="", model_a="", model_b="", endpoint_a="", endpoint_b="", owner="bob")
    with pytest.raises(HTTPException) as delete_other:
        _endpoint(router, "/api/compare/{comp_id}", "DELETE")(request, comp_id="not-owned")
    assert delete_other.value.status_code == 404
    db.first = Comparison(id="legacy-delete", prompt="", model_a="", model_b="", endpoint_a="", endpoint_b="", owner=None)
    with pytest.raises(HTTPException) as delete_legacy:
        _endpoint(router, "/api/compare/{comp_id}", "DELETE")(request, comp_id="legacy-delete")
    assert delete_legacy.value.status_code == 404


def test_editor_draft_routes_crud_and_error_paths(monkeypatch):
    import routes.editor_draft_routes as draft_routes

    class EditorDraft:
        id = Column()
        owner = Column()
        is_active = Column()
        updated_at = Column()

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.created_at = kwargs.get("created_at", dt.datetime(2026, 1, 1))
            self.updated_at = kwargs.get("updated_at", dt.datetime(2026, 1, 2))
            self.is_active = kwargs.get("is_active", True)

    class Query:
        def __init__(self, db):
            self.db = db

        def filter(self, *conditions):
            self.conditions = conditions
            return self

        def order_by(self, *_args):
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def all(self):
            return self.db.rows

        def first(self):
            return self.db.first

    class DB:
        def __init__(self):
            self.rows = [
                EditorDraft(
                    id="d1",
                    owner="alice",
                    name="Draft",
                    source_image_id="img",
                    width=100,
                    height=200,
                    payload=json.dumps({"layers": []}),
                    thumbnail="thumb",
                )
            ]
            self.first = self.rows[0]
            self.added = []
            self.commits = 0
            self.rollbacks = 0

        def query(self, _model):
            return Query(self)

        def add(self, draft):
            self.added.append(draft)
            self.first = draft

        def commit(self):
            self.commits += 1

        def refresh(self, _draft):
            return None

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    db = DB()
    monkeypatch.setattr(draft_routes, "EditorDraft", EditorDraft)
    monkeypatch.setattr(draft_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(draft_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(draft_routes.uuid, "uuid4", lambda: "draft-uuid")

    router = draft_routes.setup_editor_draft_routes()
    request = RequestLike()

    assert draft_routes._owns(db.first, "alice") is True
    assert draft_routes._owns(db.first, None) is True
    assert draft_routes._owns(db.first, "bob") is False
    assert draft_routes._summary(db.first)["name"] == "Draft"

    listed = asyncio.run(_endpoint(router, "/api/editor-drafts")(request))
    assert listed["drafts"][0]["id"] == "d1"
    fetched = asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}")(request, "d1"))
    assert fetched["payload"] == {"layers": []}

    db.first.payload = "{"
    assert asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}")(request, "d1"))["payload"] == {}
    db.first = None
    with pytest.raises(HTTPException) as missing:
        asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}")(request, "missing"))
    assert missing.value.status_code == 404

    created = asyncio.run(
        _endpoint(router, "/api/editor-drafts", "POST")(
            request,
            draft_routes.DraftCreate(name="n" * 250, source_image_id="img2", width=1, height=2, payload={"x": 1}, thumbnail="t"),
        )
    )
    assert created["id"] == "draft-uuid"
    assert len(db.added[-1].name) == 200
    assert json.loads(db.added[-1].payload) == {"x": 1}

    db.first = db.added[-1]
    updated = asyncio.run(
        _endpoint(router, "/api/editor-drafts/{draft_id}", "PUT")(
            request,
            "draft-uuid",
            draft_routes.DraftUpdate(name="", width=5, height=6, payload={"y": 2}, thumbnail="t2"),
        )
    )
    assert updated["width"] == 5
    assert db.first.name == ""

    db.first = EditorDraft(id="other", owner="bob", name="x", payload="{}", width=None, height=None, thumbnail=None)
    with pytest.raises(HTTPException):
        asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}", "PUT")(request, "other", draft_routes.DraftUpdate(name="x")))

    class BrokenDB(DB):
        def commit(self):
            raise RuntimeError("commit failed")

    broken_update = BrokenDB()
    broken_update.first = EditorDraft(id="broken-update", owner="alice", name="x", payload="{}", width=None, height=None, thumbnail=None)
    monkeypatch.setattr(draft_routes, "SessionLocal", lambda: broken_update)
    with pytest.raises(HTTPException) as update_failed:
        asyncio.run(
            _endpoint(router, "/api/editor-drafts/{draft_id}", "PUT")(
                request,
                "broken-update",
                draft_routes.DraftUpdate(name="new"),
            )
        )
    assert update_failed.value.status_code == 500
    assert broken_update.rollbacks == 1

    monkeypatch.setattr(draft_routes, "SessionLocal", lambda: db)
    db.first = EditorDraft(id="delete-me", owner="alice", name="x", payload="{}", width=None, height=None, thumbnail=None)
    assert asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}", "DELETE")(request, "delete-me")) == {
        "status": "deleted",
        "id": "delete-me",
    }
    assert db.first.is_active is False
    db.first = None
    with pytest.raises(HTTPException) as delete_missing:
        asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}", "DELETE")(request, "missing"))
    assert delete_missing.value.status_code == 404

    broken = BrokenDB()
    monkeypatch.setattr(draft_routes, "SessionLocal", lambda: broken)
    with pytest.raises(HTTPException) as create_failed:
        asyncio.run(_endpoint(router, "/api/editor-drafts", "POST")(request, draft_routes.DraftCreate(payload={})))
    assert create_failed.value.status_code == 500
    assert broken.rollbacks == 1

    broken_delete = BrokenDB()
    broken_delete.first = EditorDraft(id="broken-delete", owner="alice", name="x", payload="{}", width=None, height=None, thumbnail=None)
    monkeypatch.setattr(draft_routes, "SessionLocal", lambda: broken_delete)
    with pytest.raises(HTTPException) as delete_failed:
        asyncio.run(_endpoint(router, "/api/editor-drafts/{draft_id}", "DELETE")(request, "broken-delete"))
    assert delete_failed.value.status_code == 500
    assert broken_delete.rollbacks == 1


def test_embedding_routes_models_download_delete_and_endpoint(monkeypatch, tmp_path):
    import routes.embedding_routes as embedding_routes

    cache_dir = tmp_path / "fastembed"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(cache_dir))
    monkeypatch.setenv("FASTEMBED_MODEL", "active/model")
    monkeypatch.setenv("EMBEDDING_URL", "http://env")
    monkeypatch.setenv("EMBEDDING_MODEL", "env-model")
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(tmp_path / "embedding_endpoint.json"))
    embedding_routes._downloading.clear()

    assert embedding_routes._cache_dir() == str(cache_dir)
    assert embedding_routes._model_cache_name("org/model") == "models--org--model"
    assert embedding_routes._active_model() == "active/model"
    assert embedding_routes._load_custom_endpoint() == {}
    (tmp_path / "embedding_endpoint.json").write_text("{", encoding="utf-8")
    assert embedding_routes._load_custom_endpoint() == {}
    embedding_routes._save_custom_endpoint({"url": "http://saved", "model": "saved-model"})
    assert embedding_routes._load_custom_endpoint()["model"] == "saved-model"

    hf_dir = cache_dir / "models--hf--cached" / "snapshots" / "one"
    hf_dir.mkdir(parents=True)
    (hf_dir / "model.onnx").write_bytes(b"x" * 1024)
    assert embedding_routes._is_downloaded("hf/cached") is True
    assert embedding_routes._dir_size_mb(str(cache_dir)) >= 0

    class TextEmbedding:
        catalog = [
            {"model": "active/model", "sources": {"hf": "hf/cached"}, "dim": 384, "size_in_GB": 0.1, "description": "active"},
            {"model": "other/model", "sources": {"hf": "hf/other"}, "dim": 768, "size_in_GB": 1.0, "description": "other"},
            {"model": "no/source", "sources": {}, "dim": 1, "size_in_GB": 2.0, "description": "none"},
        ]
        created = []

        def __init__(self, model_name, cache_dir):
            if model_name == "boom/model":
                raise RuntimeError("download boom")
            self.created.append((model_name, cache_dir))

        @classmethod
        def list_supported_models(cls):
            return list(cls.catalog)

    fastembed = types.ModuleType("fastembed")
    fastembed.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)

    router = embedding_routes.setup_embedding_routes()
    models = _endpoint(router, "/api/embeddings/models")()
    assert models[0]["model"] == "active/model"
    assert models[0]["downloaded"] is True
    assert models[0]["cached_size_mb"] is not None

    with pytest.raises(HTTPException) as unknown_download:
        asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("missing"))
    assert unknown_download.value.status_code == 404
    assert asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("active/model")) == {
        "status": "already_downloaded",
        "model": "active/model",
    }
    embedding_routes._downloading["other/model"] = True
    assert asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("other/model")) == {
        "status": "already_downloading",
        "model": "other/model",
    }
    embedding_routes._downloading.clear()
    assert asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("other/model")) == {
        "status": "downloaded",
        "model": "other/model",
    }

    TextEmbedding.catalog.append({"model": "boom/model", "sources": {"hf": "hf/boom"}})
    with pytest.raises(HTTPException) as download_failed:
        asyncio.run(_endpoint(router, "/api/embeddings/models/{model_name:path}/download", "POST")("boom/model"))
    assert download_failed.value.status_code == 500
    assert "boom/model" not in embedding_routes._downloading

    status = _endpoint(router, "/api/embeddings/models/{model_name:path}/status")("active/model")
    assert status["downloaded"] is True
    with pytest.raises(HTTPException) as unknown_status:
        _endpoint(router, "/api/embeddings/models/{model_name:path}/status")("missing")
    assert unknown_status.value.status_code == 404

    with pytest.raises(HTTPException) as active_delete:
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("active/model")
    assert active_delete.value.status_code == 400
    embedding_routes._downloading["other/model"] = True
    with pytest.raises(HTTPException):
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("other/model")
    embedding_routes._downloading.clear()
    with pytest.raises(HTTPException) as no_source:
        _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("no/source")
    assert no_source.value.status_code == 400
    assert _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("other/model") == {
        "deleted": False,
        "message": "Model not cached",
    }
    other_dir = cache_dir / "models--hf--other" / "blobs"
    other_dir.mkdir(parents=True)
    (other_dir / "blob").write_text("x", encoding="utf-8")
    assert _endpoint(router, "/api/embeddings/models/{model_name:path}", "DELETE")("other/model") == {
        "deleted": True,
        "model": "other/model",
    }
    assert not (cache_dir / "models--hf--other").exists()

    assert _endpoint(router, "/api/embeddings/endpoint")()["url"] == "http://saved"
    with pytest.raises(HTTPException) as empty_url:
        _endpoint(router, "/api/embeddings/endpoint", "POST")(url="   ", model="")
    assert empty_url.value.status_code == 400

    class Response:
        def raise_for_status(self):
            return None

    httpx = types.ModuleType("httpx")
    httpx.post = lambda url, json=None, timeout=None: Response()
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    monkeypatch.setattr(embedding_routes, "load_features", lambda: {"external_model_endpoints": True})
    rag_singleton = types.ModuleType("src.rag_singleton")
    rag_singleton.rag_instance = "cached"
    rag_singleton._last_attempt = 10
    embeddings = types.ModuleType("src.embeddings")
    reset_calls = []
    embeddings.reset_http_embed_state = lambda: reset_calls.append("embed")
    chroma = types.ModuleType("src.chroma_client")
    chroma.reset_client = lambda: reset_calls.append("chroma")
    monkeypatch.setitem(sys.modules, "src.rag_singleton", rag_singleton)
    monkeypatch.setitem(sys.modules, "src.embeddings", embeddings)
    monkeypatch.setitem(sys.modules, "src.chroma_client", chroma)
    src_pkg = importlib.import_module("src")
    monkeypatch.setattr(src_pkg, "rag_singleton", rag_singleton, raising=False)
    monkeypatch.setattr(src_pkg, "embeddings", embeddings, raising=False)
    monkeypatch.setattr(src_pkg, "chroma_client", chroma, raising=False)

    saved = _endpoint(router, "/api/embeddings/endpoint", "POST")(url=" http://endpoint ", model="embed")
    assert saved == {"success": True, "url": "http://endpoint", "model": "embed"}
    assert os.environ["EMBEDDING_URL"] == "http://endpoint"
    assert rag_singleton.rag_instance is None
    assert reset_calls == ["embed", "chroma"]

    httpx.post = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    with pytest.raises(HTTPException) as unreachable:
        _endpoint(router, "/api/embeddings/endpoint", "POST")(url="http://bad", model="")
    assert unreachable.value.status_code == 400

    assert _endpoint(router, "/api/embeddings/endpoint", "DELETE")() == {"success": True}
    assert "EMBEDDING_URL" not in os.environ
    assert not os.path.exists(embedding_routes._ENDPOINT_FILE)
