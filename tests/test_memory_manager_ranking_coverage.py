import importlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("module_name", ["src.memory", "services.memory.memory"])
def test_memory_manager_file_lifecycle_and_basic_helpers(module_name, tmp_path):
    memory_mod = importlib.import_module(module_name)

    assert memory_mod.tokenize('Hello, "world"!') == ["Hello", "world"]
    assert memory_mod.get_text_similarity("", "anything") == 0.0
    assert memory_mod.get_text_similarity("alpha beta", "beta gamma") == pytest.approx(1 / 3)

    manager = memory_mod.MemoryManager(str(tmp_path))
    assert json.loads((tmp_path / "memory.json").read_text(encoding="utf-8")) == []

    raw_entries = [{"text": "User likes Python", "owner": "alice"}]
    (tmp_path / "memory.json").write_text(json.dumps(raw_entries), encoding="utf-8")
    loaded = manager.load_all()
    assert loaded[0]["text"] == "User likes Python"
    assert loaded[0]["source"] == "unknown"
    assert loaded[0]["category"] == "fact"
    assert "id" in loaded[0]
    if module_name == "src.memory":
        assert loaded[0]["uses"] == 0

    assert manager.load(owner="alice")[0]["text"] == "User likes Python"
    assert manager.load(owner="bob") == []

    saved = [{"text": "Saved fact"}]
    manager.save(saved)
    saved_on_disk = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    assert saved_on_disk[0]["text"] == "Saved fact"
    assert saved_on_disk[0]["source"] == "user"

    entry = manager.add_entry("  A durable memory  ", source="auto", category="identity", owner="alice")
    assert entry["text"] == "A durable memory"
    assert entry["owner"] == "alice"
    if module_name == "src.memory":
        assert entry["uses"] == 0
    with pytest.raises(ValueError):
        manager.add_entry("   ")

    entries = [{"text": "Same"}, {"text": "different"}]
    assert manager.find_duplicates(" same ", entries) == [{"text": "Same"}]

    extracted = manager.extract_memory_from_chat(
        [
            {"role": "user", "content": "ignore me"},
            {"role": "assistant", "content": "Memory notes\n1. Loves local AI\n---"},
        ],
        session_id="s1",
    )
    assert extracted[0]["text"] == "Loves local AI"
    assert extracted[0]["session_id"] == "s1"

    assert manager.process_inline_memory_command("remember: likes dark mode") == (
        True,
        "likes dark mode",
    )
    assert manager.process_inline_memory_command("just chatting") == (False, "")


def test_memory_manager_legacy_migration_owner_claims_and_uses(tmp_path):
    import services.memory.memory as service_memory
    import src.memory as src_memory

    service_manager = service_memory.MemoryManager(str(tmp_path / "service"))
    service_dir = tmp_path / "service"
    (service_dir / "memory.json").write_text("{bad json", encoding="utf-8")
    (service_dir / "memory.txt").write_text("Old memory\n\nSecond memory\n", encoding="utf-8")
    migrated = service_manager.load_all()
    assert [entry["text"] for entry in migrated] == ["Old memory", "Second memory"]

    service_manager.save([{"id": "one", "text": "Ownerless"}, {"id": "two", "text": "Owned", "owner": "bob"}])
    service_manager.claim_ownerless("alice")
    claimed = service_manager.load_all()
    assert next(entry for entry in claimed if entry["id"] == "one")["owner"] == "alice"
    assert next(entry for entry in claimed if entry["id"] == "two")["owner"] == "bob"

    empty_service = service_memory.MemoryManager(str(tmp_path / "empty-service"))
    (tmp_path / "empty-service" / "memory.json").write_text("{bad json", encoding="utf-8")
    assert empty_service.load_all() == []

    src_manager = src_memory.MemoryManager(str(tmp_path / "src"))
    src_manager.save([{"id": "a", "text": "A", "uses": 1}, {"id": "b", "text": "B"}])
    src_manager.increment_uses([])
    src_manager.increment_uses(["a", "b", "missing"])
    src_entries = {entry["id"]: entry for entry in src_manager.load_all()}
    assert src_entries["a"]["uses"] == 2
    assert src_entries["b"]["uses"] == 1


