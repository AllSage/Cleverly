import asyncio
import json
import sys
import types
from types import SimpleNamespace


def test_skill_format_round_trips_frontmatter_body_and_dict():
    from services.memory import skill_format

    assert skill_format.slugify("  Build: Thing!! ") == "build-thing"
    assert skill_format.slugify("!!!", fallback="fallback") == "fallback"
    assert skill_format._split_top_level("a,[b,c],'d,e'", ",") == ["a", "[b,c]", "'d,e'"]
    assert skill_format._parse_scalar("[one, 2, true, null]") == ["one", 2, True, None]
    assert skill_format._emit_scalar("needs: quoting, yes") == json.dumps("needs: quoting, yes")
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
    assert "## Verification" in skill_format.emit_body({"verification": ["Confirm"]})
    assert skill_format.emit_frontmatter({"empty": "", "name": "x"}) == "name: x"
    assert skill_format.parse_frontmatter("no frontmatter")[0] == {}


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
