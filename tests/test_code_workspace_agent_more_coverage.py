import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import code_workspace_agent as agent


class Column:
    def __eq__(self, other):
        return ("eq", other)


class Endpoint:
    is_enabled = Column()

    def __init__(self, name, base_url, *, api_key="sk", cached_models="[]", owner=None):
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        self.cached_models = cached_models
        self.owner = owner


class Query:
    def __init__(self, endpoints):
        self.endpoints = endpoints

    def filter(self, *args):
        return self

    def all(self):
        return list(self.endpoints)


class DB:
    def __init__(self, endpoints):
        self.endpoints = endpoints
        self.closed = False

    def query(self, model):
        return Query(self.endpoints)

    def close(self):
        self.closed = True


def test_model_key_resolution_and_extractors(monkeypatch):
    assert agent._slug("GLM-5.2!") == "glm52"
    assert agent._model_match("GLM-5.2", "z-ai/glm-5.2-air")
    assert not agent._model_match("", "model")
    assert agent._extract_json('```json\n{"paths":["a.py"],"plan":"edit"}\n```') == {
        "paths": ["a.py"],
        "plan": "edit",
    }
    assert agent._extract_json('prefix {"ok": true} suffix') == {"ok": True}
    assert agent._extract_json("[1, 2]") == {}
    assert agent._extract_json("no json") == {}
    assert agent._extract_json("prefix {bad json} suffix") == {}
    assert agent._extract_diff("```diff\ndiff --git a/a b/a\n--- a/a\n+++ b/a\n```").startswith("diff --git")
    assert agent._extract_diff("*** Begin Patch\n*** End Patch") == "*** Begin Patch\n*** End Patch\n"
    assert agent._extract_diff("nothing useful") == ""

    monkeypatch.setattr(agent, "normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(agent, "build_chat_url", lambda base: f"{base}/chat/completions")
    monkeypatch.setattr(agent, "build_headers", lambda key, base: {"Authorization": f"Bearer {key}", "Base": base})
    monkeypatch.setattr(agent, "offline_mode", lambda: True)
    monkeypatch.setattr(agent, "load_features", lambda: {"external_model_endpoints": True})
    monkeypatch.setattr(agent, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(agent, "ModelEndpoint", Endpoint)

    with pytest.raises(agent.code_workspace.CodeWorkspaceError, match="Set Code Workspace model key"):
        agent.resolve_model_key("")

    remote_only = DB([Endpoint("remote", "https://api.example/v1", cached_models='["glm"]')])
    monkeypatch.setattr(agent, "SessionLocal", lambda: remote_only)
    with pytest.raises(agent.code_workspace.CodeWorkspaceError, match="No enabled local"):
        agent.resolve_model_key("glm")
    assert remote_only.closed is True

    monkeypatch.setattr(agent, "offline_mode", lambda: False)
    monkeypatch.setattr(agent, "load_features", lambda: {"external_model_endpoints": False})
    remote_disabled = DB([Endpoint("remote", "https://api.example/v1", cached_models='["glm"]')])
    monkeypatch.setattr(agent, "SessionLocal", lambda: remote_disabled)
    with pytest.raises(agent.code_workspace.CodeWorkspaceError, match="No enabled local"):
        agent.resolve_model_key("glm")
    assert remote_disabled.closed is True

    monkeypatch.setattr(agent, "load_features", lambda: {"external_model_endpoints": True})

    local = DB([
        Endpoint("Local One", "http://localhost:11434/v1", cached_models='["z-ai/GLM-5.2"]', owner="alice"),
        Endpoint("Other", "http://localhost:8000/v1", cached_models='not-json', owner="bob"),
    ])
    monkeypatch.setattr(agent, "SessionLocal", lambda: local)
    url, model, headers = agent.resolve_model_key("glm52@Local", owner="alice")
    assert url == "http://localhost:11434/v1/chat/completions"
    assert model == "z-ai/GLM-5.2"
    assert headers["Authorization"] == "Bearer sk"

    owner_filtered = DB([
        Endpoint("Bob", "http://localhost:1/v1", cached_models='["glm"]', owner="bob"),
        Endpoint("Alice", "http://localhost:2/v1", cached_models='["glm"]', owner="alice"),
    ])
    monkeypatch.setattr(agent, "SessionLocal", lambda: owner_filtered)
    assert agent.resolve_model_key("glm", owner="alice")[0] == "http://localhost:2/v1/chat/completions"

    invalid_cache = DB([
        Endpoint("Bad Cache", "http://localhost:3/v1", cached_models='not-json'),
        Endpoint("Good Cache", "http://localhost:4/v1", cached_models='["glm"]'),
    ])
    monkeypatch.setattr(agent, "SessionLocal", lambda: invalid_cache)
    assert agent.resolve_model_key("glm")[0] == "http://localhost:4/v1/chat/completions"

    hinted = DB([Endpoint("Local One", "http://localhost:11434/v1", cached_models='["other"]')])
    monkeypatch.setattr(agent, "SessionLocal", lambda: hinted)
    assert agent.resolve_model_key("new-model@Local")[1] == "new-model"

    single = DB([Endpoint("Only", "http://localhost:11434/v1", cached_models='["other"]')])
    monkeypatch.setattr(agent, "SessionLocal", lambda: single)
    assert agent.resolve_model_key("uncached")[1] == "uncached"

    many = DB([
        Endpoint("One", "http://localhost:1/v1", cached_models='["a"]'),
        Endpoint("Two", "http://localhost:2/v1", cached_models='["b"]'),
    ])
    monkeypatch.setattr(agent, "SessionLocal", lambda: many)
    with pytest.raises(agent.code_workspace.CodeWorkspaceError, match="Model key did not match"):
        agent.resolve_model_key("missing")


def test_listing_context_fallback_and_choose_files(monkeypatch, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    src = workspace / "src"
    src.mkdir()
    readme = workspace / "README.md"
    readme.write_text("readme", encoding="utf-8")
    app = src / "app.py"
    app.write_text("print('hi')", encoding="utf-8")
    image = workspace / "image.png"
    image.write_bytes(b"\x89PNG")

    monkeypatch.setattr(agent.code_workspace, "_require_workspace", lambda *_args, **_kwargs: (workspace, {}))
    monkeypatch.setattr(agent.code_workspace, "_iter_workspace_files", lambda root: [
        (readme, readme.stat()),
        (app, app.stat()),
        (image, image.stat()),
    ])
    listing = agent._repo_listing("w1", "alice")
    assert listing == [
        {"path": "README.md", "size": 6},
        {"path": "src/app.py", "size": 11},
        {"path": "image.png", "size": 4},
    ]
    monkeypatch.setattr(agent.code_workspace, "MAX_AGENT_TREE_ENTRIES", 1)
    assert agent._repo_listing("w1", "alice") == [{"path": "README.md", "size": 6}]
    monkeypatch.setattr(agent.code_workspace, "MAX_AGENT_TREE_ENTRIES", 999)
    assert agent._fallback_paths(listing, "change app readme")[:2] == ["README.md", "src/app.py"]

    def read_file(_workspace_id, rel, **kwargs):
        if rel == "missing.py":
            raise agent.code_workspace.CodeWorkspaceError("missing")
        return {"path": rel, "content": "body"}

    monkeypatch.setattr(agent.code_workspace, "read_file", read_file)
    context = agent._read_context_files("w1", ["src/app.py", "src/app.py", "missing.py", ""], "alice")
    assert context == [{"path": "src/app.py", "content": "body"}]
    monkeypatch.setattr(agent.code_workspace, "MAX_AGENT_FILES", 1)
    limited_context = agent._read_context_files("w1", ["README.md", "src/app.py"], "alice")
    assert limited_context == [{"path": "README.md", "content": "body"}]
    monkeypatch.setattr(agent.code_workspace, "MAX_AGENT_FILES", 8)

    async def fake_llm(_url, _model, messages, **kwargs):
        assert "Repo files" in messages[-1]["content"]
        return '{"paths":["src/app.py"],"plan":"edit app"}'

    monkeypatch.setattr(agent, "llm_call_async", fake_llm)
    paths, plan = asyncio.run(agent._choose_files("url", "model", {}, "task", listing))
    assert paths == ["src/app.py"]
    assert plan == "edit app"

    prompt = agent._build_patch_prompt("task", [{"path": "a.py", "content": "x"}], "dirty", "failed")
    assert "Previous attempt/test output" in prompt[-1]["content"]
    assert "Return one unified diff" in prompt[-1]["content"]
    monkeypatch.setattr(agent, "MAX_CONTEXT_CHARS", 10)
    tiny_prompt = agent._build_patch_prompt("task", [{"path": "big.py", "content": "x" * 50}], "dirty")
    assert "--- FILE:" not in tiny_prompt[-1]["content"]
    monkeypatch.setattr(agent, "MAX_CONTEXT_CHARS", 120_000)


def test_run_agent_draft_and_apply_paths(monkeypatch):
    monkeypatch.setattr(agent, "resolve_model_key", lambda key, owner="": ("url", "model", {"H": "1"}))
    monkeypatch.setattr(agent.code_workspace, "create_snapshot", lambda *args, **kwargs: {"id": "snap"})
    monkeypatch.setattr(agent, "_repo_listing", lambda *args, **kwargs: [
        {"path": "README.md", "size": 10},
        {"path": "src/app.py", "size": 20},
    ])
    monkeypatch.setattr(agent.code_workspace, "_normalize_allowed_paths", lambda paths: list(paths or []))
    monkeypatch.setattr(agent.code_workspace, "_is_allowed_path", lambda path, allowed: not allowed or path in allowed or any(path.startswith(a + "/") for a in allowed))
    monkeypatch.setattr(agent, "_read_context_files", lambda wid, paths, owner, root=None: [
        {"path": path, "content": "old"} for path in paths if path != "missing.py"
    ])
    monkeypatch.setattr(agent, "_choose_files", lambda *args, **kwargs: asyncio.sleep(0, result=(["README.md"], "chosen plan")))
    monkeypatch.setattr(agent.code_workspace, "git_status", lambda *args, **kwargs: {"stdout": "clean"})
    monkeypatch.setattr(agent.code_workspace, "git_diff", lambda *args, **kwargs: {"stdout": "diff"})

    calls = []

    async def fake_llm(*args, **kwargs):
        calls.append(args)
        return "```diff\ndiff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@\n-old\n+new\n```"

    monkeypatch.setattr(agent, "llm_call_async", fake_llm)
    draft = asyncio.run(agent.run_agent("w1", "update readme", owner="alice", model_key="glm", apply_changes=False))
    assert draft["applied"] is False
    assert draft["proposed_diff"].startswith("diff --git")
    assert draft["plan"] == "chosen plan"

    patch_calls = []
    monkeypatch.setattr(agent.code_workspace, "apply_unified_diff", lambda *args, **kwargs: patch_calls.append(kwargs) or {"exit_code": 0, "stderr": ""})
    monkeypatch.setattr(agent.code_workspace, "run_command", lambda *args, **kwargs: {"exit_code": 0, "stdout": "ok", "stderr": ""})
    applied = asyncio.run(agent.run_agent(
        "w1",
        "update readme",
        owner="alice",
        model_key="glm",
        selected_paths=["README.md", "src/app.py"],
        allowed_paths=["README.md"],
        apply_changes=True,
        test_command="pytest -q",
    ))
    assert applied["applied"] is True
    assert applied["applied_diff"].startswith("diff --git")
    assert applied["selected_paths"] == ["README.md"]
    assert patch_calls[0]["allowed_paths"] == ["README.md"]
    applied_without_tests = asyncio.run(agent.run_agent(
        "w1",
        "update readme",
        owner="alice",
        model_key="glm",
        selected_paths=["README.md"],
        apply_changes=True,
    ))
    assert applied_without_tests["applied"] is True

    with pytest.raises(agent.code_workspace.CodeWorkspaceError, match="Agent task is required"):
        asyncio.run(agent.run_agent("w1", "   ", model_key="glm"))

    monkeypatch.setattr(agent, "_read_context_files", lambda *args, **kwargs: [])
    with pytest.raises(agent.code_workspace.CodeWorkspaceError, match="No readable source files"):
        asyncio.run(agent.run_agent("w1", "task", model_key="glm", selected_paths=["missing.py"]))

    monkeypatch.setattr(agent, "_read_context_files", lambda wid, paths, owner, root=None: [
        {"path": path, "content": "old"} for path in paths
    ])
    monkeypatch.setattr(agent, "_choose_files", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("choose failed")))
    fallback = asyncio.run(agent.run_agent("w1", "readme", owner="alice", model_key="glm", apply_changes=False))
    assert "README.md" in fallback["selected_paths"]

    async def no_diff_llm(*args, **kwargs):
        return "no diff here"

    monkeypatch.setattr(agent, "llm_call_async", no_diff_llm)
    no_diff = asyncio.run(agent.run_agent("w1", "readme", owner="alice", model_key="glm", selected_paths=["README.md"], apply_changes=False))
    assert no_diff["proposed_diff"] == ""
    assert no_diff["steps"][-1]["error"] == "Model did not return a unified diff"

    monkeypatch.setattr(agent, "llm_call_async", fake_llm)
    monkeypatch.setattr(agent.code_workspace, "apply_unified_diff", lambda *args, **kwargs: {"exit_code": 1, "stderr": "patch failed"})
    patch_failed = asyncio.run(agent.run_agent("w1", "readme", owner="alice", model_key="glm", selected_paths=["README.md"], apply_changes=True))
    assert patch_failed["applied"] is False
    assert patch_failed["steps"][-1]["exit_code"] == 1

    test_calls = []

    def flaky_test(*args, **kwargs):
        test_calls.append(1)
        if len(test_calls) == 1:
            return {"exit_code": 1, "stdout": "bad", "stderr": "failed"}
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(agent.code_workspace, "apply_unified_diff", lambda *args, **kwargs: {"exit_code": 0, "stderr": ""})
    monkeypatch.setattr(agent.code_workspace, "run_command", flaky_test)
    retried = asyncio.run(agent.run_agent(
        "w1",
        "readme",
        owner="alice",
        model_key="glm",
        selected_paths=["README.md"],
        apply_changes=True,
        test_command="pytest -q",
        max_rounds=2,
    ))
    assert retried["test_result"]["exit_code"] == 0
    assert len(test_calls) == 2
