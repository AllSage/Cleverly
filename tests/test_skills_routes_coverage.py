import asyncio
import importlib
import json
import sys
import types
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


def _fresh_skills_routes():
    sys.modules.pop("routes.skills_routes", None)
    return importlib.import_module("routes.skills_routes")


class RequestLike:
    def __init__(self, body=None, user="alice", headers=None):
        self._body = body or {}
        self.headers = headers or {"content-type": "application/json"}
        self.state = SimpleNamespace(current_user=user)

    async def json(self):
        return self._body


class FakeSkillsManager:
    def __init__(self):
        self.skills = [
            {
                "id": "deploy",
                "name": "deploy",
                "description": "Deploy safely",
                "category": "ops",
                "status": "published",
                "confidence": 0.9,
                "owner": "alice",
                "tags": ["deploy"],
                "when_to_use": "deployment",
                "procedure": ["check", "deploy"],
            },
            {
                "id": "legacy",
                "name": "legacy",
                "description": "Legacy",
                "category": "ops",
                "status": "draft",
                "confidence": 0.5,
                "owner": None,
            },
        ]
        self.markdown = {
            "deploy": "---\nname: deploy\ndescription: Deploy safely\n---\n\n## Procedure\n\n1. check\n",
            "legacy": None,
        }
        self.updated = []
        self.deleted = []
        self.audit = []
        self.necessity = []

    def load(self, owner=None):
        if owner is None:
            return list(self.skills)
        return [s for s in self.skills if s.get("owner") == owner or s.get("_include_for_owner")]

    def index_for(self, owner=None):
        return [{"name": s["name"], "description": s.get("description", ""), "category": s.get("category", "general")} for s in self.load(owner)]

    def add_skill(self, **kwargs):
        entry = {
            "id": kwargs.get("name") or kwargs.get("title") or "new",
            "name": kwargs.get("name") or kwargs.get("title") or "new",
            "description": kwargs.get("description") or kwargs.get("title") or "",
            "owner": kwargs.get("owner"),
            "status": kwargs.get("status", "draft"),
        }
        if kwargs.get("name") == "dedupe":
            entry["_deduped"] = True
        self.skills.append(entry)
        self.markdown[entry["name"]] = "---\nname: %s\n---\n" % entry["name"]
        return entry

    def read_skill_md(self, name):
        return self.markdown.get(name)

    def update_skill(self, name, updates):
        self.updated.append((name, updates))
        if updates.get("force_fail"):
            return False
        for skill in self.skills:
            if skill["name"] == name:
                skill.update(updates)
                if "name" in updates:
                    self.markdown[updates["name"]] = self.markdown.pop(name, "---\nname: renamed\n---\n")
                return True
        return False

    def delete_skill(self, name):
        self.deleted.append(name)
        before = len(self.skills)
        self.skills = [s for s in self.skills if s["name"] != name]
        return len(self.skills) != before

    def get_relevant_skills(self, query, skills, max_items=10):
        return [s for s in skills if query.lower() in (s.get("description", "").lower() + s.get("name", "").lower())][:max_items]

    def set_audit(self, *args, **kwargs):
        self.audit.append((args, kwargs))

    def set_necessity(self, *args, **kwargs):
        self.necessity.append((args, kwargs))


