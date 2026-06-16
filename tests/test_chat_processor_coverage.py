import time
from types import SimpleNamespace


class MemoryManager:
    def __init__(self, entries):
        self.entries = entries
        self.incremented = []

    def load(self, owner=None):
        self.owner = owner
        return list(self.entries)

    def increment_uses(self, ids):
        self.incremented.append(list(ids))


class RagManager:
    def __init__(self, results=None, error=False):
        self.results = results or []
        self.error = error

    def search(self, message, k=5, owner=None):
        if self.error:
            raise RuntimeError("rag down")
        self.call = (message, k, owner)
        return list(self.results)


class Vector:
    healthy = True

    def __init__(self, results):
        self.results = results

    def search(self, message, k=15):
        self.call = (message, k)
        return list(self.results)


def test_content_tokens_and_hybrid_retrieve_keyword_vector_and_categories(monkeypatch):
    import src.chat_processor as cp

    monkeypatch.setattr(cp.time, "time", lambda: 100000.0)
    assert cp._content_tokens("Hello, my favorite llama-code thing!") == ["favorite", "llama-code"]

    entries = [
        {"id": "identity", "text": "My name is Ada Lovelace", "category": "identity", "timestamp": 99999.0},
        {"id": "contact", "text": "Email me at ada@example.com", "category": "contact", "timestamp": 1.0},
        {"id": "pref", "text": "I prefer local models", "category": "preference", "timestamp": 1.0},
        {"id": "noise", "text": "Unrelated cooking notes", "category": "fact", "timestamp": 99999.0},
    ]
    processor = cp.ChatProcessor(MemoryManager(entries), SimpleNamespace())
    identity = processor._hybrid_retrieve("what is my name?", entries, k=2)
    assert identity[0]["id"] == "identity"
    contact = processor._hybrid_retrieve("what is my email address?", entries, k=2)
    assert contact[0]["id"] == "contact"
    pref = processor._hybrid_retrieve("what model do I prefer?", entries, k=2)
    assert pref[0]["id"] == "pref"

    vector = Vector([{"memory_id": "noise", "score": 0.9}, {"memory_id": "pref", "score": 0.4}])
    processor = cp.ChatProcessor(MemoryManager(entries), SimpleNamespace(), memory_vector=vector)
    vector_only = processor._hybrid_retrieve("and the of", entries, k=1)
    assert vector_only == [entries[3]]
    assert vector.call == ("and the of", 3)

    assert processor._hybrid_retrieve("hello", [], k=3) == []
    assert cp.ChatProcessor(MemoryManager(entries), SimpleNamespace())._hybrid_retrieve("and the", entries) == []


def test_build_context_preface_injects_memory_rag_web_urls_and_skills(monkeypatch):
    import src.chat_processor as cp

    memories = [
        {"id": "pinned", "text": "User is Ada", "category": "identity", "pinned": True, "timestamp": time.time()},
        {"id": "extended", "text": "Ada likes local vector search", "category": "preference", "timestamp": time.time()},
    ]
    memory = MemoryManager(memories)
    rag_results = [
        {"similarity": 0.9, "metadata": {"filename": "guide.md"}, "document": "Doc body about local vector search"},
        {"similarity": 0.1, "metadata": {"filename": "low.md"}, "document": "Low score"},
    ]
    personal = SimpleNamespace(rag_manager=RagManager(rag_results))
    skills = SimpleNamespace(index_for=lambda owner=None: [
        {"name": "deploy", "description": "Deploy locally", "category": "ops"},
        {"name": "review", "description": "", "category": "code"},
    ])
    processor = cp.ChatProcessor(memory, personal, skills_manager=skills)

    monkeypatch.setattr(cp, "comprehensive_web_search", lambda message, time_filter=None, return_sources=False: ("web context", [{"url": "u"}]))
    monkeypatch.setattr(cp, "fetch_webpage_content", lambda url: {"success": True, "content": "page content"})
    monkeypatch.setattr(cp, "extract_urls", lambda message: ["https://example.com", "https://youtube.com/watch?v=x"])

    preface, rag_sources, web_sources = processor.build_context_preface(
        "Find local vector search at https://example.com",
        session=SimpleNamespace(),
        use_web=True,
        use_rag=True,
        use_memory=True,
        time_filter="week",
        preset_system_prompt="Preset prompt",
        owner="alice",
        character_name="Ada",
        agent_mode=True,
    )

    joined = "\n".join(item["content"] for item in preface)
    assert "Preset prompt" in joined
    assert "Core facts about the user" in joined
    assert "Ada likes local vector search" in joined
    assert "Relevant documents" in joined
    assert "web context" in joined
    assert "page content" in joined
    assert "Available skills" in joined
    assert rag_sources == [{"filename": "guide.md", "snippet": "Doc body about local vector search", "similarity": 0.9}]
    assert web_sources == [{"url": "u"}]
    assert memory.incremented == [["pinned", "extended"]]


def test_build_context_preface_error_and_skip_branches(monkeypatch):
    import src.chat_processor as cp

    memory = MemoryManager([{"id": "p", "text": "Pinned", "pinned": True}])
    memory.increment_uses = lambda ids: (_ for _ in ()).throw(RuntimeError("counter down"))
    personal = SimpleNamespace(rag_manager=RagManager(error=True))
    processor = cp.ChatProcessor(memory, personal, skills_manager=SimpleNamespace(index_for=lambda owner=None: (_ for _ in ()).throw(RuntimeError("skills down"))))

    monkeypatch.setattr(cp, "comprehensive_web_search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("web down")))
    monkeypatch.setattr(cp, "extract_urls", lambda message: ["https://a.com", "https://b.com", "https://c.com", "https://d.com"])
    monkeypatch.setattr(cp, "fetch_webpage_content", lambda url: (_ for _ in ()).throw(AssertionError("should skip")))

    preface, rag_sources, web_sources = processor.build_context_preface(
        "x" * 2500,
        session=SimpleNamespace(),
        use_web=True,
        use_rag=True,
        use_memory=True,
        agent_mode=True,
        incognito=True,
    )

    joined = "\n".join(item["content"] for item in preface)
    assert "Web search encountered an error" in joined
    assert "Pinned" in joined
    assert "Available skills" not in joined
    assert rag_sources == []
    assert web_sources == []

    no_context = processor.build_context_preface("hello", SimpleNamespace(), use_web=False, use_rag=False, use_memory=False)
    assert len(no_context[0]) == 1
