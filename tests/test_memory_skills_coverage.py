import asyncio
import builtins
import json
import os
import sys
import types
from types import SimpleNamespace


def test_skill_format_round_trips_frontmatter_body_and_dict():
    from services.memory import skill_format

    assert skill_format.slugify("  Build: Thing!! ") == "build-thing"
    assert skill_format.slugify("!!!", fallback="fallback") == "fallback"
    assert skill_format._split_top_level("a,[b,c],'d,e'", ",") == ["a", "[b,c]", "'d,e'"]
    assert skill_format._parse_scalar("") == ""
    assert skill_format._parse_scalar("[]") == []
    assert skill_format._parse_scalar("[one, 2, true, null]") == ["one", 2, True, None]
    assert skill_format._parse_scalar("no") is False
    assert skill_format._emit_scalar(None) == "null"
    assert skill_format._emit_scalar(False) == "false"
    assert skill_format._emit_scalar("needs: quoting, yes") == json.dumps("needs: quoting, yes")
    assert skill_format._as_list(None) == []
    assert skill_format._as_list("tag") == ["tag"]
    assert skill_format._as_float("bad", 0.4) == 0.4

    markdown = """---
name: Test Skill
description: Handles tricky input
version: 2
category: Dev Ops
tags:
  - git
  - deploy
platforms: [windows, linux]
requires_toolsets: [shell]
fallback_for_toolsets: [browser]
status: published
confidence: "0.91"
source: taught
teacher_model: teacher
owner: alice
created: 2026-01-01T00:00:00Z
---

Loose introduction.

## When to Use

When deployments need a repeatable fix.

## Procedure

1. Check status
2. Apply fix
   with continuation

## Pitfalls

- Avoid secrets

## Verification

- Tests pass

Trailing note.
"""

    fm, body = skill_format.parse_frontmatter(markdown)
    assert fm["tags"] == ["git", "deploy"]
    parsed_body = skill_format.parse_body(body)
    assert parsed_body["procedure"] == ["Check status", "Apply fix with continuation"]
    assert "Loose introduction" in parsed_body["body_extra"]
    assert parsed_body["verification"] == ["Tests pass Trailing note."]

    skill = skill_format.Skill.from_markdown(markdown, path="SKILL.md")
    assert skill.name == "test-skill"
    assert skill.category == "Dev Ops"
    assert skill.confidence == 0.91
    as_dict = skill.to_dict()
    assert as_dict["title"] == "Handles tricky input"
    assert as_dict["steps"] == ["Check status", "Apply fix with continuation"]

    emitted = skill.to_markdown()
    reparsed = skill_format.Skill.from_markdown(emitted)
    assert reparsed.name == "test-skill"
    assert reparsed.procedure == skill.procedure
    assert skill_format.parse_body("")["procedure"] == []
    assert skill_format._parse_list_lines("plain paragraph") == ["plain paragraph"]
    assert "## Verification" in skill_format.emit_body({"verification": ["Confirm"]})
    assert skill_format.emit_frontmatter({"empty": "", "name": "x"}) == "name: x"
    assert skill_format.parse_frontmatter("no frontmatter")[0] == {}
    assert skill_format.parse_frontmatter("---\nname: no end")[0] == {}
    fm_with_comments, _ = skill_format.parse_frontmatter("---\n# ignored\n\nname: Commented\n---\nBody")
    assert fm_with_comments == {"name": "Commented"}
    generated = skill_format.Skill.from_markdown("---\ndescription: Generated\n---\n\n")
    assert generated.created.endswith("Z")


