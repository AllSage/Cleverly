import asyncio
import base64
import importlib
import json
import sys
import types
from pathlib import Path
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
    def __init__(self, body=None, user="alice", body_error=None):
        self._body = body
        self._body_error = body_error
        self.state = SimpleNamespace(current_user=user)

    async def json(self):
        if self._body_error:
            raise self._body_error
        return self._body


def _install_fake_mcp(monkeypatch):
    mcp = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    mcp_stdio = types.ModuleType("mcp.server.stdio")
    mcp_types = types.ModuleType("mcp.types")

    class Server:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            return lambda func: func

        def call_tool(self):
            return lambda func: func

        def create_initialization_options(self):
            return {"server": self.name}

        async def run(self, *_args):
            self.ran = True

    class Tool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TextContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Stdio:
        async def __aenter__(self):
            return "read", "write"

        async def __aexit__(self, *_args):
            return False

    mcp_server.Server = Server
    mcp_stdio.stdio_server = lambda: Stdio()
    mcp_types.Tool = Tool
    mcp_types.TextContent = TextContent
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", mcp_server)
    monkeypatch.setitem(sys.modules, "mcp.server.stdio", mcp_stdio)
    monkeypatch.setitem(sys.modules, "mcp.types", mcp_types)


def _fresh_module(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_backup_routes_export_import_encrypted_and_validation(monkeypatch):
    import routes.backup_routes as backup_routes

    saved_settings = []
    saved_features = []
    saved_prefs = []
    monkeypatch.setattr(backup_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(backup_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(backup_routes, "load_settings", lambda: {"offline": True})
    monkeypatch.setattr(backup_routes, "load_features", lambda: {"email": False})
    monkeypatch.setattr(backup_routes, "save_settings", lambda settings: saved_settings.append(settings))
    monkeypatch.setattr(backup_routes, "save_features", lambda features: saved_features.append(features))

    prefs_module = types.ModuleType("routes.prefs_routes")
    prefs_module._load_for_user = lambda user: {"theme": "dark"}
    prefs_module._save_for_user = lambda user, prefs: saved_prefs.append((user, prefs))
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_module)

    class MemoryManager:
        def __init__(self):
            self.saved = None

        def load(self, owner=None):
            return [{"id": "m1", "text": "Remember this", "owner": owner}]

        def load_all(self):
            return [{"id": "old", "text": "existing"}]

        def save(self, memories):
            self.saved = memories

    class PresetManager:
        def __init__(self):
            self.saved = None

        def get_all(self):
            return {"custom": {"name": "Custom"}}

        def save(self, presets):
            self.saved = presets

    class SkillsManager:
        def __init__(self):
            self.saved = None

        def load(self, owner=None):
            return [{"id": "s1", "title": "Skill", "owner": owner}]

        def load_all(self):
            return [{"id": "known", "title": "Known"}]

        def save(self, skills):
            self.saved = skills

    memory = MemoryManager()
    presets = PresetManager()
    skills = SkillsManager()
    router = backup_routes.setup_backup_routes(memory, presets, skills)
    request = RequestLike(user="alice")

    key = backup_routes._derive_backup_key("password", b"salt", iterations=1)
    assert len(base64.urlsafe_b64decode(key)) == 32
    summary = backup_routes._summarize_backup_payload({"version": 1, "memories": [1, 2], "settings": {"a": 1}, "features": {}})
    assert summary["recognized_sections"] == ["features", "memories", "settings"]
    assert summary["recognized"]["features"] == 0
    scalar_summary = backup_routes._summarize_backup_payload({"settings": "present"})
    assert scalar_summary["recognized"]["settings"] == 1

    exported = asyncio.run(_endpoint(router, "/api/export")(request))
    exported_payload = json.loads(exported.body)
    assert exported_payload["exported_by"] == "alice"
    assert exported_payload["memories"][0]["owner"] == "alice"
    assert exported.headers["content-disposition"].startswith("attachment; filename=cleverly_backup_")

    with pytest.raises(HTTPException) as invalid_json:
        asyncio.run(_endpoint(router, "/api/import", "POST")(RequestLike(body_error=RuntimeError("bad"))))
    assert invalid_json.value.status_code == 400

    with pytest.raises(HTTPException) as non_object:
        asyncio.run(_endpoint(router, "/api/import", "POST")(RequestLike(body=[])))
    assert non_object.value.status_code == 400

    assert asyncio.run(_endpoint(router, "/api/import", "POST")(RequestLike(body={"unknown": []}))) == {
        "ok": False,
        "message": "No recognized data found in the file",
    }

    imported = asyncio.run(
        _endpoint(router, "/api/import", "POST")(
            RequestLike(
                body={
                    "memories": [{"id": "new", "text": "New memory"}, {"id": "dup", "text": "existing"}, "bad"],
                    "skills": [{"id": "skill2", "title": "New Skill"}, {"id": "known", "title": "Known"}],
                    "presets": {"custom": {"name": "Imported"}, "list": [{"id": "x"}]},
                    "settings": {"offline": False},
                    "features": {"email": True},
                    "preferences": {"font": "mono"},
                },
                user="alice",
            )
        )
    )
    assert imported["ok"] is True
    assert "1 memories" in imported["imported"]
    assert memory.saved[-1]["owner"] == "alice"
    assert skills.saved[-1]["owner"] == "alice"
    assert presets.saved["custom"]["name"] == "Imported"
    assert saved_settings[-1]["offline"] is False
    assert saved_features[-1]["email"] is True
    assert saved_prefs[-1] == ("alice", {"theme": "dark", "font": "mono"})

    skipped_skills = asyncio.run(
        _endpoint(router, "/api/import", "POST")(
            RequestLike(
                body={
                    "skills": [
                        {"id": "missing-title"},
                        {"id": "title-dup", "title": "Known"},
                    ]
                },
                user="alice",
            )
        )
    )
    assert skipped_skills["imported"] == ["0 skills"]

    fast_key = base64.urlsafe_b64encode(b"0" * 32)
    monkeypatch.setattr(backup_routes, "_derive_backup_key", lambda password, salt, iterations=backup_routes.BACKUP_KDF_ITERATIONS: fast_key)
    encrypted_export = asyncio.run(
        _endpoint(router, "/api/backup/encrypted/export", "POST")(
            backup_routes.EncryptedBackupExportRequest(password="password1"),
            request,
        )
    )
    encrypted_bundle = json.loads(encrypted_export.body)
    assert encrypted_bundle["format"] == "cleverly.encrypted-backup.v1"

    dry_run = asyncio.run(
        _endpoint(router, "/api/backup/encrypted/import", "POST")(
            backup_routes.EncryptedBackupImportRequest(password="password1", backup=encrypted_bundle, dry_run=True),
            request,
        )
    )
    assert dry_run["ok"] is True
    assert dry_run["dry_run"] is True
    assert "memories" in dry_run["summary"]["recognized"]

    def encrypted_payload(payload):
        salt = b"1" * 16
        token = backup_routes.Fernet(fast_key).encrypt(json.dumps(payload).encode("utf-8"))
        return {
            "format": "cleverly.encrypted-backup.v1",
            "iterations": backup_routes.BACKUP_KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "token": token.decode("ascii"),
        }

    with pytest.raises(HTTPException) as encrypted_not_object:
        asyncio.run(
            _endpoint(router, "/api/backup/encrypted/import", "POST")(
                backup_routes.EncryptedBackupImportRequest(password="password1", backup=encrypted_payload(["not", "dict"])),
                request,
            )
        )
    assert encrypted_not_object.value.status_code == 400

    dry_run_empty = asyncio.run(
        _endpoint(router, "/api/backup/encrypted/import", "POST")(
            backup_routes.EncryptedBackupImportRequest(password="password1", backup=encrypted_payload({"version": 1}), dry_run=True),
            request,
        )
    )
    assert dry_run_empty["ok"] is False

    import_empty = asyncio.run(
        _endpoint(router, "/api/backup/encrypted/import", "POST")(
            backup_routes.EncryptedBackupImportRequest(password="password1", backup=encrypted_payload({"version": 1})),
            request,
        )
    )
    assert import_empty == {"ok": False, "message": "No recognized data found in the encrypted backup"}

    imported_encrypted = asyncio.run(
        _endpoint(router, "/api/backup/encrypted/import", "POST")(
            backup_routes.EncryptedBackupImportRequest(password="password1", backup=encrypted_bundle),
            request,
        )
    )
    assert imported_encrypted["ok"] is True

    with pytest.raises(HTTPException) as bad_format:
        asyncio.run(
            _endpoint(router, "/api/backup/encrypted/import", "POST")(
                backup_routes.EncryptedBackupImportRequest(password="password1", backup={"format": "bad"}),
                request,
            )
        )
    assert bad_format.value.status_code == 400

    encrypted_bundle["token"] = "not-a-token"
    with pytest.raises(HTTPException) as bad_token:
        asyncio.run(
            _endpoint(router, "/api/backup/encrypted/import", "POST")(
                backup_routes.EncryptedBackupImportRequest(password="password1", backup=encrypted_bundle),
                request,
            )
        )
    assert bad_token.value.status_code == 400


def test_admin_wipe_routes_all_kinds_helpers_and_errors(monkeypatch, tmp_path):
    import routes.admin_wipe_routes as admin_wipe

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(admin_wipe, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(admin_wipe, "require_admin", lambda request: None)
    for model_name in (
        "DbSession",
        "DbChatMessage",
        "Memory",
        "Note",
        "ScheduledTask",
        "TaskRun",
        "Document",
        "DocumentVersion",
        "GalleryImage",
        "CalendarEvent",
        "CalendarCal",
    ):
        monkeypatch.setattr(admin_wipe, model_name, type(model_name, (), {}))

    memory_file = data_dir / "memory.json"
    memory_state = data_dir / "memory_tidy_state.json"
    memory_file.write_text("[1]", encoding="utf-8")
    memory_state.write_text("state", encoding="utf-8")
    admin_wipe._wipe_memory_files()
    assert json.loads(memory_file.read_text(encoding="utf-8")) == []
    assert not memory_state.exists()

    remove_me = data_dir / "remove-me"
    remove_me.mkdir()
    (remove_me / "x").write_text("x", encoding="utf-8")
    admin_wipe._rmtree_quiet(str(remove_me))
    assert not remove_me.exists()

    memory_file.write_text("[1]", encoding="utf-8")
    memory_state.write_text("state", encoding="utf-8")
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("open failed")))
    monkeypatch.setattr(admin_wipe.os, "remove", lambda *_args: (_ for _ in ()).throw(OSError("remove failed")))
    admin_wipe._wipe_memory_files()

    failing_tree = data_dir / "failing-tree"
    failing_tree.mkdir()
    monkeypatch.setattr(admin_wipe.shutil, "rmtree", lambda *_args: (_ for _ in ()).throw(OSError("rmtree failed")))
    admin_wipe._rmtree_quiet(str(failing_tree))
    monkeypatch.undo()
    monkeypatch.setattr(admin_wipe, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(admin_wipe, "require_admin", lambda request: None)
    for model_name in (
        "DbSession",
        "DbChatMessage",
        "Memory",
        "Note",
        "ScheduledTask",
        "TaskRun",
        "Document",
        "DocumentVersion",
        "GalleryImage",
        "CalendarEvent",
        "CalendarCal",
    ):
        monkeypatch.setattr(admin_wipe, model_name, type(model_name, (), {}))

    class Query:
        def __init__(self, db, model):
            self.db = db
            self.model = model

        def count(self):
            return self.db.counts.get(self.model.__name__, 3)

        def delete(self):
            if self.db.raise_on_delete:
                raise RuntimeError("delete failed")
            self.db.deleted.append(self.model.__name__)
            return self.count()

    class DB:
        def __init__(self):
            self.counts = {}
            self.deleted = []
            self.commits = 0
            self.rollbacks = 0
            self.closed = 0
            self.raise_on_delete = False

        def query(self, model):
            return Query(self, model)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    db = DB()
    monkeypatch.setattr(admin_wipe, "SessionLocal", lambda: db)

    cleared = []
    memory_vector = types.ModuleType("src.memory_vector")
    memory_vector.get_memory_vector_store = lambda: SimpleNamespace(clear=lambda: cleared.append("memory"))
    monkeypatch.setitem(sys.modules, "src.memory_vector", memory_vector)

    skills_dir = data_dir / "skills" / "one"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("skill", encoding="utf-8")
    (data_dir / "skills.json").write_text("[]", encoding="utf-8")
    (data_dir / "gallery").mkdir()
    (data_dir / "gallery_uploads").mkdir()

    session_manager = SimpleNamespace(sessions={"s": object()})
    router = admin_wipe.setup_admin_wipe_routes(session_manager)
    wipe = _endpoint(router, "/api/admin/wipe/{kind}", "DELETE")
    request = RequestLike()

    assert wipe("chats", request)["count"] == 3
    assert session_manager.sessions == {}
    assert wipe("memory", request)["kind"] == "memory"
    assert cleared == ["memory"]
    assert wipe("skills", request)["count"] == 1
    assert not (data_dir / "skills").exists()
    assert not (data_dir / "skills.json").exists()
    assert wipe("notes", request)["kind"] == "notes"
    assert wipe("tasks", request)["kind"] == "tasks"
    assert "TaskRun" in db.deleted
    assert wipe("documents", request)["kind"] == "documents"
    assert "DocumentVersion" in db.deleted
    assert wipe("gallery", request)["kind"] == "gallery"
    assert not (data_dir / "gallery").exists()
    assert wipe("calendar", request)["kind"] == "calendar"
    assert "CalendarEvent" in db.deleted

    class BadSessions:
        def clear(self):
            raise RuntimeError("clear failed")

    bad_session_router = admin_wipe.setup_admin_wipe_routes(SimpleNamespace(sessions=BadSessions()))
    assert _endpoint(bad_session_router, "/api/admin/wipe/{kind}", "DELETE")("chats", request)["kind"] == "chats"

    memory_vector.get_memory_vector_store = lambda: (_ for _ in ()).throw(RuntimeError("vector down"))
    assert wipe("memory", request)["kind"] == "memory"

    (data_dir / "skills.json").write_text("[]", encoding="utf-8")
    real_remove = admin_wipe.os.remove
    monkeypatch.setattr(admin_wipe.os, "remove", lambda path: (_ for _ in ()).throw(OSError("legacy busy")) if str(path).endswith("skills.json") else real_remove(path))
    assert wipe("skills", request)["kind"] == "skills"

    with pytest.raises(HTTPException) as unknown:
        wipe("unknown", request)
    assert unknown.value.status_code == 400

    db.raise_on_delete = True
    with pytest.raises(HTTPException) as failed:
        wipe("notes", request)
    assert failed.value.status_code == 500
    assert db.rollbacks >= 1
    assert db.closed >= 1


def test_memory_mcp_server_actions_and_init(monkeypatch):
    _install_fake_mcp(monkeypatch)
    memory_server = _fresh_module("mcp_servers.memory_server")

    class MemoryManager:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.saved = None
            self.entries = [
                {"id": "abcdef123456", "text": "A long memory" * 20, "category": "fact"},
                {"id": "pref1111", "text": "Likes blue", "category": "preference"},
            ]

        def load(self):
            return list(self.entries)

        def load_all(self):
            return list(self.entries)

        def save(self, memories):
            self.saved = memories
            self.entries = list(memories)

        def add_entry(self, text, source="", category="fact"):
            return {"id": "new123456", "text": text, "category": category, "source": source}

        def get_relevant_memories(self, query, memories, threshold=0, max_items=20):
            return [m for m in memories if query.lower() in m.get("text", "").lower()][:max_items]

    class VectorStore:
        healthy = True

        def __init__(self, data_dir):
            self.added = []
            self.removed = []

        def add(self, *args):
            self.added.append(args)

        def remove(self, memory_id):
            self.removed.append(memory_id)

    constants = types.ModuleType("src.constants")
    constants.DATA_DIR = "data"
    memory_module = types.ModuleType("src.memory")
    memory_module.MemoryManager = MemoryManager
    vector_module = types.ModuleType("src.memory_vector")
    vector_module.MemoryVectorStore = VectorStore
    monkeypatch.setitem(sys.modules, "src.constants", constants)
    monkeypatch.setitem(sys.modules, "src.memory", memory_module)
    monkeypatch.setitem(sys.modules, "src.memory_vector", vector_module)

    memory_server._initialized = False
    memory_server._memory_manager = None
    memory_server._memory_vector = None
    memory_server._ensure_init()
    assert isinstance(memory_server._memory_manager, MemoryManager)
    assert isinstance(memory_server._memory_vector, VectorStore)
    memory_server._ensure_init()

    tools = asyncio.run(memory_server.list_tools())
    assert tools[0].name == "manage_memory"
    assert "action" in tools[0].inputSchema["required"]
    assert asyncio.run(memory_server.call_tool("other", {}))[0].text == "Unknown tool: other"

    listed = asyncio.run(memory_server.call_tool("manage_memory", {"action": "list"}))[0].text
    assert "Found 2 memory entries" in listed
    assert "..." in listed
    memory_server._memory_manager.entries = [
        {"id": f"id{i:03d}", "text": f"memory {i}", "category": "fact"}
        for i in range(101)
    ]
    assert "... and 1 more" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "list"}))[0].text
    memory_server._memory_manager.entries = [
        {"id": "abcdef123456", "text": "A long memory" * 20, "category": "fact"},
        {"id": "pref1111", "text": "Likes blue", "category": "preference"},
    ]
    assert "preference" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "list", "category": "preference"}))[0].text
    assert "No memories found" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "list", "category": "missing"}))[0].text

    class BrokenVector(VectorStore):
        healthy = True

        def add(self, *args):
            raise RuntimeError("vector add failed")

        def remove(self, memory_id):
            raise RuntimeError("vector remove failed")

    memory_server._memory_vector = BrokenVector("data")
    assert "cannot be empty" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "add"}))[0].text
    assert "Memory added" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "add", "text": "New fact", "category": "event"}))[0].text

    assert "edit needs" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "edit"}))[0].text
    assert "not found" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "edit", "memory_id": "zzz", "text": "x"}))[0].text
    assert "Memory updated" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "edit", "memory_id": "new", "text": "Updated"}))[0].text

    class AddOnlyBrokenVector(VectorStore):
        healthy = True

        def add(self, *args):
            raise RuntimeError("vector add failed")

    memory_server._memory_vector = AddOnlyBrokenVector("data")
    assert "Memory updated" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "edit", "memory_id": "pref1111", "text": "Still blue"}))[0].text
    memory_server._memory_vector = BrokenVector("data")

    assert "delete needs" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "delete"}))[0].text
    assert "not found" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "delete", "memory_id": "zzz"}))[0].text
    assert "Memory deleted" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "delete", "memory_id": "new"}))[0].text

    assert "search needs" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "search"}))[0].text
    assert "No memories found" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "search", "text": "nothing"}))[0].text
    assert "matching memories" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "search", "text": "blue"}))[0].text
    monkeypatch.delattr(MemoryManager, "get_relevant_memories")
    assert "matching memories" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "search", "text": "blue"}))[0].text
    assert "Unknown action" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "bad"}))[0].text

    class UnhealthyVector(VectorStore):
        healthy = False

    vector_module.MemoryVectorStore = UnhealthyVector
    memory_server._initialized = False
    memory_server._memory_vector = None
    memory_server._ensure_init()
    assert memory_server._memory_vector is None

    class RaisingVector(VectorStore):
        def __init__(self, data_dir):
            raise RuntimeError("vector unavailable")

    vector_module.MemoryVectorStore = RaisingVector
    memory_server._initialized = False
    memory_server._memory_vector = None
    memory_server._ensure_init()
    assert memory_server._memory_vector is None

    asyncio.run(memory_server.run())
    assert memory_server.server.ran is True

    memory_server._memory_manager = None
    memory_server._initialized = True
    assert "not available" in asyncio.run(memory_server.call_tool("manage_memory", {"action": "list"}))[0].text


