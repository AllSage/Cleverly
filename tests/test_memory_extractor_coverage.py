import builtins
import json
from types import SimpleNamespace

import pytest


class FakeSession:
    def __init__(self, messages, *, owner="alice", session_id="sid1", name="Session Name"):
        self._messages = messages
        self.owner = owner
        self.session_id = session_id
        self.name = name

    def get_context_messages(self):
        return list(self._messages)


class FakeMemoryManager:
    def __init__(self, path, entries=None):
        self.memory_file = str(path)
        self.entries = list(entries or [])
        self.saved_payloads = []
        self.added = []
        self.duplicate_texts = set()

    def load_all(self):
        return list(self.entries)

    def load(self, owner=None):
        if owner is None:
            return list(self.entries)
        return [e for e in self.entries if e.get("owner") == owner or e.get("owner") is None]

    def save(self, entries):
        self.entries = list(entries)
        self.saved_payloads.append(list(entries))

    def add_entry(self, text, source="auto", category="fact", owner=None):
        entry = {
            "id": f"m{len(self.entries) + 1}",
            "text": text,
            "source": source,
            "category": category,
            "owner": owner,
        }
        self.entries.append(entry)
        self.added.append(entry)
        return entry

    def find_duplicates(self, text, existing):
        return text in self.duplicate_texts or any(e.get("text") == text for e in existing)


class FakeVector:
    def __init__(self, *, healthy=True, similar_id=None):
        self.healthy = healthy
        self.similar_id = similar_id
        self.added = []
        self.rebuilt = []

    def find_similar(self, _text, threshold=0.72):
        return self.similar_id

    def add(self, entry_id, text):
        self.added.append((entry_id, text))

    def rebuild(self, entries):
        self.rebuilt.append(list(entries))


@pytest.mark.parametrize(
    ("message", "expected_text", "expected_role"),
    [
        (SimpleNamespace(role="User", content="  hello  "), "hello", "user"),
        ({"role": "assistant", "content": [{"text": "one"}, {"content": "two"}, "three"]}, "one two three", "assistant"),
        ({"content": None}, "", ""),
    ],
)
def test_memory_extractor_helpers_and_fallbacks(tmp_path, message, expected_text, expected_role):
    from services.memory import memory_extractor as me

    manager = FakeMemoryManager(tmp_path / "memory.json")
    assert me._tidy_state_path(manager).endswith("memory_tidy_state.json")
    assert me._message_text(message) == expected_text
    assert me._message_role(message) == expected_role

    one = [{"id": "b", "text": "Beta", "category": "fact"}, {"id": "a", "text": "Alpha", "category": "identity"}]
    two = list(reversed(one))
    assert me._fingerprint_entries(one) == me._fingerprint_entries(two)

    assert me._clean_memory_value("  the Austin!  ") == "Austin"
    assert me._clean_memory_value("https://example.com") == ""
    assert me._clean_memory_value("x" * 90) == ""
    assert me._is_text_duplicate("User likes Python", [{"text": "User likes Python a lot"}])
    assert not me._is_text_duplicate("", [{"text": "anything"}])

    facts = me._fallback_memory_candidates(
        [
            {"role": "user", "content": "My name is Ada\nI live in Austin."},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Call me Sage. I love quiet local tools."},
        ]
    )
    assert facts[:2] == [
        {"text": "User's name is Ada", "category": "identity"},
        {"text": "User lives in Austin", "category": "identity"},
    ]

    state_file = tmp_path / "memory_tidy_state.json"
    state_file.write_text("{bad json", encoding="utf-8")
    assert me._load_tidy_state(manager) == {}
    me._save_tidy_state(manager, "alice", "abc")
    assert json.loads(state_file.read_text(encoding="utf-8"))["alice"]["fingerprint"] == "abc"


