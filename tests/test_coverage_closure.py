import builtins
import importlib
import sys
import types
from types import SimpleNamespace

import pytest


def test_pdf_runtime_returns_installed_fitz_module(monkeypatch):
    from src.pdf_runtime import load_pymupdf_for_pdf_viewer

    fake_fitz = types.ModuleType("fitz")
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

    assert load_pymupdf_for_pdf_viewer() is fake_fitz


def test_message_needs_tools_ignores_empty_input():
    from src.action_intents import message_needs_tools

    assert message_needs_tools("") is False
    assert message_needs_tools(None) is False


def test_research_low_quality_fails_open_for_bad_summary_object():
    from src.research_utils import is_low_quality

    class BadSummary:
        def lower(self):
            raise RuntimeError("boom")

    assert is_low_quality(BadSummary()) is False


def _import_secret_storage(tmp_path, monkeypatch):
    sys.modules.pop("src.secret_storage", None)
    from src import secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    return secret_storage


def test_secret_storage_loads_existing_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    secret_storage = _import_secret_storage(tmp_path, monkeypatch)
    key = Fernet.generate_key()
    (tmp_path / ".app_key").write_bytes(key)

    assert secret_storage._load_or_create_key() == key


def test_secret_storage_generic_decrypt_failure_returns_empty(tmp_path, monkeypatch):
    secret_storage = _import_secret_storage(tmp_path, monkeypatch)

    class BrokenFernet:
        def decrypt(self, _token):
            raise ValueError("bad token")

    monkeypatch.setattr(secret_storage, "_get_fernet", lambda: BrokenFernet())

    assert secret_storage.decrypt("enc:not-base64") == ""


def test_compat_helpers_cover_fallbacks(monkeypatch):
    from src.compat import first_column, getenv, request_header

    monkeypatch.delenv("CLEVERLY_TEST_ENV", raising=False)
    assert getenv("CLEVERLY_TEST_ENV", "fallback") == "fallback"
    monkeypatch.setenv("CLEVERLY_TEST_ENV", "set")
    assert getenv("CLEVERLY_TEST_ENV") == "set"

    assert request_header({"x-test": "1"}, "missing", "default") == "default"
    assert first_column(["id", "name"], "email", "name") == "name"
    assert first_column(["id"], "email", "name") is None


def test_tool_security_public_and_owner_branches(monkeypatch):
    import src.tool_security as tool_security

    assert tool_security.is_public_blocked_tool(None) is False
    assert tool_security.is_public_blocked_tool("mcp__memory__search") is True
    assert tool_security.is_public_blocked_tool("send_email") is True
    assert tool_security.is_public_blocked_tool("safe_tool") is False

    class UnconfiguredAuth:
        is_configured = False

        def is_admin(self, _owner):
            return False

    class ConfiguredAuth:
        is_configured = True

        def is_admin(self, owner):
            return owner == "admin"

    import core.auth

    monkeypatch.setattr(core.auth, "AuthManager", lambda: UnconfiguredAuth())
    assert tool_security.owner_is_admin_or_single_user(None) is True
    assert tool_security.blocked_tools_for_owner("anyone") == set()

    monkeypatch.setattr(core.auth, "AuthManager", lambda: ConfiguredAuth())
    assert tool_security.owner_is_admin_or_single_user("admin") is True
    assert tool_security.owner_is_admin_or_single_user("user") is False
    assert "send_email" in tool_security.blocked_tools_for_owner("user")

    monkeypatch.setattr(core.auth, "AuthManager", lambda: (_ for _ in ()).throw(RuntimeError("auth broke")))
    assert tool_security.owner_is_admin_or_single_user("admin") is False


