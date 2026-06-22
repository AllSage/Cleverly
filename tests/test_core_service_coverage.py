import asyncio
import datetime as dt
import importlib
import json
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import Response


def test_local_audit_append_truncate_read_and_error_paths(monkeypatch, tmp_path):
    from src import local_audit

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    path = local_audit.audit_path()
    assert path.parent == tmp_path / "audit"
    assert path.name == "local-audit.jsonl"

    record = local_audit.append_audit("  model_benchmark  ", {"ok": True}, user="alice", source="operator")
    assert record["action"] == "model_benchmark"
    assert record["user"] == "alice"

    long_record = local_audit.append_audit("x" * 120, {"blob": "y" * 6000}, user="u" * 200, source="s" * 120)
    assert len(long_record["action"]) == local_audit.MAX_ACTION_LEN
    assert len(long_record["user"]) == 120
    assert len(long_record["source"]) == 80
    assert long_record["detail"]["truncated"] is True

    path.write_text(path.read_text(encoding="utf-8") + "not-json\n[]\n", encoding="utf-8")
    rows = local_audit.read_audit(limit=10)
    assert rows[0]["detail"]["truncated"] is True
    assert rows[1]["action"] == "model_benchmark"

    assert len(local_audit.read_audit(limit=0)) == 2

    original_read_text = local_audit.Path.read_text

    def broken_read_text(self, *args, **kwargs):
        if str(self).endswith("local-audit.jsonl"):
            raise OSError("cannot read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(local_audit.Path, "read_text", broken_read_text)
    assert local_audit.read_audit() == []

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty-data"))
    assert local_audit.read_audit() == []


def test_operator_checks_include_read_only_container_plan(monkeypatch, tmp_path):
    from src import operator_checks

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "cleverly-test")
    monkeypatch.setenv("CLEVERLY_CONTAINER_NAME", "cleverly-app")
    monkeypatch.setattr(
        operator_checks,
        "evaluate_offline_policy",
        lambda include_db=True: {
            "checks": [],
            "strict": True,
            "offline": True,
            "break_glass": False,
        },
    )
    monkeypatch.setattr(operator_checks, "get_setting", lambda *_args, **_kwargs: "", raising=False)
    monkeypatch.setattr(operator_checks.Path, "exists", lambda _self: False)

    result = operator_checks.run_operator_checks()
    plan = result["container_plan"]

    assert plan["source"] == "compose manifest and environment"
    assert plan["compose_project"] == "cleverly-test"
    assert plan["docker_socket_mounted"] is False
    assert any(
        service["compose_service"] == "cleverly"
        and service["container_name"] == "cleverly-app"
        and service["required"] is True
        for service in plan["services"]
    )
    assert any(service["compose_service"] == "chromadb" and service["profile"] == "support" for service in plan["services"])
    assert plan["host_commands"][0]["risk"] == "read-only"
    assert "docker compose ps" in {command["command"] for command in plan["host_commands"]}


def test_operator_service_snapshot_uses_read_only_local_probes(monkeypatch, tmp_path):
    from urllib import error as url_error

    from src import operator_checks

    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    worker_dir = data_dir / "code-workspaces" / ".worker"
    worker_dir.mkdir(parents=True)
    logs_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("LOG_DIR", str(logs_dir))
    monkeypatch.setenv("CODE_WORKSPACE_RUNNER", "worker")
    monkeypatch.setenv("CODE_WORKSPACE_WORKER_DIR", str(worker_dir))
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("CHROMADB_HOST", "chromadb")
    monkeypatch.setenv("CHROMADB_PORT", "8000")
    monkeypatch.setenv("SEARXNG_INSTANCE", "https://search.example.test")

    called = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return b"ok"

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        called.append(url)
        if "ollama" in url or "chromadb" in url:
            return Response()
        raise url_error.URLError("not running")

    monkeypatch.setattr(operator_checks.url_request, "urlopen", fake_urlopen)
    monkeypatch.setattr(operator_checks, "_docker_like", lambda: False)

    snapshot = operator_checks.run_operator_service_snapshot()
    by_id = {item["id"]: item for item in snapshot["services"]}

    assert snapshot["summary"]["error"] == 0
    assert by_id["data-dir"]["state"] == "ok"
    assert by_id["code-worker-queue"]["state"] == "ok"
    assert by_id["ollama"]["state"] == "ok"
    assert by_id["chromadb"]["state"] == "ok"
    assert by_id["searxng"]["state"] == "warn"
    assert "External endpoint configured but not probed" in by_id["searxng"]["detail"]
    assert all("search.example.test" not in url for url in called)


