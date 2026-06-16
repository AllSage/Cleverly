import argparse
import importlib
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_core_models_session_and_message_behaviors():
    module_path = Path(__file__).resolve().parents[1] / "core" / "models.py"
    spec = importlib.util.spec_from_file_location("_core_models_under_test", module_path)
    models = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = models
    try:
        spec.loader.exec_module(models)
    finally:
        sys.modules.pop(spec.name, None)

    message = models.ChatMessage("user", "hello", metadata={"file": "a.txt"})
    assert message.to_dict() == {"role": "user", "content": "hello", "metadata": {"file": "a.txt"}}
    assert message.get("role") == "user"
    assert message.get("missing", "fallback") == "fallback"

    plain = models.ChatMessage("assistant", "hi")
    assert plain.to_dict() == {"role": "assistant", "content": "hi"}

    persisted = []

    class Manager:
        def _persist_message(self, session_id, msg):
            persisted.append((session_id, msg.content))

    models.set_session_manager(Manager())
    session = models.Session(id="s1", name="Chat", endpoint_url="http://local", model="m")
    assert session.history == []
    assert session.headers == {}
    session.add_message(message)
    assert session.message_count == 1
    assert persisted == [("s1", "hello")]
    assert session.get_context_messages() == [message.to_dict()]
    assert session.get("model") == "m"
    assert session.get("missing", 7) == 7

    models.set_session_manager(None)


def test_preset_manager_defaults_legacy_migration_templates_and_errors(monkeypatch, tmp_path):
    from src.preset_manager import PresetManager

    manager = PresetManager(str(tmp_path))
    assert "custom" in manager.get_all()
    assert manager.get("brainstorm")["name"] == "Brainstorm"
    assert manager.get("missing") is None

    assert manager.update_custom(0.4, 128, "system", name="Analyst", enabled=True, inject_prefix="pre", inject_suffix="post")
    assert manager.get("custom")["character_name"] == "Analyst"

    assert manager.save_user_template({"id": "t1", "name": "One"})
    assert manager.save_user_template({"id": "t1", "name": "Updated"})
    assert manager.get_user_templates() == [{"id": "t1", "name": "Updated"}]
    assert manager.delete_user_template("t1")
    assert manager.get_user_templates() == []

    assert manager.save_group_presets([{"id": "g1"}])
    assert manager.get_group_presets() == [{"id": "g1"}]

    legacy_prompt = "You are a helpful, balanced assistant. Match your response style to the user's needs."
    legacy = {
        "custom": {
            "name": "Custom",
            "system_prompt": legacy_prompt,
            "temperature": 0.2,
            "max_tokens": 123,
        }
    }
    (tmp_path / "presets.json").write_text(json.dumps(legacy), encoding="utf-8")
    migrated = PresetManager(str(tmp_path))
    assert migrated.get("custom")["enabled"] is False
    assert migrated.get("custom")["system_prompt"] == ""

    (tmp_path / "presets.json").write_text("{", encoding="utf-8")
    assert PresetManager(str(tmp_path)).get("custom")["name"] == "Custom"

    def broken_open(*args, **kwargs):
        raise OSError("cannot write")

    monkeypatch.setattr("builtins.open", broken_open)
    assert manager.save({"x": 1}) is False


def test_search_ranking_domain_relevance_recency_and_news_adjustments(monkeypatch):
    from src.search import ranking

    assert ranking._domain("https://Example.COM/path") == "example.com"
    assert ranking._domain(None) in ("", b"")
    assert ranking._domain([]) in ("", b"")

    class FixedDateTime(ranking.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 6, 16)

    monkeypatch.setattr(ranking, "datetime", FixedDateTime)

    results = [
        {
            "title": "Sports headline unrelated",
            "snippet": "NBA championship breaking news",
            "url": "https://sports.yahoo.com/story",
            "age": "bad-date",
        },
        {
            "title": "Sweden latest news",
            "snippet": "Latest news from Sweden today with daily coverage",
            "url": "https://www.reuters.com/world/sweden",
            "age": "2026-06-15",
        },
        {
            "title": "University research",
            "snippet": "Sweden policy archive",
            "url": "https://example.edu/paper",
            "age": "2026-05-01",
        },
        {
            "title": "",
            "snippet": "",
            "url": "not a url",
            "age": None,
        },
    ]

    ranked = ranking.rank_search_results("latest Sweden news", results)
    assert ranked[0]["url"] == "https://www.reuters.com/world/sweden"
    assert ranked[-1]["url"] == "https://sports.yahoo.com/story"

    sports_ranked = ranking.rank_search_results("NBA latest sports news", results)
    assert sports_ranked[0]["url"] in {"https://sports.yahoo.com/story", "https://www.reuters.com/world/sweden"}


