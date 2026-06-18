import pytest


@pytest.mark.asyncio
async def test_consolidate_memory_does_not_claim_or_delete_ownerless_for_authenticated_owner(monkeypatch):
    from src import builtin_actions
    import src.endpoint_resolver as endpoint_resolver
    import src.memory as memory_module

    class MemoryManager:
        saved = None

        def __init__(self, _data_dir):
            self.entries = [
                {"id": "alice", "text": "User likes local models.", "owner": "alice"},
                {"id": "legacy", "text": "User likes local models.", "owner": None},
                {"id": "bob", "text": "User likes local models.", "owner": "bob"},
            ]

        def load_all(self):
            return list(self.entries)

        def save(self, entries):
            MemoryManager.saved = list(entries)

    monkeypatch.setattr(memory_module, "MemoryManager", MemoryManager)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda *_args, **_kwargs: ("", "", {}))

    with pytest.raises(builtin_actions.TaskNoop) as exc:
        await builtin_actions.action_consolidate_memory("alice")

    assert "no duplicates" in str(exc.value)
    assert MemoryManager.saved is None