def test_rag_mcp_server_actions_and_errors(monkeypatch, tmp_path):
    _install_fake_mcp(monkeypatch)
    rag_server = _fresh_module("mcp_servers.rag_server")

    class RagManager:
        def __init__(self):
            self.removed = []

        def index_personal_documents(self, directory):
            return {"indexed_count": 7}

        def remove_directory(self, directory):
            self.removed.append(directory)

    class PersonalDocs:
        def __init__(self, personal_dir, rag_manager):
            self.personal_dir = personal_dir
            self.rag_manager = rag_manager
            self.index = [{"name": "a.txt"}]
            self.removed = []

        def get_indexed_directories(self):
            return ["docs"]

        def remove_directory(self, directory):
            self.removed.append(directory)

    rag_singleton = types.ModuleType("src.rag_singleton")
    rag_manager = RagManager()
    rag_singleton.get_rag_manager = lambda: rag_manager
    constants = types.ModuleType("src.constants")
    constants.PERSONAL_DIR = "personal"
    personal_docs = types.ModuleType("src.personal_docs")
    personal_docs.PersonalDocsManager = PersonalDocs
    monkeypatch.setitem(sys.modules, "src.rag_singleton", rag_singleton)
    monkeypatch.setitem(sys.modules, "src.constants", constants)
    monkeypatch.setitem(sys.modules, "src.personal_docs", personal_docs)

    rag_server._initialized = False
    rag_server._ensure_init()
    assert rag_server._rag_manager is rag_manager
    assert isinstance(rag_server._personal_docs_manager, PersonalDocs)

    tools = asyncio.run(rag_server.list_tools())
    assert tools[0].name == "manage_rag"
    assert asyncio.run(rag_server.call_tool("other", {}))[0].text == "Unknown tool: other"
    listed = asyncio.run(rag_server.call_tool("manage_rag", {"action": "list"}))[0].text
    assert "Indexed directories" in listed
    assert "a.txt" in listed
    rag_server._personal_docs_manager.index = [{"name": f"file{i}.txt"} for i in range(51)]
    many_files = asyncio.run(rag_server.call_tool("manage_rag", {"action": "list"}))[0].text
    assert "... and 1 more" in many_files

    rag_server._personal_docs_manager.index = []
    rag_server._personal_docs_manager.get_indexed_directories = lambda: []
    assert "No files" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "list"}))[0].text
    rag_server._personal_docs_manager.get_indexed_directories = lambda: (_ for _ in ()).throw(RuntimeError("index bad"))
    assert "index bad" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "list"}))[0].text

    assert "needs a directory" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "add_directory"}))[0].text
    assert "Directory not found" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "add_directory", "directory": str(tmp_path / "missing")}))[0].text
    docs = tmp_path / "docs"
    docs.mkdir()
    assert "7 chunks" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "add_directory", "directory": str(docs)}))[0].text

    rag_server._rag_manager.index_personal_documents = lambda directory: (_ for _ in ()).throw(RuntimeError("index fail"))
    assert "Failed to index" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "add_directory", "directory": str(docs)}))[0].text
    rag_server._rag_manager = None
    assert "RAG manager not available" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "add_directory", "directory": str(docs)}))[0].text

    rag_server._rag_manager = rag_manager
    assert "needs a directory" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "remove_directory"}))[0].text
    assert "removed" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "remove_directory", "directory": "docs"}))[0].text
    rag_server._personal_docs_manager.remove_directory = lambda directory: (_ for _ in ()).throw(RuntimeError("remove fail"))
    assert "Failed to remove" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "remove_directory", "directory": "docs"}))[0].text
    rag_server._personal_docs_manager = None
    assert "Personal docs manager not available" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "list"}))[0].text
    assert "Personal docs manager not available" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "remove_directory", "directory": "docs"}))[0].text
    assert "Unknown action" in asyncio.run(rag_server.call_tool("manage_rag", {"action": "bad"}))[0].text

    rag_singleton.get_rag_manager = lambda: (_ for _ in ()).throw(RuntimeError("rag down"))
    personal_docs.PersonalDocsManager = lambda *args: (_ for _ in ()).throw(RuntimeError("docs down"))
    rag_server._initialized = False
    rag_server._rag_manager = None
    rag_server._personal_docs_manager = None
    rag_server._ensure_init()
    assert rag_server._rag_manager is None
    assert rag_server._personal_docs_manager is None

    asyncio.run(rag_server.run())
    assert rag_server.server.ran is True