def test_skills_manager_disk_crud_usage_relevance_and_references(tmp_path, monkeypatch):
    from services.memory import skills as skills_module
    from services.memory.skills import SkillsManager, _jaccard, _to_float, _tokenize

    monkeypatch.setattr("time.time", lambda: 12345)
    manager = SkillsManager(str(tmp_path))
    assert _tokenize("Hello, HELLO x!") == {"hello", "x"}
    assert _jaccard({"a"}, {"a", "b"}) == 0.5
    assert _to_float("bad", 0.2) == 0.2

    added = manager.add_skill(
        title="Deploy Fix",
        problem="Deployment fails",
        solution="Use the safe deploy path",
        steps=["Inspect logs", "Run deploy"],
        tags=["deploy"],
        source="learned",
        confidence=0.9,
        owner="alice",
        status="published",
        platforms=["windows"],
        requires_toolsets=["shell"],
    )
    assert added["name"] == "deploy-fix"
    assert manager.load(owner="bob") == []
    assert manager.load(owner="alice")[0]["owner"] == "alice"

    monkeypatch.setattr(skills_module, "_jaccard", lambda _a, _b: 1.0)
    deduped = manager.add_skill(
        title="Deploy Fix",
        problem="Deployment fails",
        steps=["Inspect logs", "Run deploy"],
        tags=["deploy"],
        source="learned",
        owner="alice",
    )
    assert deduped["_deduped"] is True
    assert manager._load_usage()["deploy-fix"]["uses"] == 1

    user_duplicate = manager.add_skill(title="Deploy Fix", source="user", owner="alice")
    assert user_duplicate["name"] == "deploy-fix-2"

    assert manager.update_skill(
        "deploy-fix-2",
        {
            "name": "Deploy Fix Renamed",
            "category": "ops",
            "title": "Renamed skill",
            "problem": "New problem",
            "steps": ["Do thing"],
            "tags": ["ops"],
        },
    )
    assert manager.read_skill_md("deploy-fix-renamed").startswith("---")
    ref_dir = tmp_path / "skills" / "ops" / "deploy-fix-renamed" / "references"
    ref_dir.mkdir()
    (ref_dir / "note.txt").write_text("reference", encoding="utf-8")
    assert manager.read_skill_reference("deploy-fix-renamed", "references/note.txt") == "reference"
    assert manager.read_skill_reference("deploy-fix-renamed", "../_usage.json") is None
    assert manager.read_skill_reference("deploy-fix-renamed", "missing.txt") is None

    manager.set_audit("deploy-fix", "passed", by_teacher=True, worker_model="worker", teacher_model="teacher")
    manager.set_necessity("deploy-fix", False, redundant_with=["other"], reason="covered")
    loaded = {s["name"]: s for s in manager.load(owner="alice")}
    assert loaded["deploy-fix"]["audit_verdict"] == "passed"
    assert loaded["deploy-fix"]["necessity"]["reason"] == "covered"

    index = manager.index_for(owner="alice", active_toolsets=["shell"], platform="windows")
    assert index[0]["name"] == "deploy-fix"
    assert manager.index_for(owner="alice", active_toolsets=[], platform="windows") == []

    relevant = manager.get_relevant_skills("deploy logs", threshold=0.1, min_confidence=0.8)
    assert relevant[0]["name"] == "deploy-fix"
    assert manager.get_relevant_skills("", threshold=0.1) == []

    assert manager.delete_skill("deploy-fix-renamed") is True
    assert manager.delete_skill("missing") is False

    legacy_file = tmp_path / "skills.json"
    legacy_file.write_text(
        json.dumps([{"id": "old", "title": "Old Skill", "problem": "legacy", "steps": ["one"], "owner": "alice"}]),
        encoding="utf-8",
    )
    assert any(s.get("_legacy") for s in manager.load_all())

    unowned = manager.add_skill(title="Unowned", source="user", status="published")
    assert manager.backfill_owner("alice", valid_owners={"bob"}) >= 1
    assert any(s["name"] == unowned["name"] and s["owner"] == "alice" for s in manager.load(owner="alice"))