@pytest.mark.asyncio
async def test_extract_and_store_uses_llm_fallback_dedup_and_events(monkeypatch, tmp_path):
    from services.memory import memory_extractor as me
    import src.event_bus as event_bus
    import src.llm_core as llm_core

    calls = []

    async def fake_llm_call_async(*_args, **_kwargs):
        calls.append((_args, _kwargs))
        return "```json\n[{\"text\":\"User works on Cleverly.\",\"category\":\"project\"}, \"User likes local AI.\"]\n```"

    fired = []
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)
    monkeypatch.setattr(event_bus, "fire_event", lambda name, owner: fired.append((name, owner)))
    monkeypatch.setattr(me, "_extractions_since_audit", 0)

    manager = FakeMemoryManager(tmp_path / "memory.json")
    vector = FakeVector()
    session = FakeSession(
        [
            {"role": "user", "content": "My name is Ada and I prefer offline tools."},
            {"role": "assistant", "content": "Noted."},
        ],
        session_id="session-1",
    )

    await me.extract_and_store(session, manager, vector, "http://local", "model", {"H": "1"})

    texts = [entry["text"] for entry in manager.entries]
    assert "User works on Cleverly." in texts
    assert "User likes local AI." in texts
    assert any(entry["pinned"] is True for entry in manager.entries if entry["category"] == "identity")
    assert len(vector.added) == len(manager.entries)
    assert fired == [("memory_added", "alice")] * len(manager.entries)

    duplicate_manager = FakeMemoryManager(tmp_path / "memory.json", entries=[{"id": "old", "text": "User works on Cleverly.", "owner": "alice"}])
    duplicate_manager.duplicate_texts.add("User works on Cleverly.")
    duplicate_vector = FakeVector(healthy=True, similar_id="old")
    await me.extract_and_store(session, duplicate_manager, duplicate_vector, "http://local", "model")
    assert duplicate_vector.added == []