def test_atomic_write_text_replaces_file(tmp_path):
    from core.atomic_io import atomic_write_text

    target = tmp_path / "nested" / "note.txt"
    atomic_write_text(str(target), "first")
    atomic_write_text(str(target), "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert not list(target.parent.glob("*.tmp.*"))


def test_request_model_validators_cover_invalid_and_valid_values():
    from src.request_models import ChatRequest, MemoryAddRequest

    chat = ChatRequest(message="  hello  ", session="s1", time_filter="decade")
    assert chat.message == "hello"
    assert chat.time_filter is None

    valid_chat = ChatRequest(message="hello", session="s1", time_filter="week")
    assert valid_chat.time_filter == "week"

    assert MemoryAddRequest(text="remember this", category="unknown").category == "fact"
    assert MemoryAddRequest(text="remember this", category="goal").category == "goal"


def test_operator_checks_route_returns_run_operator_checks(monkeypatch):
    import routes.operator_routes as operator_routes

    monkeypatch.setattr(
        operator_routes,
        "run_operator_checks",
        lambda: {"summary": {"ok": 1, "warn": 0, "fail": 0}, "checks": []},
    )
    router = operator_routes.setup_operator_routes()
    checks_route = next(route for route in router.routes if route.path == "/api/operator/checks")

    assert checks_route.endpoint() == {
        "ok": True,
        "summary": {"ok": 1, "warn": 0, "fail": 0},
        "checks": [],
    }


def test_mcp_common_truncate_short_and_long_text():
    from mcp_servers._common import truncate

    assert truncate("short", limit=10) == "short"
    assert truncate("abcdef", limit=3) == "abc\n... (truncated, 6 chars total)"


def test_chroma_client_import_error_open_port_and_success(monkeypatch):
    import src.chroma_client as chroma_client

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "chromadb":
            raise ImportError("missing chromadb")
        return real_import(name, *args, **kwargs)

    chroma_client.reset_client()
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="chromadb-client"):
        chroma_client.get_chroma_client()

    class FakeClient:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.heartbeat_called = False

        def heartbeat(self):
            self.heartbeat_called = True

    fake_chromadb = types.SimpleNamespace(HttpClient=FakeClient)
    monkeypatch.setattr(builtins, "__import__", real_import)
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setenv("CHROMADB_HOST", "127.0.0.1")
    monkeypatch.setenv("CHROMADB_PORT", "8100")
    monkeypatch.setattr(chroma_client, "_port_open", lambda host, port: True)
    chroma_client.reset_client()

    client = chroma_client.get_chroma_client()

    assert client is chroma_client.get_chroma_client()
    assert client.host == "127.0.0.1"
    assert client.port == 8100
    assert client.heartbeat_called is True


def test_docs_service_delegates_to_rag_manager(monkeypatch):
    import services.docs.service as docs_service

    class FakeRag:
        def __init__(self, persist_directory):
            self.persist_directory = persist_directory

        def search(self, query, k=5):
            return [
                {"text": "alpha", "source": "a.txt", "score": 0.9, "metadata": {"page": 1}},
                {"content": "beta", "metadata": {"source": "b.txt"}, "score": 0.7},
            ]

        def index_personal_documents(self, directory):
            return {"indexed": 2, "failed": 1, "errors": ["bad.pdf"]}

        def add_document(self, text, metadata):
            return text == "doc" and metadata == {"source": "manual"}

        def get_stats(self):
            return {"chunks": 2}

        def rebuild_index(self):
            return True

    monkeypatch.setattr(docs_service, "RAGManager", FakeRag)
    service = docs_service.DocsService(persist_dir="custom")

    assert service.rag.persist_directory == "custom"
    chunks = pytest.run(async_fn=service.query("q", top_k=2)) if hasattr(pytest, "run") else None
    if chunks is None:
        import asyncio

        chunks = asyncio.run(service.query("q", top_k=2))
    assert chunks[0].text == "alpha"
    assert chunks[0].source == "a.txt"
    assert chunks[0].metadata == {"page": 1}
    assert chunks[1].text == "beta"
    assert chunks[1].source == "b.txt"

    import asyncio

    indexed = asyncio.run(service.index("docs"))
    assert indexed.indexed == 2
    assert indexed.failed == 1
    assert indexed.errors == ["bad.pdf"]
    assert asyncio.run(service.add_document("doc", {"source": "manual"})) is True
    assert service.get_stats() == {"chunks": 2}
    assert service.rebuild_index() is True