@pytest.mark.parametrize("module_name", ["src.memory", "services.memory.memory"])
def test_memory_manager_relevance_scoring(module_name, tmp_path):
    memory_mod = importlib.import_module(module_name)
    manager = memory_mod.MemoryManager(str(tmp_path))

    memories = [
        {"id": "identity", "text": "My name is Ada Lovelace"},
        {"id": "contact", "text": "Work email is ada@example.com"},
        {"id": "pref", "text": "User likes local offline AI"},
        {"id": "task", "text": "Todo remind me about the meeting"},
        {"id": "fact", "text": "Python project uses FastAPI"},
        {"id": "irrelevant", "text": "The garden has tomatoes"},
    ]

    categorized = manager.categorize_memory_by_relevance("What email contact should I use?", memories)
    assert categorized["contacts"] == [memories[1]]
    categorized = manager.categorize_memory_by_relevance("What does the user like?", memories)
    assert categorized["preferences"] == [memories[2]]
    categorized = manager.categorize_memory_by_relevance("schedule a reminder task", memories)
    assert categorized["tasks"] == [memories[3]]
    categorized = manager.categorize_memory_by_relevance("FastAPI Python project", memories)
    assert categorized["facts"] == [memories[4]]

    assert manager.get_relevant_memories("", memories) == []
    identity = manager.get_relevant_memories("what is my name", memories)
    assert identity[0]["id"] == "identity"
    contact = manager.get_relevant_memories("contact email", memories, threshold=0.01)
    assert contact[0]["id"] == "contact"
    pref = manager.get_relevant_memories("favorite local AI", memories, threshold=0.01)
    assert pref[0]["id"] == "pref"
    task = manager.get_relevant_memories("remind meeting schedule", memories, threshold=0.01)
    assert task[0]["id"] == "task"
    exact = manager.get_relevant_memories("Python project uses FastAPI", memories, threshold=0.5)
    assert exact[0]["id"] == "fact"
    limited = manager.get_relevant_memories("AI project email meeting name", memories, threshold=0.01, max_items=2)
    assert len(limited) == 2