@pytest.mark.asyncio
async def test_extract_and_store_fallback_and_noop_paths(monkeypatch, tmp_path):
    from services.memory import memory_extractor as me
    import src.llm_core as llm_core

    async def failing_llm(*_args, **_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(llm_core, "llm_call_async", failing_llm)
    manager = FakeMemoryManager(tmp_path / "memory.json")
    session = FakeSession(
        [
            {"role": "user", "content": "I want to travel to Tokyo."},
            {"role": "assistant", "content": "Good idea."},
        ]
    )
    await me.extract_and_store(session, manager, FakeVector(healthy=False), "url", "model")
    assert manager.entries[0]["text"] == "User wants to visit Tokyo"

    short_session = FakeSession([{"role": "user", "content": "My name is Ada"}])
    await me.extract_and_store(short_session, manager, None, "url", "model")
    assert len(manager.entries) == 1

    async def bad_json(*_args, **_kwargs):
        return "not json"

    monkeypatch.setattr(llm_core, "llm_call_async", bad_json)
    empty_manager = FakeMemoryManager(tmp_path / "memory.json")
    await me.extract_and_store(
        FakeSession([{"role": "user", "content": "temporary question"}, {"role": "assistant", "content": "answer"}]),
        empty_manager,
        None,
        "url",
        "model",
    )
    assert empty_manager.entries == []


@pytest.mark.asyncio
async def test_memory_extractor_remaining_parse_dedupe_and_audit_edges(monkeypatch, tmp_path):
    from services.memory import memory_extractor as me
    import src.event_bus as event_bus
    import src.llm_core as llm_core

    manager = FakeMemoryManager(tmp_path / "memory.json")
    real_open = builtins.open

    def failing_state_open(path, mode="r", *args, **kwargs):
        if str(path).endswith("memory_tidy_state.json") and "w" in mode:
            raise OSError("readonly")
        return real_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as state_patch:
        state_patch.setattr(builtins, "open", failing_state_open)
        me._save_tidy_state(manager, "alice", "abc")

    assert me._fallback_memory_candidates(
        [
            {"role": "assistant", "content": "ignored"},
            {"role": "user", "content": f"My name is {'A' * 51}"},
            {"role": "user", "content": "My name is Ada"},
            {"role": "user", "content": "My name is Ada"},
            {"role": "user", "content": ""},
        ]
    ) == [{"text": "User's name is Ada", "category": "identity"}]
    assert not me._is_text_duplicate("new fact", [{"text": ""}])

    async def dict_llm(*_args, **_kwargs):
        return '{"text":"not a list"}'

    monkeypatch.setattr(llm_core, "llm_call_async", dict_llm)
    dict_manager = FakeMemoryManager(tmp_path / "dict-memory.json")
    await me.extract_and_store(
        FakeSession([{"role": "user", "content": "temporary"}, {"role": "assistant", "content": "reply"}]),
        dict_manager,
        None,
        "url",
        "model",
    )
    assert dict_manager.entries == []

    async def mixed_llm(*_args, **_kwargs):
        return json.dumps(
            [
                123,
                {"text": "abc", "category": "fact"},
                {"text": "", "category": "fact"},
                {"text": "User works on Cleverly.", "category": "project"},
            ]
        )

    monkeypatch.setattr(llm_core, "llm_call_async", mixed_llm)
    mixed_manager = FakeMemoryManager(tmp_path / "mixed-memory.json")
    await me.extract_and_store(
        FakeSession([{"role": "user", "content": "remember this"}, {"role": "assistant", "content": "ok"}]),
        mixed_manager,
        None,
        "url",
        "model",
    )
    assert [entry["text"] for entry in mixed_manager.entries] == ["User works on Cleverly."]

    duplicate_manager = FakeMemoryManager(tmp_path / "duplicate-memory.json")
    duplicate_manager.duplicate_texts.add("User works on Cleverly.")
    await me.extract_and_store(
        FakeSession([{"role": "user", "content": "remember this"}, {"role": "assistant", "content": "ok"}]),
        duplicate_manager,
        None,
        "url",
        "model",
    )
    assert duplicate_manager.entries == []

    fuzzy_manager = FakeMemoryManager(
        tmp_path / "fuzzy-memory.json",
        entries=[{"id": "old", "text": "User works on Cleverly", "owner": "alice"}],
    )
    await me.extract_and_store(
        FakeSession([{"role": "user", "content": "remember this"}, {"role": "assistant", "content": "ok"}]),
        fuzzy_manager,
        None,
        "url",
        "model",
    )
    assert len(fuzzy_manager.entries) == 1

    class NameOnlySession:
        owner = "alice"
        name = "Named Session"

        def get_context_messages(self):
            return [{"role": "user", "content": "remember this"}, {"role": "assistant", "content": "ok"}]

    event_manager = FakeMemoryManager(tmp_path / "event-memory.json")
    monkeypatch.setattr(event_bus, "fire_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event down")))
    await me.extract_and_store(NameOnlySession(), event_manager, None, "url", "model")
    assert event_manager.entries[0]["session_id"] == "Named Session"

    async def audit_stub(*_args, **_kwargs):
        audit_stub.calls.append((_args, _kwargs))
        return {"before": 1, "after": 1}

    audit_stub.calls = []
    threshold_manager = FakeMemoryManager(tmp_path / "threshold-memory.json")
    monkeypatch.setattr(me, "_extractions_since_audit", me.AUDIT_INTERVAL - 1)
    monkeypatch.setattr(me, "audit_memories", audit_stub)
    monkeypatch.setattr(event_bus, "fire_event", lambda *_args, **_kwargs: None)
    await me.extract_and_store(
        FakeSession([{"role": "user", "content": "remember this"}, {"role": "assistant", "content": "ok"}]),
        threshold_manager,
        None,
        "url",
        "model",
    )
    assert audit_stub.calls

    class BrokenSession:
        def get_context_messages(self):
            raise RuntimeError("session broke")

    await me.extract_and_store(BrokenSession(), threshold_manager, None, "url", "model")