def test_local_training_error_paths_and_helpers(tmp_path):
    from src import local_training
    from src.local_training import LocalTrainingError

    assert local_training.list_datasets(tmp_path) == []
    assert local_training.list_artifacts(tmp_path) == []
    assert local_training._slug("***", "fallback") == "fallback"

    with pytest.raises(LocalTrainingError, match="at least 32"):
        local_training.create_dataset("tiny", "short", root=tmp_path)

    with pytest.raises(LocalTrainingError, match="limited"):
        local_training.create_dataset("huge", "x" * (local_training.MAX_DATASET_CHARS + 1), root=tmp_path)

    with pytest.raises(LocalTrainingError, match="Dataset not found"):
        local_training._dataset_text("missing", root=tmp_path)

    dataset = local_training.create_dataset("Corpus", "abcdef " * 6, root=tmp_path)

    with pytest.raises(LocalTrainingError, match="Order must be a number"):
        local_training.train_ngram(dataset["id"], order="bad", root=tmp_path)

    with pytest.raises(LocalTrainingError, match="between"):
        local_training.train_ngram(dataset["id"], order=local_training.MAX_ORDER + 1, root=tmp_path)

    artifact = local_training.train_ngram(dataset["id"], order=1, root=tmp_path)

    with pytest.raises(LocalTrainingError, match="Artifact not found"):
        local_training.generate_text("missing", root=tmp_path)

    with pytest.raises(LocalTrainingError, match="Max chars must be a number"):
        local_training.generate_text(artifact["id"], max_chars="bad", root=tmp_path)

    with pytest.raises(LocalTrainingError, match="between"):
        local_training.generate_text(artifact["id"], max_chars=0, root=tmp_path)

    with pytest.raises(LocalTrainingError, match="Temperature must be a number"):
        local_training.generate_text(artifact["id"], temperature="hot", root=tmp_path)

    with pytest.raises(LocalTrainingError, match="Temperature must be between"):
        local_training.generate_text(artifact["id"], temperature=3, root=tmp_path)

    model_path = tmp_path / "artifacts" / artifact["id"] / "model.json"
    model = local_training._read_json(model_path)
    model["schema"] = "unknown"
    model_path.write_text(__import__("json").dumps(model), encoding="utf-8")
    with pytest.raises(LocalTrainingError, match="Unsupported artifact"):
        local_training._load_model(artifact["id"], root=tmp_path)


def test_offline_policy_db_and_startup_branches(monkeypatch, tmp_path):
    from src import offline_policy, settings

    class Endpoint:
        is_enabled = True

        def __init__(self, name, base_url):
            self.name = name
            self.base_url = base_url

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args):
            return self

        def all(self):
            return self.rows

    class DB:
        def __init__(self, rows=None, fail=False):
            self.rows = rows or []
            self.fail = fail
            self.closed = False

        def query(self, _model):
            if self.fail:
                raise RuntimeError("db down")
            return Query(self.rows)

        def close(self):
            self.closed = True

    db = DB([Endpoint("remote", "https://api.example.test/v1")])
    monkeypatch.setattr(offline_policy, "SessionLocal", lambda: db, raising=False)
    monkeypatch.setattr(offline_policy, "ModelEndpoint", Endpoint, raising=False)
    monkeypatch.setitem(sys.modules, "core.database", SimpleNamespace(SessionLocal=lambda: db, ModelEndpoint=Endpoint))
    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "host-data"))
    monkeypatch.setenv("CODE_WORKSPACE_RUNNER", "in-process")
    monkeypatch.setenv("CLEVERLY_REQUIRE_CODE_WORKER", "1")
    monkeypatch.setattr(offline_policy, "_docker_like", lambda: False)
    monkeypatch.setattr(offline_policy, "_enabled_online_features", lambda: [])
    settings._invalidate_caches()
    try:
        report = offline_policy.evaluate_offline_policy(include_db=True)
    finally:
        settings._invalidate_caches()

    assert db.closed is True
    assert any(item["id"] == "model-endpoints-local" and item["status"] == "fail" for item in report["checks"])
    assert any(item["id"] == "code-worker" and item["status"] == "fail" for item in report["checks"])

    db_error = DB(fail=True)
    monkeypatch.setitem(
        sys.modules,
        "core.database",
        SimpleNamespace(SessionLocal=lambda: db_error, ModelEndpoint=Endpoint),
    )
    settings._invalidate_caches()
    try:
        error_report = offline_policy.evaluate_offline_policy(include_db=True)
    finally:
        settings._invalidate_caches()
    assert any("Could not verify model endpoints" in item["detail"] for item in error_report["checks"])

    monkeypatch.setattr(
        offline_policy,
        "evaluate_offline_policy",
        lambda include_db=True: {"summary": {"fail": 1}, "checks": [{"status": "fail", "label": "x", "detail": "bad"}]},
    )
    monkeypatch.setattr(offline_policy, "strict_policy_enabled", lambda: True)
    with pytest.raises(offline_policy.OfflinePolicyError, match="x: bad"):
        offline_policy.enforce_startup_policy()

    monkeypatch.setattr(offline_policy, "strict_policy_enabled", lambda: False)
    assert offline_policy.enforce_startup_policy()["summary"]["fail"] == 1


