import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.personal_routes as personal_routes


def endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class PersonalDocs:
    def __init__(self):
        self.index = [{
            "name": "doc.txt",
            "size": 3,
            "path": "docs/doc.txt",
            "source_dir": "docs",
            "chunks": ["private local model notes", "calendar unrelated"],
        }]
        self.directories = ["docs"]
        self.added = []
        self.removed = []
        self.excluded = []
        self.refreshes = 0
        self.raise_add = False
        self.raise_remove = False
        self.raise_exclude = False

    def get_indexed_directories(self):
        return list(self.directories)

    def refresh_index(self):
        self.refreshes += 1

    def add_directory(self, directory, index=False):
        if self.raise_add:
            raise RuntimeError("add failed")
        self.added.append((directory, index))

    def remove_directory(self, directory):
        if self.raise_remove:
            raise RuntimeError("remove failed")
        self.removed.append(directory)

    def exclude_file(self, filepath):
        if self.raise_exclude:
            raise RuntimeError("exclude failed")
        self.excluded.append(filepath)


class Rag:
    def __init__(
        self,
        *,
        index_result=None,
        remove_raises=False,
        delete_raises=False,
        add_results=None,
        search_results=None,
        search_raises=False,
    ):
        self.index_result = index_result or {"success": True, "indexed_count": 2, "failed_count": 1}
        self.remove_raises = remove_raises
        self.delete_raises = delete_raises
        self.add_results = list(add_results) if add_results is not None else [True]
        self.search_results = search_results
        self.search_raises = search_raises
        self.indexed = []
        self.removed = []
        self.deleted = []
        self.documents = []
        self.searches = []

    def index_personal_documents(self, directory, owner=None):
        self.indexed.append((directory, owner))
        return dict(self.index_result)

    def remove_directory(self, directory):
        self.removed.append(directory)
        if self.remove_raises:
            raise RuntimeError("rag remove failed")

    def delete_by_source(self, filepath):
        self.deleted.append(filepath)
        if self.delete_raises:
            raise RuntimeError("delete failed")
        return 3

    def _split_into_chunks(self, text, chunk_size=500):
        return [part for part in text.split("|") if part]

    def add_document(self, chunk, metadata):
        self.documents.append((chunk, metadata))
        return self.add_results.pop(0) if self.add_results else True

    def search(self, query, k=5, owner=None):
        self.searches.append((query, k, owner))
        if self.search_raises:
            raise RuntimeError("vector failed")
        return list(self.search_results or [])

    def get_stats(self):
        return {"embedding_model": "local-hash-384 @ local://hash"}


class Upload:
    def __init__(self, filename, data=b"text", *, raises=False):
        self.filename = filename
        self.data = data
        self.raises = raises

    async def read(self, _limit):
        if self.raises:
            raise RuntimeError("read failed")
        return self.data


class Request:
    state = SimpleNamespace(current_user="alice", user="alice")


def setup_router(monkeypatch, tmp_path, rag):
    docs = PersonalDocs()
    personal_dir = tmp_path / "personal"
    uploads_dir = tmp_path / "uploads"
    personal_dir.mkdir()
    uploads_dir.mkdir()
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(personal_dir))
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    monkeypatch.setattr(personal_routes, "get_current_user", lambda request: request.state.current_user)
    router = personal_routes.setup_personal_routes(docs, rag_manager=None, rag_available=True)
    return router, docs, personal_dir, uploads_dir