def test_operator_runtime_plan_is_read_only_and_profiles_resource_roots(monkeypatch, tmp_path):
    from collections import namedtuple

    from src.operator_runtime import run_operator_runtime_plan

    Usage = namedtuple("Usage", "total used free")
    data_root = tmp_path / "data"
    logs_root = tmp_path / "logs"
    training_root = data_root / "training"
    model_root = data_root / "models"
    code_root = data_root / "code-workspaces"
    tmp_root = tmp_path / "tmp"
    for path in (data_root, logs_root, training_root, model_root, code_root, tmp_root):
        path.mkdir(parents=True)
    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("CLEVERLY_TMPFS_SIZE", "512m")
    monkeypatch.setenv("CLEVERLY_PIDS_LIMIT", "256")

    def fake_disk_usage(_path):
        return Usage(total=100 * 1024 * 1024 * 1024, used=80 * 1024 * 1024 * 1024, free=20 * 1024 * 1024 * 1024)

    plan = run_operator_runtime_plan(
        "alice",
        data_root=data_root,
        logs_root=logs_root,
        docker_like=True,
        disk_usage=fake_disk_usage,
        memory_info={
            "total_bytes": 16 * 1024 * 1024 * 1024,
            "available_bytes": 12 * 1024 * 1024 * 1024,
        },
        cgroup_info={
            "memory_limit_bytes": 8 * 1024 * 1024 * 1024,
            "memory_current_bytes": 4 * 1024 * 1024 * 1024,
            "pids_max": "512",
            "pids_current": 48,
        },
        roots=[
            {"id": "data", "label": "App data", "path": data_root, "required": True, "role": "data"},
            {"id": "logs", "label": "App logs", "path": logs_root, "required": False, "role": "logs"},
            {"id": "training", "label": "Training", "path": training_root, "required": False, "role": "training"},
            {"id": "models", "label": "Models", "path": model_root, "required": False, "role": "models"},
            {"id": "code-workspaces", "label": "Code", "path": code_root, "required": False, "role": "code"},
            {"id": "tmp", "label": "Temp", "path": tmp_root, "required": True, "role": "temp"},
        ],
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    disk_ids = {row["id"] for row in plan["disk_rows"]}

    assert plan["mode"] == "read-only-runtime-resource-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["docker_like"] is True
    assert plan["summary"]["offline"] is True
    assert plan["summary"]["root_count"] == 6
    assert plan["summary"]["existing_root_count"] == 6
    assert plan["summary"]["missing_required_count"] == 0
    assert plan["summary"]["low_space_root_count"] == 0
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["reads_file_contents"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["deletes_files"] is False
    assert plan["summary"]["downloads_models"] is False
    assert plan["summary"]["pulls_images"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/runtime-plan" in api_paths
    assert "/api/runtime" in api_paths
    assert {"data", "training", "models", "code-workspaces", "tmp"} <= disk_ids
    assert all(row["executes"] is False for row in plan["disk_rows"])
    assert all(row["executes"] is False for row in plan["job_rows"])
    assert any(row["requires_approval"] is True for row in plan["job_rows"])
    assert "does not run shell commands" in plan["approval"]["policy"]
    assert plan["paths"]["data_root"] == str(data_root)
    assert plan["paths"]["logs_root"] == str(logs_root)


def test_operator_repair_plan_is_read_only_and_approval_gated(monkeypatch):
    from src import operator_repair

    monkeypatch.setattr(
        operator_repair,
        "run_operator_service_snapshot",
        lambda: {
            "summary": {"ok": 1, "warn": 0, "error": 1, "loading": 1, "required": 2},
            "services": [
                {
                    "id": "cleverly-api",
                    "label": "Cleverly app API",
                    "state": "ok",
                    "detail": "healthy",
                    "required": True,
                    "kind": "app",
                    "target": "in-process",
                },
                {
                    "id": "data-dir",
                    "label": "App data volume",
                    "state": "error",
                    "detail": "/app/data missing",
                    "required": True,
                    "kind": "path",
                    "target": "/app/data",
                },
                {
                    "id": "chromadb",
                    "label": "ChromaDB vector service",
                    "state": "loading",
                    "detail": "No local endpoint configured",
                    "required": False,
                    "kind": "http",
                    "target": "",
                },
            ],
        },
    )
    monkeypatch.setattr(
        operator_repair,
        "run_operator_checks",
        lambda: {
            "container_plan": {
                "source": "compose manifest and environment",
                "compose_project": "cleverly-test",
                "docker_socket_mounted": False,
                "services": [],
                "host_commands": [
                    {"label": "List compose service state", "risk": "read-only", "command": "docker compose ps"},
                    {
                        "label": "Inspect core service logs",
                        "risk": "read-only",
                        "command": "docker compose logs --tail=120 cleverly cleverly_code_worker cleverly_proxy",
                    },
                    {
                        "label": "Recreate app service only",
                        "risk": "approval-required",
                        "command": "docker compose up -d --force-recreate --no-deps cleverly",
                    },
                    {
                        "label": "Start optional support services without pulling",
                        "risk": "approval-required",
                        "command": "docker compose --profile support up -d --no-build --pull never",
                    },
                ],
            },
        },
    )

    plan = operator_repair.run_operator_repair_plan()

    assert plan["mode"] == "read-only-local-repair-plan"
    assert plan["summary"]["state"] == "error"
    assert plan["summary"]["required_issues"] == 1
    assert plan["approval"]["required"] is True
    assert plan["paths"]["data"] == "/app/data"
    assert all(step["executes"] is False for step in plan["steps"])
    assert all(command["executes"] is False for command in plan["host_commands"])
    assert any(
        step["id"] == "repair-core-service"
        and step["approval_required"] is True
        and "docker compose up" in step["command"]
        for step in plan["steps"]
    )
    assert any(
        step["id"] == "start-support-services"
        and step["approval_required"] is True
        and "--pull never" in step["command"]
        for step in plan["steps"]
    )
    assert "never executes host Docker commands" in plan["approval"]["policy"]


def test_operator_note_task_draft_is_read_only_and_task_compatible():
    from src.operator_note_tasks import run_operator_note_task_draft

    now = dt.datetime(2026, 6, 21, 8, 30, tzinfo=dt.timezone.utc)
    plan = run_operator_note_task_draft(
        "alice",
        notes=[
            {
                "id": "empty",
                "owner": "alice",
                "title": "",
                "content": "",
                "items": [],
                "updated_at": "2026-06-20T12:00:00",
            },
            {
                "id": "n1",
                "owner": "alice",
                "title": "Launch checklist",
                "content": "Verify the local operator console before sharing.",
                "items": [{"text": "Run smoke checks", "done": False}],
                "updated_at": "2026-06-21T12:00:00",
            },
        ],
        now=now,
    )

    draft = plan["draft"]

    assert plan["mode"] == "read-only-note-task-draft"
    assert plan["summary"]["creates_task"] is False
    assert plan["summary"]["notes"] == 2
    assert plan["summary"]["selected"] is True
    assert plan["selected_note"]["id"] == "n1"
    assert draft["name"] == "Follow up: Launch checklist"
    assert draft["task_type"] == "llm"
    assert draft["trigger_type"] == "schedule"
    assert draft["schedule"] == "once"
    assert draft["scheduled_date"].startswith("2026-06-22T09:00:00")
    assert draft["output_target"] == "session"
    assert draft["notifications_enabled"] is True
    assert "Verify the local operator console" in draft["prompt"]
    assert "Run smoke checks" in draft["prompt"]
    assert plan["approval"]["required_to_save"] is True
    assert "does not create" in plan["approval"]["policy"]
    assert any(row["selected"] is True and row["draft"]["name"] == draft["name"] for row in plan["candidates"])


def test_operator_change_brief_is_read_only_and_lists_local_evidence():
    from src.operator_change_brief import run_operator_change_brief

    now = dt.datetime(2026, 6, 21, 15, 30, tzinfo=dt.timezone.utc)
    plan = run_operator_change_brief(
        "alice",
        workspaces=[
            {
                "id": "repo-1",
                "name": "Cleverly",
                "owner": "alice",
                "path": "/app/data/code-workspaces/repo-1",
                "updated_at": "2026-06-21T12:00:00Z",
            },
            {
                "id": "old-repo",
                "name": "Old Repo",
                "owner": "alice",
                "path": "/app/data/code-workspaces/old-repo",
                "updated_at": "2026-06-18T12:00:00Z",
            },
            {
                "id": "other-user",
                "name": "Other User",
                "owner": "bob",
                "updated_at": "2026-06-21T12:00:00Z",
            },
        ],
        activity=[
            {
                "id": "act-1",
                "owner": "alice",
                "title": "Opened Change Brief",
                "status": "ok",
                "detail": "local command",
                "updated_at": "2026-06-21T13:00:00Z",
            },
            {
                "id": "old-act",
                "owner": "alice",
                "title": "Old command",
                "updated_at": "2026-06-18T13:00:00Z",
            },
        ],
        now=now,
    )

    commands = {row["command"] for row in plan["evidence_commands"]}

    assert plan["mode"] == "read-only-change-brief"
    assert plan["window"]["start"] == "2026-06-20T00:00:00Z"
    assert plan["summary"]["workspace_count"] == 2
    assert plan["summary"]["changed_workspace_count"] == 1
    assert plan["summary"]["activity_count"] == 1
    assert plan["summary"]["creates_changes"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["workspace_rows"][0]["id"] == "repo-1"
    assert plan["changed_workspace_rows"][0]["id"] == "repo-1"
    assert plan["activity_rows"][0]["id"] == "act-1"
    assert "git status --short" in commands
    assert "git diff --stat" in commands
    assert all(command["executes"] is False for command in plan["evidence_commands"])
    assert plan["approval"]["required"] is False
    assert "does not run shell commands" in plan["approval"]["policy"]
    assert plan["paths"]["workspaces"] == "data/code-workspaces/workspaces.json"


def test_operator_backup_plan_is_read_only_and_approval_gated(tmp_path):
    from src.operator_backup import run_operator_backup_plan

    data_root = tmp_path / "data"
    logs_root = tmp_path / "logs"
    for path in (
        data_root / "personal_docs",
        data_root / "uploads",
        data_root / "gallery",
        data_root / "deep_research",
        data_root / "code-workspaces",
        data_root / "training",
        data_root / "models",
        logs_root,
    ):
        path.mkdir(parents=True)
    for path in (
        data_root / "app.db",
        data_root / "auth.json",
        data_root / "sessions.json",
        data_root / "operator_activity.json",
    ):
        path.write_text("{}", encoding="utf-8")

    plan = run_operator_backup_plan(
        "alice",
        data_root=data_root,
        logs_root=logs_root,
        backup_audit=[
            {
                "action": "encrypted_backup_exported",
                "user": "alice",
                "timestamp": "2026-06-21T13:00:00Z",
                "detail": {"filename": "cleverly_encrypted_backup.json"},
            }
        ],
    )

    commands = {row["command"] for row in plan["host_commands"]}
    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-backup-verify-plan"
    assert plan["summary"]["encrypted_export_sections"] == 6
    assert plan["summary"]["full_snapshot_items"] == 12
    assert plan["summary"]["audit_count"] == 1
    assert plan["summary"]["creates_backup"] is False
    assert plan["summary"]["restores_data"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["requires_export_approval"] is True
    assert all(row["executes"] is False for row in plan["sequence_rows"])
    assert all(row["executes"] is False for row in plan["host_commands"])
    assert "python scripts/cleverly-backup snapshot --pretty" in commands
    assert "python scripts/cleverly-backup verify PATH --pretty" in commands
    assert "/api/backup/encrypted/export" in api_paths
    assert "/api/backup/encrypted/import" in api_paths
    assert plan["approval"]["required"] is True
    assert "does not export" in plan["approval"]["policy"]
    assert plan["paths"]["backup_script"] == "scripts/cleverly-backup"


def test_operator_file_ops_plan_is_read_only_and_approval_gated(tmp_path):
    from src.operator_file_ops import run_operator_file_ops_plan

    data_root = tmp_path / "data"
    logs_root = tmp_path / "logs"
    docs_root = data_root / "personal_docs"
    uploads_root = data_root / "uploads"
    code_root = data_root / "code-workspaces"
    for path in (docs_root, uploads_root, code_root, logs_root):
        path.mkdir(parents=True)
    (data_root / "app.db").write_text("sqlite", encoding="utf-8")
    (data_root / "auth.json").write_text("{}", encoding="utf-8")
    (docs_root / "note.md").write_text("local doc", encoding="utf-8")
    (uploads_root / "upload.txt").write_text("upload", encoding="utf-8")

    plan = run_operator_file_ops_plan(
        "alice",
        data_root=data_root,
        logs_root=logs_root,
        roots=[
            {"id": "data", "path": data_root, "description": "Data root"},
            {"id": "docs", "path": docs_root, "description": "Docs"},
            {"id": "uploads", "path": uploads_root, "description": "Uploads"},
            {"id": "auth", "path": data_root / "auth.json", "description": "Auth", "sensitive": True},
            {"id": "missing", "path": data_root / "missing", "description": "Missing"},
        ],
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-file-ops-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["root_count"] == 5
    assert plan["summary"]["existing_root_count"] == 4
    assert plan["summary"]["missing_required_count"] == 1
    assert plan["summary"]["sensitive_root_count"] == 1
    assert plan["summary"]["direct_file_count"] >= 3
    assert plan["summary"]["reads_file_contents"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["copies_files"] is False
    assert plan["summary"]["moves_files"] is False
    assert plan["summary"]["deletes_files"] is False
    assert plan["summary"]["uploads_files"] is False
    assert plan["summary"]["imports_files"] is False
    assert plan["summary"]["indexes_files"] is False
    assert plan["summary"]["exports_files"] is False
    assert plan["summary"]["restores_files"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["requires_write_approval"] is True
    assert plan["summary"]["requires_delete_approval"] is True
    assert "/api/operator/file-ops-plan" in api_paths
    assert "/api/personal/upload" in api_paths
    assert "/api/personal/file" in api_paths
    assert "/api/backup/encrypted/export" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["title"] == "Delete/restore gate" for row in plan["operation_rows"])
    assert plan["sensitive_rows"][0]["id"] == "auth"
    assert "does not read file contents" in plan["approval"]["policy"]
    assert plan["paths"]["data_root"] == str(data_root)


def test_operator_activity_plan_audits_timeline_without_execution(tmp_path):
    from src.operator_activity import run_operator_activity_plan

    records = [
        {
            "id": "a1",
            "owner": "alice",
            "command_id": "run-tests",
            "title": "Run Tests",
            "status": "success",
            "detail": "Tests passed",
            "trust": "approval",
            "trust_mode": "ask",
            "events": [{"status": "started", "detail": "Queued"}, {"status": "success", "detail": "Done"}],
            "updated_at": "2026-06-21T12:00:00Z",
        },
        {
            "id": "a2",
            "owner": "alice",
            "command_id": "repair-container",
            "title": "Repair Container",
            "status": "failed",
            "detail": "Docker restart failed",
            "updated_at": "2026-06-21T12:01:00Z",
        },
        {
            "id": "b1",
            "owner": "bob",
            "command_id": "other",
            "status": "success",
        },
    ]

    plan = run_operator_activity_plan(
        "alice",
        records=records,
        activity_path=tmp_path / "operator_activity.json",
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-activity-timeline-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["record_count"] == 2
    assert plan["summary"]["event_count"] == 2
    assert plan["summary"]["detail_count"] == 2
    assert plan["summary"]["trust_count"] == 1
    assert plan["summary"]["retryable_count"] == 2
    assert plan["summary"]["failure_count"] == 1
    assert plan["summary"]["missing_trust_count"] == 1
    assert plan["summary"]["writes_activity"] is False
    assert plan["summary"]["deletes_activity"] is False
    assert plan["summary"]["retries_commands"] is False
    assert plan["summary"]["runs_commands"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/activity-plan" in api_paths
    assert "/api/operator/activity/{activity_id}" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert plan["gap_rows"]
    assert "does not write records" in plan["approval"]["policy"]


def test_operator_code_test_plan_infers_commands_without_execution(tmp_path, monkeypatch):
    from src.operator_code import run_operator_code_test_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    monkeypatch.setenv("CODE_WORKSPACE_RUNNER", "worker")
    monkeypatch.setenv("CODE_WORKSPACE_WORKER_DIR", str(tmp_path / "worker"))

    plan = run_operator_code_test_plan(
        "alice",
        workspaces=[
            {
                "id": "repo-1",
                "name": "Demo Repo",
                "owner": "alice",
                "path": str(repo),
                "updated_at": "2026-06-21T12:00:00Z",
            },
            {
                "id": "other",
                "name": "Other Repo",
                "owner": "bob",
                "path": str(repo),
            },
        ],
    )

    commands = {row["command"] for row in plan["candidate_commands"] if row["command"]}

    assert plan["mode"] == "read-only-code-test-plan"
    assert plan["summary"]["workspace_count"] == 1
    assert plan["summary"]["candidate_command_count"] == 2
    assert plan["summary"]["runner"] == "worker"
    assert plan["summary"]["runs_tests"] is False
    assert plan["summary"]["changes_files"] is False
    assert plan["summary"]["creates_snapshot"] is False
    assert plan["summary"]["requires_run_approval"] is True
    assert plan["workspace_rows"][0]["id"] == "repo-1"
    assert "npm test" in commands
    assert "python -m pytest -q" in commands
    assert all(row["executes"] is False for row in plan["candidate_commands"])
    assert all(row["executes"] is False for row in plan["sequence_rows"])
    assert plan["approval"]["required"] is True
    assert "does not run tests" in plan["approval"]["policy"]
    assert plan["paths"]["worker_queue"] == str(tmp_path / "worker")


def test_operator_build_watch_plan_is_read_only_and_approval_gated(tmp_path, monkeypatch):
    from src.operator_build_watch import run_operator_build_watch_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({"scripts": {"build": "vite build", "test": "vitest run"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODE_WORKSPACE_RUNNER", "worker")
    monkeypatch.setenv("CODE_WORKSPACE_WORKER_DIR", str(tmp_path / "worker"))

    plan = run_operator_build_watch_plan(
        "alice",
        workspaces=[
            {
                "id": "repo-1",
                "name": "Demo Repo",
                "owner": "alice",
                "path": str(repo),
                "updated_at": "2026-06-21T12:00:00Z",
            },
            {
                "id": "other",
                "name": "Other Repo",
                "owner": "bob",
                "path": str(repo),
            },
        ],
    )

    commands = {row["command"] for row in plan["candidate_commands"] if row["command"]}
    api_paths = {row["path_template"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-build-watch-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["workspace_count"] == 1
    assert plan["summary"]["candidate_command_count"] == 2
    assert plan["summary"]["runner"] == "worker"
    assert plan["summary"]["starts_loop"] is False
    assert plan["summary"]["runs_build"] is False
    assert plan["summary"]["edits_files"] is False
    assert plan["summary"]["creates_snapshot"] is False
    assert plan["summary"]["restores_snapshot"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["requires_loop_approval"] is True
    assert plan["summary"]["route_command_id"] == "watch-build-until-green"
    assert plan["summary"]["approval_command_id"] == "request-build-watch-loop"
    assert "npm run build" in commands
    assert "npm test" in commands
    assert "/api/code-workspaces/{workspace_id}/run" in api_paths
    assert all(row["executes"] is False for row in plan["candidate_commands"])
    assert all(row["executes"] is False for row in plan["sequence_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["sequence_rows"])
    assert plan["approval"]["required"] is True
    assert "does not start loops" in plan["approval"]["policy"]
    assert plan["paths"]["worker_queue"] == str(tmp_path / "worker")


def test_operator_document_search_plan_is_read_only_and_local_first(tmp_path):
    from src.operator_documents import run_operator_document_search_plan

    personal_dir = tmp_path / "personal_docs"
    personal_dir.mkdir()

    class PersonalDocs:
        index = [
            {
                "name": "operator-console.md",
                "path": str(personal_dir / "operator-console.md"),
                "size": 128,
                "chunks": ["Cleverly searches local documents through RAG, then keyword fallback."],
            }
        ]

        def get_indexed_directories(self):
            return [str(personal_dir / "projects")]

        def get_stats(self):
            return {
                "total_documents": 1,
                "total_chunks": 1,
                "total_size_bytes": 128,
                "directories_count": 2,
                "base_directory": str(personal_dir),
                "additional_directories": [str(personal_dir / "projects")],
            }

    class Rag:
        def get_stats(self):
            return {"embedding_model": "local-hash-384", "total_documents": 1, "chunks": 1}

    plan = run_operator_document_search_plan(
        "alice",
        personal_docs_manager=PersonalDocs(),
        rag_manager=Rag(),
        data_root=tmp_path,
        personal_dir=personal_dir,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-document-search-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["document_count"] == 1
    assert plan["summary"]["chunk_count"] == 1
    assert plan["summary"]["directory_count"] == 2
    assert plan["summary"]["vector_ready"] is True
    assert plan["summary"]["keyword_ready"] is True
    assert plan["summary"]["runs_search"] is False
    assert plan["summary"]["reads_query"] is False
    assert plan["summary"]["indexes_files"] is False
    assert plan["summary"]["changes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["route_command_id"] == "search-local-documents"
    assert "/api/personal/search" in api_paths
    assert "/api/personal/reload" in api_paths
    assert all(row["executes"] is False for row in plan["route_rows"])
    assert all(row["executes"] is False for row in plan["guard_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["guard_rows"])
    assert "does not run a query" in plan["approval"]["policy"]
    assert plan["paths"]["personal_docs"] == str(personal_dir)


def test_operator_voice_plan_is_read_only_and_permission_gated(tmp_path):
    from src.operator_voice import run_operator_voice_plan

    plan = run_operator_voice_plan(
        "alice",
        settings={
            "stt_enabled": True,
            "stt_provider": "browser",
            "stt_model": "base",
            "tts_enabled": True,
            "tts_provider": "browser",
            "tts_model": "browser",
            "tts_voice": "",
            "tts_speed": "1",
        },
        stt_stats={"available": True, "provider": "browser", "model": "Browser (Web Speech API)"},
        tts_stats={"available": True, "provider": "browser", "model": "Browser (Web Speech API)"},
        features={"external_model_endpoints": False},
        offline=True,
        data_root=tmp_path,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-voice-io-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["stt_provider"] == "browser"
    assert plan["summary"]["tts_provider"] == "browser"
    assert plan["summary"]["browser_voice_configured"] is True
    assert plan["summary"]["voice_command_ready"] is True
    assert plan["summary"]["voice_io_ready"] is True
    assert plan["summary"]["requires_browser_permission"] is True
    assert plan["summary"]["requires_user_activation"] is True
    assert plan["summary"]["starts_microphone"] is False
    assert plan["summary"]["records_audio"] is False
    assert plan["summary"]["transcribes_audio"] is False
    assert plan["summary"]["speaks_audio"] is False
    assert plan["summary"]["synthesizes_audio"] is False
    assert plan["summary"]["changes_settings"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["route_command_id"] == "start-voice-command"
    assert plan["summary"]["setup_command_id"] == "enable-browser-voice-mode"
    assert "/api/operator/voice-plan" in api_paths
    assert "/api/stt/transcribe" in api_paths
    assert "/api/tts/synthesize" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["permission_rows"])
    assert any(row["requires_approval"] is True for row in plan["input_rows"])
    assert "does not start the microphone" in plan["approval"]["policy"]
    assert plan["paths"]["tts_cache"] == str(tmp_path / "tts_cache")


def test_operator_autonomy_plan_audits_policy_without_execution():
    from src.operator_autonomy import run_operator_autonomy_plan

    commands = [
        {
            "id": "summarize-today",
            "title": "Today Briefing",
            "category": "Briefing",
            "trust": "local",
            "keywords": ["summarize today"],
        },
        {
            "id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "category": "Automation",
            "trust": "approval",
            "workflow": True,
            "keywords": ["watch build", "build passes"],
        },
        {
            "id": "open-research",
            "title": "Open Research",
            "category": "Research",
            "trust": "network",
        },
        {
            "id": "delete-activity",
            "title": "Delete Activity",
            "category": "Safety",
            "trust": "danger",
            "alwaysAsk": True,
        },
    ]
    workflows = [
        {
            "id": "watch-build-until-green",
            "phrase": "Watch this repo until the build passes.",
            "commandId": "request-build-watch-loop",
            "approvalId": "request-build-watch-loop",
            "expectedRouteId": "request-build-watch-loop",
            "proof": "Ask before starting the loop",
            "state": "ok",
        }
    ]
    activity = [
        {
            "id": "a1",
            "owner": "alice",
            "command_id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "status": "pending_approval",
            "events": [{"status": "approved", "detail": "Approved by user"}],
        },
        {
            "id": "a2",
            "owner": "alice",
            "command_id": "open-research",
            "title": "Research",
            "status": "failed",
            "detail": "Blocked by offline policy",
        },
    ]

    plan = run_operator_autonomy_plan(
        "alice",
        commands=commands,
        workflows=workflows,
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        activity=activity,
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-autonomy-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["command_count"] == 4
    assert plan["summary"]["workflow_count"] == 1
    assert plan["summary"]["workflow_command_count"] == 1
    assert plan["summary"]["ask_command_count"] == 3
    assert plan["summary"]["network_command_count"] == 1
    assert plan["summary"]["danger_command_count"] == 1
    assert plan["summary"]["workflow_ask_count"] == 1
    assert plan["summary"]["route_ready_count"] == 1
    assert plan["summary"]["approval_gated_count"] == 1
    assert plan["summary"]["pending_count"] == 1
    assert plan["summary"]["failure_count"] == 1
    assert plan["summary"]["retryable_count"] == 2
    assert plan["summary"]["decision_count"] == 1
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["approves_commands"] is False
    assert plan["summary"]["retries_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["changes_policy"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/autonomy-plan" in api_paths
    assert "/api/operator/policy" in api_paths
    assert "/api/operator/routes" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["permission_rows"])
    assert "does not route commands" in plan["approval"]["policy"]


def test_operator_memory_plan_is_read_only_and_profiles_local_memory():
    from src.operator_memory import run_operator_memory_plan

    memories = [
        {
            "id": "m1",
            "text": "User prefers local-first tools by default.",
            "category": "preference",
            "owner": "alice",
            "pinned": True,
        },
        {
            "id": "m2",
            "text": "User is building Cleverly into a local AI operating console.",
            "category": "project",
            "owner": "alice",
        },
        {
            "id": "m3",
            "text": "User chose llama3.2:3b as the primary Ollama model.",
            "category": "decision",
            "owner": "alice",
        },
        {
            "id": "m4",
            "text": "When containers are unhealthy, review the repair plan before restart.",
            "category": "workflow",
            "owner": "alice",
        },
        {
            "id": "m5",
            "text": "Call me Alice.",
            "category": "identity",
            "owner": "alice",
        },
    ]

    plan = run_operator_memory_plan(
        "alice",
        memories=memories,
        notes=[{"id": "n1", "title": "Daily note"}],
        prefs={
            "memory_enabled": True,
            "auto_memory": True,
            "skills_enabled": True,
            "default_model": "llama3.2:3b",
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    bucket_counts = {bucket["key"]: bucket["count"] for bucket in plan["buckets"]}

    assert plan["mode"] == "read-only-memory-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["memory_count"] == 5
    assert plan["summary"]["note_count"] == 1
    assert plan["summary"]["pinned_count"] == 1
    assert plan["summary"]["profile_complete_count"] == 5
    assert plan["summary"]["profile_gap_count"] == 0
    assert plan["summary"]["memory_enabled"] is True
    assert plan["summary"]["auto_memory"] is True
    assert plan["summary"]["skills_enabled"] is True
    assert plan["summary"]["default_model"] == "llama3.2:3b"
    assert bucket_counts["identity"] == 1
    assert bucket_counts["preferences"] == 1
    assert bucket_counts["projects"] == 1
    assert bucket_counts["decisions"] == 1
    assert bucket_counts["workflows"] == 1
    assert plan["summary"]["writes_memories"] is False
    assert plan["summary"]["adds_memories"] is False
    assert plan["summary"]["imports_files"] is False
    assert plan["summary"]["extracts_with_model"] is False
    assert plan["summary"]["audits_with_model"] is False
    assert plan["summary"]["deletes_memories"] is False
    assert plan["summary"]["runs_automation"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/memory-plan" in api_paths
    assert "/api/memory/add" in api_paths
    assert "/api/memory/{memory_id}" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["guard_rows"])
    assert "does not add memories" in plan["approval"]["policy"]


def test_operator_workday_plan_is_read_only_and_profiles_local_schedule():
    from datetime import datetime, timezone

    from src.operator_workday import run_operator_workday_plan

    now = datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc)
    plan = run_operator_workday_plan(
        "alice",
        now=now,
        tasks=[
            {
                "id": "t1",
                "name": "Morning review",
                "status": "active",
                "next_run": "2026-06-21T16:00:00Z",
            },
            {
                "id": "t2",
                "name": "Overdue follow-up",
                "status": "active",
                "next_run": "2026-06-21T12:00:00Z",
            },
            {
                "id": "t3",
                "name": "Paused task",
                "status": "paused",
                "next_run": "2026-06-21T17:00:00Z",
            },
        ],
        runs=[
            {"id": "r1", "task_id": "t1", "status": "running", "started_at": "2026-06-21T14:59:00Z"},
            {"id": "r2", "task_id": "t2", "status": "error", "started_at": "2026-06-21T14:00:00Z"},
        ],
        events=[
            {"uid": "e1", "summary": "Standup", "start": "2026-06-21T17:00:00Z", "end": "2026-06-21T17:30:00Z"},
            {"uid": "e2", "summary": "Tomorrow", "start": "2026-06-22T17:00:00Z", "end": "2026-06-22T17:30:00Z"},
        ],
        notes=[
            {"id": "n1", "title": "Follow-up note", "content": "Create task from this"},
            {"id": "n2", "title": "Empty note", "content": ""},
        ],
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-workday-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["task_count"] == 3
    assert plan["summary"]["active_task_count"] == 2
    assert plan["summary"]["due_today_count"] == 2
    assert plan["summary"]["overdue_count"] == 1
    assert plan["summary"]["active_run_count"] == 1
    assert plan["summary"]["failed_run_count"] == 1
    assert plan["summary"]["calendar_event_count"] == 2
    assert plan["summary"]["today_event_count"] == 1
    assert plan["summary"]["note_task_candidate_count"] == 1
    assert plan["summary"]["creates_tasks"] is False
    assert plan["summary"]["updates_tasks"] is False
    assert plan["summary"]["runs_tasks"] is False
    assert plan["summary"]["creates_calendar_events"] is False
    assert plan["summary"]["syncs_calendar"] is False
    assert plan["summary"]["edits_notes"] is False
    assert plan["summary"]["sends_notifications"] is False
    assert plan["summary"]["runs_automation"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["requires_write_approval"] is True
    assert "/api/operator/workday-plan" in api_paths
    assert "/api/operator/briefing" in api_paths
    assert "/api/tasks?include_last_run=true" in api_paths
    assert "/api/calendar/events" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["title"] == "Workday write boundary" for row in plan["work_rows"])
    assert "does not create tasks" in plan["approval"]["policy"]
    assert plan["paths"]["tasks"] == "data/app.db:scheduled_tasks"


def test_operator_model_snapshot_summarizes_local_training(monkeypatch, tmp_path):
    from src import operator_models

    primary_manifest = tmp_path / "cleverly-primary-model.json"
    primary_manifest.write_text(json.dumps({"primary_model": "llama3.2:3b", "source": "test"}), encoding="utf-8")
    training_root = tmp_path / "training"

    monkeypatch.setattr(operator_models, "PRIMARY_MODEL_FILE", primary_manifest)
    monkeypatch.setattr(operator_models, "ensure_training_dirs", lambda: training_root)
    monkeypatch.setattr(operator_models, "load_settings", lambda: {"default_model": ""})
    monkeypatch.setattr(operator_models, "load_features", lambda: {"external_model_endpoints": False})
    monkeypatch.setattr(
        operator_models,
        "_endpoint_rows",
        lambda: [
            {
                "id": "ollama",
                "name": "Ollama",
                "base_url": "http://ollama:11434/v1",
                "is_enabled": True,
                "local": True,
                "scope": "local",
                "model_count": 1,
                "models": ["llama3.2:3b"],
            }
        ],
    )
    monkeypatch.setattr(
        operator_models,
        "list_datasets",
        lambda: [{"id": "dataset-1", "name": "Dataset", "chars": 128}],
    )
    monkeypatch.setattr(
        operator_models,
        "list_artifacts",
        lambda: [{"id": "artifact-1", "name": "Tiny Model", "type": "char-ngram"}],
    )
    monkeypatch.setattr(
        operator_models,
        "finetune_status",
        lambda: {
            "dependencies": {"available": True, "missing": []},
            "trainable_models": [{"id": "base-1", "name": "Base"}],
            "ollama_models": [{"id": "ollama-1", "name": "llama3.2:3b"}],
            "jobs": [{"job_id": "job-1", "status": "complete"}],
            "base_models_dir": str(training_root / "finetune" / "base-models"),
            "adapters_dir": str(training_root / "finetune" / "adapters"),
            "max_steps": 1000,
            "default_target_modules": "q_proj",
        },
    )

    snapshot = operator_models.run_operator_model_snapshot()

    assert snapshot["mode"] == "read-only-local"
    assert snapshot["primary"]["model"] == "llama3.2:3b"
    assert snapshot["endpoints"]["counts"]["local_enabled"] == 1
    assert snapshot["training"]["dataset_count"] == 1
    assert snapshot["training"]["artifact_count"] == 1
    assert snapshot["finetune"]["trainable_count"] == 1
    assert snapshot["finetune"]["job_counts"]["complete"] == 1
    assert snapshot["readiness"]["state"] == "ok"


def test_operator_model_ops_plan_is_read_only_and_approval_gated():
    from src.operator_model_ops import run_operator_model_ops_plan

    plan = run_operator_model_ops_plan(
        "alice",
        snapshot={
            "primary": {
                "model": "llama3.2:3b",
                "configured": True,
                "path": "data/cleverly-primary-model.json",
            },
            "endpoints": {
                "counts": {
                    "total": 2,
                    "enabled": 2,
                    "local_enabled": 1,
                    "external_enabled": 1,
                    "models": 3,
                },
                "items": [
                    {
                        "id": "ollama",
                        "name": "Ollama",
                        "base_url": "http://ollama:11434/v1",
                        "is_enabled": True,
                        "local": True,
                        "scope": "local",
                        "model_count": 1,
                    },
                    {
                        "id": "remote",
                        "name": "Remote",
                        "base_url": "https://models.example/v1",
                        "is_enabled": True,
                        "local": False,
                        "scope": "external",
                        "model_count": 2,
                    },
                ],
            },
            "training": {
                "dataset_count": 1,
                "artifact_count": 1,
                "datasets": [{"id": "dataset-1"}],
                "artifacts": [{"id": "artifact-1"}],
            },
            "finetune": {
                "dependencies": {"available": True, "missing": []},
                "trainable_count": 1,
                "ollama_runtime_count": 1,
                "job_counts": {"total": 2, "active": 1, "failed": 0, "complete": 1},
                "trainable_models": [{"id": "base-1"}],
                "ollama_models": [{"id": "ollama-1", "name": "llama3.2:3b"}],
            },
            "features": {"external_model_endpoints": True, "offline": False},
            "readiness": {"state": "warn", "warnings": ["external model endpoints are enabled"]},
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-model-ops-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["primary_model"] == "llama3.2:3b"
    assert plan["summary"]["endpoint_count"] == 2
    assert plan["summary"]["local_enabled_count"] == 1
    assert plan["summary"]["external_enabled_count"] == 1
    assert plan["summary"]["dataset_count"] == 1
    assert plan["summary"]["artifact_count"] == 1
    assert plan["summary"]["trainable_count"] == 1
    assert plan["summary"]["ollama_runtime_count"] == 1
    assert plan["summary"]["active_job_count"] == 1
    assert plan["summary"]["sets_primary_model"] is False
    assert plan["summary"]["registers_endpoints"] is False
    assert plan["summary"]["deletes_endpoints"] is False
    assert plan["summary"]["pulls_models"] is False
    assert plan["summary"]["downloads_models"] is False
    assert plan["summary"]["starts_serving"] is False
    assert plan["summary"]["benchmarks_models"] is False
    assert plan["summary"]["starts_training"] is False
    assert plan["summary"]["starts_finetune"] is False
    assert plan["summary"]["changes_settings"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["requires_action_approval"] is True
    assert "/api/operator/model-ops-plan" in api_paths
    assert "/api/offline-control/models/primary" in api_paths
    assert "/api/model/download" in api_paths
    assert "/api/model/serve" in api_paths
    assert "/api/training/finetune/jobs" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["title"] == "Model operation boundary" for row in plan["operation_rows"])
    assert "does not set the primary model" in plan["approval"]["policy"]
    assert plan["paths"]["model_endpoints"] == "data/app.db:model_endpoints"


def test_operator_training_plan_is_read_only_and_approval_gated():
    from src.operator_training import run_operator_training_plan

    plan = run_operator_training_plan(
        "alice",
        model_snapshot={
            "primary": {
                "model": "llama3.2:3b",
                "configured": True,
                "path": "data/cleverly-primary-model.json",
            },
            "endpoints": {"counts": {"local_enabled": 1, "enabled": 1}},
            "training": {
                "dataset_count": 1,
                "artifact_count": 1,
                "datasets": [{"id": "dataset-1", "name": "Dataset", "chars": 256}],
                "artifacts": [{"id": "artifact-1", "name": "Tiny Model", "type": "char-ngram"}],
                "paths": {
                    "root": "data/training",
                    "datasets": "data/training/datasets",
                    "artifacts": "data/training/artifacts",
                    "finetune_jobs": "data/training/finetune/jobs",
                    "finetune_adapters": "data/training/finetune/adapters",
                    "finetune_base_models": "data/training/finetune/base-models",
                },
            },
            "finetune": {
                "dependencies": {"available": True, "missing": []},
                "trainable_models": [{"id": "base-1", "name": "Base"}],
                "trainable_count": 1,
                "jobs": [{"job_id": "job-1", "status": "complete"}],
                "job_counts": {"total": 1, "active": 0, "failed": 0, "complete": 1},
            },
            "readiness": {"state": "ok", "ready": True},
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}

    assert plan["mode"] == "read-only-training-run-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["dataset_count"] == 1
    assert plan["summary"]["artifact_count"] == 1
    assert plan["summary"]["primary_model"] == "llama3.2:3b"
    assert plan["summary"]["local_model_ready"] is True
    assert plan["summary"]["lora_ready"] is True
    assert plan["summary"]["creates_dataset"] is False
    assert plan["summary"]["starts_training"] is False
    assert plan["summary"]["creates_model"] is False
    assert plan["summary"]["runs_finetune"] is False
    assert plan["summary"]["pulls_models"] is False
    assert plan["summary"]["changes_endpoints"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["requires_run_approval"] is True
    assert "/api/training/train" in api_paths
    assert "/api/training/finetune/jobs" in api_paths
    assert all(row["executes"] is False for row in plan["sequence_rows"])
    assert all(row["executes"] is False for row in plan["route_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert plan["approval"]["required"] is True
    assert "does not create datasets" in plan["approval"]["policy"]
    assert plan["paths"]["datasets"] == "data/training/datasets"


def test_operator_briefing_snapshot_builds_headlines(monkeypatch):
    from src import operator_briefing

    monkeypatch.setattr(operator_briefing, "_tasks", lambda owner: {
        "total": 2,
        "active": 1,
        "paused": 1,
        "due_today": 1,
        "items": [],
    })
    monkeypatch.setattr(operator_briefing, "_runs", lambda owner: {
        "total": 1,
        "failed": 0,
        "running": 0,
        "items": [],
    })
    monkeypatch.setattr(operator_briefing, "_events", lambda owner: {
        "total": 1,
        "today": 1,
        "items": [],
    })
    monkeypatch.setattr(operator_briefing, "_notes", lambda owner: {"total": 0, "pinned": 0, "items": []})
    monkeypatch.setattr(operator_briefing, "_memories", lambda owner: {"total": 0, "items": []})
    monkeypatch.setattr(operator_briefing, "_operator_activity", lambda owner: {
        "total": 3,
        "failed": 0,
        "items": [],
        "path": "data/operator_activity.json",
    })
    monkeypatch.setattr(operator_briefing, "_operator_workflows", lambda owner: {
        "configured": True,
        "loop_count": 9,
        "workflow_count": 9,
        "ready_count": 9,
        "approval_gated_count": 3,
        "updated_at": "now",
        "path": "data/operator_workflows.json",
    })
    monkeypatch.setattr(operator_briefing, "_model_summary", lambda: {
        "readiness": {"state": "ok", "summary": "model and training controls ready"},
    })
    monkeypatch.setattr(operator_briefing, "_service_summary", lambda: {
        "summary": {"ok": 8, "warn": 0, "error": 0},
        "services": [],
    })

    snapshot = operator_briefing.run_operator_briefing_snapshot("alice")

    assert snapshot["mode"] == "read-only-local"
    assert snapshot["owner"] == "alice"
    assert snapshot["sections"]["tasks"]["due_today"] == 1
    assert snapshot["sections"]["workflows"]["ready_count"] == 9
    titles = {row["title"] for row in snapshot["headline_rows"]}
    assert {"Tasks", "Calendar", "Automation routes", "Models and training"} <= titles


def test_operator_command_router_proves_approval_gated_target_route():
    from src.operator_command_router import resolve_operator_route, resolve_operator_route_matrix

    commands = [
        {
            "id": "open-container-repair-plan",
            "title": "Open Container Repair Plan",
            "subtitle": "Review local service health before repair",
            "category": "Safety",
            "trust": "local",
            "priority": 45,
            "keywords": ["container repair plan", "fix containers", "unhealthy services"],
        },
        {
            "id": "request-container-fix",
            "title": "Ask To Fix Container Health",
            "subtitle": "Prepare an approval-gated repair pass",
            "category": "Safety",
            "trust": "approval",
            "priority": 48,
            "keywords": ["check containers and fix", "fix anything unhealthy", "restart containers"],
        },
    ]
    workflows = [
        {
            "id": "container-health-target",
            "phrase": "Check the containers and fix anything unhealthy.",
            "title": "Approval-gated Container Repair Request",
            "area": "Services",
            "commandId": "open-container-repair-plan",
            "approvalId": "request-container-fix",
            "expectedRouteId": "request-container-fix",
            "proof": "Typed approval before any repair request",
            "state": "ok",
        },
    ]
    policy = {"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"}

    route = resolve_operator_route(
        "Cleverly, check containers and fix anything unhealthy.",
        commands,
        workflows,
        policy,
    )

    assert route["selected"]["id"] == "request-container-fix"
    assert route["selected"]["approval_required"] is True
    assert route["summary"]["approval_required"] is True
    assert route["selected"]["workflow_evidence"][0]["expected_route_id"] == "request-container-fix"

    matrix = resolve_operator_route_matrix(commands, workflows, policy)
    assert matrix["summary"]["total"] == 1
    assert matrix["summary"]["ready"] == 1
    assert matrix["rows"][0]["route_ready"] is True
    assert matrix["rows"][0]["approval_ready"] is True


def test_operator_command_router_prefers_expected_workflow_route_on_tie():
    from src.operator_command_router import resolve_operator_route, resolve_operator_route_matrix

    commands = [
        {
            "id": "watch-build-until-green",
            "title": "Open Build Watch Plan",
            "subtitle": "Review workspace before watching a repo",
            "category": "Automation",
            "trust": "local",
            "priority": 50,
            "keywords": ["watch build", "build passes", "watch this repo until the build passes"],
        },
        {
            "id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "subtitle": "Open the build loop and send the approval-gated repo request",
            "category": "Automation",
            "trust": "approval",
            "priority": 50,
            "keywords": ["watch build", "build passes", "watch this repo until the build passes"],
        },
    ]
    workflows = [
        {
            "id": "watch-build-until-green",
            "phrase": "Watch this repo until the build passes.",
            "commandId": "watch-build-until-green",
            "approvalId": "request-build-watch-loop",
            "expectedRouteId": "request-build-watch-loop",
            "proof": "Typed approval before loop request",
            "state": "ok",
        }
    ]
    policy = {"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"}

    route = resolve_operator_route("Watch this repo until the build passes.", commands, workflows, policy)
    matrix = resolve_operator_route_matrix(commands, workflows, policy)

    assert route["selected"]["id"] == "request-build-watch-loop"
    assert route["selected"]["approval_required"] is True
    assert "expected route" in route["selected"]["workflow_evidence"][0]["reason"]
    assert matrix["summary"]["ready"] == 1
    assert matrix["rows"][0]["selected_id"] == "request-build-watch-loop"


def test_operator_command_text_uses_backend_route_preflight_before_local_fallback():
    from pathlib import Path

    commands_js = Path("static/js/operatorCommands.js").read_text(encoding="utf-8")
    center_js = Path("static/js/commandCenter.js").read_text(encoding="utf-8")
    palette_js = Path("static/js/commandPalette.js").read_text(encoding="utf-8")
    code_workspace_js = Path("static/js/codeWorkspace.js").read_text(encoding="utf-8")
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    route_text_idx = commands_js.index("async function routeText")
    backend_call_idx = commands_js.index("backendRouteText(value, options)", route_text_idx)
    local_match_idx = commands_js.index("const command = commandForText(value);", route_text_idx)

    assert "requestJson('/api/operator/route'" in commands_js
    assert "function commandFromBackendRoute(route)" in commands_js
    assert backend_call_idx < local_match_idx
    assert "forceApproval: backendRoute?.selected?.approval_required === true" in commands_js
    assert "route_source: routeProof.source" in commands_js
    assert "route_path: routeProof.path" in commands_js
    assert "function recordActivity(record = {})" in commands_js
    assert "mirrorActivityRecord(activity);" in commands_js
    assert "recordActivity," in commands_js
    assert "function renderCommandRoutePreviewState(value, backendRoute = null)" in center_js
    assert "operatorCommands.backendRouteText(value, { source: 'dashboard-preview'" in center_js
    assert "root.dataset.routeSource = backendSelected ? 'backend'" in center_js
    assert "Backend route requires approval" in center_js
    assert "function renderPreviewState(query = '', backendRoute = null)" in palette_js
    assert "operatorCommands.backendRouteText(value, { source: 'palette-preview'" in palette_js
    assert "node.dataset.routeSource = backendSelected ? 'backend'" in palette_js
    assert "Backend route requires approval" in palette_js
    assert "async function runTypedRoute()" in palette_js
    assert "await operatorCommands.routeText(value, { source: 'palette' });" in palette_js
    enter_idx = palette_js.index("if (event.key === 'Enter')")
    enter_block = palette_js[enter_idx:palette_js.index("\n}\n\nfunction init", enter_idx)]
    assert "runTypedRoute();" in enter_block
    assert "runCommand(command.id)" not in enter_block
    assert "window.codeWorkspaceModule = codeWorkspaceModule;" in app_js
    assert "import operatorCommands from './operatorCommands.js?v=20260621-code-run-ledger';" in code_workspace_js
    assert "export async function open(options = {})" in code_workspace_js
    assert "async function stageRunCommand(options = {})" in code_workspace_js
    assert "input.value = command;" in code_workspace_js
    assert "Nothing has executed yet." in code_workspace_js
    assert "function recordRunActivity(command, data = {}, error = null)" in code_workspace_js
    assert "source: 'code-workspace-run'" in code_workspace_js
    assert "run_command: trimmed" in code_workspace_js
    assert "stdout," in code_workspace_js
    assert "stderr," in code_workspace_js
    assert "recordRunActivity(command, data);" in code_workspace_js
    assert "recordRunActivity(command, {}, error);" in code_workspace_js
    assert "const CODE_TEST_STAGE_ACTION_PREFIX = 'stage-code-test-command:';" in center_js
    assert "Run Output:" in center_js
    assert "Run Command: ${activity.run_command}" in center_js
    assert "stdout:\\n${activity.stdout}" in center_js
    assert "action: commandText ? codeTestStageAction(stageKey) : 'open-code'" in center_js
    assert "await window.codeWorkspaceModule.open({" in center_js
    assert "function recordCodeTestStage(row)" in center_js
    assert "operatorCommands.recordActivity({" in center_js
    assert "title: 'Staged Code Test Command'" in center_js
    assert "status: 'staged'" in center_js
    assert "staged_command: row.command" in center_js
    assert "No tests run until the Code Workspace Run button is pressed" in center_js
    assert "recordCodeTestStage(row);" in center_js
    assert "command: row.command" in center_js
    assert "panel: 'run'" in center_js


def test_operator_experience_plan_proves_goal_target_phrases_without_execution():
    from src.operator_experience import run_operator_experience_plan

    commands = [
        {
            "id": "summarize-today",
            "title": "Summarize Today",
            "category": "Briefing",
            "trust": "local",
            "keywords": ["summarize today", "today briefing", "cleverly summarize today"],
        },
        {
            "id": "open-container-repair-plan",
            "title": "Open Container Repair Plan",
            "category": "Safety",
            "trust": "local",
            "priority": 45,
            "keywords": ["container repair plan", "check containers", "unhealthy services"],
        },
        {
            "id": "request-container-fix",
            "title": "Ask To Fix Container Health",
            "category": "Safety",
            "trust": "approval",
            "priority": 50,
            "keywords": ["check containers and fix", "fix anything unhealthy", "restart containers"],
        },
        {
            "id": "run-tests",
            "title": "Run Workspace Tests",
            "category": "Code",
            "trust": "approval",
            "keywords": ["open my code workspace and run the tests", "run tests", "code workspace tests"],
        },
        {
            "id": "open-training-run-plan",
            "title": "Open Training Run Plan",
            "category": "Training",
            "trust": "approval",
            "keywords": ["train a small model on this dataset", "training run plan", "small model"],
        },
        {
            "id": "watch-build-until-green",
            "title": "Open Build Watch Plan",
            "category": "Automation",
            "trust": "local",
            "priority": 45,
            "keywords": ["watch this repo until the build passes", "watch build"],
        },
        {
            "id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "category": "Automation",
            "trust": "approval",
            "priority": 50,
            "keywords": ["watch this repo until the build passes", "build passes"],
        },
        {
            "id": "draft-task-from-note",
            "title": "Create Task From Note",
            "category": "Tasks",
            "trust": "local",
            "keywords": ["create a task from this note", "note to task"],
        },
        {
            "id": "search-local-documents",
            "title": "Search Local Documents",
            "category": "Documents",
            "trust": "local",
            "keywords": ["search my local documents for this", "local documents"],
        },
        {
            "id": "explain-changes-since-yesterday",
            "title": "Explain Changes Since Yesterday",
            "category": "Activity",
            "trust": "local",
            "keywords": ["explain what changed since yesterday", "change brief"],
        },
        {
            "id": "prepare-backup",
            "title": "Prepare Backup",
            "category": "Safety",
            "trust": "approval",
            "keywords": ["prepare a backup and verify it", "backup verify"],
        },
        {
            "id": "request-backup-export",
            "title": "Request Backup Export",
            "category": "Safety",
            "trust": "approval",
            "alwaysAsk": True,
            "keywords": ["backup export", "encrypted export"],
        },
    ]

    plan = run_operator_experience_plan(
        "alice",
        commands=commands,
        workflows=[],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        configured={"commands": True, "workflows": False, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    target_by_id = {row["id"]: row for row in plan["target_rows"]}

    assert plan["mode"] == "read-only-target-experience-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["target_count"] == 9
    assert plan["summary"]["ready_count"] == 9
    assert plan["summary"]["route_ready_count"] == 9
    assert plan["summary"]["approval_target_count"] == 3
    assert plan["summary"]["approval_ready_count"] == 3
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert target_by_id["container-health"]["selected_id"] == "request-container-fix"
    assert target_by_id["watch-build"]["selected_id"] == "request-build-watch-loop"
    assert target_by_id["backup-verify"]["approval_mode"] == "ask"
    assert "/api/operator/experience-plan" in api_paths
    assert "/api/operator/document-search-plan" in api_paths
    assert "/api/operator/backup-plan" in api_paths
    assert all(row["executes"] is False for row in plan["target_rows"])
    assert any(row["requires_approval"] is True for row in plan["target_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert plan["paths"]["commands"] == "data/operator_commands.json"


def test_operator_console_plan_maps_dashboard_sections_without_execution():
    from src.operator_console import run_operator_console_plan

    action_ids = [
        "open-operator-runbook",
        "open-model-routing-map",
        "open-offline",
        "open-operations-queue",
        "open-memory-profile",
        "open-work-preflight",
        "open-code-workspace-map",
        "open-training-run-plan",
        "open-activity-preflight",
        "open-command-palette",
        "summarize-today",
        "open-voice-preflight",
    ]
    commands = [
        {
            "id": action_id,
            "title": action_id.replace("-", " ").title(),
            "category": "Operator",
            "trust": "local",
            "keywords": [action_id.replace("-", " ")],
        }
        for action_id in action_ids
    ]

    plan = run_operator_console_plan(
        "alice",
        commands=commands,
        workflows=[{"id": "briefing"}],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    section_ids = {row["id"] for row in plan["section_rows"]}

    assert plan["mode"] == "read-only-console-readiness-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["section_count"] == 10
    assert plan["summary"]["ready_count"] == 10
    assert plan["summary"]["entry_ready_count"] == 4
    assert plan["summary"]["dashboard_sections_ready"] is True
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["approves_actions"] is False
    assert {
        "system-status",
        "active-models",
        "offline-security",
        "active-jobs",
        "recent-memory",
        "tasks",
        "calendar",
        "code-workspaces",
        "training-jobs",
        "alerts",
    } == section_ids
    assert "/api/operator/console-plan" in api_paths
    assert "/api/operator/runtime-plan" in api_paths
    assert "/api/operator/model-ops-plan" in api_paths
    assert all(row["executes"] is False for row in plan["section_rows"])
    assert all(row["uses_network"] is False for row in plan["section_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "logs/" in plan["paths"]["data_paths"]


def test_operator_toolchain_plan_maps_named_modules_without_execution():
    from src.operator_toolchain import run_operator_toolchain_plan

    action_ids = [
        "open-offline",
        "open-model-routing-map",
        "open-embedding-preflight",
        "open-research-preflight",
        "open-training-run-plan",
        "open-code-workspace-map",
        "open-voice-preflight",
        "open-work-preflight",
        "open-calendar",
        "open-memory-profile",
        "open-notes",
        "open-library-preflight",
        "open-gallery",
        "open-loops",
        "prepare-backup",
        "open-local-services-map",
        "open-operator-runbook",
        "open-command-palette",
        "open-activity-preflight",
    ]
    commands = [
        {
            "id": action_id,
            "title": action_id.replace("-", " ").title(),
            "category": "Operator",
            "trust": "local",
            "keywords": [action_id.replace("-", " ")],
        }
        for action_id in action_ids
    ]

    plan = run_operator_toolchain_plan(
        "alice",
        commands=commands,
        workflows=[{"id": "build-watch"}],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    module_ids = {row["id"] for row in plan["module_rows"]}

    assert plan["mode"] == "read-only-toolchain-integration-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["module_count"] == 16
    assert plan["summary"]["ready_count"] == 16
    assert plan["summary"]["entry_ready_count"] == 4
    assert plan["summary"]["network_capable_count"] == 7
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["approves_actions"] is False
    assert {
        "offline-control",
        "ollama",
        "chromadb-rag",
        "searxng-research",
        "training-lab",
        "code-workspace",
        "voice-io",
        "tasks",
        "calendar",
        "memory",
        "notes",
        "library",
        "gallery",
        "agent-loops",
        "backups-recovery",
        "docker-services",
    } == module_ids
    assert "/api/operator/toolchain-plan" in api_paths
    assert "/api/training/status" in api_paths
    assert "/api/rag/stats" in api_paths
    assert all(row["executes"] is False for row in plan["module_rows"])
    assert all(row["uses_network"] is False for row in plan["module_rows"])
    assert any(row["network_capable"] is True for row in plan["module_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "cleverly-chromadb-data:/data" in plan["paths"]["data_paths"]


def test_operator_safety_plan_maps_high_risk_boundaries_without_execution():
    from src.operator_safety import run_operator_safety_plan

    commands = [
        {
            "id": "request-container-fix",
            "title": "Request Container Repair",
            "category": "Safety",
            "trust": "approval",
            "keywords": ["fix containers"],
        },
        {
            "id": "request-backup-export",
            "title": "Request Backup Export",
            "category": "Safety",
            "trust": "approval",
            "alwaysAsk": True,
            "keywords": ["backup export"],
        },
        {
            "id": "run-tests",
            "title": "Run Workspace Tests",
            "category": "Code",
            "trust": "approval",
            "keywords": ["run tests"],
        },
        {
            "id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "category": "Automation",
            "trust": "approval",
            "keywords": ["watch build"],
        },
        {
            "id": "prepare-backup",
            "title": "Prepare Backup",
            "category": "Safety",
            "trust": "local",
            "keywords": ["backup"],
        },
        {
            "id": "open-offline",
            "title": "Open Offline Control",
            "category": "Safety",
            "trust": "local",
            "keywords": ["offline"],
        },
        {
            "id": "open-trust-controls",
            "title": "Open Trust Controls",
            "category": "Safety",
            "trust": "local",
            "keywords": ["trust"],
        },
        {
            "id": "open-code-workspace-map",
            "title": "Open Code Workspace Map",
            "category": "Code",
            "trust": "local",
            "keywords": ["code"],
        },
        {
            "id": "open-local-services-map",
            "title": "Open Local Services Map",
            "category": "Safety",
            "trust": "local",
            "keywords": ["services"],
        },
        {
            "id": "open-research-preflight",
            "title": "Open Research Preflight",
            "category": "Research",
            "trust": "local",
            "keywords": ["research"],
        },
        {
            "id": "open-model-routing-map",
            "title": "Open Model Routing Map",
            "category": "Models",
            "trust": "local",
            "keywords": ["models"],
        },
        {
            "id": "open-library-preflight",
            "title": "Open Library Preflight",
            "category": "Library",
            "trust": "local",
            "keywords": ["library"],
        },
        {
            "id": "open-automation-map",
            "title": "Open Automation Map",
            "category": "Automation",
            "trust": "local",
            "keywords": ["automation"],
        },
    ]

    plan = run_operator_safety_plan(
        "alice",
        commands=commands,
        workflows=[{"id": "build-watch"}],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    risk_ids = {row["id"] for row in plan["risk_rows"]}

    assert plan["mode"] == "read-only-safety-boundary-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["risk_count"] == 5
    assert plan["summary"]["ready_count"] == 5
    assert plan["summary"]["ask_ready_count"] == 6
    assert plan["summary"]["ask_total"] == 6
    assert plan["summary"]["network_capable_count"] == 1
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["approves_actions"] is False
    assert {"destructive", "network", "credential", "filesystem", "shell"} == risk_ids
    assert "/api/operator/safety-plan" in api_paths
    assert "/api/operator/file-ops-plan" in api_paths
    assert "/api/operator/code-test-plan" in api_paths
    assert all(row["executes"] is False for row in plan["risk_rows"])
    assert all(row["uses_network"] is False for row in plan["risk_rows"])
    assert any(row["network_capable"] is True for row in plan["risk_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "data/auth.json" in plan["paths"]["data_paths"]


def test_operator_goal_plan_maps_operating_console_goal_without_execution():
    from src.operator_goal import run_operator_goal_plan

    action_ids = [
        "open-cleverly-goal-prompt",
        "open-console-readiness-audit",
        "open-capability-map",
        "open-trust-controls",
        "open-autonomy-map",
        "open-memory-profile",
        "open-operator-runbook",
        "open-local-services-map",
        "open-activity-preflight",
        "open-command-palette",
        "open-local-data-map",
    ]
    commands = [
        {
            "id": action_id,
            "title": action_id.replace("-", " ").title(),
            "category": "Operator",
            "trust": "local",
            "keywords": [action_id.replace("-", " ")],
        }
        for action_id in action_ids
    ]

    plan = run_operator_goal_plan(
        "alice",
        commands=commands,
        workflows=[{"id": "summarize-today"}],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    all_rows = [*plan["principle_rows"], *plan["definition_rows"], *plan["evidence_rows"]]

    assert plan["mode"] == "read-only-goal-readiness-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["principle_count"] == 7
    assert plan["summary"]["definition_count"] == 4
    assert plan["summary"]["evidence_count"] == 8
    assert plan["summary"]["requirement_count"] == 19
    assert plan["summary"]["ready_count"] == 19
    assert plan["summary"]["issue_count"] == 0
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["approves_actions"] is False
    assert "/api/operator/goal-plan" in api_paths
    assert "/api/operator/safety-plan" in api_paths
    assert "/api/operator/toolchain-plan" in api_paths
    assert "/api/operator/console-plan" in api_paths
    assert all(row["executes"] is False for row in all_rows)
    assert all(row["uses_network"] is False for row in all_rows)
    assert any(row["id"] == "safety-by-default" for row in plan["principle_rows"])
    assert any(row["id"] == "docker-runtime-reliability" for row in plan["definition_rows"])
    assert any(row["id"] == "goal-readiness-proof" for row in plan["evidence_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "cleverly-chromadb-data:/data" in plan["paths"]["data_paths"]


def test_operator_route_endpoint_uses_persisted_catalog(monkeypatch):
    import routes.operator_routes as operator_routes

    commands = [
        {
            "id": "summarize-today",
            "title": "Today Briefing",
            "subtitle": "Summarize local tasks, calendar, models, services, and activity",
            "category": "Briefing",
            "trust": "local",
            "priority": 50,
            "keywords": ["summarize today", "today briefing", "cleverly summarize today"],
        }
    ]
    workflows = [
        {
            "id": "summarize-today-target",
            "phrase": "Cleverly, summarize today.",
            "title": "Today Briefing",
            "area": "Briefing",
            "commandId": "summarize-today",
            "expectedRouteId": "summarize-today",
            "proof": "Read-only local snapshot",
            "state": "ok",
        }
    ]
    monkeypatch.setattr(
        operator_routes,
        "_command_store",
        lambda: {"version": 1, "owners": {"alice": {"commands": commands}}},
    )
    monkeypatch.setattr(
        operator_routes,
        "_workflow_store",
        lambda: {"version": 1, "owners": {"alice": {"workflows": workflows}}},
    )
    monkeypatch.setattr(
        operator_routes,
        "_policy_store",
        lambda: {"version": 1, "owners": {"alice": {"policy": {"local": "auto", "approval": "ask"}}}},
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_change_brief",
        lambda owner, since="yesterday": {
            "mode": "read-only-change-brief",
            "owner": owner,
            "window": {"label": since, "start": "2026-06-20T00:00:00Z", "end": "2026-06-21T00:00:00Z"},
            "summary": {"creates_changes": False, "runs_shell": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_backup_plan",
        lambda owner: {
            "mode": "read-only-backup-verify-plan",
            "owner": owner,
            "summary": {"creates_backup": False, "restores_data": False, "runs_shell": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_activity_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-activity-timeline-plan",
            "owner": owner,
            "summary": {"writes_activity": False, "retries_commands": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_code_test_plan",
        lambda owner: {
            "mode": "read-only-code-test-plan",
            "owner": owner,
            "summary": {"runs_tests": False, "changes_files": False, "requires_run_approval": True},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_build_watch_plan",
        lambda owner: {
            "mode": "read-only-build-watch-plan",
            "owner": owner,
            "summary": {"starts_loop": False, "runs_build": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_document_search_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-document-search-plan",
            "owner": owner,
            "summary": {"runs_search": False, "indexes_files": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_file_ops_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-file-ops-plan",
            "owner": owner,
            "summary": {"writes_files": False, "deletes_files": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_runtime_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-runtime-resource-plan",
            "owner": owner,
            "summary": {"starts_jobs": False, "runs_shell": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_console_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-console-readiness-plan",
            "owner": owner,
            "summary": {"executes_commands": False, "starts_jobs": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_toolchain_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-toolchain-integration-plan",
            "owner": owner,
            "summary": {"executes_commands": False, "starts_jobs": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_safety_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-safety-boundary-plan",
            "owner": owner,
            "summary": {"executes_commands": False, "starts_jobs": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_goal_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-goal-readiness-plan",
            "owner": owner,
            "summary": {"routes_commands": False, "executes_commands": False, "starts_jobs": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_experience_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-target-experience-plan",
            "owner": owner,
            "summary": {"routes_commands": False, "executes_commands": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_training_plan",
        lambda owner: {
            "mode": "read-only-training-run-plan",
            "owner": owner,
            "summary": {"starts_training": False, "creates_model": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_voice_plan",
        lambda owner: {
            "mode": "read-only-voice-io-plan",
            "owner": owner,
            "summary": {"starts_microphone": False, "speaks_audio": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_autonomy_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-autonomy-plan",
            "owner": owner,
            "summary": {"routes_commands": False, "approves_commands": False, "changes_policy": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_memory_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-memory-plan",
            "owner": owner,
            "summary": {"writes_memories": False, "adds_memories": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_model_ops_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-model-ops-plan",
            "owner": owner,
            "summary": {"sets_primary_model": False, "starts_serving": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_workday_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-workday-plan",
            "owner": owner,
            "summary": {"creates_tasks": False, "creates_calendar_events": False, "uses_network": False},
        },
    )

    router = operator_routes.setup_operator_routes()
    route_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/route" and "POST" in route.methods
    )
    matrix_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/routes"
    )
    change_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/change-brief"
    )
    backup_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/backup-plan"
    )
    activity_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/activity-plan"
    )
    code_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/code-test-plan"
    )
    build_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/build-watch-plan"
    )
    document_search_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/document-search-plan"
    )
    file_ops_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/file-ops-plan"
    )
    runtime_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/runtime-plan"
    )
    console_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/console-plan"
    )
    toolchain_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/toolchain-plan"
    )
    safety_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/safety-plan"
    )
    goal_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/goal-plan"
    )
    experience_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/experience-plan"
    )
    training_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/training-plan"
    )
    voice_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/voice-plan"
    )
    autonomy_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/autonomy-plan"
    )
    memory_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/memory-plan"
    )
    model_ops_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/model-ops-plan"
    )
    workday_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/workday-plan"
    )
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(state=SimpleNamespace(personal_docs_manager=None, rag_manager=None)),
    )

    routed = route_endpoint(request, {"text": "Cleverly, summarize today."})
    matrix = matrix_endpoint(request)
    brief = change_endpoint(request)
    backup = backup_endpoint(request)
    activity_plan = activity_endpoint(request)
    code_plan = code_endpoint(request)
    build_plan = build_endpoint(request)
    document_search_plan = document_search_endpoint(request)
    file_ops_plan = file_ops_endpoint(request)
    runtime_plan = runtime_endpoint(request)
    console_plan = console_endpoint(request)
    toolchain_plan = toolchain_endpoint(request)
    safety_plan = safety_endpoint(request)
    goal_plan = goal_endpoint(request)
    experience_plan = experience_endpoint(request)
    training_plan = training_endpoint(request)
    voice_plan = voice_endpoint(request)
    autonomy_plan = autonomy_endpoint(request)
    memory_plan = memory_endpoint(request)
    model_ops_plan = model_ops_endpoint(request)
    workday_plan = workday_endpoint(request)

    assert routed["ok"] is True
    assert routed["selected"]["id"] == "summarize-today"
    assert routed["selected"]["approval_required"] is False
    assert routed["paths"]["commands"] == "data/operator_commands.json"
    assert matrix["summary"]["ready"] == 1
    assert matrix["rows"][0]["selected_id"] == "summarize-today"
    assert brief["ok"] is True
    assert brief["owner"] == "alice"
    assert brief["summary"]["runs_shell"] is False
    assert backup["ok"] is True
    assert backup["owner"] == "alice"
    assert backup["summary"]["creates_backup"] is False
    assert activity_plan["ok"] is True
    assert activity_plan["owner"] == "alice"
    assert activity_plan["summary"]["writes_activity"] is False
    assert code_plan["ok"] is True
    assert code_plan["owner"] == "alice"
    assert code_plan["summary"]["runs_tests"] is False
    assert build_plan["ok"] is True
    assert build_plan["owner"] == "alice"
    assert build_plan["summary"]["starts_loop"] is False
    assert document_search_plan["ok"] is True
    assert document_search_plan["owner"] == "alice"
    assert document_search_plan["summary"]["runs_search"] is False
    assert file_ops_plan["ok"] is True
    assert file_ops_plan["owner"] == "alice"
    assert file_ops_plan["summary"]["writes_files"] is False
    assert file_ops_plan["summary"]["deletes_files"] is False
    assert runtime_plan["ok"] is True
    assert runtime_plan["owner"] == "alice"
    assert runtime_plan["summary"]["starts_jobs"] is False
    assert runtime_plan["summary"]["runs_shell"] is False
    assert console_plan["ok"] is True
    assert console_plan["owner"] == "alice"
    assert console_plan["summary"]["executes_commands"] is False
    assert console_plan["summary"]["starts_jobs"] is False
    assert toolchain_plan["ok"] is True
    assert toolchain_plan["owner"] == "alice"
    assert toolchain_plan["summary"]["executes_commands"] is False
    assert toolchain_plan["summary"]["starts_jobs"] is False
    assert safety_plan["ok"] is True
    assert safety_plan["owner"] == "alice"
    assert safety_plan["summary"]["executes_commands"] is False
    assert safety_plan["summary"]["starts_jobs"] is False
    assert goal_plan["ok"] is True
    assert goal_plan["owner"] == "alice"
    assert goal_plan["summary"]["routes_commands"] is False
    assert goal_plan["summary"]["executes_commands"] is False
    assert goal_plan["summary"]["starts_jobs"] is False
    assert experience_plan["ok"] is True
    assert experience_plan["owner"] == "alice"
    assert experience_plan["summary"]["routes_commands"] is False
    assert experience_plan["summary"]["executes_commands"] is False
    assert training_plan["ok"] is True
    assert training_plan["owner"] == "alice"
    assert training_plan["summary"]["starts_training"] is False
    assert voice_plan["ok"] is True
    assert voice_plan["owner"] == "alice"
    assert voice_plan["summary"]["starts_microphone"] is False
    assert voice_plan["summary"]["speaks_audio"] is False
    assert autonomy_plan["ok"] is True
    assert autonomy_plan["owner"] == "alice"
    assert autonomy_plan["summary"]["routes_commands"] is False
    assert autonomy_plan["summary"]["approves_commands"] is False
    assert memory_plan["ok"] is True
    assert memory_plan["owner"] == "alice"
    assert memory_plan["summary"]["writes_memories"] is False
    assert memory_plan["summary"]["adds_memories"] is False
    assert model_ops_plan["ok"] is True
    assert model_ops_plan["owner"] == "alice"
    assert model_ops_plan["summary"]["sets_primary_model"] is False
    assert model_ops_plan["summary"]["starts_serving"] is False
    assert workday_plan["ok"] is True
    assert workday_plan["owner"] == "alice"
    assert workday_plan["summary"]["creates_tasks"] is False
    assert workday_plan["summary"]["creates_calendar_events"] is False


def test_middleware_admin_paths_and_security_headers(monkeypatch):
    from core import middleware

    class AuthManager:
        is_configured = True

        def is_admin(self, user):
            return user == "admin"

    def req(user=None, headers=None):
        return SimpleNamespace(
            headers=headers or {},
            client=SimpleNamespace(host="127.0.0.1"),
            state=SimpleNamespace(current_user=user),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=AuthManager())),
            url=SimpleNamespace(path="/"),
        )

    assert middleware.require_admin(req("admin")) is None
    with pytest.raises(HTTPException) as denied:
        middleware.require_admin(req("alice"))
    assert denied.value.status_code == 403

    assert middleware.require_admin(req(headers={middleware.INTERNAL_TOOL_HEADER: middleware.INTERNAL_TOOL_TOKEN})) is None
    assert middleware.require_admin(req(user="internal-tool")) is None

    class BadHeaders:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("bad headers")

    with pytest.raises(HTTPException):
        middleware.require_admin(req("alice", headers=BadHeaders()))

    async def run_path(path):
        request = req("admin")
        request.url.path = path

        async def call_next(_request):
            return Response("ok")

        mw = middleware.SecurityHeadersMiddleware(app=object())
        return await mw.dispatch(request, call_next), request.state.csp_nonce

    normal, nonce = asyncio.run(run_path("/chat"))
    assert normal.headers["X-Frame-Options"] == "DENY"
    normal_csp = normal.headers["Content-Security-Policy"]
    assert f"nonce-{nonce}" in normal_csp
    assert "https://cdn.jsdelivr.net" not in normal_csp
    assert "script-src 'self'" in normal_csp

    report, _ = asyncio.run(run_path("/api/research/report/abc"))
    assert "https:" in report.headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in report.headers

    tool, _ = asyncio.run(run_path("/api/tools/x/render"))
    assert "Content-Security-Policy" not in tool.headers
    assert tool.headers["X-Content-Type-Options"] == "nosniff"


def test_auth_helpers_privileges_and_owner_filter(monkeypatch):
    from src import auth_helpers

    class AuthManager:
        is_configured = True

        def __init__(self, privs=None):
            self.privs = privs if privs is not None else {"can_upload": True}

        def get_privileges(self, user):
            if self.privs == "raise":
                raise RuntimeError("auth failed")
            return self.privs

    def req(user=None, auth=None, host="203.0.113.10"):
        return SimpleNamespace(
            state=SimpleNamespace(current_user=user),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth)),
            client=SimpleNamespace(host=host),
            headers={},
        )

    assert auth_helpers.get_current_user(req("alice")) == "alice"
    assert auth_helpers.require_user(req("alice", AuthManager())) == "alice"
    assert auth_helpers.require_user(req(None, None, host="127.0.0.1")) == ""

    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert auth_helpers.require_user(req(None, AuthManager(), host="127.0.0.1")) == ""
    proxied = req(None, AuthManager(), host="127.0.0.1")
    proxied.headers = {"x-forwarded-for": "198.51.100.20"}
    with pytest.raises(HTTPException) as proxied_unauth:
        auth_helpers.require_user(proxied)
    assert proxied_unauth.value.status_code == 401
    monkeypatch.delenv("AUTH_ENABLED", raising=False)

    with pytest.raises(HTTPException) as unauth:
        auth_helpers.require_user(req(None, AuthManager()))
    assert unauth.value.status_code == 401

    with pytest.raises(HTTPException):
        auth_helpers.require_user(req(None, None, host="198.51.100.2"))

    assert auth_helpers.require_privilege(req("alice", AuthManager({"can_upload": True})), "can_upload") == "alice"
    assert auth_helpers.require_privilege(req("alice", None), "can_upload") == "alice"
    assert auth_helpers.require_privilege(req("alice", AuthManager("raise")), "can_upload") == "alice"
    assert auth_helpers.require_privilege(req(None, None, host="localhost"), "can_upload") == ""

    with pytest.raises(HTTPException) as forbidden:
        auth_helpers.require_privilege(req("alice", AuthManager({"can_upload": False})), "can_upload")
    assert forbidden.value.status_code == 403
    assert "can upload" in forbidden.value.detail

    class Expr:
        def __init__(self, text):
            self.text = text

        def __eq__(self, other):
            return Expr(f"{self.text}={other}")

        def __or__(self, other):
            return Expr(f"({self.text}|{other.text})")

        def __repr__(self):
            return self.text

    class Model:
        owner = Expr("owner")

    class Query:
        def __init__(self):
            self.filters = []

        def filter(self, expr):
            self.filters.append(repr(expr))
            return self

    query = Query()
    assert auth_helpers.owner_filter(query, Model, "") is query
    assert query.filters == []
    auth_helpers.owner_filter(query, Model, "alice")
    auth_helpers.owner_filter(query, Model, "alice", include_shared=False)
    assert query.filters[0].startswith("(")
    assert query.filters[1] == "owner=alice"


def test_search_cache_key_and_cleanup(monkeypatch, tmp_path):
    from src.search import cache as search_cache

    assert search_cache.generate_cache_key("abc") == search_cache.hashlib.sha256(b"abc").hexdigest()

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    now = dt.datetime.now()
    old = now - dt.timedelta(days=2)
    fresh = now
    for key in ("old", "fresh", "lru1", "lru2"):
        (cache_dir / f"{key}.cache").write_text(key, encoding="utf-8")
    index = {
        "old": old,
        "fresh": fresh,
        "missing": fresh,
        "lru1": old,
        "lru2": fresh,
    }
    monkeypatch.setattr(search_cache, "CACHE_MAX_ENTRIES", 1)
    search_cache.cache_metrics.update({"hits": 0, "misses": 0, "evictions": 0})

    search_cache.cleanup_cache(cache_dir, index, max_age=dt.timedelta(days=1))

    assert "old" not in index
    assert "missing" not in index
    assert len(index) == 1
    assert search_cache.cache_metrics["evictions"] >= 4
    assert not (cache_dir / "old.cache").exists()


def test_services_search_cache_module_alias_uses_same_behaviors(monkeypatch, tmp_path):
    from services.search import cache as service_cache

    assert service_cache.generate_cache_key("abc") == service_cache.hashlib.sha256(b"abc").hexdigest()

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    for key in ("expired", "fresh", "lru"):
        (cache_dir / f"{key}.cache").write_text("x", encoding="utf-8")
    service_cache.cache_metrics.update({"hits": 0, "misses": 0, "evictions": 0})
    index = {
        "expired": dt.datetime.now() - dt.timedelta(days=3),
        "missing": dt.datetime.now(),
        "fresh": dt.datetime.now(),
        "lru": dt.datetime.now() - dt.timedelta(minutes=1),
    }
    monkeypatch.setattr(service_cache, "CACHE_MAX_ENTRIES", 1)

    service_cache.cleanup_cache(cache_dir, index, max_age=dt.timedelta(days=1))

    assert len(index) == 1
    assert "expired" not in index
    assert "missing" not in index
    assert service_cache.cache_metrics["evictions"] >= 3


def test_settings_cache_offline_user_and_save_paths(monkeypatch, tmp_path):
    from src import settings

    settings_file = tmp_path / "settings.json"
    features_file = tmp_path / "features.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(settings, "FEATURES_FILE", str(features_file))
    monkeypatch.delenv("CLEVERLY_OFFLINE", raising=False)
    settings._invalidate_caches()

    assert settings.offline_mode() is False
    loaded = settings.load_settings()
    assert loaded["tts_provider"] == "disabled"
    assert settings.load_settings() is loaded
    assert settings.get_setting("missing", "fallback") == "fallback"

    settings_file.write_text(json.dumps({"tts_provider": "browser"}), encoding="utf-8")
    settings._invalidate_caches()
    assert settings.load_settings()["tts_provider"] == "browser"

    features_file.write_text(json.dumps({"web_search": False}), encoding="utf-8")
    settings._invalidate_caches()
    assert settings.load_features()["web_search"] is False
    assert settings.load_features() is settings.load_features()

    settings.save_settings({"tts_provider": "endpoint:x"})
    assert json.loads(settings_file.read_text(encoding="utf-8"))["tts_provider"] == "endpoint:x"
    settings.save_features({"memory": False})
    assert json.loads(features_file.read_text(encoding="utf-8"))["memory"] is False

    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    settings._invalidate_caches()
    offline_settings = settings.load_settings()
    assert offline_settings["search_provider"] == "disabled"
    assert offline_settings["search_fallback_chain"] == []
    offline_features = settings.load_features()
    assert offline_features["email"] is False
    assert offline_features["mcp"] is False

    prefs_module = types.SimpleNamespace(_load_for_user=lambda owner: {"vision_model": "local-vision", "empty": ""})
    monkeypatch.setitem(importlib.import_module("sys").modules, "routes.prefs_routes", prefs_module)
    assert settings.get_user_setting("vision_model", owner="alice") == "local-vision"
    assert settings.get_user_setting("tts_provider", owner="alice") == "endpoint:x"

    prefs_module._load_for_user = lambda owner: (_ for _ in ()).throw(RuntimeError("prefs bad"))
    assert settings.get_user_setting("vision_model", owner="alice", default="fallback") == ""

    settings_file.write_text("{", encoding="utf-8")
    features_file.write_text("{", encoding="utf-8")
    settings._invalidate_caches()
    assert settings.load_settings()["tts_provider"] == "disabled"
    assert settings.load_features()["memory"] is True


def test_startup_ollama_probe_blocks_external_url_offline(monkeypatch):
    from src import startup_endpoints

    monkeypatch.setattr(startup_endpoints, "offline_mode", lambda: True)
    monkeypatch.setattr(startup_endpoints, "load_features", lambda: {"external_model_endpoints": True})
    monkeypatch.setattr(startup_endpoints, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(startup_endpoints.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))

    assert startup_endpoints._ollama_models("https://ollama.example:11434") == []


def test_startup_ollama_probe_blocks_external_url_feature_disabled(monkeypatch):
    from src import startup_endpoints

    monkeypatch.setattr(startup_endpoints, "offline_mode", lambda: False)
    monkeypatch.setattr(startup_endpoints, "load_features", lambda: {"external_model_endpoints": False})
    monkeypatch.setattr(startup_endpoints, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(startup_endpoints.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))

    assert startup_endpoints._ollama_models("https://ollama.example:11434") == []


def test_memory_service_vector_and_keyword_paths(monkeypatch, tmp_path):
    import services.memory.service as memory_service

    class Manager:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.added = []
            self.deleted = []

        def add_memory(self, entry):
            self.added.append(entry)

        def search_memories(self, query, limit=5):
            return [{"id": "m1", "text": query, "timestamp": 1, "session_id": "s1"}][:limit]

        def get_memories(self, limit=100):
            return [{"id": "m2", "text": "all", "timestamp": 2, "session_id": None}][:limit]

        def delete_memory(self, memory_id):
            self.deleted.append(memory_id)
            return True

    class VectorStore:
        def __init__(self, data_dir):
            self.data_dir = data_dir
            self.added = []

        def add(self, text, metadata):
            self.added.append((text, metadata))

        def search(self, query, k=5):
            return [{"id": "v1", "text": query, "timestamp": 3, "session_id": "s2", "metadata": {"score": 1}}]

    monkeypatch.setattr(memory_service, "MemoryManager", Manager)
    monkeypatch.setattr(memory_service, "MemoryVectorStore", VectorStore)
    monkeypatch.setattr(memory_service.os.path, "exists", lambda path: path.endswith("memory_vectors"))

    vector_service = memory_service.MemoryService(str(tmp_path))
    remembered = asyncio.run(vector_service.remember("remember me", session_id="s1"))
    assert remembered.id
    assert vector_service.manager.added[0]["text"] == "remember me"
    assert vector_service.vector_store.added[0][1]["session_id"] == "s1"
    recalled = asyncio.run(vector_service.recall("query"))
    assert recalled.memories[0].metadata == {"score": 1}

    monkeypatch.setattr(memory_service.os.path, "exists", lambda path: False)
    keyword_service = memory_service.MemoryService(str(tmp_path))
    keyword = asyncio.run(keyword_service.recall("needle", top_k=1))
    assert keyword.memories[0].text == "needle"
    assert keyword.total == 1
    assert keyword_service.get_all()[0].text == "all"
    assert keyword_service.delete("m2") is True
    assert keyword_service.manager.deleted == ["m2"]