def test_skills_routes_crud_builtin_jobs_and_search(monkeypatch):
    skills_routes = _fresh_skills_routes()
    manager = FakeSkillsManager()
    monkeypatch.setattr(skills_routes, "get_current_user", lambda request: request.state.current_user)
    monkeypatch.setattr(skills_routes, "require_admin", lambda request: None)

    events = []
    event_bus = types.ModuleType("src.event_bus")
    event_bus.fire_event = lambda event, user: events.append((event, user))
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    agent_loop = types.ModuleType("src.agent_loop")
    agent_loop.TOOL_SECTIONS = {("web", "search"): "- Use ```search``` for web lookups.", "email": "Email tools"}
    agent_loop.get_builtin_overrides = lambda: {"web": "Override web instructions"}
    monkeypatch.setitem(sys.modules, "src.agent_loop", agent_loop)

    saved_settings = []
    settings = types.ModuleType("src.settings")
    settings.load_settings = lambda: {"builtin_tool_overrides": {"web": "old"}}
    settings.save_settings = lambda data: saved_settings.append(data)
    settings.get_setting = lambda key, default=None: default
    monkeypatch.setitem(sys.modules, "src.settings", settings)

    endpoint_resolver = types.ModuleType("src.endpoint_resolver")
    endpoint_resolver.resolve_endpoint = lambda profile: ("http://local", "model-old", {"H": "1"})
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", endpoint_resolver)
    llm_core = types.ModuleType("src.llm_core")
    llm_core.list_model_ids = lambda url, headers=None: ["model-served"]
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)

    async def fake_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(skills_routes, "_run_skill_test_job", fake_job)
    monkeypatch.setattr(skills_routes, "_run_audit_all_job", fake_job)
    monkeypatch.setattr(skills_routes, "_resolve_audit_models", lambda: ("http://local", "audit-model", {}, None))
    monkeypatch.setattr(skills_routes, "_skill_test_jobs", {})
    monkeypatch.setattr(skills_routes, "_skill_audit_jobs", {})

    router = skills_routes.setup_skills_routes(manager)
    request = RequestLike()

    assert asyncio.run(_endpoint(router, "/api/skills")(request))["count"] == 1
    assert asyncio.run(_endpoint(router, "/api/skills/index")(request))["index"][0]["name"] == "deploy"

    builtin = asyncio.run(_endpoint(router, "/api/skills/builtin")(request))
    assert builtin["count"] == 3
    assert next(item for item in builtin["builtin"] if item["name"] == "web")["is_overridden"] is True
    assert asyncio.run(_endpoint(router, "/api/skills/builtin/{name}")(name="web", request=request))["text"] == "Override web instructions"
    with pytest.raises(HTTPException) as missing_builtin:
        asyncio.run(_endpoint(router, "/api/skills/builtin/{name}")(name="missing", request=request))
    assert missing_builtin.value.status_code == 404

    with pytest.raises(HTTPException) as bad_override:
        asyncio.run(_endpoint(router, "/api/skills/builtin/{name}", "PUT")(name="missing", request=RequestLike(body={"text": "x"})))
    assert bad_override.value.status_code == 404
    with pytest.raises(HTTPException) as empty_override:
        asyncio.run(_endpoint(router, "/api/skills/builtin/{name}", "PUT")(name="web", request=RequestLike(body={"text": ""})))
    assert empty_override.value.status_code == 400
    assert asyncio.run(_endpoint(router, "/api/skills/builtin/{name}", "PUT")(name="web", request=RequestLike(body={"text": "new"}))) == {
        "ok": True,
        "name": "web",
        "is_overridden": True,
    }
    assert saved_settings[-1]["builtin_tool_overrides"]["web"] == "new"
    assert asyncio.run(_endpoint(router, "/api/skills/builtin/{name}", "DELETE")(name="web", request=request)) == {
        "ok": True,
        "name": "web",
        "is_overridden": False,
    }

    added = asyncio.run(
        _endpoint(router, "/api/skills/add", "POST")(
            request,
            skills_routes.SkillAddRequest(name="new-skill", description="New", procedure=["step"]),
        )
    )
    assert added["ok"] is True
    assert events[-1] == ("skill_added", "alice")
    deduped = asyncio.run(
        _endpoint(router, "/api/skills/add", "POST")(
            request,
            skills_routes.SkillAddRequest(name="dedupe", description="Duplicate"),
        )
    )
    assert deduped["deduped"] is True

    assert asyncio.run(_endpoint(router, "/api/skills/{skill_id}")(request, "deploy"))["name"] == "deploy"
    with pytest.raises(HTTPException) as missing_skill:
        asyncio.run(_endpoint(router, "/api/skills/{skill_id}")(request, "missing"))
    assert missing_skill.value.status_code == 404

    assert "markdown" in asyncio.run(_endpoint(router, "/api/skills/{skill_id}/markdown")(request, "deploy"))
    manager.skills.append({"id": "legacy", "name": "legacy", "owner": "alice", "_include_for_owner": True})
    with pytest.raises(HTTPException) as no_source:
        asyncio.run(_endpoint(router, "/api/skills/{skill_id}/markdown")(request, "legacy"))
    assert no_source.value.status_code == 404

    test_started = asyncio.run(_endpoint(router, "/api/skills/{skill_id}/test", "POST")(RequestLike(body={}), "deploy"))
    assert test_started["status"] == "running"
    assert test_started["model"] == "model-served"
    status = asyncio.run(_endpoint(router, "/api/skills/{skill_id}/test-status")(request, "deploy"))
    assert status["status"] == "running"
    monkeypatch.setattr(skills_routes, "_resolve_audit_models", lambda: (_ for _ in ()).throw(ValueError("no model")))
    with pytest.raises(HTTPException) as no_audit_model:
        asyncio.run(_endpoint(router, "/api/skills/audit-all", "POST")(RequestLike(body={})))
    assert no_audit_model.value.status_code == 400
    monkeypatch.setattr(skills_routes, "_resolve_audit_models", lambda: ("http://local", "audit-model", {}, None))

    audit_started = asyncio.run(_endpoint(router, "/api/skills/audit-all", "POST")(RequestLike(body={"names": ["deploy", "missing"]})))
    assert audit_started["status"] == "running"
    assert asyncio.run(_endpoint(router, "/api/skills/audit-all/status")(request))["status"] == "running"
    assert asyncio.run(_endpoint(router, "/api/skills/audit-all/cancel", "POST")(request))["status"] == "cancelled"

    with pytest.raises(HTTPException) as empty_md:
        asyncio.run(_endpoint(router, "/api/skills/{skill_id}/markdown", "POST")(RequestLike(body={"markdown": ""}), "deploy"))
    assert empty_md.value.status_code == 400
    save_md = asyncio.run(
        _endpoint(router, "/api/skills/{skill_id}/markdown", "POST")(
            RequestLike(body={"markdown": "---\nname: saved\ndescription: Saved\n---\n\n## Procedure\n\n1. go\n"}),
            "deploy",
        )
    )
    assert save_md == {"ok": True, "name": "saved"}

    assert asyncio.run(_endpoint(router, "/api/skills/{skill_id}", "PUT")(request, "saved", skills_routes.SkillUpdateRequest())) == {"ok": True}
    assert asyncio.run(
        _endpoint(router, "/api/skills/{skill_id}", "PUT")(
            request,
            "saved",
            skills_routes.SkillUpdateRequest(description="Changed"),
        )
    ) == {"ok": True}
    with pytest.raises(HTTPException) as update_missing:
        asyncio.run(_endpoint(router, "/api/skills/{skill_id}", "PUT")(request, "missing", skills_routes.SkillUpdateRequest(description="x")))
    assert update_missing.value.status_code == 404

    assert asyncio.run(_endpoint(router, "/api/skills/{skill_id}", "DELETE")(request, "saved")) == {"ok": True}
    with pytest.raises(HTTPException) as delete_missing:
        asyncio.run(_endpoint(router, "/api/skills/{skill_id}", "DELETE")(request, "missing"))
    assert delete_missing.value.status_code == 404

    with pytest.raises(HTTPException) as blank_search:
        asyncio.run(_endpoint(router, "/api/skills/search", "POST")(RequestLike(body={"query": " "})))
    assert blank_search.value.status_code == 400
    searched = asyncio.run(_endpoint(router, "/api/skills/search", "POST")(RequestLike(body={"query": "new"})))
    assert searched["count"] >= 1