def test_upload_path_helpers_reject_unsafe_commonpath(monkeypatch, tmp_path):
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))
    assert os.path.basename(personal_routes._personal_upload_dir_for_owner(" ../alice ")) == "_alice"

    upload_dir = personal_routes._personal_upload_dir_for_owner("alice")
    path, stored_name, display = personal_routes._unique_personal_upload_path(upload_dir, "")
    assert os.path.dirname(path) == upload_dir
    assert stored_name.startswith("upload-")
    assert display == "upload"

    with monkeypatch.context() as m:
        m.setattr(personal_routes, "secure_filename", lambda _name: ".dotfile")
        _path, stored_name, display = personal_routes._unique_personal_upload_path(upload_dir, "dot")
        assert stored_name.startswith("upload-")
        assert display == "upload"

    real_commonpath = os.path.commonpath
    monkeypatch.setattr(personal_routes.os.path, "commonpath", lambda _paths: str(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="owner path"):
        personal_routes._personal_upload_dir_for_owner("alice")
    monkeypatch.setattr(personal_routes.os.path, "commonpath", lambda _paths: str(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="filename"):
        personal_routes._unique_personal_upload_path(upload_dir, "file.txt")
    monkeypatch.setattr(personal_routes.os.path, "commonpath", real_commonpath)


def test_personal_list_and_reload(monkeypatch, tmp_path):
    router, docs, _personal_dir, _uploads_dir = setup_router(monkeypatch, tmp_path, Rag())

    listed = endpoint(router, "/api/personal")(owner="alice", _admin=None)
    assert listed == {"files": [{"name": "doc.txt", "size": 3, "path": "docs/doc.txt"}], "directories": ["docs"]}
    reloaded = endpoint(router, "/api/personal/reload", "POST")(owner="alice", _admin=None)
    assert reloaded == {"ok": True, "count": 1}
    assert docs.refreshes == 1


def test_personal_search_vector_keyword_and_errors(monkeypatch, tmp_path):
    vector_hit = {
        "id": "doc-1",
        "document": "private vector result about local models",
        "metadata": {
            "source": "/docs/private.txt",
            "filename": "private.txt",
            "directory": "/docs",
            "type": ".txt",
            "chunk_id": 2,
        },
        "similarity": 0.92,
        "distance": 0.08,
    }
    rag = Rag(search_results=[vector_hit])
    router, _docs, _personal_dir, _uploads_dir = setup_router(monkeypatch, tmp_path, rag)
    handler = endpoint(router, "/api/personal/search", "GET")

    result = handler(Request(), q="local models", limit=3, owner="alice", _admin=None)

    assert result["search_type"] == "vector"
    assert result["embedding_model"] == "local-hash-384 @ local://hash"
    assert result["results"][0]["title"] == "private.txt"
    assert result["results"][0]["search_type"] == "vector"
    assert rag.searches == [("local models", 3, "alice")]

    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: Rag(search_results=[]))
    keyword = handler(Request(), q="model", limit=5, owner="alice", _admin=None)
    assert keyword["search_type"] == "keyword"
    assert keyword["results"][0]["title"] == "doc.txt"
    assert keyword["results"][0]["search_type"] == "keyword"

    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: Rag(search_raises=True))
    fallback = handler(Request(), q="private", limit=5, owner="alice", _admin=None)
    assert fallback["search_type"] == "keyword"
    assert fallback["vector_error"] == "vector failed"

    with pytest.raises(HTTPException) as empty:
        handler(Request(), q="   ", limit=5, owner="alice", _admin=None)
    assert empty.value.status_code == 400


@pytest.mark.asyncio
async def test_add_directory_to_rag_success_and_errors(monkeypatch, tmp_path):
    rag = Rag()
    router, docs, personal_dir, _uploads_dir = setup_router(monkeypatch, tmp_path, rag)
    target = personal_dir / "docs"
    target.mkdir()
    handler = endpoint(router, "/api/personal/add_directory", "POST")

    result = await handler(Request(), SimpleNamespace(directory="docs"), owner="alice", _admin=None)
    assert result["success"] is True
    assert result["indexed_count"] == 2
    assert rag.indexed == [(str(target), "alice")]
    assert docs.added == [(str(target), False)]

    with pytest.raises(HTTPException) as empty:
        await handler(Request(), SimpleNamespace(directory=""), owner="alice", _admin=None)
    assert empty.value.status_code == 400

    with pytest.raises(HTTPException) as outside:
        await handler(Request(), SimpleNamespace(directory=str(tmp_path / "outside")), owner="alice", _admin=None)
    assert outside.value.status_code == 403

    with monkeypatch.context() as m:
        m.setattr(
            personal_routes.os.path,
            "commonpath",
            lambda _paths: (_ for _ in ()).throw(ValueError("mixed drives")),
        )
        with pytest.raises(HTTPException) as mixed_drive:
            await handler(Request(), SimpleNamespace(directory="docs"), owner="alice", _admin=None)
        assert mixed_drive.value.status_code == 403

    with pytest.raises(HTTPException) as missing:
        await handler(Request(), SimpleNamespace(directory="missing"), owner="alice", _admin=None)
    assert missing.value.status_code == 404

    file_path = personal_dir / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException) as not_dir:
        await handler(Request(), SimpleNamespace(directory="file.txt"), owner="alice", _admin=None)
    assert not_dir.value.status_code == 400

    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: Rag(index_result={"success": False, "message": "bad"}))
    with pytest.raises(HTTPException) as failed_index:
        await handler(Request(), SimpleNamespace(directory="docs"), owner="alice", _admin=None)
    assert failed_index.value.status_code == 500

    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: None)
    with pytest.raises(HTTPException) as no_rag:
        await handler(Request(), SimpleNamespace(directory="docs"), owner="alice", _admin=None)
    assert no_rag.value.status_code == 503

    docs.raise_add = True
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: Rag())
    with pytest.raises(HTTPException) as unexpected:
        await handler(Request(), SimpleNamespace(directory="docs"), owner="alice", _admin=None)
    assert unexpected.value.status_code == 500