def test_skill_extractor_thresholds_json_cleanup_duplicates_and_events(monkeypatch):
    from services.memory import skill_extractor

    class Session:
        session_id = "s1"

        def __init__(self, history):
            self.history = history

        def get_context_messages(self):
            return self.history

    class SkillsManager:
        def __init__(self):
            self.skills = []
            self.added = []

        def load(self, owner=None):
            return list(self.skills)

        def add_skill(self, **kwargs):
            entry = {"id": "skill-1", "title": kwargs["title"], **kwargs}
            self.added.append(kwargs)
            self.skills.append({"title": kwargs["title"]})
            return entry

    async def fake_llm(_url, _model, messages, headers=None, timeout=None):
        assert timeout == 30
        assert headers == {"H": "1"}
        assert messages[0]["role"] == "system"
        assert "[user]" in messages[1]["content"]
        return """```json
{"title":"Repeat Build","problem":"Build failed","solution":"Run the checked path","steps":["inspect","fix","test"],"tags":["build"],"confidence":0.8}
```"""

    llm_core = types.ModuleType("src.llm_core")
    llm_core.llm_call_async = fake_llm
    text_helpers = types.ModuleType("src.text_helpers")
    text_helpers.strip_think = lambda text, **_kwargs: text
    events = []
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda event, owner: events.append((event, owner))
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)
    monkeypatch.setitem(sys.modules, "src.text_helpers", text_helpers)
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    manager = SkillsManager()
    session = Session(
        [
            {"role": "user", "content": [{"type": "text", "text": "please fix build"}]},
            {"role": "assistant", "content": "x" * 600},
        ]
    )
    assert asyncio.run(skill_extractor.maybe_extract_skill(session, manager, "url", "model", {"H": "1"}, 1, 1, owner="alice")) is None

    extracted = asyncio.run(skill_extractor.maybe_extract_skill(session, manager, "url", "model", {"H": "1"}, 2, 1, owner="alice"))
    assert extracted["title"] == "Repeat Build"
    assert manager.added[-1]["session_id"] == "s1"
    assert events == [("skill_added", "alice")]

    duplicate = asyncio.run(skill_extractor.maybe_extract_skill(session, manager, "url", "model", {"H": "1"}, 2, 1, owner="alice"))
    assert duplicate is None

    async def low_confidence(*_args, **_kwargs):
        return json.dumps({"title": "Weak", "confidence": 0.2})

    llm_core.llm_call_async = low_confidence
    assert asyncio.run(skill_extractor.maybe_extract_skill(session, SkillsManager(), "url", "model", {}, 2, 1)) is None

    async def null_response(*_args, **_kwargs):
        return "null"

    llm_core.llm_call_async = null_response
    assert asyncio.run(skill_extractor.maybe_extract_skill(session, SkillsManager(), "url", "model", {}, 2, 1)) is None

    async def invalid_json(*_args, **_kwargs):
        return "not json"

    llm_core.llm_call_async = invalid_json
    assert asyncio.run(skill_extractor.maybe_extract_skill(session, SkillsManager(), "url", "model", {}, 2, 1)) is None

    assert asyncio.run(
        skill_extractor.maybe_extract_skill(Session([]), SkillsManager(), "url", "model", {}, 2, 1)
    ) is None


def test_skill_extractor_remaining_parse_and_failure_edges(monkeypatch):
    from services.memory import skill_extractor

    class Session:
        session_id = "s2"

        def __init__(self, history):
            self.history = history

        def get_context_messages(self):
            return self.history

    class SkillsManager:
        def __init__(self):
            self.skills = []

        def load(self, owner=None):
            return []

        def add_skill(self, **kwargs):
            entry = {"id": "skill-edge", "title": kwargs["title"], **kwargs}
            self.skills.append(entry)
            return entry

    session = Session([{"role": "user", "content": "fix it"}, {"role": "assistant", "content": "done"}])

    async def embedded_json(*_args, **_kwargs):
        return 'thinking first {"title":"Fallback Parse","confidence":"not-a-number"} trailing'

    llm_core = types.ModuleType("src.llm_core")
    llm_core.llm_call_async = embedded_json
    text_helpers = types.ModuleType("src.text_helpers")

    def broken_strip(*_args, **_kwargs):
        raise RuntimeError("strip unavailable")

    text_helpers.strip_think = broken_strip
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event down"))
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)
    monkeypatch.setitem(sys.modules, "src.text_helpers", text_helpers)
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    extracted = asyncio.run(skill_extractor.maybe_extract_skill(session, SkillsManager(), "url", "model", {}, 2, 1))
    assert extracted["title"] == "Fallback Parse"
    assert extracted["confidence"] == "not-a-number"

    async def list_json(*_args, **_kwargs):
        return "[]"

    llm_core.llm_call_async = list_json
    assert asyncio.run(skill_extractor.maybe_extract_skill(session, SkillsManager(), "url", "model", {}, 2, 1)) is None

    async def no_title(*_args, **_kwargs):
        return '{"confidence": 0.8}'

    llm_core.llm_call_async = no_title
    assert asyncio.run(skill_extractor.maybe_extract_skill(session, SkillsManager(), "url", "model", {}, 2, 1)) is None

    class BrokenSession:
        def get_context_messages(self):
            raise RuntimeError("history failed")

    assert asyncio.run(skill_extractor.maybe_extract_skill(BrokenSession(), SkillsManager(), "url", "model", {}, 2, 1)) is None