@pytest.mark.asyncio
async def test_audit_memories_success_skip_bad_json_and_safety(monkeypatch, tmp_path):
    from services.memory import memory_extractor as me
    import src.llm_core as llm_core

    entries = [
        {"id": "a", "text": "User likes Python.", "category": "preference", "owner": "alice", "keep": True},
        {"id": "b", "text": "User lives in Austin.", "category": "identity", "owner": "alice"},
        {"id": "legacy", "text": "Legacy fact.", "category": "fact", "owner": None},
        {"id": "other", "text": "Bob fact.", "category": "fact", "owner": "bob"},
    ]
    manager = FakeMemoryManager(tmp_path / "memory.json", entries=entries)
    vector = FakeVector()

    async def clean_llm(*_args, **_kwargs):
        return (
            "```json\n"
            "[{\"id\":\"a\",\"text\":\"User prefers Python.\",\"category\":\"preference\"},"
            "{\"id\":\"missing\",\"text\":\"Invented\",\"category\":\"fact\"}]"
            "\n```"
        )

    monkeypatch.setattr(llm_core, "llm_call_async", clean_llm)
    result = await me.audit_memories(manager, vector, "url", "model", owner="alice")
    assert result == {"before": 3, "after": 1}
    assert manager.entries[0]["text"] == "User prefers Python."
    assert manager.entries[0]["keep"] is True
    assert any(entry["id"] == "other" for entry in manager.entries)
    assert vector.rebuilt[0][0]["id"] == "a"

    tidy_manager = FakeMemoryManager(tmp_path / "memory.json", entries=[{"id": "a", "text": "User prefers Python.", "category": "preference"}])
    fp = me._fingerprint_entries(tidy_manager.entries)
    me._save_tidy_state(tidy_manager, None, fp)
    assert await me.audit_memories(tidy_manager, None, "url", "model") == {
        "before": 1,
        "after": 1,
        "already_tidy": True,
    }

    empty_manager = FakeMemoryManager(tmp_path / "memory.json", entries=[])
    assert await me.audit_memories(empty_manager, None, "url", "model") == {"before": 0, "after": 0}

    async def bad_llm(*_args, **_kwargs):
        return "<think>noise</think> nope"

    monkeypatch.setattr(llm_core, "llm_call_async", bad_llm)
    bad_manager = FakeMemoryManager(tmp_path / "memory.json", entries=[{"id": "a", "text": "A", "category": "fact"}])
    assert (await me.audit_memories(bad_manager, None, "url", "model"))["error"] == "bad_json"

    many_entries = [{"id": f"m{i}", "text": f"Fact {i}", "category": "fact"} for i in range(8)]
    unsafe_manager = FakeMemoryManager(tmp_path / "memory.json", entries=many_entries)

    async def unsafe_llm(*_args, **_kwargs):
        return '[{"id":"m1","text":"Only one survives","category":"fact"}]'

    monkeypatch.setattr(llm_core, "llm_call_async", unsafe_llm)
    unsafe = await me.audit_memories(unsafe_manager, None, "url", "model")
    assert unsafe == {"before": 8, "after": 8, "error": "unsafe_removal"}
    assert unsafe_manager.entries == many_entries

    async def exploding_llm(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_core, "llm_call_async", exploding_llm)
    assert "boom" in (await me.audit_memories(bad_manager, None, "url", "model"))["error"]


@pytest.mark.asyncio
async def test_audit_memories_parse_cleanup_ownerless_and_error_edges(monkeypatch, tmp_path):
    from services.memory import memory_extractor as me
    import src.llm_core as llm_core

    async def empty_llm(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(llm_core, "llm_call_async", empty_llm)
    empty_raw_manager = FakeMemoryManager(tmp_path / "empty-raw-memory.json", entries=[{"id": "a", "text": "A", "category": "fact"}])
    assert (await me.audit_memories(empty_raw_manager, None, "url", "model"))["error"] == "bad_json"

    async def bracket_llm(*_args, **_kwargs):
        return 'notes before [{"id":"a","text":"User prefers compact tests.","category":"preference"}] after'

    monkeypatch.setattr(llm_core, "llm_call_async", bracket_llm)
    bracket_manager = FakeMemoryManager(
        tmp_path / "bracket-memory.json",
        entries=[{"id": "a", "text": "User likes tests.", "category": "fact"}],
    )
    assert await me.audit_memories(bracket_manager, None, "url", "model") == {"before": 1, "after": 1}
    assert bracket_manager.entries == [{"id": "a", "text": "User prefers compact tests.", "category": "preference"}]

    async def noisy_list_llm(*_args, **_kwargs):
        return json.dumps(
            [
                "skip me",
                {"id": "a", "text": "", "category": "fact"},
                {"id": "a", "text": "User keeps local notes.", "category": "fact"},
            ]
        )

    monkeypatch.setattr(llm_core, "llm_call_async", noisy_list_llm)
    noisy_manager = FakeMemoryManager(
        tmp_path / "noisy-memory.json",
        entries=[{"id": "a", "text": "User writes notes.", "category": "fact"}],
    )
    assert await me.audit_memories(noisy_manager, None, "url", "model") == {"before": 1, "after": 1}
    assert noisy_manager.entries[0]["text"] == "User keeps local notes."