@pytest.mark.asyncio
async def test_remove_directory_success_best_effort_and_errors(monkeypatch, tmp_path):
    router, docs, _personal_dir, _uploads_dir = setup_router(monkeypatch, tmp_path, Rag(remove_raises=True))
    handler = endpoint(router, "/api/personal/remove_directory", "DELETE")

    result = await handler(directory="docs", owner="alice", _admin=None)
    assert result["success"] is True
    assert docs.removed == ["docs"]

    with pytest.raises(HTTPException) as empty:
        await handler(directory="", owner="alice", _admin=None)
    assert empty.value.status_code == 400

    docs.raise_remove = True
    with pytest.raises(HTTPException) as failed:
        await handler(directory="docs", owner="alice", _admin=None)
    assert failed.value.status_code == 500


@pytest.mark.asyncio
async def test_upload_files_to_rag_indexes_text_pdf_and_failures(monkeypatch, tmp_path):
    rag = Rag(add_results=[True, False, True])
    router, docs, _personal_dir, uploads_dir = setup_router(monkeypatch, tmp_path, rag)
    monkeypatch.setattr(personal_routes, "MAX_PERSONAL_UPLOAD_BYTES", 8)
    personal_docs_module = SimpleNamespace(extract_pdf_text=lambda _path: "pdf chunk")
    monkeypatch.setitem(sys.modules, "src.personal_docs", personal_docs_module)
    handler = endpoint(router, "/api/personal/upload", "POST")

    result = await handler(
        Request(),
        [
            Upload("notes.txt", b"one|two"),
            Upload("big.txt", b"123456789"),
            Upload("empty.txt", b"   "),
            Upload("paper.pdf", b"%PDF"),
            Upload("bad.txt", raises=True),
        ],
    )

    assert result["success"] is True
    assert result["uploaded"] == ["notes.txt", "paper.pdf"]
    assert result["indexed_count"] == 2
    assert result["failed_count"] == 4
    assert docs.added == [(os.path.join(str(uploads_dir), "alice"), False)]
    assert [chunk for chunk, _meta in rag.documents] == ["one", "two", "pdf chunk"]
    assert all(meta["owner"] == "alice" for _chunk, meta in rag.documents)

    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: None)
    with pytest.raises(HTTPException) as no_rag:
        await handler(Request(), [Upload("notes.txt", b"one")])
    assert no_rag.value.status_code == 503


@pytest.mark.asyncio
async def test_delete_file_from_rag_removes_upload_and_handles_failures(monkeypatch, tmp_path):
    rag = Rag()
    router, docs, _personal_dir, uploads_dir = setup_router(monkeypatch, tmp_path, rag)
    user_dir = uploads_dir / "alice"
    user_dir.mkdir()
    uploaded = user_dir / "doc.txt"
    uploaded.write_text("delete me", encoding="utf-8")
    handler = endpoint(router, "/api/personal/file", "DELETE")

    result = await handler(filepath=str(uploaded), owner="alice", _admin=None)
    assert result == {"success": True, "removed_chunks": 3, "deleted_from_disk": True}
    assert not uploaded.exists()
    assert docs.excluded == [str(uploaded)]

    rag.delete_raises = True
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    result = await handler(filepath=str(outside), owner="alice", _admin=None)
    assert result == {"success": True, "removed_chunks": 0, "deleted_from_disk": False}
    assert outside.exists()

    with monkeypatch.context() as m:
        m.setattr(
            personal_routes.os.path,
            "commonpath",
            lambda _paths: (_ for _ in ()).throw(ValueError("mixed drives")),
        )
        result = await handler(filepath=str(outside), owner="alice", _admin=None)
        assert result["deleted_from_disk"] is False

    docs.raise_exclude = True
    with pytest.raises(HTTPException) as failed:
        await handler(filepath=str(outside), owner="alice", _admin=None)
    assert failed.value.status_code == 500