def test_image_generation_mcp_server_success_errors_and_gallery(monkeypatch, tmp_path):
    _install_fake_mcp(monkeypatch)
    image_server = _fresh_module("mcp_servers.image_gen_server")
    monkeypatch.chdir(tmp_path)

    tools = asyncio.run(image_server.list_tools())
    assert tools[0].name == "generate_image"
    assert asyncio.run(image_server.call_tool("other", {}))[0].text == "Unknown tool: other"
    assert "prompt is required" in asyncio.run(image_server.call_tool("generate_image", {}))[0].text

    settings_module = types.ModuleType("src.settings")
    settings = {"image_model": "", "image_quality": "high"}
    settings_module.load_settings = lambda: dict(settings)
    settings_module.get_setting = lambda key, default=None: default
    settings_module.offline_mode = lambda: False
    ai_module = types.ModuleType("src.ai_interaction")
    resolved = []

    def resolve_model(model_spec):
        resolved.append(model_spec)
        if model_spec == "gpt-image-1.5":
            raise ValueError("missing candidate")
        return "http://local/v1/chat/completions", model_spec, {"Authorization": "Bearer x"}

    ai_module._resolve_model = resolve_model
    monkeypatch.setitem(sys.modules, "src.settings", settings_module)
    monkeypatch.setitem(sys.modules, "src.ai_interaction", ai_module)

    class Response:
        def __init__(self, status_code=200, body=None, text=""):
            self.status_code = status_code
            self._body = body or {}
            self.text = text

        def json(self):
            return self._body

    class Client:
        next_response = Response(body={"data": [{"b64_json": base64.b64encode(b"png").decode("ascii")}]} )
        posts = []

        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return self.next_response

    httpx_module = types.ModuleType("httpx")
    httpx_module.AsyncClient = Client
    httpx_module.Timeout = lambda **kwargs: kwargs

    class TimeoutException(Exception):
        pass

    httpx_module.TimeoutException = TimeoutException
    monkeypatch.setitem(sys.modules, "httpx", httpx_module)
    monkeypatch.setattr(image_server.uuid, "uuid4", lambda: SimpleNamespace(hex="abcdef1234567890", __str__=lambda self: "uuid"))

    gallery_added = []

    class DB:
        def add(self, image):
            gallery_added.append(image)

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    class GalleryImage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    database_module = types.ModuleType("src.database")
    database_module.SessionLocal = lambda: DB()
    database_module.GalleryImage = GalleryImage
    monkeypatch.setitem(sys.modules, "src.database", database_module)

    success = asyncio.run(image_server.call_tool("generate_image", {"prompt": "Draw this", "size": "bad", "quality": "bad"}))[0].text
    assert "Generated image for: Draw this" in success
    assert "/api/generated-image/abcdef123456.png" in success
    assert (tmp_path / "data" / "generated_images" / "abcdef123456.png").read_bytes() == b"png"
    assert gallery_added[-1].filename == "abcdef123456.png"
    assert Client.posts[-1][0] == "http://local/v1/images/generations"
    assert Client.posts[-1][1]["size"] == "1024x1024"
    assert Client.posts[-1][1]["quality"] == "medium"

    settings_module.get_setting = lambda key, default=None: False
    disabled = asyncio.run(image_server.call_tool("generate_image", {"prompt": "x"}))[0].text
    assert "disabled" in disabled
    settings_module.get_setting = lambda key, default=None: default

    settings_module.offline_mode = lambda: True
    ai_module._resolve_model = lambda model_spec: ("https://api.openai.com/v1/chat/completions", "gpt-image-1", {})
    blocked = asyncio.run(image_server.call_tool("generate_image", {"prompt": "x", "model": "gpt-image-1"}))[0].text
    assert "External image generation endpoint is disabled in offline mode" in blocked
    settings_module.offline_mode = lambda: False
    ai_module._resolve_model = resolve_model

    settings["image_model"] = "dall-e-3"
    Client.next_response = Response(status_code=500, body={"error": {"message": "bad request"}}, text="raw")
    failed_api = asyncio.run(image_server.call_tool("generate_image", {"prompt": "x", "size": "bad"}))[0].text
    assert "bad request" in failed_api

    Client.next_response = Response(body={"data": []})
    assert "No images" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x"}))[0].text
    Client.next_response = Response(body={"data": [{"url": "http://image"}]})
    assert "http://image" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x"}))[0].text
    Client.next_response = Response(body={"data": [{"weird": True}]})
    assert "Unexpected image" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x"}))[0].text

    ai_module._resolve_model = lambda model_spec: (_ for _ in ()).throw(ValueError("no model"))
    assert "no model" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x", "model": "bad"}))[0].text
    settings["image_model"] = ""
    assert "No image model found" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x"}))[0].text

    class TimeoutClient(Client):
        async def post(self, *args, **kwargs):
            raise TimeoutException("slow")

    httpx_module.AsyncClient = TimeoutClient
    ai_module._resolve_model = lambda model_spec: ("http://local/v1/chat/completions", "gpt-image-1", {})
    assert "timed out" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x"}))[0].text

    class BrokenJsonResponse(Response):
        def json(self):
            raise RuntimeError("bad json")

    httpx_module.AsyncClient = Client
    Client.next_response = BrokenJsonResponse(status_code=500, text="raw error")
    assert "raw error" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x", "model": "gpt-image-1"}))[0].text

    database_module.SessionLocal = lambda: (_ for _ in ()).throw(RuntimeError("gallery unavailable"))
    Client.next_response = Response(body={"data": [{"b64_json": base64.b64encode(b"png2").decode("ascii")}]})
    assert "Generated image" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x", "model": "gpt-image-1"}))[0].text

    class RaisingClient(Client):
        async def post(self, *args, **kwargs):
            raise RuntimeError("post failed")

    httpx_module.AsyncClient = RaisingClient
    assert "post failed" in asyncio.run(image_server.call_tool("generate_image", {"prompt": "x", "model": "gpt-image-1"}))[0].text

    asyncio.run(image_server.run())
    assert image_server.server.ran is True
