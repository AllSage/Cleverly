import sys
import types


def test_app_initializer_creates_managers_rebuilds_memory_and_loads_brave(monkeypatch, tmp_path):
    import src.app_initializer as app_initializer

    dirs = {
        "DATA_DIR": tmp_path / "data",
        "PERSONAL_DIR": tmp_path / "personal",
        "RUNBOOK_DIR": tmp_path / "runbooks",
        "UPLOAD_DIR": tmp_path / "uploads",
    }
    for name, path in dirs.items():
        monkeypatch.setattr(app_initializer, name, str(path))
    monkeypatch.setattr(app_initializer, "SESSIONS_FILE", str(tmp_path / "sessions.json"))

    class MemoryManager:
        def __init__(self, data_dir):
            self.data_dir = data_dir

        def load(self):
            return [{"id": "m1", "text": "remember"}]

    class SkillsManager:
        def __init__(self, data_dir):
            self.data_dir = data_dir

    class SessionManager:
        def __init__(self, sessions_file):
            self.sessions_file = sessions_file

    class UploadHandler:
        def __init__(self, base_dir, upload_dir):
            self.base_dir = base_dir
            self.upload_dir = upload_dir

    class PersonalDocsManager:
        index = ["doc"]

        def __init__(self, personal_dir, rag_manager):
            self.personal_dir = personal_dir
            self.rag_manager = rag_manager

    class APIKeyManager:
        def __init__(self, data_dir):
            self.data_dir = data_dir

        def load(self):
            return {"brave": "brave-key"}

    class PresetManager:
        presets = {"custom": {}}

        def __init__(self, data_dir):
            self.data_dir = data_dir

    class ChatProcessor:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class ResearchHandler:
        pass

    class ChatHandler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ModelDiscovery:
        def __init__(self, host, key):
            self.host = host
            self.key = key

    set_sessions = []
    search_keys = []
    monkeypatch.setattr(app_initializer, "MemoryManager", MemoryManager)
    monkeypatch.setattr(app_initializer, "SkillsManager", SkillsManager)
    monkeypatch.setattr(app_initializer, "SessionManager", SessionManager)
    monkeypatch.setattr(app_initializer, "set_session_manager", lambda manager: set_sessions.append(manager))
    monkeypatch.setattr(app_initializer, "UploadHandler", UploadHandler)
    monkeypatch.setattr(app_initializer, "PersonalDocsManager", PersonalDocsManager)
    monkeypatch.setattr(app_initializer, "APIKeyManager", APIKeyManager)
    monkeypatch.setattr(app_initializer, "PresetManager", PresetManager)
    monkeypatch.setattr(app_initializer, "ChatProcessor", ChatProcessor)
    monkeypatch.setattr(app_initializer, "ResearchHandler", ResearchHandler)
    monkeypatch.setattr(app_initializer, "ChatHandler", ChatHandler)
    monkeypatch.setattr(app_initializer, "ModelDiscovery", ModelDiscovery)
    monkeypatch.setattr(app_initializer, "update_search_config", lambda **kwargs: search_keys.append(kwargs))

    class MemoryVectorStore:
        healthy = True
        rebuilt = []

        def __init__(self, data_dir, embedding_model=None):
            self.data_dir = data_dir
            self.embedding_model = embedding_model

        def count(self):
            return 0

        def rebuild(self, entries):
            self.rebuilt.append(entries)

    memory_vector_module = types.ModuleType("src.memory_vector")
    memory_vector_module.MemoryVectorStore = MemoryVectorStore
    monkeypatch.setitem(sys.modules, "src.memory_vector", memory_vector_module)
    rag = types.SimpleNamespace(_model="embed")

    managers = app_initializer.initialize_managers(str(tmp_path), rag_manager=rag)

    for path in dirs.values():
        assert path.exists()
    assert set_sessions
    assert managers["memory_vector"].embedding_model == "embed"
    assert MemoryVectorStore.rebuilt == [[{"id": "m1", "text": "remember"}]]
    assert search_keys == [{"api_key": "brave-key"}]
    assert managers["PERSONAL_INDEX"] == ["doc"]


def test_app_initializer_memory_vector_degraded_and_import_failure(monkeypatch, tmp_path):
    import src.app_initializer as app_initializer

    monkeypatch.setattr(app_initializer, "create_directories", lambda: None)
    monkeypatch.setattr(app_initializer, "MemoryManager", lambda data_dir: types.SimpleNamespace(load=lambda: []))
    monkeypatch.setattr(app_initializer, "SkillsManager", lambda data_dir: object())
    monkeypatch.setattr(app_initializer, "SessionManager", lambda sessions_file: object())
    monkeypatch.setattr(app_initializer, "set_session_manager", lambda manager: None)
    monkeypatch.setattr(app_initializer, "UploadHandler", lambda base_dir, upload_dir: object())
    monkeypatch.setattr(app_initializer, "PersonalDocsManager", lambda personal_dir, rag_manager: types.SimpleNamespace(index=[]))
    monkeypatch.setattr(app_initializer, "APIKeyManager", lambda data_dir: types.SimpleNamespace(load=lambda: {}))
    monkeypatch.setattr(app_initializer, "PresetManager", lambda data_dir: types.SimpleNamespace(presets={}))
    monkeypatch.setattr(app_initializer, "ChatProcessor", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_initializer, "ResearchHandler", lambda: object())
    monkeypatch.setattr(app_initializer, "ChatHandler", lambda **kwargs: object())
    monkeypatch.setattr(app_initializer, "ModelDiscovery", lambda host, key: object())

    class DegradedMemoryVector:
        healthy = False

        def __init__(self, *args, **kwargs):
            pass

    memory_vector_module = types.ModuleType("src.memory_vector")
    memory_vector_module.MemoryVectorStore = DegradedMemoryVector
    monkeypatch.setitem(sys.modules, "src.memory_vector", memory_vector_module)
    assert app_initializer.initialize_managers(str(tmp_path))["memory_vector"] is None

    class BrokenMemoryVector:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("vector down")

    memory_vector_module.MemoryVectorStore = BrokenMemoryVector
    assert app_initializer.initialize_managers(str(tmp_path))["memory_vector"] is None
