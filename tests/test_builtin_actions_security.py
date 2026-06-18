import pytest
import sys
import types
from datetime import datetime, timezone


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


@pytest.mark.asyncio
async def test_ping_notes_does_not_dispatch_ownerless_notes_for_authenticated_owner(monkeypatch, tmp_path):
    from src import builtin_actions

    monkeypatch.chdir(tmp_path)

    class Column:
        def __init__(self, name):
            self.name = name

        def __eq__(self, value):
            return ("eq", self.name, value)

        def __ne__(self, value):
            return ("ne", self.name, value)

        def isnot(self, value):
            return ("isnot", self.name, value)

    class Note:
        archived = Column("archived")
        due_date = Column("due_date")

        def __init__(self, note_id, title, owner):
            self.id = note_id
            self.title = title
            self.owner = owner
            self.archived = False
            self.due_date = datetime.now(timezone.utc).isoformat()
            self.content = ""
            self.items = ""

    class Query:
        def __init__(self, notes):
            self.notes = list(notes)

        def filter(self, *exprs):
            for expr in exprs:
                if not isinstance(expr, tuple) or len(expr) < 3:
                    continue
                op, name, value = expr[:3]
                if op == "eq":
                    self.notes = [note for note in self.notes if getattr(note, name) == value]
                elif op == "ne":
                    self.notes = [note for note in self.notes if getattr(note, name) != value]
                elif op == "isnot":
                    self.notes = [note for note in self.notes if getattr(note, name) is not value]
            return self

        def all(self):
            return list(self.notes)

    class Db:
        def __init__(self):
            self.notes = [
                Note("alice-note", "Alice reminder", "alice"),
                Note("legacy-note", "Legacy reminder", None),
                Note("bob-note", "Bob reminder", "bob"),
            ]
            self.closed = False

        def query(self, _model):
            return Query(self.notes)

        def close(self):
            self.closed = True

    def scoped_owner_filter(query, _model, user, *, include_shared=True):
        if include_shared:
            query.notes = [note for note in query.notes if note.owner in (user, None)]
        else:
            query.notes = [note for note in query.notes if note.owner == user]
        return query

    dispatched = []

    async def dispatch_reminder(**kwargs):
        dispatched.append(kwargs)

    db = Db()
    core_database = types.ModuleType("core.database")
    core_database.SessionLocal = lambda: db
    core_database.Note = Note
    note_routes = types.ModuleType("routes.note_routes")
    note_routes.dispatch_reminder = dispatch_reminder
    monkeypatch.setitem(sys.modules, "core.database", core_database)
    monkeypatch.setitem(sys.modules, "routes.note_routes", note_routes)
    monkeypatch.setattr(builtin_actions, "owner_filter", scoped_owner_filter)

    result, ok = await builtin_actions.action_ping_notes("alice")

    assert ok is True
    assert result == "Pinged 1 note(s): Alice reminder"
    assert [item["note_id"] for item in dispatched] == ["alice-note"]
    assert dispatched[0]["owner"] == "alice"
    assert db.closed is True


@pytest.mark.asyncio
async def test_shell_actions_block_network_commands_in_offline_mode(monkeypatch):
    from src import builtin_actions

    calls = []

    async def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return "ran", True

    monkeypatch.setattr(builtin_actions, "offline_mode", lambda: True)
    monkeypatch.setattr(builtin_actions, "_run_subprocess", fake_run)

    assert await builtin_actions.action_run_local("alice", script="curl https://example.com") == (
        "Network shell commands are disabled",
        False,
    )
    assert await builtin_actions.action_run_script("alice", script="echo ok", host="user@example.test") == (
        "Remote scripts are disabled",
        False,
    )
    assert await builtin_actions.action_ssh_command("alice", command="echo ok", host="user@example.test") == (
        "Remote shell commands are disabled",
        False,
    )
    assert await builtin_actions.action_ssh_command("alice", command="git fetch origin", host="localhost") == (
        "Network shell commands are disabled",
        False,
    )

    assert calls == []


@pytest.mark.asyncio
async def test_shell_actions_block_network_when_integrations_disabled(monkeypatch):
    from src import builtin_actions

    calls = []

    async def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return "ran", True

    monkeypatch.setattr(builtin_actions, "offline_mode", lambda: False)
    monkeypatch.setattr(builtin_actions, "load_features", lambda: {"network_integrations": False})
    monkeypatch.setattr(builtin_actions, "_run_subprocess", fake_run)

    assert await builtin_actions.action_run_local("alice", script="curl https://example.com") == (
        "Network shell commands are disabled",
        False,
    )
    assert await builtin_actions.action_run_script("alice", script="echo ok", host="user@example.test") == (
        "Remote scripts are disabled",
        False,
    )
    assert await builtin_actions.action_ssh_command("alice", command="echo ok", host="user@example.test") == (
        "Remote shell commands are disabled",
        False,
    )

    assert await builtin_actions.action_run_local("alice", script="echo ok") == ("ran", True)
    assert len(calls) == 1

    monkeypatch.setattr(
        builtin_actions,
        "load_features",
        lambda: (_ for _ in ()).throw(RuntimeError("settings unavailable")),
    )
    assert await builtin_actions.action_run_local("alice", script="curl https://example.com") == (
        "Network shell commands are disabled",
        False,
    )
    assert len(calls) == 1