def test_operator_page_renders_nonce_and_refresh_button():
    import routes.operator_routes as operator_routes

    router = operator_routes.setup_operator_routes()
    page_route = next(route for route in router.routes if route.path == "/api/operator/page")
    request = SimpleNamespace(state=SimpleNamespace(csp_nonce='nonce"<x>'))

    response = page_route.endpoint(request)
    body = response.body.decode("utf-8")

    assert "Cleverly Operator Status" in body
    assert 'nonce="nonce&quot;&lt;x&gt;"' in body
    assert "Refresh" in body


def test_offline_policy_docker_and_feature_failure_branches(monkeypatch, tmp_path):
    from src import offline_policy, settings

    original_path = offline_policy.Path

    class DockerEnvPath:
        def __init__(self, value):
            self.value = value

        def exists(self):
            return self.value == "/.dockerenv"

    monkeypatch.setattr(offline_policy, "Path", DockerEnvPath)
    assert offline_policy._docker_like() is True

    class FakePath:
        def __init__(self, value):
            self.value = value

        def exists(self):
            return False

        def read_text(self, *args, **kwargs):
            return "0::/docker/container"

    monkeypatch.setattr(offline_policy, "Path", FakePath)
    assert offline_policy._docker_like() is True

    class BrokenPath:
        def __init__(self, value):
            self.value = value

        def exists(self):
            return False

        def read_text(self, *args, **kwargs):
            raise OSError("cannot read")

    monkeypatch.setattr(offline_policy, "Path", BrokenPath)
    assert offline_policy._docker_like() is False
    monkeypatch.setattr(offline_policy, "Path", original_path)

    monkeypatch.setattr("src.settings.load_features", lambda: (_ for _ in ()).throw(RuntimeError("bad settings")))
    assert offline_policy._enabled_online_features() == ["feature load failed"]

    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    settings._invalidate_caches()
    try:
        report = offline_policy.evaluate_offline_policy(include_db=False)
    finally:
        settings._invalidate_caches()
    assert any(
        item["id"] == "online-features-hidden" and item["status"] == "fail"
        for item in report["checks"]
    )