@pytest.mark.parametrize("module_name", ["src.search.ranking", "services.search.ranking"])
def test_search_ranking_news_sports_domains_and_recency(module_name, monkeypatch):
    ranking = importlib.import_module(module_name)
    recent = datetime.now() - timedelta(days=2)
    mid = datetime.now() - timedelta(days=12)
    old = datetime.now() - timedelta(days=60)

    results = [
        {
            "title": "NBA latest sports news",
            "snippet": "NBA sports latest news and championship headlines",
            "url": "https://www.nba.com/news/story",
            "age": recent.strftime("%Y-%m-%d"),
        },
        {
            "title": "Canada latest news",
            "snippet": "Breaking news from Canada with daily coverage",
            "url": "https://www.reuters.com/world/canada",
            "age": recent.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        {
            "title": "Canada policy archive",
            "snippet": "Canada policy background",
            "url": "https://example.edu/policy",
            "age": old.strftime("%Y-%m-%d %H:%M:%S"),
        },
        {
            "title": "",
            "snippet": "",
            "url": "not a url",
            "age": "unknown",
        },
        {
            "title": "Canada nonprofit update",
            "snippet": "Canada community update",
            "url": "https://example.org/news",
            "age": mid.strftime("%Y-%m-%d"),
        },
    ]

    ranked = ranking.rank_search_results("Canada latest news", results)
    assert ranked[0]["url"].startswith("https://www.reuters.com")
    assert ranked[-1]["url"] == "not a url"
    assert ranking._domain("https://Example.ORG/path") == "example.org"
    assert ranking._domain(None) in ("", b"")
    with monkeypatch.context() as scoped:
        scoped.setattr(
            ranking,
            "urlparse",
            lambda _url: (_ for _ in ()).throw(ValueError("bad url")),
        )
        assert ranking._domain("bad") == ""

    low_value_ranked = ranking.rank_search_results(
        "Canada latest news",
        [
            {
                "title": "Canada latest news",
                "snippet": "Canada latest news daily coverage",
                "url": "https://sports.yahoo.com/story",
                "age": recent.strftime("%Y-%m-%d"),
            },
            {
                "title": "Canada latest news",
                "snippet": "Canada latest news daily coverage",
                "url": "https://www.reuters.com/world/canada",
                "age": recent.strftime("%Y-%m-%d"),
            },
        ],
    )
    assert low_value_ranked[-1]["url"] == "https://sports.yahoo.com/story"

    sports_ranked = ranking.rank_search_results("NBA latest sports news", results)
    assert sports_ranked[0]["url"] == "https://www.nba.com/news/story"
    assert ranking.rank_search_results("", [{"title": "Only", "snippet": "", "url": "", "age": None}])[0]["title"] == "Only"


class EncodedVectors(list):
    def tolist(self):
        return list(self)


class FakeEmbeddingModel:
    url = "fake://embeddings"

    def __init__(self):
        self.calls = []

    def encode(self, texts, normalize_embeddings=True):
        self.calls.append((list(texts), normalize_embeddings))
        return EncodedVectors([[float(len(text)), 1.0] for text in texts])


class FakeCollection:
    def __init__(self, *, fail_delete=False):
        self.items = {}
        self.add_calls = []
        self.deleted_ids = []
        self.fail_delete = fail_delete
        self.query_ids = ["m1", "m2"]
        self.query_distances = [0.1, 0.33333]

    def count(self):
        return len(self.items)

    def get(self, ids):
        return {"ids": [mid for mid in ids if mid in self.items]}

    def add(self, ids, embeddings, documents, metadatas):
        self.add_calls.append((list(ids), list(embeddings), list(documents), list(metadatas)))
        for mid, embedding, document, metadata in zip(ids, embeddings, documents, metadatas):
            self.items[mid] = {
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }

    def delete(self, ids):
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.deleted_ids.extend(ids)
        for mid in ids:
            self.items.pop(mid, None)

    def query(self, query_embeddings, n_results):
        return {
            "ids": [self.query_ids[:n_results]],
            "distances": [self.query_distances[:n_results]],
        }


class FakeChromaClient:
    def __init__(self):
        self.collection = FakeCollection()
        self.deleted = []

    def get_or_create_collection(self, name, metadata=None):
        self.last_collection_name = name
        self.last_metadata = metadata
        if self.deleted:
            self.collection = FakeCollection()
        return self.collection

    def delete_collection(self, name):
        self.deleted.append(name)


@pytest.mark.parametrize("module_name", ["src.memory_vector", "services.memory.memory_vector"])
def test_memory_vector_store_lifecycle_search_and_rebuild(module_name, monkeypatch, tmp_path):
    import src.chroma_client as chroma_client
    import src.embeddings as embeddings

    vector_mod = importlib.import_module(module_name)
    client = FakeChromaClient()
    model = FakeEmbeddingModel()
    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: client)
    monkeypatch.setattr(embeddings, "get_embedding_client", lambda: model)

    store = vector_mod.MemoryVectorStore(str(tmp_path))
    assert store.healthy is True
    assert store.count() == 0
    assert client.last_collection_name == "cleverly_memories"
    assert client.last_metadata == {"hnsw:space": "cosine"}

    store.add("m1", "alpha memory")
    store.add("m1", "duplicate ignored")
    assert store.count() == 1
    assert client.collection.add_calls[0][0] == ["m1"]
    assert model.calls[0] == (["alpha memory"], True)

    client.collection.add(["m2"], [[2.0, 1.0]], ["beta memory"], [{"source": "memory"}])
    assert store.search("alpha", k=8) == [
        {"memory_id": "m1", "score": 0.9},
        {"memory_id": "m2", "score": 0.6667},
    ]
    assert store.find_similar("alpha duplicate", threshold=0.8) == "m1"
    assert store.find_similar("alpha duplicate", threshold=0.95) is None

    store.remove("m2")
    assert client.collection.deleted_ids == ["m2"]
    client.collection.fail_delete = True
    store.remove("missing")

    memories = [{"id": f"id{i}", "text": f"text {i}"} for i in range(105)]
    memories += [{"id": "blank", "text": "  "}, {"id": "", "text": "missing id"}]
    store.rebuild(memories)
    assert client.deleted == ["cleverly_memories"]
    rebuilt_calls = client.collection.add_calls
    assert len(rebuilt_calls) == 2
    assert len(rebuilt_calls[0][0]) == 100
    assert len(rebuilt_calls[1][0]) == 5

    monkeypatch.setattr(client, "delete_collection", lambda name: (_ for _ in ()).throw(RuntimeError("missing")))
    store.rebuild([])
    assert client.last_collection_name == "cleverly_memories"


@pytest.mark.parametrize("module_name", ["src.memory_vector", "services.memory.memory_vector"])
def test_memory_vector_store_unhealthy_paths(module_name, monkeypatch, tmp_path):
    import src.chroma_client as chroma_client
    import src.embeddings as embeddings

    vector_mod = importlib.import_module(module_name)
    monkeypatch.setattr(embeddings, "get_embedding_client", lambda: None)
    monkeypatch.setattr(
        chroma_client,
        "get_chroma_client",
        lambda: (_ for _ in ()).throw(RuntimeError("chroma down")),
    )

    store = vector_mod.MemoryVectorStore(str(tmp_path), embedding_model=None)
    assert store.healthy is False
    assert store.count() == 0
    assert store.search("anything") == []
    assert store.find_similar("anything") is None
    store.add("id", "text")
    store.remove("id")
    store.rebuild([{"id": "id", "text": "text"}])