def test_cli_helpers_emit_fail_parser_and_run(monkeypatch, capsys):
    from scripts._lib import cli

    module_path = Path(__file__).resolve().parents[1] / "scripts" / "_lib" / "cli.py"
    repo_root = str(module_path.resolve().parent.parent.parent)
    original_path = list(sys.path)
    sys.path[:] = [item for item in sys.path if item != repo_root]
    spec = importlib.util.spec_from_file_location("_cli_reimport_for_path_branch", module_path)
    cli_reimport = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(cli_reimport)
        assert sys.path[0] == repo_root
    finally:
        sys.path[:] = original_path

    logger = logging.getLogger()
    handler = logging.StreamHandler()
    logger.handlers = [handler]
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    cli.quiet_logs()
    assert logger.level == logging.INFO
    assert handler.level == logging.INFO

    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    cli.emit({"time": object()}, SimpleNamespace(pretty=False))
    compact = capsys.readouterr().out
    assert compact.startswith("{")
    assert "\n" == compact[-1]

    cli.emit({"a": 1}, SimpleNamespace(pretty=True))
    assert "  \"a\"" in capsys.readouterr().out

    with pytest.raises(SystemExit) as fail_exit:
        cli.fail("bad", code=7)
    assert fail_exit.value.code == 7
    assert "error: bad" in capsys.readouterr().err

    parser = cli.common_parser("cleverly-test", "desc")
    assert parser.prog == "cleverly-test"
    assert parser._common_parents
    assert cli.run(parser, ["--version"]) == 0
    assert "cleverly-test" in capsys.readouterr().out

    called = []
    parser_ok = argparse.ArgumentParser()
    parser_ok.set_defaults(func=lambda args: called.append(args))
    assert cli.run(parser_ok, []) == 0
    assert called

    parser_keyboard = argparse.ArgumentParser()
    parser_keyboard.set_defaults(func=lambda args: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.run(parser_keyboard, []) == 130
    assert "interrupted" in capsys.readouterr().err

    parser_error = argparse.ArgumentParser()
    parser_error.set_defaults(func=lambda args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(SystemExit):
        cli.run(parser_error, [])
    assert "error: boom" in capsys.readouterr().err

    parser_exit = argparse.ArgumentParser()
    parser_exit.set_defaults(func=lambda args: (_ for _ in ()).throw(SystemExit(5)))
    with pytest.raises(SystemExit) as exit_passthrough:
        cli.run(parser_exit, [])
    assert exit_passthrough.value.code == 5


def test_rag_singleton_success_throttle_unhealthy_and_errors(monkeypatch):
    import src.rag_singleton as rag_singleton

    rag_singleton.rag_instance = "cached"
    assert rag_singleton.get_rag_manager() == "cached"

    rag_singleton.rag_instance = None
    rag_singleton._last_attempt = 100.0
    monkeypatch.setattr(rag_singleton.time, "monotonic", lambda: 101.0)
    assert rag_singleton.get_rag_manager() is None

    class HealthyVectorRAG:
        healthy = True

        def __init__(self, persist_directory):
            self.persist_directory = persist_directory

    fake_module = types.ModuleType("src.rag_vector")
    fake_module.VectorRAG = HealthyVectorRAG
    monkeypatch.setitem(sys.modules, "src.rag_vector", fake_module)
    rag_singleton._last_attempt = 0.0
    monkeypatch.setattr(rag_singleton.time, "monotonic", lambda: 200.0)
    manager = rag_singleton.get_rag_manager()
    assert isinstance(manager, HealthyVectorRAG)
    assert manager.persist_directory.endswith(str(Path("data") / "rag"))

    class UnhealthyVectorRAG:
        healthy = False

        def __init__(self, persist_directory):
            pass

    fake_module.VectorRAG = UnhealthyVectorRAG
    rag_singleton.rag_instance = None
    rag_singleton._last_attempt = 0.0
    monkeypatch.setattr(rag_singleton.time, "monotonic", lambda: 300.0)
    assert rag_singleton.get_rag_manager() is None

    class BrokenVectorRAG:
        def __init__(self, persist_directory):
            raise RuntimeError("bad chroma")

    fake_module.VectorRAG = BrokenVectorRAG
    rag_singleton._last_attempt = 0.0
    monkeypatch.setattr(rag_singleton.time, "monotonic", lambda: 400.0)
    assert rag_singleton.get_rag_manager() is None

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "src.rag_vector":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    rag_singleton._last_attempt = 0.0
    monkeypatch.delitem(sys.modules, "src.rag_vector", raising=False)
    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.setattr(rag_singleton.time, "monotonic", lambda: 500.0)
    assert rag_singleton.get_rag_manager() is None


def test_code_workspace_worker_claim_run_result_and_error_paths(monkeypatch, tmp_path):
    import src.code_workspace_worker as worker

    queue = tmp_path / ".worker"
    for name in ("pending", "running", "results"):
        (queue / name).mkdir(parents=True)

    pending = queue / "pending" / "job1.json"
    pending.write_text("{}", encoding="utf-8")
    claimed = worker._claim_job(queue)
    assert claimed == queue / "running" / "job1.json"

    class BadJob:
        name = "bad.json"

        def replace(self, _target):
            raise OSError("busy")

    monkeypatch.setattr(worker.Path, "glob", lambda self, pattern: [BadJob()] if self.name == "pending" else [])
    assert worker._claim_job(queue) is None

    writes = []
    monkeypatch.setattr(worker.code_workspace, "_atomic_write_json", lambda path, result: writes.append((path, result)))
    worker._write_result(queue, "job-x", {"ok": True})
    assert writes[-1][0] == queue / "results" / "job-x.json"
    assert writes[-1][1] == {"ok": True}

    root = tmp_path / "root"
    workspace = root / "repo"
    workspace.mkdir(parents=True)

    def fake_run(path, command, timeout):
        assert path == workspace.resolve()
        assert command == "pytest -q"
        assert timeout == 9
        return {"stdout": "ok", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(worker.code_workspace, "workspace_root", lambda: root)
    monkeypatch.setattr(worker.code_workspace, "_worker_root", lambda root_arg: queue)
    monkeypatch.setattr(worker.code_workspace, "_run_workspace_shell", fake_run)
    job_path = queue / "running" / "run.json"
    job_path.write_text(json.dumps({"id": "run1", "workspace": str(workspace), "command": "pytest -q", "timeout": 9, "root": str(root)}), encoding="utf-8")
    worker._run_job(queue, job_path)
    assert writes[-1][0] == queue / "results" / "run1.json"
    assert writes[-1][1]["runner"] == "worker"
    assert not job_path.exists()

    escaped = queue / "running" / "escaped.json"
    escaped.write_text(json.dumps({"id": "bad", "workspace": str(tmp_path), "command": "pytest", "root": str(root)}), encoding="utf-8")
    worker._run_job(queue, escaped)
    assert "escaped workspace root" in writes[-1][1]["stderr"]

    denied = queue / "running" / "denied.json"
    denied.write_text(json.dumps({"id": "denied", "workspace": str(workspace), "command": "curl http://example.test", "root": str(root)}), encoding="utf-8")
    worker._run_job(queue, denied)
    assert "blocked in offline code workspace mode" in writes[-1][1]["stderr"]

    invalid = queue / "running" / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    worker._run_job(queue, invalid)
    assert writes[-1][0] == queue / "results" / "invalid.json"
    assert writes[-1][1]["exit_code"] == 1

    bad_command = queue / "running" / "bad-command.json"
    bad_command.write_text(json.dumps({"id": "fallback-id", "workspace": str(workspace), "command": "pytest -q", "root": str(root)}), encoding="utf-8")
    monkeypatch.setattr(worker.code_workspace, "_run_workspace_shell", lambda *_args: (_ for _ in ()).throw(RuntimeError("runner failed")))
    monkeypatch.setattr(worker.Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("busy")))
    worker._run_job(queue, bad_command)
    assert writes[-1][0] == queue / "results" / "fallback-id.json"
    assert "runner failed" in writes[-1][1]["stderr"]

    assert worker._queue_root() == queue

    calls = {"claim": 0, "run": 0}

    def claim_once(_queue):
        calls["claim"] += 1
        return queue / "running" / "loop.json" if calls["claim"] == 1 else None

    def run_once(_queue, _job):
        calls["run"] += 1

    monkeypatch.setattr(worker, "_queue_root", lambda: queue)
    monkeypatch.setattr(worker, "_claim_job", claim_once)
    monkeypatch.setattr(worker, "_run_job", run_once)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop loop")))
    with pytest.raises(RuntimeError, match="stop loop"):
        worker.main()
    assert calls == {"claim": 2, "run": 1}
