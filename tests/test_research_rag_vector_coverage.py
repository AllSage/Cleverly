import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest


def _handler(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RESEARCH_DATA_DIR", tmp_path)
    return module.ResearchHandler()


def test_src_research_handler_persistence_reports_and_images(tmp_path, monkeypatch):
    import src.research_handler as rh

    handler = _handler(rh, tmp_path, monkeypatch)
    assert rh._bounded_int("bad", default=3, minimum=1, maximum=5) == 3
    assert rh._bounded_int(99, default=3, minimum=1, maximum=5) == 5
    assert rh._bounded_int(0, default=3, minimum=1, maximum=5) == 1

    (tmp_path / "avg.json").write_text(
        json.dumps({"status": "done", "started_at": 1.0, "completed_at": 4.5}),
        encoding="utf-8",
    )
    handler._active_tasks["active"] = {
        "status": "running",
        "progress": {"phase": "search"},
        "query": "topic",
        "started_at": 10,
    }
    active_status = handler.get_status("active")
    assert active_status["avg_duration"] == 3.5
    assert active_status["progress"]["phase"] == "search"

    (tmp_path / "disk.json").write_text(
        json.dumps(
            {
                "status": "done",
                "query": "saved",
                "started_at": 2,
                "result": "saved result",
                "sources": [{"url": "https://a.test"}],
                "raw_findings": [{"url": "https://a.test", "summary": "kept"}],
            }
        ),
        encoding="utf-8",
    )
    assert handler.get_status("disk")["query"] == "saved"
    assert handler.get_result("disk") == "saved result"
    assert handler.get_sources("disk") == [{"url": "https://a.test"}]
    assert handler.get_raw_findings("disk")[0]["summary"] == "kept"

    (tmp_path / "consumed.json").write_text(
        json.dumps({"consumed": True, "result": "hidden"}),
        encoding="utf-8",
    )
    assert handler.get_status("consumed") is None
    assert handler.get_result("consumed") is None

    findings = [
        {"url": "https://one.test", "title": "One", "summary": "useful detail", "og_image": "img.png"},
        {"url": "https://one.test", "title": "Dup", "summary": "duplicate"},
        {"url": "https://junk.test", "summary": "cookie consent banner"},
        {"url": "https://two.test", "title": "", "evidence": "evidence body"},
    ]
    sources = handler._extract_sources(findings)
    assert sources == [
        {"url": "https://one.test", "title": "One", "image": "img.png"},
        {"url": "https://two.test", "title": "https://two.test"},
    ]
    raw = handler._extract_raw_findings(findings)
    assert raw == [
        {"url": "https://one.test", "title": "One", "summary": "useful detail"},
        {"url": "https://one.test", "title": "Dup", "summary": "duplicate"},
        {"url": "https://two.test", "title": "Untitled", "summary": "evidence body"},
    ]
    assert handler._extract_raw_findings([object()]) == []

    fired = []
    fake_event_bus = types.ModuleType("src.event_bus")
    fake_event_bus.fire_event = lambda name, owner=None: fired.append((name, owner))
    monkeypatch.setitem(sys.modules, "src.event_bus", fake_event_bus)

    researcher = SimpleNamespace(findings=findings, evolving_report="partial", get_stats=lambda: {"Rounds": 1})
    handler._save_result(
        "saved",
        {
            "query": "topic",
            "status": "done",
            "result": "result",
            "raw_report": "raw",
            "stats": {"Rounds": 2},
            "category": "local",
            "started_at": 1.0,
            "owner": "alice",
            "researcher": researcher,
        },
    )
    saved = json.loads((tmp_path / "saved.json").read_text(encoding="utf-8"))
    assert saved["owner"] == "alice"
    assert saved["raw_findings"][0]["summary"] == "useful detail"
    assert fired == [("research_completed", "alice")]
    assert handler._get_session_json("saved")["query"] == "topic"

    fake_visual = types.ModuleType("src.visual_report")
    fake_visual.generate_visual_report = lambda **kwargs: f"html:{kwargs['question']}:{kwargs['hidden_images']}"
    monkeypatch.setitem(sys.modules, "src.visual_report", fake_visual)
    assert handler.get_report_html("missing") is None
    assert handler.get_report_html("saved") == "html:topic:[]"

    assert handler.hide_image("missing", "img") is False
    assert handler.hide_image("saved", "img") is True
    assert handler.hide_image("saved", "img") is True
    assert json.loads((tmp_path / "saved.json").read_text(encoding="utf-8"))["hidden_images"] == ["img"]
    assert handler.unhide_all_images("missing") is False
    assert handler.unhide_all_images("saved") is True
    assert json.loads((tmp_path / "saved.json").read_text(encoding="utf-8"))["hidden_images"] == []

    handler.clear_result("saved")
    assert handler._get_session_json("saved")["consumed"] is True


@pytest.mark.asyncio
async def test_src_research_handler_llm_paths_start_and_fallback(tmp_path, monkeypatch):
    import src.research_handler as rh

    handler = _handler(rh, tmp_path, monkeypatch)

    assert await handler.synthesize_query(SimpleNamespace(history=[]), "latest", "url", "model") == "latest"

    llm_calls = []
    fake_llm = types.ModuleType("src.llm_core")

    async def llm_call_async(**kwargs):
        llm_calls.append(kwargs)
        return '```json\n{"sub_questions":["a"],"key_topics":["b"],"success_criteria":"done"}\n```'

    fake_llm.llm_call_async = llm_call_async
    monkeypatch.setitem(sys.modules, "src.llm_core", fake_llm)
    fake_deep = types.ModuleType("src.deep_research")
    fake_deep.RESEARCH_PLAN_PROMPT = "Plan for {question}"
    monkeypatch.setitem(sys.modules, "src.deep_research", fake_deep)

    sess = SimpleNamespace(
        history=[
            SimpleNamespace(role="user", content="old"),
            SimpleNamespace(role="assistant", content="answer"),
        ]
    )
    assert (await handler.synthesize_query(sess, "latest context", "url", "model")).startswith("```json")
    plan = await handler.generate_plan("question", "url", "model")
    assert plan["sub_questions"] == ["a"]
    assert plan["key_topics"] == ["b"]

    async def failing_llm_call_async(**kwargs):
        raise RuntimeError("llm down")

    fake_llm.llm_call_async = failing_llm_call_async
    assert await handler.synthesize_query(sess, "fallback", "url", "model") == "fallback"
    assert await handler.generate_plan("question", "url", "model") is None

    async def ok_llm_call_async(**kwargs):
        return "ok"

    fake_llm.llm_call_async = ok_llm_call_async
    await rh.ResearchHandler._probe_endpoint("url", "model", {"Authorization": "Bearer x"})

    async def auth_error_llm_call_async(**kwargs):
        raise RuntimeError("401 Unauthorized")

    fake_llm.llm_call_async = auth_error_llm_call_async
    with pytest.raises(RuntimeError, match="requires an API key"):
        await rh.ResearchHandler._probe_endpoint("url", "model")

    async def net_error_llm_call_async(**kwargs):
        raise RuntimeError("connection refused")

    fake_llm.llm_call_async = net_error_llm_call_async
    with pytest.raises(RuntimeError, match="Cannot reach model"):
        await rh.ResearchHandler._probe_endpoint("url", "model")

    async def fake_probe(endpoint, model, headers=None):
        return None

    monkeypatch.setattr(rh.ResearchHandler, "_probe_endpoint", staticmethod(fake_probe))

    class FakeDeepResearcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.findings = [{"url": "https://src.test", "summary": "good"}]
            self.evolving_report = "evolving"

        async def research(self, query, **kwargs):
            self.research_kwargs = kwargs
            return "<think>hidden</think>\nfinal report"

        def get_stats(self):
            return {"Rounds": 2, "Queries": 3, "URLs": 4}

        def cancel(self):
            self.cancelled = True

    fake_deep.DeepResearcher = FakeDeepResearcher
    fake_settings = types.ModuleType("src.settings")
    fake_settings.get_setting = lambda key, default=None: {
        "research_max_tokens": "500",
        "research_extraction_timeout_seconds": "bad",
        "research_extraction_concurrency": "99",
    }.get(key, default)
    monkeypatch.setitem(sys.modules, "src.settings", fake_settings)

    entry = {}
    progress = []
    report = await handler.call_research_service(
        "query",
        "url",
        "model",
        progress_callback=progress.append,
        _task_entry=entry,
        prior_report="prior",
        prior_findings=[{"url": "old"}],
        prior_urls={"https://old.test"},
        max_rounds=5,
        extraction_timeout=None,
        extraction_concurrency=None,
    )
    assert progress == [{"phase": "probing", "model": "model"}]
    assert "final report" in report
    assert entry["stats"]["Rounds"] == 2
    assert entry["raw_report"] == "final report"
    assert entry["researcher"].kwargs["extraction_timeout"] == 90
    assert entry["researcher"].kwargs["extraction_concurrency"] == 12

    completed = []

    async def fake_call_research_service(*args, **kwargs):
        kwargs["_task_entry"]["researcher"] = SimpleNamespace(findings=[{"url": "https://done", "summary": "ok"}])
        return "background result"

    monkeypatch.setattr(handler, "call_research_service", fake_call_research_service)
    started = handler.start_research(
        "bg",
        "topic",
        "url",
        "model",
        hard_timeout=10,
        on_complete=lambda *args: completed.append(args),
        owner="alice",
    )
    assert started["status"] == "running"
    await handler._active_tasks["bg"]["task"]
    assert handler._active_tasks["bg"]["status"] == "done"
    assert completed[0][0] == "bg"
    assert handler.get_result("bg") == "background result"

    handler._active_tasks["cancel"] = {
        "status": "running",
        "researcher": SimpleNamespace(cancel=lambda: completed.append(("cancelled",))),
        "task": SimpleNamespace(done=lambda: True, cancel=lambda: completed.append(("task",))),
    }
    assert handler.cancel_research("missing") is False
    assert handler.cancel_research("cancel") is True
    assert handler.cancel_research("cancel") is False

    class Legacy:
        findings = [1, 2]
        source_reports = [1]
        progress_tracker = SimpleNamespace(counters={"searches_executed": 3, "urls_processed": 4})

        def start_research(self, query, max_time):
            return "legacy report"

    handler._legacy_engine = Legacy()
    legacy = await handler._fallback_research("query", "url", "model", 5, "primary")
    assert "legacy report" in legacy
    assert handler._get_legacy_stats()["Searches"] == 3

    class BrokenLegacy(Legacy):
        def start_research(self, query, max_time):
            raise RuntimeError("legacy bad")

    handler._legacy_engine = BrokenLegacy()
    import src.search as search_pkg

    monkeypatch.setattr(search_pkg, "comprehensive_web_search", lambda query: f"search:{query}")
    assert "search:query" in await handler._fallback_research("query", "url", "model", 5, "primary")

    monkeypatch.setattr(
        search_pkg,
        "comprehensive_web_search",
        lambda query: (_ for _ in ()).throw(RuntimeError("search bad")),
    )
    assert "Complete Research Failure" in handler._handle_research_failure("query", "primary")


@pytest.mark.asyncio
async def test_src_research_handler_timeout_branch(tmp_path, monkeypatch):
    import src.research_handler as rh

    handler = _handler(rh, tmp_path, monkeypatch)
    completed = []

    async def fake_call_research_service(*args, **kwargs):
        kwargs["_task_entry"]["researcher"] = SimpleNamespace(
            evolving_report="partial report",
            findings=[{"url": "https://partial", "summary": "useful"}],
            get_stats=lambda: {"Rounds": 1, "Queries": 1, "URLs": 1},
        )
        return "ignored"

    async def timeout_after_running(coro, timeout):
        await coro
        raise asyncio.TimeoutError()

    monkeypatch.setattr(handler, "call_research_service", fake_call_research_service)
    monkeypatch.setattr(rh.asyncio, "wait_for", timeout_after_running)

    handler.start_research(
        "timeout",
        "topic",
        "url",
        "model",
        hard_timeout=1,
        on_complete=lambda *args: completed.append(args),
    )
    await handler._active_tasks["timeout"]["task"]
    assert handler._active_tasks["timeout"]["status"] == "done"
    assert "partial report" in handler._active_tasks["timeout"]["result"]
    assert completed[0][0] == "timeout"


@pytest.mark.parametrize("module_name", ["services.research.research_handler"])
def test_service_research_handler_success_and_persistence(module_name, tmp_path, monkeypatch):
    module = __import__(module_name, fromlist=["ResearchHandler"])
    handler = _handler(module, tmp_path, monkeypatch)

    findings = [
        {"url": "https://one.test", "title": "One", "summary": "summary"},
        {"url": "https://one.test", "title": "Duplicate", "summary": "summary"},
    ]
    researcher = SimpleNamespace(findings=findings, evolving_report="raw", get_stats=lambda: {"Rounds": 1})
    handler._save_result(
        "svc",
        {
            "query": "topic",
            "status": "done",
            "result": "result",
            "started_at": 1,
            "researcher": researcher,
        },
    )
    assert handler.get_status("svc")["query"] == "topic"
    assert handler.get_result("svc") == "result"
    assert handler.get_sources("svc") == [{"url": "https://one.test", "title": "One"}]
    assert handler._extract_sources(findings) == [{"url": "https://one.test", "title": "One"}]

    report = handler._format_research_report(
        "topic",
        "full report",
        {"Rounds": 2, "Queries": 3, "URLs": 4},
        1.2,
        findings=findings,
        evolving_report="raw",
    )
    assert "Research Summary" in report
    assert "Raw collected findings" in report

    class FakeDeepResearcher:
        def __init__(self, **kwargs):
            self.findings = findings
            self.evolving_report = "raw"

        async def research(self, query):
            return "deep report"

        def get_stats(self):
            return {"Rounds": 1, "Queries": 1, "URLs": 1}

    fake_deep = types.ModuleType("src.deep_research")
    fake_deep.DeepResearcher = FakeDeepResearcher
    monkeypatch.setitem(sys.modules, "src.deep_research", fake_deep)
    fake_settings = types.ModuleType("src.settings")
    fake_settings.get_setting = lambda key, default=None: default
    monkeypatch.setitem(sys.modules, "src.settings", fake_settings)

    result = asyncio.run(handler.call_research_service("topic", "url", "model", max_time=5))
    assert "deep report" in result

    class Legacy:
        findings = [1]
        source_reports = [1, 2]
        progress_tracker = SimpleNamespace(counters={"searches_executed": 3, "urls_processed": 4})

        def start_research(self, query, max_time):
            return "legacy"

    handler._legacy_engine = Legacy()
    assert "legacy" in asyncio.run(handler._fallback_research("topic", "url", "model", 5, "primary"))
    assert handler._get_legacy_stats()["URLs"] == 4

    import src.search as search_pkg

    handler._legacy_engine = None
    monkeypatch.setattr(search_pkg, "comprehensive_web_search", lambda query: "basic")
    assert "basic" in handler._handle_research_failure("topic", "primary")
    monkeypatch.setattr(
        search_pkg,
        "comprehensive_web_search",
        lambda query: (_ for _ in ()).throw(RuntimeError("search failed")),
    )
    assert "Complete Research Failure" in handler._handle_research_failure("topic", "primary")
    assert "Research Engine Unavailable" in handler._format_error_response("err", "topic")

    handler.clear_result("svc")
    assert not (tmp_path / "svc.json").exists()


class FakeEmbeddingModel:
    url = "http://embed"
    model = "fake-embed"

    def encode(self, texts, normalize_embeddings=True):
        return [[float(len(text)), 1.0] for text in texts]


class FakeCollection:
    def __init__(self):
        self.rows = {}
        self.fail_query = False
        self.fail_get = False
        self.fail_count = False

    def count(self):
        if self.fail_count:
            raise RuntimeError("count failed")
        return len(self.rows)

    def add(self, ids, embeddings, documents, metadatas):
        for doc_id, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.rows[doc_id] = {"embedding": emb, "document": doc, "metadata": meta}

    def get(self, ids=None, where=None, include=None):
        if self.fail_get:
            raise RuntimeError("get failed")
        row_ids = list(self.rows)
        if ids is not None:
            row_ids = [doc_id for doc_id in ids if doc_id in self.rows]
        if where:
            row_ids = [doc_id for doc_id in row_ids if self._matches(self.rows[doc_id]["metadata"], where)]
        return {
            "ids": row_ids,
            "documents": [self.rows[doc_id]["document"] for doc_id in row_ids],
            "metadatas": [self.rows[doc_id]["metadata"] for doc_id in row_ids],
        }

    def query(self, query_embeddings, n_results, where=None, include=None):
        if self.fail_query:
            raise RuntimeError("query failed")
        row_ids = list(self.rows)
        if where:
            row_ids = [doc_id for doc_id in row_ids if self._matches(self.rows[doc_id]["metadata"], where)]
        row_ids = row_ids[:n_results]
        distances = [0.1 + (idx * 0.2) for idx, _ in enumerate(row_ids)]
        return {
            "ids": [row_ids],
            "documents": [[self.rows[doc_id]["document"] for doc_id in row_ids]],
            "metadatas": [[self.rows[doc_id]["metadata"] for doc_id in row_ids]],
            "distances": [distances],
        }

    def delete(self, ids):
        for doc_id in ids:
            self.rows.pop(doc_id, None)

    @staticmethod
    def _matches(meta, where):
        for key, expected in where.items():
            value = meta.get(key)
            if isinstance(expected, dict) and "$contains" in expected:
                if expected["$contains"] not in str(value):
                    return False
            elif value != expected:
                return False
        return True


def _fake_rag(rag_vector, tmp_path):
    rag = object.__new__(rag_vector.VectorRAG)
    rag.persist_directory = str(tmp_path)
    rag._model = FakeEmbeddingModel()
    rag._collection = FakeCollection()
    rag._healthy = True
    return rag


def test_vector_rag_init_documents_search_and_stats(tmp_path, monkeypatch):
    import src.rag_vector as rv

    collection = FakeCollection()

    class FakeClient:
        def __init__(self):
            self.deleted = []

        def get_or_create_collection(self, name, metadata=None):
            assert name == rv.COLLECTION_NAME
            return collection

        def delete_collection(self, name):
            self.deleted.append(name)

    fake_client = FakeClient()
    fake_chroma = types.ModuleType("src.chroma_client")
    fake_chroma.get_chroma_client = lambda: fake_client
    fake_embeddings = types.ModuleType("src.embeddings")
    fake_embeddings.get_embedding_client = lambda: FakeEmbeddingModel()
    monkeypatch.setitem(sys.modules, "src.chroma_client", fake_chroma)
    monkeypatch.setitem(sys.modules, "src.embeddings", fake_embeddings)

    rag = rv.VectorRAG(str(tmp_path / "persist"))
    assert rag.healthy is True
    assert rag.collection is collection
    assert rag.get_stats()["document_count"] == 0

    assert rag.add_document("", {"source": "x"}) is False
    assert rag.add_document("alpha beta", {}) is False
    assert rag.add_document("alpha beta", {"source": "one", "owner": "alice"}) is True
    assert rag.add_document("alpha beta", {"source": "one", "owner": "alice"}) is True

    batch = rag.add_documents_batch(
        [
            ("gamma delta", {"source": "two", "owner": "bob"}),
            ("", {"source": "bad"}),
            ("epsilon alpha", {"source": "three", "owner": "alice"}),
        ]
    )
    assert batch["success"] is True
    assert batch["added_count"] == 2
    assert batch["failed_count"] == 1
    assert rag.add_documents_batch([])["success"] is False
    assert rag.add_documents_batch([(None, {})])["message"] == "No valid documents"

    results = rag.search("alpha", k=2, owner="alice")
    assert [r["metadata"]["owner"] for r in results] == ["alice", "alice"]
    assert results[0]["similarity"] >= results[1]["similarity"]
    assert rag.search("", k=2) == []

    collection.fail_query = True
    fallback = rag.search("gamma", k=3)
    assert fallback[0]["search_type"] == "keyword_fallback"
    assert rag.retrieve("gamma", 1) == [fallback[0]["document"]]

    assert rag.rebuild_index() is True
    assert fake_client.deleted == [rv.COLLECTION_NAME]

    rag._collection = collection
    collection.fail_get = True
    assert rag.delete_by_source("missing") == 0
    assert rag.add_document("zeta", {"source": "fail"}) is False
    assert rag.add_documents_batch([("zeta", {"source": "fail"})])["success"] is False
    collection.fail_count = True
    assert rag.get_stats()["healthy"] is False

    fake_embeddings.get_embedding_client = lambda: None
    failed = rv.VectorRAG(str(tmp_path / "bad"))
    assert failed.healthy is False


def test_vector_rag_index_remove_reindex_chunks_and_errors(tmp_path, monkeypatch):
    import src.rag_vector as rv

    rag = _fake_rag(rv, tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("First sentence. Second sentence. Third sentence.", encoding="utf-8")
    (docs / "empty.md").write_text("   ", encoding="utf-8")
    (docs / "bad.txt").write_bytes(b"\xff\xfe\x00")
    (docs / "doc.pdf").write_text("pdf placeholder", encoding="utf-8")
    (docs / "skip.bin").write_text("skip", encoding="utf-8")

    fake_personal_docs = types.ModuleType("src.personal_docs")
    fake_personal_docs.extract_pdf_text = lambda path: "PDF sentence. More PDF content."
    monkeypatch.setitem(sys.modules, "src.personal_docs", fake_personal_docs)

    indexed = rag.index_personal_documents(str(docs), file_extensions={".txt", ".md", ".pdf"}, owner="alice")
    assert indexed["success"] is True
    assert indexed["indexed_count"] >= 2
    assert indexed["failed_count"] == 1

    long_sentence = "x" * 120
    chunks = rag._split_into_chunks(
        f"One short. {long_sentence} Another short. Final sentence.",
        chunk_size=50,
        overlap=10,
    )
    assert len(chunks) >= 4
    assert rag._split_into_chunks("") == []
    assert rag._split_into_chunks("tiny", chunk_size=50) == ["tiny"]

    removed_none = rag.remove_directory("missing")
    assert removed_none["success"] is True
    assert removed_none["removed_count"] == 0

    removed = rag.remove_directory(str(docs))
    assert removed["success"] is True
    assert removed["removed_count"] >= 1

    reindexed = rag.reindex_directory(str(docs), file_extensions={".txt"})
    assert reindexed["success"] is True
    assert "Re-index" in reindexed["message"]

    source = next(iter(rag._collection.rows.values()))["metadata"]["source"]
    assert rag.delete_by_source(source) >= 1
    assert rag.delete_by_source("missing") == 0

    rag._collection.fail_get = True
    assert rag.remove_directory(str(docs))["success"] is False
    assert rag.index_personal_documents(str(tmp_path / "does-not-exist"))["success"] is True

    rag._healthy = False
    assert rag.remove_directory(str(docs))["success"] is False
    assert rag.delete_by_source(source) == 0
    assert rag.get_stats()["error"] == "Collection not initialized"
    assert rag.add_document("text", {"source": "x"}) is False
    assert rag.add_documents_batch([("text", {"source": "x"})])["success"] is False


def test_vector_rag_remaining_error_and_overlap_edges(tmp_path, monkeypatch):
    import src.rag_vector as rv

    rag = _fake_rag(rv, tmp_path)
    assert rag.search("alpha") == []
    assert rag._keyword_search_fallback("alpha") == []

    rag.add_document("alpha bob", {"source": "bob.txt", "owner": "bob"})
    assert rag._keyword_search_fallback("alpha", owner="alice") == []

    class EmptyGetCollection(FakeCollection):
        def count(self):
            return 1

        def get(self, ids=None, where=None, include=None):
            return {"ids": [], "documents": [], "metadatas": []}

    rag._collection = EmptyGetCollection()
    assert rag._keyword_search_fallback("alpha") == []

    rag._collection = FakeCollection()
    rag._collection.fail_count = True
    assert rag._keyword_search_fallback("alpha") == []

    rag._healthy = False
    assert rag.search("alpha") == []
    rag._healthy = True

    class DeleteFailsClient:
        def delete_collection(self, _name):
            raise RuntimeError("delete missing")

        def get_or_create_collection(self, name, metadata=None):
            assert name == rv.COLLECTION_NAME
            return FakeCollection()

    fake_chroma = types.ModuleType("src.chroma_client")
    fake_chroma.get_chroma_client = lambda: DeleteFailsClient()
    monkeypatch.setitem(sys.modules, "src.chroma_client", fake_chroma)
    assert rag.rebuild_index() is True

    fake_chroma.get_chroma_client = lambda: (_ for _ in ()).throw(RuntimeError("chroma down"))
    assert rag.rebuild_index() is False
    assert rag.healthy is False
    rag._healthy = True
    rag._collection = FakeCollection()

    docs = tmp_path / "docs-edge"
    docs.mkdir()
    (docs / "note.txt").write_text("edge content", encoding="utf-8")
    monkeypatch.setattr(rag, "add_document", lambda *_args, **_kwargs: False)
    indexed = rag.index_personal_documents(str(docs), file_extensions={".txt"})
    assert indexed["failed_count"] == 1

    monkeypatch.setattr(rv.os, "walk", lambda _directory: (_ for _ in ()).throw(RuntimeError("walk failed")))
    failed_index = rag.index_personal_documents(str(docs), file_extensions={".txt"})
    assert failed_index["success"] is False

    monkeypatch.setattr(rag, "remove_directory", lambda _directory: {"success": False, "message": "remove failed"})
    assert rag.reindex_directory(str(docs)) == {"success": False, "message": "remove failed"}

    overlap_chunks = rag._split_into_chunks("Alpha. Beta. Gamma. Delta.", chunk_size=13, overlap=6)
    assert overlap_chunks[0] == "Alpha. Beta."
    assert overlap_chunks[1].startswith("Beta.")