def test_local_training_remaining_edge_paths(tmp_path):
    import json

    from src import local_training
    from src.local_training import LocalTrainingError

    assert local_training._sorted_metadata(tmp_path / "does-not-exist") == []
    root = tmp_path
    local_training.ensure_training_dirs(root)
    dataset_dir = root / "datasets"
    (dataset_dir / "file.txt").write_text("not a dir", encoding="utf-8")
    (dataset_dir / "missing-meta").mkdir()
    bad_meta = dataset_dir / "bad-meta"
    bad_meta.mkdir()
    (bad_meta / "metadata.json").write_text("{", encoding="utf-8")

    assert local_training._sorted_metadata(dataset_dir) == []

    dataset = local_training.create_dataset("Tiny", "x" * 32, root=root)
    (root / "datasets" / dataset["id"] / "text.txt").write_text("short", encoding="utf-8")
    with pytest.raises(LocalTrainingError, match="too small"):
        local_training.train_ngram(dataset["id"], order=1, root=root)

    full = local_training.create_dataset("Full", "abc " * 12, root=root)
    artifact = local_training.train_ngram(full["id"], order=1, root=root)
    model_path = root / "artifacts" / artifact["id"] / "model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))

    assert local_training._pick({}, 1, __import__("random").Random(1)) == ""
    assert local_training._pick({"a": 1, "b": 2}, 0, __import__("random").Random(1)) == "b"
    assert local_training._pick({"a": 1}, 1, __import__("random").Random(0)) == "a"

    class HighRandom:
        def random(self):
            return 2.0

    assert local_training._pick({"z": 1}, 1, HighRandom()) == "z"

    model["transitions"] = {}
    model_path.write_text(json.dumps(model), encoding="utf-8")
    assert local_training.generate_text(artifact["id"], max_chars=3, root=root)["completion"] == ""


def test_custom_exception_attributes_and_messages():
    import core.exceptions as core_exceptions
    import src.exceptions as src_exceptions

    for module in (core_exceptions, src_exceptions):
        missing = module.SessionNotFoundError("s1")
        assert missing.session_id == "s1"
        assert str(missing) == "Session 's1' not found"

        upload = module.InvalidFileUploadError("bad upload", filename="x.txt")
        assert upload.message == "bad upload"
        assert upload.filename == "x.txt"

        llm = module.LLMServiceError("down", endpoint="http://local")
        assert llm.message == "down"
        assert llm.endpoint == "http://local"

        web = module.WebSearchError("blocked", query="q")
        assert web.message == "blocked"
        assert web.query == "q"


def test_rag_manager_delegates_to_vector_rag(monkeypatch):
    import src.rag_manager as rag_manager

    class FakeVectorRAG:
        def __init__(self, persist_directory):
            self.persist_directory = persist_directory

        def search(self, query, k):
            return [{"query": query, "k": k}]

        def index_personal_documents(self, directory):
            return {"directory": directory}

        def retrieve(self, query, k):
            return [f"{query}:{k}"]

        def rebuild_index(self):
            return True

        def get_stats(self):
            return {"ok": True}

        def add_document(self, text, metadata):
            return {"text": text, "metadata": metadata}

        def add_documents_batch(self, docs):
            return {"count": len(docs)}

    monkeypatch.setattr(rag_manager, "VectorRAG", FakeVectorRAG)
    manager = rag_manager.RAGManager(persist_directory="rag-data")

    assert manager.vector_rag.persist_directory == "rag-data"
    assert manager.search("q", 2) == [{"query": "q", "k": 2}]
    assert manager.index_personal_documents("docs") == {"directory": "docs"}
    assert manager.retrieve("q", 3) == ["q:3"]
    assert manager.rebuild_index() is True
    assert manager.get_stats() == {"ok": True}
    assert manager.add_document("text", {"source": "s"}) == {"text": "text", "metadata": {"source": "s"}}
    assert manager.add_documents_batch([("a", {}), ("b", {})]) == {"count": 2}