def test_skills_manager_storage_legacy_and_backfill_edges(tmp_path, monkeypatch):
    from services.memory import skills as skills_module
    from services.memory.skills import SkillsManager, _jaccard

    assert _jaccard(set(), {"x"}) == 0.0

    manager = SkillsManager(str(tmp_path))
    usage_path = tmp_path / "skills" / "_usage.json"
    usage_path.write_text("[]", encoding="utf-8")
    assert manager._load_usage() == {}
    usage_path.write_text("{bad", encoding="utf-8")
    assert manager._load_usage() == {}

    import core.atomic_io as atomic_io

    monkeypatch.setattr(atomic_io, "atomic_write_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("atomic down")))
    manager._save_usage({"skill": {"uses": 1}})
    assert json.loads(usage_path.read_text(encoding="utf-8"))["skill"]["uses"] == 1

    empty_root = SkillsManager(str(tmp_path / "empty-root"))
    os.rmdir(empty_root.skills_root)
    assert list(empty_root._iter_skill_files()) == []

    bad_skill_dir = tmp_path / "skills" / "bad" / "bad-skill"
    bad_skill_dir.mkdir(parents=True)
    bad_skill_path = bad_skill_dir / "SKILL.md"
    bad_skill_path.write_text("---\nname: broken\n---\n", encoding="utf-8")
    monkeypatch.setattr(skills_module.Skill, "from_markdown", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse failed")))
    assert manager._read_skill(str(bad_skill_path)) is None
    monkeypatch.undo()

    assert manager.backfill_owner("") == 0

    class DummySkill:
        def __init__(self, name, owner):
            self.name = name
            self.owner = owner

    fake_manager = SkillsManager(str(tmp_path / "fake-backfill"))
    monkeypatch.setattr(fake_manager, "_iter_skill_files", lambda: iter(["bad", "primary", "valid", "rewrite"]))
    monkeypatch.setattr(
        fake_manager,
        "_read_skill",
        lambda path: {
            "bad": None,
            "primary": DummySkill("primary", "alice"),
            "valid": DummySkill("valid", "bob"),
            "rewrite": DummySkill("rewrite", ""),
        }[path],
    )
    monkeypatch.setattr(fake_manager, "_write_skill", lambda _sk: (_ for _ in ()).throw(RuntimeError("readonly")))
    assert fake_manager.backfill_owner("alice", valid_owners={"bob"}) == 0

    load_manager = SkillsManager(str(tmp_path / "load-skip"))
    monkeypatch.setattr(load_manager, "_iter_skill_files", lambda: iter(["bad"]))
    monkeypatch.setattr(load_manager, "_read_skill", lambda _path: None)
    assert load_manager.load_all() == []

    legacy_manager = SkillsManager(str(tmp_path / "legacy"))
    legacy_manager.add_skill(title="Old Skill", owner="alice")
    (tmp_path / "legacy" / "skills.json").write_text(
        json.dumps(
            [
                "skip",
                {"id": "dupe", "title": "Old Skill"},
                {"id": "fresh", "title": "Fresh Legacy", "steps": ["step"]},
            ]
        ),
        encoding="utf-8",
    )
    legacy_entries = legacy_manager.load(owner=None)
    assert any(entry.get("_legacy") and entry["name"] == "fresh-legacy" for entry in legacy_entries)
    assert sum(1 for entry in legacy_entries if entry["name"] == "old-skill") == 1

    (tmp_path / "legacy" / "skills.json").write_text("{bad", encoding="utf-8")
    assert legacy_manager.load_all()

    dedupe_manager = SkillsManager(str(tmp_path / "dedupe-record-use"))
    dedupe_manager.add_skill(title="Duplicate Base", problem="same", steps=["one"])
    monkeypatch.setattr(skills_module, "_jaccard", lambda _a, _b: 1.0)
    monkeypatch.setattr(dedupe_manager, "record_use", lambda _name: (_ for _ in ()).throw(RuntimeError("usage locked")))
    deduped = dedupe_manager.add_skill(title="Duplicate Base", problem="same", steps=["one"])
    assert deduped["_deduped"] is True


def test_skills_manager_update_delete_read_index_and_relevance_edges(tmp_path, monkeypatch):
    from services.memory import skills as skills_module
    from services.memory.skills import SkillsManager

    manager = SkillsManager(str(tmp_path))
    manager.add_skill(title="Solution Only", steps=[], owner="alice")
    assert manager.update_skill("solution-only", {"solution": "Store this in body extra"})
    assert "Store this in body extra" in manager.read_skill_md("solution-only")

    manager.add_skill(title="Rename Me", owner="alice")
    manager.record_use("rename-me")
    assert manager.update_skill("rename-me", {"name": "Renamed Skill"})
    usage = manager._load_usage()
    assert "renamed-skill" in usage and "rename-me" not in usage

    manager.add_skill(title="Target Skill", owner="alice")
    manager.add_skill(title="Source Skill", owner="alice")
    assert manager.update_skill("source-skill", {"name": "Target Skill"}) is False
    assert manager.update_skill("missing-skill", {"description": "x"}) is False

    manager.add_skill(title="Delete Fails", owner="alice")
    with monkeypatch.context() as delete_patch:
        delete_patch.setattr(skills_module.os, "remove", lambda _path: (_ for _ in ()).throw(RuntimeError("locked")))
        assert manager.delete_skill("delete-fails") is False

    manager.add_skill(title="Delete Usage", owner="alice")
    manager.record_use("delete-usage")
    assert "delete-usage" in manager._load_usage()
    assert manager.delete_skill("delete-usage") is True
    assert "delete-usage" not in manager._load_usage()

    read_manager = SkillsManager(str(tmp_path / "read"))

    class FakeSkill:
        name = "fake"

    fake_path = str(tmp_path / "read" / "skills" / "general" / "fake" / "SKILL.md")
    os.makedirs(os.path.dirname(fake_path), exist_ok=True)
    with open(fake_path, "w", encoding="utf-8") as f:
        f.write("content")
    monkeypatch.setattr(read_manager, "_iter_skill_files", lambda: iter([fake_path]))
    monkeypatch.setattr(read_manager, "_read_skill", lambda _path: FakeSkill())

    real_open = builtins.open

    def failing_open(path, *args, **kwargs):
        if str(path) == fake_path:
            raise OSError("unreadable")
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as read_patch:
        read_patch.setattr(builtins, "open", failing_open)
        assert read_manager.read_skill_md("fake") is None
    assert read_manager.read_skill_md("other") is None

    ref_path = str(tmp_path / "read" / "skills" / "general" / "fake" / "references" / "note.txt")
    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    with open(ref_path, "w", encoding="utf-8") as f:
        f.write("reference")

    def failing_ref_open(path, *args, **kwargs):
        if str(path) == ref_path:
            raise OSError("unreadable")
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as ref_patch:
        ref_patch.setattr(builtins, "open", failing_ref_open)
        assert read_manager.read_skill_reference("fake", "references/note.txt") is None
    assert read_manager.read_skill_reference("other", "references/note.txt") is None

    index_manager = SkillsManager(str(tmp_path / "index"))
    index_manager.add_skill(title="Teacher Draft", source="teacher-escalation", status="draft", owner="alice")
    index_manager.add_skill(title="Linux Only", status="published", platforms=["linux"], owner="alice")
    index_manager.add_skill(title="Fallback Browser", status="published", fallback_for_toolsets=["browser"], owner="alice")
    index_names = {entry["name"] for entry in index_manager.index_for(owner="alice", platform="windows", active_toolsets=["browser"])}
    assert "teacher-draft" in index_names
    assert "linux-only" not in index_names
    assert "fallback-browser" not in index_names

    relevance_manager = SkillsManager(str(tmp_path / "relevance"))
    assert relevance_manager.get_relevant_skills(
        "deploy",
        skills=[{"name": "teacher", "description": "deploy", "status": "draft", "source": "teacher-escalation"}],
        threshold=0.1,
        min_confidence=0.5,
    ) == []
    assert relevance_manager.get_relevant_skills(
        "deploy",
        skills=[
            {
                "name": "teacher",
                "description": "deploy",
                "status": "draft",
                "source": "teacher-escalation",
                "confidence": "bad",
            }
        ],
        threshold=0.1,
        min_confidence=0.5,
    ) == []
    legacy_result = relevance_manager.get_relevant_skills(
        "exact phrase",
        skills=[{"name": "legacy", "description": "has exact phrase", "status": "draft", "source": "user"}],
        threshold=0.5,
        min_confidence=0.5,
    )
    assert legacy_result[0]["name"] == "legacy"