def test_skills_route_audit_helpers_and_evaluators(monkeypatch):
    skills_routes = _fresh_skills_routes()

    assert "Test this skill end-to-end" in skills_routes._skill_test_task({"when_to_use": "docs"})
    assert skills_routes._should_check_retrieval_precision({"tags": ["network"]}) is True
    assert skills_routes._should_check_retrieval_precision({"name": "narrow", "description": "specific"}) is False
    assert skills_routes._audit_flag_text({"a": "Generic"}, ["trivial"]) == "generic trivial"
    assert skills_routes._audit_generic_blocker(None, {"necessary": False, "reason": "too generic"}, None) == "too generic"
    assert skills_routes._audit_generic_blocker({"tags": ["generic"]}, None, None) == "Skill is tagged generic"
    assert skills_routes._audit_generic_blocker(None, None, {"summary": "trivial saved procedure"}) == "Audit flagged the skill as generic or unnecessary"

    responses = []

    async def fake_llm(*_args, **_kwargs):
        return responses.pop(0)

    llm_core = types.ModuleType("src.llm_core")
    llm_core.llm_call_async = fake_llm
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)

    responses.append('<think>ignore</think> {"verdict":"pass","confidence":0.88,"summary":"works","issues":["metadata: tag"]}')
    verdict = asyncio.run(skills_routes._eval_skill_run("md", "task", "transcript", "url", "model", {}))
    assert verdict["verdict"] == "pass"
    assert verdict["confidence"] == 0.88

    responses.append('{"necessary": false, "redundant_with": ["other"], "reason": "duplicate"}')
    necessity = asyncio.run(skills_routes._eval_skill_necessity("md", [{"name": "other"}], "url", "model", {}))
    assert necessity == {"necessary": False, "redundant_with": ["other"], "reason": "duplicate"}

    responses.append('{"ok": false, "summary": "too broad", "issues": ["metadata: retrieval: narrow it"]}')
    precision = asyncio.run(skills_routes._eval_skill_retrieval_precision("md", [], "url", "model", {}))
    assert precision["ok"] is False

    async def failing_llm(*_args, **_kwargs):
        raise RuntimeError("llm down")

    llm_core.llm_call_async = failing_llm
    assert asyncio.run(skills_routes._eval_skill_necessity("md", [], "url", "model", {})) is None
    assert asyncio.run(skills_routes._eval_skill_retrieval_precision("md", [], "url", "model", {})) is None

    manager = FakeSkillsManager()
    manager.skills.append(
        {
            "id": "deploy-2",
            "name": "deploy-2",
            "description": "Deploy safely",
            "category": "ops",
            "status": "draft",
            "confidence": 0.7,
            "owner": "alice",
            "procedure": ["check", "deploy"],
            "tags": ["deploy"],
        }
    )
    assert skills_routes._skill_duplicate_blocker(manager, "deploy-2", "alice") == "deploy"
    assert manager.necessity

    prefs_routes = types.ModuleType("routes.prefs_routes")
    prefs_routes._load_for_user = lambda owner: {"auto_approve_skills": True, "skill_min_confidence": 0.8}
    settings = types.ModuleType("src.settings")
    settings.get_setting = lambda key, default=None: default
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_routes)
    monkeypatch.setitem(sys.modules, "src.settings", settings)
    status = skills_routes._audit_finalize_status(
        manager,
        "deploy",
        "alice",
        "pass",
        0.95,
        {"necessary": True},
        {"summary": "works", "issues": []},
    )
    assert status == "published"

    assert skills_routes._apply_skill_md(
        manager,
        "deploy",
        "---\nname: other\ndescription: Updated\n---\n\n## Procedure\n\n1. do\n",
        "alice",
    ) is True
    assert manager.updated[-1][0] == "deploy"
    assert skills_routes._apply_skill_md(manager, "deploy", "bad: [", "alice") is True