def test_rag_manager_absolute_import_fallback(monkeypatch):
    import importlib.util
    from pathlib import Path

    class FakeVectorRAG:
        pass

    real_import = builtins.__import__
    fake_src_rag_vector = types.ModuleType("src.rag_vector")
    fake_src_rag_vector.VectorRAG = FakeVectorRAG
    monkeypatch.setitem(sys.modules, "src.rag_vector", fake_src_rag_vector)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "rag_vector" and level == 0:
            raise ImportError("top-level missing")
        if name == "rag_vector" and level == 1:
            raise ImportError("relative missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    module_path = Path(__file__).resolve().parents[1] / "src" / "rag_manager.py"
    spec = importlib.util.spec_from_file_location("src.rag_manager_fallback_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.VectorRAG is FakeVectorRAG


def test_search_service_returns_results_and_fetches_content(monkeypatch):
    import asyncio
    import services.search.service as search_service

    async def fake_search(query, max_results, fetch_content):
        return [
            {"url": "https://example.test", "title": "Example", "snippet": query, "content": "body"},
            {"url": "https://empty.test"},
        ]

    async def fake_fetch(url):
        return f"content:{url}"

    monkeypatch.setattr(search_service, "comprehensive_web_search", fake_search)
    monkeypatch.setattr(search_service, "fetch_webpage_content", fake_fetch)
    monkeypatch.setattr(search_service, "get_search_config", lambda: {"enabled": True})

    service = search_service.SearchService(default_depth=2, fetch_content=False)
    response = asyncio.run(service.search("local models"))

    assert service.fetch_content_enabled is False
    assert response.query == "local models"
    assert response.total == 2
    assert response.results[0].title == "Example"
    assert response.results[0].content == "body"
    assert response.results[1].url == "https://empty.test"
    assert asyncio.run(service.fetch_content("https://example.test")) == "content:https://example.test"
    assert service.get_config() == {"enabled": True}


def test_research_service_maps_handler_results(monkeypatch):
    import asyncio
    import services.research.service as research_service

    class FakeHandler:
        async def call_research_service(self, topic, endpoint, model, max_time=300, progress_callback=None):
            if progress_callback:
                progress_callback({"step": "started"})
            return {
                "answer": "summary",
                "sources": [{"url": "u", "title": "t", "snippet": "s", "relevance": 0.5}],
                "sections": ["one"],
                "tokens_used": 42,
            }

        def start_research(self, session_id, topic, endpoint, model, max_time):
            return {"session_id": session_id, "topic": topic, "max_time": max_time}

        def get_status(self, session_id):
            return {"session_id": session_id, "status": "running"}

        def cancel_research(self, session_id):
            return session_id == "s1"

    monkeypatch.setattr(research_service, "ResearchHandler", FakeHandler)
    service = research_service.ResearchService()
    progress = []
    result = asyncio.run(service.research("topic", "endpoint", "model", max_time=5, on_progress=progress.append))

    assert progress == [{"step": "started"}]
    assert result.query == "topic"
    assert result.summary == "summary"
    assert result.sources[0].relevance == 0.5
    assert result.sections == ["one"]
    assert result.tokens_used == 42
    assert result.duration_seconds >= 0
    assert service.start_background("s1", "topic", "endpoint", "model", 9)["max_time"] == 9
    assert service.get_status("s1")["status"] == "running"
    assert service.cancel("s1") is True


def test_app_helpers_file_and_path_helpers(tmp_path, monkeypatch):
    from src import app_helpers

    assert app_helpers.read_if_exists(str(tmp_path / "missing.txt")) == ""
    text_file = tmp_path / "note.txt"
    text_file.write_text("  hello\n", encoding="utf-8")
    assert app_helpers.read_if_exists(str(text_file)) == "hello"

    data_file = tmp_path / "data.bin"
    data_file.write_bytes(b"abc")
    assert app_helpers.file_to_data_url(str(data_file), "application/octet-stream") == "data:application/octet-stream;base64,YWJj"

    joined = app_helpers.abs_join(str(tmp_path), "child")
    assert joined.endswith("child")
    assert app_helpers.inside_base_dir(str(tmp_path), joined) is True
    assert app_helpers.inside_base_dir(str(tmp_path), str(tmp_path.parent)) is False
    monkeypatch.setattr(app_helpers.os.path, "commonpath", lambda paths: (_ for _ in ()).throw(ValueError("bad path")))
    assert app_helpers.inside_base_dir(str(tmp_path), joined) is False


def test_text_helpers_reasoning_prose_and_aliases():
    from src.text_helpers import _strip_reasoning_prose, strip_think, strip_thinking

    assert strip_thinking(None) == ""
    assert strip_think("<think time=\"1\">hidden</think reason=\"x\">answer") == "answer"
    assert strip_think("The user asks: explain.\n\n# Answer\nDone") == "# Answer\nDone"
    assert strip_think("We need to answer briefly.\n\n**ANSWER**\nDone") == "**ANSWER**\nDone"
    assert strip_think("I need to draft this.\n\nFinal text.", prose=True) == "Final text."
    assert strip_think("I need to draft this.", prose=True) == "I need to draft this."
    assert _strip_reasoning_prose("   ") == "   "
    assert _strip_reasoning_prose("Normal paragraph.\n\nAnother normal paragraph.") == "Normal paragraph.\n\nAnother normal paragraph."
    assert _strip_reasoning_prose("Keep this.\n\nI need to draft this.") == "I need to draft this."


def test_service_package_exports_import(monkeypatch):
    stt_module = types.ModuleType("services.stt.stt_service")
    stt_module.get_stt_service = lambda: "stt"
    monkeypatch.setitem(sys.modules, "services.stt.stt_service", stt_module)
    sys.modules.pop("services.stt", None)
    stt_pkg = importlib.import_module("services.stt")
    assert stt_pkg.__all__ == ["get_stt_service"]
    assert stt_pkg.get_stt_service() == "stt"

    tts_module = types.ModuleType("services.tts.tts_service")
    tts_module.TTSService = object
    tts_module.get_tts_service = lambda: "tts"
    monkeypatch.setitem(sys.modules, "services.tts.tts_service", tts_module)
    sys.modules.pop("services.tts", None)
    tts_pkg = importlib.import_module("services.tts")
    assert tts_pkg.__all__ == ["TTSService", "get_tts_service"]
    assert tts_pkg.get_tts_service() == "tts"

    youtube_module = types.ModuleType("services.youtube.youtube_handler")
    for name in (
        "init_youtube",
        "is_youtube_url",
        "extract_youtube_id",
        "extract_transcript_async",
        "format_transcript_for_context",
        "fetch_youtube_comments",
        "format_comments_for_context",
    ):
        setattr(youtube_module, name, lambda *args, **kwargs: name)
    monkeypatch.setitem(sys.modules, "services.youtube.youtube_handler", youtube_module)
    sys.modules.pop("services.youtube", None)
    youtube_pkg = importlib.import_module("services.youtube")
    assert "extract_youtube_id" in youtube_pkg.__all__


def test_core_package_exports_are_wired_with_stubbed_dependencies(monkeypatch):
    import importlib.util
    from pathlib import Path

    llm_core = types.ModuleType("src.llm_core")
    for name in ("llm_call", "llm_call_async", "stream_llm", "list_model_ids", "normalize_model_id"):
        setattr(llm_core, name, lambda *args, **kwargs: None)
    llm_core.LLMConfig = type("LLMConfig", (), {})

    auth = types.ModuleType("core.auth")
    auth.AuthManager = type("AuthManager", (), {})
    constants = types.ModuleType("core.constants")
    constants.APP_NAME = "Cleverly"
    middleware = types.ModuleType("core.middleware")
    middleware.SecurityHeadersMiddleware = type("SecurityHeadersMiddleware", (), {})
    exceptions = types.ModuleType("core.exceptions")
    for name in ("SessionNotFoundError", "InvalidFileUploadError", "LLMServiceError", "WebSearchError"):
        setattr(exceptions, name, type(name, (Exception,), {}))
    models = types.ModuleType("core.models")
    models.Session = type("Session", (), {})
    models.ChatMessage = type("ChatMessage", (), {})
    session_manager = types.ModuleType("core.session_manager")
    session_manager.SessionManager = type("SessionManager", (), {})

    for name, module in {
        "src.llm_core": llm_core,
        "core.auth": auth,
        "core.constants": constants,
        "core.middleware": middleware,
        "core.exceptions": exceptions,
        "core.models": models,
        "core.session_manager": session_manager,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    module_path = Path(__file__).resolve().parents[1] / "core" / "__init__.py"
    spec = importlib.util.spec_from_file_location("core", module_path, submodule_search_locations=[str(module_path.parent)])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "core", module)
    spec.loader.exec_module(module)

    assert module.APP_NAME == "Cleverly"
    assert module.AuthManager is auth.AuthManager
    assert module.Session is models.Session
    assert "SessionManager" in module.__all__
