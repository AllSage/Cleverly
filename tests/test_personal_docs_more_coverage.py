import builtins
import json
import os
import sys
import types

from src import personal_docs


def test_pdf_text_handles_success_import_error_and_reader_failure(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, _path):
            self.pages = [Page("first "), Page(None), Page("second")]

    fake_pypdf = types.SimpleNamespace(PdfReader=Reader)
    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)
    assert personal_docs.extract_pdf_text("sample.pdf") == "first second"

    class FailingReader:
        def __init__(self, _path):
            raise RuntimeError("bad pdf")

    fake_pypdf.PdfReader = FailingReader
    assert personal_docs.extract_pdf_text("sample.pdf") == ""

    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("missing pypdf")
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "pypdf", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    assert personal_docs.extract_pdf_text("sample.pdf") == ""


def test_text_helpers_indexing_and_keyword_retrieval(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    (docs / "alpha.txt").write_text("Clever local model privacy notes", encoding="utf-8")
    (nested / "beta.md").write_text("offline model setup setup", encoding="utf-8")
    (docs / "skip.bin").write_text("ignored", encoding="utf-8")
    (docs / "paper.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr(personal_docs, "extract_pdf_text", lambda _path: "private pdf model notes")

    assert personal_docs.read_text_file(str(docs / "alpha.txt")).startswith("Clever")
    assert personal_docs.read_text_file(str(docs / "missing.txt")) == ""
    assert personal_docs.split_chunks("abcdef", size=4, overlap=2) == ["abcd", "cdef", "ef"]
    assert personal_docs.split_chunks("   ") == []
    tokens = personal_docs.tokenize("The Clever local AI and my docs")
    assert "clever" in tokens
    assert "the" not in tokens
    assert "my" not in tokens

    index = personal_docs.load_personal_index(str(docs), extensions=(".txt", ".md", ".pdf"))
    names = {item["name"] for item in index}
    assert names == {"alpha.txt", "nested\\beta.md" if os.name == "nt" else "nested/beta.md", "paper.pdf"}

    real_isfile = personal_docs.os.path.isfile
    monkeypatch.setattr(personal_docs.os.path, "isfile", lambda path: False if str(path).endswith("alpha.txt") else real_isfile(path))
    no_alpha = personal_docs.load_personal_index(str(docs), extensions=(".txt", ".md", ".pdf"))
    assert all(item["name"] != "alpha.txt" for item in no_alpha)

    results = personal_docs.retrieve_personal_keyword(index, "model setup privacy", k=2)
    assert len(results) == 2
    assert "chunk 1" in results[0]
    assert personal_docs.retrieve_personal_keyword(index, "the and my", k=2) == []


def test_retrieve_personal_prefers_vector_and_falls_back_to_keyword():
    index = [{"name": "local.txt", "chunks": ["local model privacy"], "size": 10, "path": "local.txt"}]

    class VectorRag:
        def search(self, query, k):
            assert query == "privacy"
            assert k == 3
            return [{"metadata": {"source": "/tmp/vector.txt"}, "document": "vector answer"}]

    assert personal_docs.retrieve_personal(index, "", rag_manager=VectorRag()) == []
    assert personal_docs.retrieve_personal(index, "privacy", k=3, rag_manager=VectorRag()) == [
        "[vector.txt :: vector search]\nvector answer"
    ]

    class EmptyVectorRag:
        def search(self, _query, _k):
            return []

    class BrokenVectorRag:
        def search(self, _query, _k):
            raise RuntimeError("offline")

    assert "local.txt" in personal_docs.retrieve_personal(index, "privacy", rag_manager=EmptyVectorRag())[0]
    assert "local.txt" in personal_docs.retrieve_personal(index, "privacy", rag_manager=BrokenVectorRag())[0]


class RecordingRag:
    def __init__(self):
        self.index_calls = []
        self.rebuild_calls = 0
        self.fail_next = False

    def index_personal_documents(self, directory, owner=None):
        self.index_calls.append((directory, owner))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("index failed")
        return {"success": True, "indexed_count": 2}

    def rebuild_index(self):
        self.rebuild_calls += 1

    def search(self, _query, _k):
        return []


def test_personal_docs_manager_tracks_excludes_removes_and_reports_stats(tmp_path):
    base = tmp_path / "base"
    extra = tmp_path / "extra"
    other = tmp_path / "other"
    base.mkdir()
    extra.mkdir()
    other.mkdir()
    base_file = base / "base.txt"
    extra_file = extra / "extra.md"
    base_file.write_text("base private local note", encoding="utf-8")
    extra_file.write_text("extra model context", encoding="utf-8")
    not_a_directory = tmp_path / "plain.txt"
    not_a_directory.write_text("plain", encoding="utf-8")

    rag = RecordingRag()
    manager = personal_docs.PersonalDocsManager(str(base), rag)
    manager.add_directory(str(extra), owner="alice")
    manager.add_directory(str(extra), owner="alice")
    manager.add_directory(str(other), index=False)
    rag.fail_next = True
    manager.add_directory(str(tmp_path / "failing"), owner="bob")

    tracked = manager.get_indexed_directories()
    assert str(extra.resolve()) in tracked
    assert rag.index_calls[0] == (str(extra.resolve()), "alice")
    assert not any(call[0] == str(other.resolve()) for call in rag.index_calls)

    manager.indexed_directories.extend([str(tmp_path / "missing"), str(not_a_directory)])
    manager.refresh_index()
    names = {item["name"] for item in manager.index}
    assert "base.txt" in names
    assert any(name.endswith("extra.md") for name in names)

    manager.exclude_file(str(base_file))
    assert all(os.path.abspath(item["path"]) != os.path.abspath(base_file) for item in manager.index)

    retrieved = manager.retrieve("model", k=2)
    assert retrieved
    file_list = manager.get_file_list()
    assert all(set(item) == {"name", "size"} for item in file_list)
    stats = manager.get_stats()
    assert stats["total_documents"] == len(manager.index)
    assert stats["directories_count"] == len(manager.indexed_directories) + 1
    assert ".md" in stats["file_types"]

    manager.remove_directory(str(extra))
    assert rag.rebuild_calls == 1
    assert str(extra.resolve()) not in manager.get_indexed_directories()
    manager.remove_directory(str(extra))


def test_manager_persistence_error_branches_and_index_all(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "indexed_directories.json").write_text("{not json", encoding="utf-8")
    (base / "excluded_files.json").write_text("{not json", encoding="utf-8")

    manager = personal_docs.PersonalDocsManager(str(base))
    assert manager.get_indexed_directories() == []
    assert manager.excluded_files == set()
    assert manager.index_all_directories() is None

    class MixedRag:
        def __init__(self):
            self.calls = 0

        def index_personal_documents(self, directory, owner=None):
            self.calls += 1
            if directory.endswith("false"):
                return {"success": False, "message": "no chunks"}
            if directory.endswith("boom"):
                raise RuntimeError("boom")
            return {"success": True, "indexed_count": 1}

    false_dir = tmp_path / "false"
    boom_dir = tmp_path / "boom"
    ok_dir = tmp_path / "ok"
    for directory in (false_dir, boom_dir, ok_dir):
        directory.mkdir()

    manager.rag_manager = MixedRag()
    manager.indexed_directories = [
        str(ok_dir),
        str(tmp_path / "missing"),
        str(false_dir),
        str(boom_dir),
    ]
    result = manager.index_all_directories()
    assert result == {"success": 2, "failed": 3}


def test_personal_docs_remaining_persistence_and_reindex_edges(tmp_path, monkeypatch):
    base = tmp_path / "base"
    extra = tmp_path / "extra"
    remaining = tmp_path / "remaining"
    base.mkdir()
    extra.mkdir()
    remaining.mkdir()
    (base / "indexed_directories.json").write_text(json.dumps([str(extra)]), encoding="utf-8")
    (base / "base.txt").write_text("base text", encoding="utf-8")
    extra_file = extra / "extra.txt"
    extra_file.write_text("extra text", encoding="utf-8")
    remaining_file = remaining / "remaining.txt"
    remaining_file.write_text("remaining text", encoding="utf-8")

    manager = personal_docs.PersonalDocsManager(str(base))
    assert manager.get_indexed_directories() == [str(extra)]

    manager.excluded_files.add(os.path.abspath(str(extra_file)))
    manager.refresh_index()
    assert all(not item["name"].endswith("extra.txt") for item in manager.index)

    real_open = builtins.open

    def failing_open(path, *args, **kwargs):
        if str(path).endswith("indexed_directories.json") or str(path).endswith("excluded_files.json"):
            raise OSError("blocked")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    manager.save_directories()
    manager._save_excluded()
    monkeypatch.setattr(builtins, "open", real_open)

    class ReindexRag:
        def __init__(self):
            self.rebuilt = 0

        def rebuild_index(self):
            self.rebuilt += 1

        def index_personal_documents(self, directory, owner=None):
            raise RuntimeError("reindex failed")

    manager.rag_manager = ReindexRag()
    manager.indexed_directories = [str(extra), str(remaining)]
    manager.remove_directory(str(extra))
    assert manager.rag_manager.rebuilt == 1

    class BaseFailRag:
        def index_personal_documents(self, directory, owner=None):
            if directory == str(base):
                raise RuntimeError("base failed")
            return {"success": True}

    manager.rag_manager = BaseFailRag()
    manager.indexed_directories = []
    assert manager.index_all_directories() == {"success": 0, "failed": 1}
