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
    assert plan["container_status"] == []
    assert plan["container_status_source"] == "not captured"
    assert plan["container_status_path"].endswith("operator_container_status.json")
    assert plan["summary"]["checks_container_alert_count"] == 2
    assert plan["summary"]["critical_checks_container_alert_count"] == 0
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    assert "No captured container status evidence" in alert_titles
    assert "Host Docker commands require approval" in alert_titles
    assert all(row["executes"] is False for row in plan["alert_rows"])
    assert all(row["runs_docker"] is False for row in plan["alert_rows"])
    assert all(row["runs_shell"] is False for row in plan["alert_rows"])
    assert any(
        service["compose_service"] == "cleverly"
        and service["container_name"] == "cleverly-app"
        and service["required"] is True
        for service in plan["services"]
    )
    assert any(service["compose_service"] == "chromadb" and service["profile"] == "support" for service in plan["services"])
    assert plan["host_commands"][0]["risk"] == "read-only"
    assert "docker compose ps" in {command["command"] for command in plan["host_commands"]}


def test_operator_checks_load_captured_container_status_evidence(monkeypatch, tmp_path):
    from src import operator_checks

    status_file = tmp_path / "container-status.json"
    status_file.write_text(
        json.dumps({
            "source": "approved host docker ps",
            "captured_at": "2026-06-22T12:00:00Z",
            "containers": [
                {
                    "name": "cleverly-app",
                    "image": "cleverly:local",
                    "status": "Up 3 minutes (healthy)",
                    "ignored": "x" * 1000,
                },
                {
                    "names": "cleverly-test-chromadb-1",
                    "compose_service": "chromadb",
                    "state": "Exited (1) 5 minutes ago",
                },
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLEVERLY_CONTAINER_STATUS_FILE", str(status_file))
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
    monkeypatch.setattr(operator_checks, "get_effective_code_workspace_model_key", lambda: "")

    result = operator_checks.run_operator_checks()
    plan = result["container_plan"]

    assert plan["container_status_source"] == "approved host docker ps"
    assert plan["container_status_captured_at"] == "2026-06-22T12:00:00Z"
    assert plan["container_status_path"] == str(status_file)
    assert plan["container_status"] == [
        {"name": "cleverly-app", "image": "cleverly:local", "status": "Up 3 minutes (healthy)"},
        {"names": "cleverly-test-chromadb-1", "compose_service": "chromadb", "state": "Exited (1) 5 minutes ago"},
    ]
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    assert plan["summary"]["checks_container_alert_count"] >= 2
    assert plan["summary"]["critical_checks_container_alert_count"] == 1
    assert "Container status needs review: cleverly-test-chromadb-1" in alert_titles
    assert "Host Docker commands require approval" in alert_titles


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
    assert snapshot["summary"]["service_snapshot_alert_count"] == 1
    assert snapshot["summary"]["critical_service_snapshot_alert_count"] == 0
    assert by_id["data-dir"]["state"] == "ok"
    assert by_id["code-worker-queue"]["state"] == "ok"
    assert by_id["ollama"]["state"] == "ok"
    assert by_id["chromadb"]["state"] == "ok"
    assert by_id["searxng"]["state"] == "warn"
    assert "External endpoint configured but not probed" in by_id["searxng"]["detail"]
    assert snapshot["alert_rows"][0]["title"] == "SearXNG search service needs review"
    assert snapshot["alert_rows"][0]["executes"] is False
    assert snapshot["alert_rows"][0]["runs_docker"] is False
    assert snapshot["alert_rows"][0]["uses_network"] is False
    assert all("search.example.test" not in url for url in called)


def test_operator_services_plan_is_read_only_and_alerts_on_service_issues():
    from src.operator_services import run_operator_services_plan

    service_snapshot = {
        "generated_at": "2026-06-22T00:00:00Z",
        "docker_like": True,
        "runner": "worker",
        "summary": {"ok": 2, "warn": 1, "error": 1, "loading": 1, "required": 2},
        "services": [
            {
                "id": "cleverly-api",
                "label": "Cleverly app API",
                "state": "ok",
                "detail": "in-process",
                "required": True,
                "kind": "app",
            },
            {
                "id": "data-dir",
                "label": "App data volume",
                "state": "error",
                "detail": "/app/data missing",
                "required": True,
                "kind": "path",
            },
            {
                "id": "chromadb",
                "label": "ChromaDB vector service",
                "state": "loading",
                "detail": "No local endpoint configured",
                "required": False,
                "kind": "http",
            },
        ],
    }
    checks = {
        "summary": {"ok": 1, "warn": 0, "fail": 0},
        "container_plan": {
            "source": "compose manifest and environment",
            "docker_socket_mounted": True,
            "compose_project": "cleverly",
            "services": [
                {
                    "compose_service": "cleverly",
                    "container_name": "cleverly",
                    "role": "app API",
                    "required": True,
                    "source": "docker-compose.yml",
                },
                {
                    "compose_service": "chromadb",
                    "container_name": "cleverly-chromadb-1",
                    "role": "optional vector database",
                    "required": False,
                    "profile": "support",
                    "source": "docker-compose.yml",
                },
            ],
            "container_status": [
                {
                    "name": "cleverly",
                    "image": "cleverly:local",
                    "status": "Up 2 minutes (healthy)",
                },
                {
                    "name": "cleverly-chromadb-1",
                    "image": "ghcr.io/chroma-core/chroma:latest",
                    "status": "Exited (1) 5 minutes ago",
                },
            ],
            "host_commands": [
                {
                    "label": "List compose service state",
                    "risk": "read-only",
                    "command": "docker compose ps",
                },
                {
                    "label": "Start optional support services without pulling",
                    "risk": "approval-required",
                    "command": "docker compose --profile support up -d --no-build --pull never",
                },
            ],
        },
    }

    plan = run_operator_services_plan("alice", service_snapshot=service_snapshot, checks=checks)

    api_paths = {row["path"] for row in plan["api_actions"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-local-services-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["service_count"] == 3
    assert plan["summary"]["required_issue_count"] == 1
    assert plan["summary"]["optional_issue_count"] == 1
    assert plan["summary"]["compose_service_count"] == 2
    assert plan["summary"]["container_status_count"] == 2
    assert plan["summary"]["container_status_visible_count"] == 2
    assert plan["summary"]["running_container_count"] == 1
    assert plan["summary"]["unhealthy_container_count"] == 1
    assert plan["summary"]["host_command_count"] == 2
    assert plan["summary"]["approval_gated_command_count"] == 1
    assert plan["summary"]["service_alert_count"] == 4
    assert plan["summary"]["critical_service_alert_count"] == 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 4
    assert plan["summary"]["restarts_services"] is False
    assert plan["summary"]["starts_services"] is False
    assert plan["summary"]["pulls_images"] is False
    assert plan["summary"]["runs_docker"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/services-plan" in api_paths
    assert "/api/operator/services" in api_paths
    assert "/api/operator/repair-plan" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-local-services-map" for row in plan["entry_rows"])
    assert all(row["repair_command_id"] == "open-container-repair-plan" for row in plan["entry_rows"])
    assert all(row["approval_command_id"] == "request-container-fix" for row in plan["entry_rows"])
    assert all(row["services_api"] == "/api/operator/services-plan" for row in plan["entry_rows"])
    assert all(row["repair_api"] == "/api/operator/repair-plan" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["restarts_services"] is False for row in plan["entry_rows"])
    assert all(row["starts_services"] is False for row in plan["entry_rows"])
    assert all(row["pulls_images"] is False for row in plan["entry_rows"])
    assert all(row["runs_docker"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "services-snapshot-read-handoff",
        "services-container-status-handoff",
        "services-repair-plan-handoff",
        "services-data-backup-boundary-handoff",
        "services-host-command-approval-handoff",
        "services-support-start-handoff",
        "services-offline-no-pull-handoff",
        "services-activity-ledger-handoff",
    }
    assert handoffs["services-container-status-handoff"]["target_api"] == "/api/operator/checks"
    assert handoffs["services-repair-plan-handoff"]["target_api"] == "/api/operator/repair-plan"
    assert handoffs["services-data-backup-boundary-handoff"]["target_api"] == "/api/operator/backup-plan"
    assert handoffs["services-support-start-handoff"]["target_api"] == "docker compose --profile support up -d --no-build --pull never"
    assert handoffs["services-snapshot-read-handoff"]["requires_approval"] is False
    assert handoffs["services-activity-ledger-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["starts_services"] is False for row in plan["handoff_rows"])
    assert all(row["repairs_services"] is False for row in plan["handoff_rows"])
    assert all(row["pulls_images"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_data"] is False for row in plan["handoff_rows"])
    assert all(row["sends_notifications"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["services-container-status-handoff"]["gated_operation"]["runs_docker"] is True
    assert handoffs["services-repair-plan-handoff"]["gated_operation"]["repairs_services"] is True
    assert handoffs["services-support-start-handoff"]["gated_operation"]["starts_services"] is True
    assert handoffs["services-offline-no-pull-handoff"]["gated_operation"]["pulls_images"] is True
    assert handoffs["services-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["host_command_rows"])
    assert any(row["id"] == "container-cleverly" and row["state"] == "ok" for row in plan["container_status_rows"])
    assert any(
        row["id"] == "container-cleverly-chromadb-1" and row["state"] == "warn"
        for row in plan["container_status_rows"]
    )
    assert "Required service needs review" in alert_titles
    assert "Optional support service pending" in alert_titles
    assert "Container status needs review" in alert_titles
    assert "Docker socket is mounted" in alert_titles
    assert "does not restart services" in plan["approval"]["policy"]


def test_operator_docker_runtime_plan_unifies_container_runtime_without_execution():
    from src.operator_docker_runtime import run_operator_docker_runtime_plan

    plan = run_operator_docker_runtime_plan(
        "alice",
        runtime_plan={
            "mode": "read-only-runtime-resource-plan",
            "summary": {
                "docker_like": True,
                "offline": True,
                "sealed_runtime_count": 4,
                "sealed_runtime_ready_count": 4,
                "state": "ok",
            },
        },
        services_plan={
            "mode": "read-only-local-services-plan",
            "summary": {
                "service_count": 5,
                "required_issue_count": 0,
                "optional_issue_count": 1,
                "container_status_count": 3,
                "container_status_visible_count": 3,
                "unhealthy_container_count": 1,
                "host_command_count": 2,
                "approval_gated_command_count": 1,
                "state": "warn",
            },
            "container_status_rows": [{"id": "container-chromadb", "state": "warn"}],
            "host_command_rows": [{"id": "start-support", "state": "warn", "requires_approval": True}],
        },
        repair_plan={
            "mode": "read-only-local-repair-plan",
            "summary": {"total": 5, "next_action": "Review optional support service.", "state": "warn"},
        },
        checks={
            "container_plan": {
                "summary": {"checks_container_alert_count": 1, "critical_checks_container_alert_count": 0},
            },
        },
        ai_runtime_plan={
            "mode": "read-only-local-ai-runtime-plan",
            "summary": {"runtime_row_count": 6, "runtime_ready_count": 5, "state": "warn"},
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    docker_row_ids = {row["id"] for row in plan["docker_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-docker-runtime-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["docker_row_count"] == 8
    assert plan["summary"]["docker_runtime_alert_count"] >= 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 1
    assert plan["summary"]["restarts_services"] is False
    assert plan["summary"]["starts_services"] is False
    assert plan["summary"]["repairs_services"] is False
    assert plan["summary"]["builds_images"] is False
    assert plan["summary"]["recreates_services"] is False
    assert plan["summary"]["pulls_images"] is False
    assert plan["summary"]["runs_docker"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["deletes_volumes"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["docker_runtime_api"] == "/api/operator/docker-runtime-plan" for row in plan["entry_rows"])
    assert all(row["builds_images"] is False for row in plan["entry_rows"])
    assert all(row["recreates_services"] is False for row in plan["entry_rows"])
    assert all(row["runs_docker"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "docker-status-capture-handoff",
        "docker-repair-restart-handoff",
        "docker-support-start-handoff",
        "docker-rebuild-recreate-handoff",
        "docker-image-pull-egress-handoff",
        "docker-volume-delete-handoff",
        "docker-activity-rollback-handoff",
    }
    assert handoffs["docker-status-capture-handoff"]["state"] == "ok"
    assert handoffs["docker-repair-restart-handoff"]["state"] == "error"
    assert handoffs["docker-status-capture-handoff"]["target_host_command"] == "docker compose ps --format json"
    assert handoffs["docker-rebuild-recreate-handoff"]["target_host_command"].startswith("docker compose build")
    assert handoffs["docker-image-pull-egress-handoff"]["target_host_command"] == "docker pull"
    assert handoffs["docker-volume-delete-handoff"]["target_api"] == "/api/operator/backup-plan"
    assert all(row["approval_command_id"] == "request-container-fix" for row in plan["handoff_rows"])
    assert all(row["requires_approval"] is True for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["starts_services"] is False for row in plan["handoff_rows"])
    assert all(row["repairs_services"] is False for row in plan["handoff_rows"])
    assert all(row["builds_images"] is False for row in plan["handoff_rows"])
    assert all(row["recreates_services"] is False for row in plan["handoff_rows"])
    assert all(row["pulls_images"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_volumes"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert "docker-image-deployment-boundary" in docker_row_ids
    assert "/api/operator/docker-runtime-plan" in api_paths
    assert "/api/operator/runtime-plan" in api_paths
    assert "/api/operator/services-plan" in api_paths
    assert "/api/operator/repair-plan" in api_paths
    assert any(
        row["path"] == "host:docker compose build cleverly cleverly-proxy"
        and row["requires_approval"] is True
        and row["builds_images"] is True
        for row in plan["api_actions"]
    )
    assert any(
        row["path"] == "host:docker compose up -d --no-build --pull never cleverly cleverly-proxy"
        and row["requires_approval"] is True
        and row["recreates_services"] is True
        for row in plan["api_actions"]
    )
    assert any(row["path"] == "host:docker pull" and row["requires_approval"] is True and row["uses_network"] is True for row in plan["api_actions"])
    assert any(row["path"] == "host:docker volume rm" and row["deletes_volumes"] is True for row in plan["api_actions"])
    assert all(row["runs_docker"] is False for row in plan["api_actions"])
    assert plan["container_status_rows"] == [{"id": "container-chromadb", "state": "warn"}]
    assert "does not restart services" in plan["approval"]["policy"]


def test_operator_credentials_plan_masks_secret_posture_without_reading_values(tmp_path):
    from src.operator_credentials import run_operator_credentials_plan

    data_root = tmp_path / "data"
    data_root.mkdir()
    for name in ("auth.json", "settings.json", "features.json", "vault.json", ".app_key"):
        (data_root / name).write_text("{}", encoding="utf-8")

    plan = run_operator_credentials_plan(
        "alice",
        settings={
            "brave_api_key": "secret-brave",
            "tavily_api_key": "",
            "reminder_email_to": "ops@example.test",
            "default_model": "llama3.2",
        },
        features={
            "web_search": True,
            "web_fetch": False,
            "deep_research": False,
            "cookbook_downloads": False,
            "cookbook_dependency_installs": False,
            "cookbook_remote_servers": False,
            "external_model_endpoints": False,
            "network_integrations": True,
            "network_notifications": False,
            "webhooks": False,
            "email": False,
            "vault": True,
            "sensitive_filter": True,
        },
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        data_root=data_root,
        offline=False,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    serialized = json.dumps(plan)

    assert plan["mode"] == "read-only-credential-posture-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["configured_secret_count"] == 1
    assert plan["summary"]["network_secret_count"] == 1
    assert plan["summary"]["network_feature_count"] == 2
    assert plan["summary"]["existing_sensitive_path_count"] == 5
    assert plan["summary"]["credential_alert_count"] == 5
    assert plan["summary"]["critical_credential_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 1
    assert plan["summary"]["reads_secrets"] is False
    assert plan["summary"]["returns_secret_values"] is False
    assert plan["summary"]["writes_credentials"] is False
    assert plan["summary"]["changes_settings"] is False
    assert plan["summary"]["unlocks_vault"] is False
    assert plan["summary"]["calls_network"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/credentials-plan" in api_paths
    assert "/api/vault/unlock" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-local-data-map" for row in plan["entry_rows"])
    assert all(row["trust_command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["offline_command_id"] == "open-offline" for row in plan["entry_rows"])
    assert all(row["credentials_api"] == "/api/operator/credentials-plan" for row in plan["entry_rows"])
    assert all(row["vault_api"] == "/api/vault/unlock" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["reads_secrets"] is False for row in plan["entry_rows"])
    assert all(row["returns_secret_values"] is False for row in plan["entry_rows"])
    assert all(row["writes_credentials"] is False for row in plan["entry_rows"])
    assert all(row["changes_settings"] is False for row in plan["entry_rows"])
    assert all(row["unlocks_vault"] is False for row in plan["entry_rows"])
    assert all(row["calls_network"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    handoff_ids = {row["id"] for row in plan["handoff_rows"]}
    assert "credential-masked-settings-handoff" in handoff_ids
    assert "credential-vault-unlock-handoff" in handoff_ids
    assert "credential-network-egress-handoff" in handoff_ids
    assert "credential-feature-gate-handoff" in handoff_ids
    assert "credential-backup-key-handoff" in handoff_ids
    assert "credential-activity-audit-handoff" in handoff_ids
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["reads_secrets"] is False for row in plan["handoff_rows"])
    assert all(row["returns_secret_values"] is False for row in plan["handoff_rows"])
    assert all(row["writes_credentials"] is False for row in plan["handoff_rows"])
    assert all(row["changes_settings"] is False for row in plan["handoff_rows"])
    assert all(row["unlocks_vault"] is False for row in plan["handoff_rows"])
    assert all(row["sends_email"] is False for row in plan["handoff_rows"])
    assert all(row["calls_network"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["approval_api"] == "/api/vault/unlock" and row["requires_approval"] is True for row in plan["handoff_rows"])
    assert any(row["id"] == "credential-network-egress-handoff" and row["network_after_approval"] is True for row in plan["handoff_rows"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert "Credential settings configured" in alert_titles
    assert "Network-capable credentials with egress enabled" in alert_titles
    assert "Vault config present" in alert_titles
    assert "secret-brave" not in serialized
    assert "ops@example.test" not in serialized
    assert "does not read secret values" in plan["approval"]["policy"]


def test_operator_data_plan_maps_local_data_boundary_without_side_effects(monkeypatch, tmp_path):
    from src.operator_data import run_operator_data_plan

    data_root = tmp_path / "data"
    logs_root = tmp_path / "logs"
    upload_root = tmp_path / "uploads"
    personal_root = tmp_path / "personal"
    for path in (data_root, logs_root, upload_root, personal_root, data_root / "tasks"):
        path.mkdir(parents=True)
    for name in ("app.db", "auth.json", "settings.json", "features.json", "memory.json"):
        (data_root / name).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_root))
    monkeypatch.setenv("PERSONAL_DIR", str(personal_root))

    plan = run_operator_data_plan("alice", data_root=data_root, logs_root=logs_root)

    api_paths = {row["path"] for row in plan["api_actions"]}
    entries = {row["entry"] for row in plan["entry_rows"]}
    scope_ids = {row["id"] for row in plan["scope_rows"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}

    assert plan["mode"] == "read-only-local-data-map-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["scope_count"] == 21
    assert plan["summary"]["required_scope_count"] == 2
    assert plan["summary"]["required_scope_ready_count"] == 2
    assert plan["summary"]["sensitive_scope_count"] == 7
    assert plan["summary"]["visible_sensitive_scope_count"] == 3
    assert plan["summary"]["data_alert_count"] == 3
    assert plan["summary"]["critical_data_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 4
    assert plan["summary"]["reads_file_contents"] is False
    assert plan["summary"]["reads_secret_values"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["deletes_files"] is False
    assert plan["summary"]["exports_data"] is False
    assert plan["summary"]["restores_data"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert {"data-root", "logs-root", "app-db", "auth-store", "memory-store", "code-workspaces", "training"} <= scope_ids
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert "/api/operator/data-plan" in api_paths
    assert "/api/operator/file-ops-plan" in api_paths
    assert "/api/operator/credentials-plan" in api_paths
    assert "/api/operator/backup-plan" in api_paths
    assert all(row["command_id"] == "open-local-data-map" for row in plan["entry_rows"])
    assert all(row["data_api"] == "/api/operator/data-plan" for row in plan["entry_rows"])
    assert all(row["backup_command_id"] == "open-backup-preflight" for row in plan["entry_rows"])
    assert all(row["trust_command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["offline_command_id"] == "open-offline" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["reads_file_contents"] is False for row in plan["entry_rows"])
    assert all(row["reads_secret_values"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert all(row["deletes_files"] is False for row in plan["entry_rows"])
    assert all(row["exports_data"] is False for row in plan["entry_rows"])
    assert all(row["restores_data"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    handoff_ids = {row["id"] for row in plan["handoff_rows"]}
    assert "data-file-ops-handoff" in handoff_ids
    assert "data-credential-posture-handoff" in handoff_ids
    assert "data-memory-profile-handoff" in handoff_ids
    assert "data-backup-coverage-handoff" in handoff_ids
    assert "data-offline-policy-handoff" in handoff_ids
    assert "data-activity-evidence-handoff" in handoff_ids
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["reads_file_contents"] is False for row in plan["handoff_rows"])
    assert all(row["reads_secret_values"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_files"] is False for row in plan["handoff_rows"])
    assert all(row["exports_data"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["target_api"] == "/api/operator/file-ops-plan" for row in plan["handoff_rows"])
    assert any(row["target_api"] == "/api/operator/credentials-plan" for row in plan["handoff_rows"])
    assert any(row["target_api"] == "/api/operator/backup-plan" and row["requires_approval"] is True for row in plan["handoff_rows"])
    assert all(row["reads_file_contents"] is False for row in plan["scope_rows"])
    assert all(row["reads_secret_values"] is False for row in plan["scope_rows"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert "Sensitive local stores visible" in alert_titles
    assert "Local data writes require explicit action" in alert_titles
    assert "Back up before risky local data work" in alert_titles
    assert "does not read file contents" in plan["approval"]["policy"]


def test_operator_data_plan_endpoint_is_registered(monkeypatch):
    import routes.operator_routes as operator_routes

    monkeypatch.setattr(
        operator_routes,
        "run_operator_data_plan",
        lambda owner: {
            "mode": "read-only-local-data-map-plan",
            "owner": owner,
            "summary": {"writes_files": False, "deletes_files": False, "uses_network": False},
        },
    )
    router = operator_routes.setup_operator_routes()
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/operator/data-plan")
    result = endpoint(SimpleNamespace(state=SimpleNamespace(current_user="alice")))

    assert result["ok"] is True
    assert result["owner"] == "alice"
    assert result["summary"]["writes_files"] is False
    assert result["summary"]["deletes_files"] is False
    assert result["summary"]["uses_network"] is False


def test_operator_tool_access_plan_is_read_only_and_surfaces_tool_gates(tmp_path):
    from src.operator_tool_access import run_operator_tool_access_plan

    data_root = tmp_path / "data"
    data_root.mkdir()

    plan = run_operator_tool_access_plan(
        "alice",
        tools=[
            {"id": "bash", "description": "Run shell commands"},
            {"id": "read_file", "description": "Read local files"},
            {"id": "web_search", "description": "Search the web"},
            {"id": "manage_mcp", "description": "Manage MCP servers"},
            {"id": "manage_skills", "description": "Manage skills"},
            {"id": "manage_settings", "description": "Manage settings"},
            {"id": "manage_tasks", "description": "Manage tasks"},
        ],
        skills=[
            {"name": "repo-build", "description": "Run repo checks", "category": "code", "status": "published", "owner": "alice"},
            {"name": "draft-note", "description": "Draft notes", "category": "notes", "status": "draft", "owner": "alice"},
        ],
        settings={"disabled_tools": ["manage_skills", "unknown_tool"]},
        features={"mcp": True, "web_search": True},
        policy={"local": "auto", "approval": "ask", "network": "auto", "danger": "auto"},
        mcp_servers=[{"id": "local", "name": "local-mcp", "transport": "stdio", "is_enabled": True, "tool_count": 2}],
        data_root=data_root,
        offline=False,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-tool-access-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["tool_count"] == 7
    assert plan["summary"]["disabled_tool_count"] == 1
    assert plan["summary"]["network_tool_count"] == 1
    assert plan["summary"]["shell_tool_count"] == 1
    assert plan["summary"]["file_tool_count"] == 1
    assert plan["summary"]["approval_gated_count"] == 7
    assert plan["summary"]["skill_count"] == 2
    assert plan["summary"]["published_skill_count"] == 1
    assert plan["summary"]["draft_skill_count"] == 1
    assert plan["summary"]["mcp_enabled"] is True
    assert plan["summary"]["mcp_server_count"] == 1
    assert plan["summary"]["mcp_tool_count"] == 2
    assert plan["summary"]["tool_access_alert_count"] >= 3
    assert plan["summary"]["critical_tool_access_alert_count"] == 2
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["executes_tools"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["changes_settings"] is False
    assert plan["summary"]["adds_mcp_servers"] is False
    assert plan["summary"]["connects_mcp"] is False
    assert plan["summary"]["deletes_mcp"] is False
    assert plan["summary"]["publishes_skills"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["reads_secret_values"] is False
    assert "/api/operator/tool-access-plan" in api_paths
    assert "/api/tools" in api_paths
    assert "/api/mcp/servers" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["executes_tools"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["id"] == "manage_skills" for row in plan["toggle_rows"])
    assert any(row["id"] == "unknown_tool" and row["known_tool"] is False for row in plan["toggle_rows"])
    assert "Core operator tools disabled" in alert_titles
    assert "Shell-capable tools are not danger ask-gated" in alert_titles
    assert "Filesystem tools are not danger ask-gated" in alert_titles
    assert "Network-capable tools are not network ask-gated" in alert_titles
    assert "does not execute tools" in plan["approval"]["policy"]


def test_operator_approval_plan_surfaces_pending_decisions_without_execution():
    from src.operator_approvals import run_operator_approval_plan

    commands = [
        {"id": "summarize-today", "title": "Summarize Today", "trust": "local", "category": "Briefing"},
        {"id": "run-tests", "title": "Run Tests", "trust": "approval", "category": "Code"},
        {"id": "request-container-fix", "title": "Container Fix", "trust": "danger", "category": "Runtime"},
        {"id": "open-research-preflight", "title": "Research", "trust": "network", "category": "Research"},
    ]
    workflows = [
        {
            "id": "build-watch",
            "phrase": "Watch this repo until the build passes",
            "expectedRouteId": "run-tests",
            "approvalId": "run-tests",
        },
        {
            "id": "missing-repair",
            "phrase": "Fix unhealthy containers",
            "expectedRouteId": "missing-command",
            "approvalId": "request-container-fix",
        },
    ]
    activity = [
        {
            "id": "act-pending",
            "command_id": "run-tests",
            "title": "Run Tests",
            "status": "pending approval",
            "detail": "Waiting for operator approval",
            "trust": "approval",
            "trust_mode": "ask",
        },
        {
            "id": "act-approved",
            "command_id": "summarize-today",
            "title": "Summarize Today",
            "status": "approved",
            "detail": "Operator approved local summary",
        },
        {
            "id": "act-failed",
            "command_id": "request-container-fix",
            "title": "Container Fix",
            "status": "failed",
            "error": "repair failed by policy",
        },
    ]

    plan = run_operator_approval_plan(
        "alice",
        commands=commands,
        workflows=workflows,
        policy={"local": "auto", "approval": "ask", "network": "auto", "danger": "auto"},
        activity=activity,
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-approval-queue-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["command_count"] == 4
    assert plan["summary"]["approval_route_count"] == 3
    assert plan["summary"]["ask_gated_count"] == 1
    assert plan["summary"]["auto_risky_count"] == 2
    assert plan["summary"]["workflow_gate_count"] == 2
    assert plan["summary"]["workflow_gate_ready_count"] == 1
    assert plan["summary"]["pending_approval_count"] == 1
    assert plan["summary"]["decision_count"] == 1
    assert plan["summary"]["decision_checkpoint_count"] == 4
    assert plan["summary"]["decision_checkpoint_ready_count"] == 1
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 1
    assert plan["summary"]["approved_count"] == 1
    assert plan["summary"]["cancelled_count"] == 0
    assert plan["summary"]["failed_activity_count"] == 1
    assert plan["summary"]["approval_alert_count"] >= 5
    assert plan["summary"]["critical_approval_alert_count"] >= 3
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["approves_commands"] is False
    assert plan["summary"]["changes_policy"] is False
    assert plan["summary"]["writes_activity"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/approval-plan" in api_paths
    assert "/api/operator/policy" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["review_command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/operator/policy" for row in plan["entry_rows"])
    assert all(row["activity_api"] == "/api/operator/activity" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["approves_commands"] is False for row in plan["entry_rows"])
    assert all(row["retries_commands"] is False for row in plan["entry_rows"])
    assert all(row["changes_policy"] is False for row in plan["entry_rows"])
    assert all(row["writes_activity"] is False for row in plan["entry_rows"])
    assert all(row["starts_workflows"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "approval-evidence-review-handoff",
        "approval-allow-cancel-handoff",
        "approval-retry-recovery-handoff",
        "approval-policy-change-handoff",
        "approval-workflow-gate-handoff",
        "approval-network-risk-handoff",
        "approval-activity-ledger-handoff",
    }
    assert handoffs["approval-evidence-review-handoff"]["target_api"] == "/api/operator/activity"
    assert handoffs["approval-allow-cancel-handoff"]["gated_operation"]["approves_commands"] is True
    assert handoffs["approval-allow-cancel-handoff"]["gated_operation"]["cancels_commands"] is True
    assert handoffs["approval-retry-recovery-handoff"]["target_api"] == "/api/operator/recovery-plan"
    assert handoffs["approval-policy-change-handoff"]["gated_operation"]["changes_policy"] is True
    assert handoffs["approval-workflow-gate-handoff"]["gated_operation"]["starts_workflows"] is True
    assert handoffs["approval-network-risk-handoff"]["network_after_approval"] is True
    assert handoffs["approval-activity-ledger-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["approves_commands"] is False for row in plan["handoff_rows"])
    assert all(row["cancels_commands"] is False for row in plan["handoff_rows"])
    assert all(row["retries_commands"] is False for row in plan["handoff_rows"])
    assert all(row["changes_policy"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["id"] == "act-pending" for row in plan["approval_queue_rows"])
    assert any(row["id"] == "act-approved" for row in plan["decision_rows"])
    assert any(row["id"] == "act-failed" for row in plan["failure_rows"])
    assert any(row["id"] == "review-evidence-before-decision" and row["state"] == "warn" for row in plan["decision_checkpoint_rows"])
    assert any(row["id"] == "trust-policy-change-gate" and row["state"] == "error" for row in plan["decision_checkpoint_rows"])
    assert all(row["executes"] is False for row in plan["decision_checkpoint_rows"])
    assert all(row["approves_commands"] is False for row in plan["decision_checkpoint_rows"])
    assert all(row["cancels_commands"] is False for row in plan["decision_checkpoint_rows"])
    assert all(row["retries_commands"] is False for row in plan["decision_checkpoint_rows"])
    assert all(row["changes_policy"] is False for row in plan["decision_checkpoint_rows"])
    assert all(row["writes_activity"] is False for row in plan["decision_checkpoint_rows"])
    assert all(row["uses_network"] is False for row in plan["decision_checkpoint_rows"])
    assert "Network trust tier is not ask-gated" in alert_titles
    assert "High Risk trust tier is not ask-gated" in alert_titles
    assert "Risky commands can auto-route" in alert_titles
    assert "Workflow approval gates need review" in alert_titles
    assert "Pending approval queue" in alert_titles
    assert "Failures require review before approval" in alert_titles
    assert "does not route commands" in plan["approval"]["policy"]


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
    entries = {row["entry"] for row in plan["entry_rows"]}
    sealed_ids = {row["id"] for row in plan["sealed_runtime_rows"]}

    assert plan["mode"] == "read-only-runtime-resource-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["docker_like"] is True
    assert plan["summary"]["offline"] is True
    assert plan["summary"]["root_count"] == 6
    assert plan["summary"]["existing_root_count"] == 6
    assert plan["summary"]["missing_required_count"] == 0
    assert plan["summary"]["low_space_root_count"] == 0
    assert plan["summary"]["runtime_alert_count"] == 1
    assert plan["summary"]["critical_runtime_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["sealed_runtime_count"] == 11
    assert plan["summary"]["sealed_runtime_ready_count"] == 11
    assert plan["summary"]["sealed_volume_count"] == 7
    assert plan["summary"]["support_service_count"] == 4
    assert plan["summary"]["network_capable_support_count"] == 1
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
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert "volume-cleverly-data" in sealed_ids
    assert "volume-cleverly-logs" in sealed_ids
    assert "service-ollama" in sealed_ids
    assert "service-chromadb" in sealed_ids
    assert "service-searxng" in sealed_ids
    assert all(row["command_id"] == "open-machine-preflight" for row in plan["entry_rows"])
    assert all(row["offline_command_id"] == "open-offline" for row in plan["entry_rows"])
    assert all(row["runtime_api"] == "/api/operator/runtime-plan" for row in plan["entry_rows"])
    assert all(row["status_api"] == "/api/runtime" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["starts_jobs"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["reads_file_contents"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert all(row["deletes_files"] is False for row in plan["entry_rows"])
    assert all(row["downloads_models"] is False for row in plan["entry_rows"])
    assert all(row["pulls_images"] is False for row in plan["entry_rows"])
    assert all(row["restarts_services"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["starts_services"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["runs_shell"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["writes_files"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["deletes_files"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["pulls_images"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["uses_network"] is False for row in plan["sealed_runtime_rows"])
    assert all(row["executes"] is False for row in plan["disk_rows"])
    assert all(row["executes"] is False for row in plan["job_rows"])
    assert any(row["requires_approval"] is True for row in plan["job_rows"])
    assert plan["alert_rows"][0]["title"] == "Heavy jobs require approval"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert "does not run shell commands" in plan["approval"]["policy"]
    assert plan["paths"]["data_root"] == str(data_root)
    assert plan["paths"]["logs_root"] == str(logs_root)


def test_operator_ai_runtime_plan_unifies_models_training_services_without_execution():
    from src.operator_ai_runtime import run_operator_ai_runtime_plan

    plan = run_operator_ai_runtime_plan(
        "alice",
        model_snapshot={
            "primary": {"model": "llama3.2", "configured": True},
            "endpoints": {"counts": {"local_enabled": 1, "external_enabled": 0}},
            "training": {"dataset_count": 2},
            "readiness": {"state": "ok"},
        },
        model_ops_plan={
            "summary": {
                "state": "ok",
                "primary_model": "llama3.2",
                "local_enabled_count": 1,
                "model_alert_count": 0,
            }
        },
        training_plan={
            "summary": {
                "state": "ok",
                "dataset_count": 2,
                "job_count": 1,
                "failed_job_count": 0,
            }
        },
        runtime_plan={
            "summary": {
                "state": "ok",
                "sealed_runtime_ready_count": 11,
                "sealed_runtime_count": 11,
                "runtime_alert_count": 0,
            }
        },
        services_plan={
            "service_rows": [
                {"id": "ollama", "state": "ok", "title": "Ollama", "detail": "local model runtime"},
                {"id": "chromadb", "state": "ok", "title": "ChromaDB", "detail": "vector store"},
                {"id": "searxng", "state": "warn", "title": "SearXNG", "detail": "network gated"},
            ]
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-local-ai-runtime-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["runtime_row_count"] == 7
    assert plan["summary"]["runtime_ready_count"] == 6
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["ai_runtime_alert_count"] == 1
    assert plan["summary"]["critical_ai_runtime_alert_count"] == 0
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 5
    assert plan["summary"]["starts_models"] is False
    assert plan["summary"]["starts_training"] is False
    assert plan["summary"]["downloads_models"] is False
    assert plan["summary"]["starts_services"] is False
    assert plan["summary"]["restarts_services"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert "/api/operator/ai-runtime-plan" in api_paths
    assert "/api/operator/model-ops-plan" in api_paths
    assert "/api/operator/training-plan" in api_paths
    assert "/api/operator/services-plan" in api_paths
    assert any(row["id"] == "ai-runtime-service-ollama" for row in plan["runtime_rows"])
    assert any(row["id"] == "ai-runtime-service-chromadb" for row in plan["runtime_rows"])
    assert any(row["id"] == "ai-runtime-service-searxng" and row["state"] == "warn" for row in plan["runtime_rows"])
    handoff_ids = {row["id"] for row in plan["handoff_rows"]}
    assert "ai-runtime-model-routing-handoff" in handoff_ids
    assert "ai-runtime-training-handoff" in handoff_ids
    assert "ai-runtime-ollama-service-handoff" in handoff_ids
    assert "ai-runtime-chromadb-context-handoff" in handoff_ids
    assert "ai-runtime-searxng-policy-handoff" in handoff_ids
    assert "ai-runtime-resource-guard-handoff" in handoff_ids
    assert all(row["starts_models"] is False for row in plan["entry_rows"])
    assert all(row["starts_models"] is False for row in plan["handoff_rows"])
    assert all(row["starts_training"] is False for row in plan["handoff_rows"])
    assert all(row["downloads_models"] is False for row in plan["handoff_rows"])
    assert all(row["starts_services"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["approval_api"] == "/api/search" and row["network_after_approval"] is True for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["api_actions"])
    assert "does not set primary models" in plan["approval"]["policy"]


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
    assert plan["summary"]["repair_alert_count"] == 6
    assert plan["summary"]["critical_repair_alert_count"] == 2
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 3
    assert plan["approval"]["required"] is True
    assert plan["approval_packet"]["approval_required"] is True
    assert plan["approval_packet"]["required_affected_count"] == 1
    assert plan["approval_packet"]["optional_affected_count"] == 1
    assert plan["approval_packet"]["executes"] is False
    assert plan["approval_packet"]["writes"] is False
    assert plan["approval_packet"]["uses_network"] is False
    assert plan["approval_packet"]["affected_services"][0]["id"] == "data-dir"
    assert any(
        command["approval_required"] is True
        and "docker compose up" in command["command"]
        for command in plan["approval_packet"]["candidate_host_commands"]
    )
    assert any(item["id"] == "verify-data-boundary" for item in plan["approval_packet"]["preflight_checklist"])
    assert "pull images" in plan["approval_packet"]["disallowed_actions"]
    assert plan["paths"]["data"] == "/app/data"
    repair_alert_titles = {row["title"] for row in plan["alert_rows"]}
    assert "Required service needs repair review" in repair_alert_titles
    assert "Data volume boundary needs verification" in repair_alert_titles
    assert "Optional service issues" in repair_alert_titles
    assert "Repair requires explicit approval" in repair_alert_titles
    assert "Host Docker commands are suggestions only" in repair_alert_titles
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-container-repair-plan" for row in plan["entry_rows"])
    assert all(row["approval_command_id"] == "request-container-fix" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["runs_docker"] is False for row in plan["entry_rows"])
    assert all(row["repairs_services"] is False for row in plan["entry_rows"])
    assert all(row["changes_files"] is False for row in plan["entry_rows"])
    assert all(row["deletes_data"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "repair-status-capture-handoff",
        "repair-backup-checkpoint-handoff",
        "repair-log-review-handoff",
        "repair-data-boundary-handoff",
        "repair-one-service-handoff",
        "repair-support-start-handoff",
        "repair-activity-ledger-handoff",
        "repair-offline-policy-handoff",
    }
    assert handoffs["repair-one-service-handoff"]["target_api"] == "docker compose up -d --force-recreate --no-deps {service}"
    assert handoffs["repair-support-start-handoff"]["target_api"] == "docker compose --profile support up -d --no-build --pull never"
    assert handoffs["repair-backup-checkpoint-handoff"]["target_api"] == "/api/operator/backup-plan"
    assert handoffs["repair-status-capture-handoff"]["requires_approval"] is False
    assert handoffs["repair-activity-ledger-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["inspects_logs"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["recreates_services"] is False for row in plan["handoff_rows"])
    assert all(row["starts_services"] is False for row in plan["handoff_rows"])
    assert all(row["repairs_services"] is False for row in plan["handoff_rows"])
    assert all(row["changes_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_data"] is False for row in plan["handoff_rows"])
    assert all(row["pulls_images"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["repair-log-review-handoff"]["gated_operation"]["inspects_logs"] is True
    assert handoffs["repair-one-service-handoff"]["gated_operation"]["repairs_services"] is True
    assert handoffs["repair-one-service-handoff"]["gated_operation"]["runs_docker"] is True
    assert handoffs["repair-support-start-handoff"]["gated_operation"]["starts_services"] is True
    assert handoffs["repair-offline-policy-handoff"]["gated_operation"]["pulls_images"] is True
    assert handoffs["repair-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
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
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["note_task_alert_count"] == 1
    assert plan["summary"]["critical_note_task_alert_count"] == 0
    assert plan["summary"]["activity_metadata_only"] is True
    assert plan["summary"]["writes_activity"] is False
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
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "draft-task-from-note" for row in plan["entry_rows"])
    assert all(row["requires_review"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["creates_task"] is False for row in plan["entry_rows"])
    assert plan["alert_rows"][0]["title"] == "Task save requires review"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert all(row["executes"] is False for row in plan["alert_rows"])
    assert all(row["creates_task"] is False for row in plan["alert_rows"])
    assert all(row["uses_network"] is False for row in plan["alert_rows"])
    assert any(row["selected"] is True and row["draft"]["name"] == draft["name"] for row in plan["candidates"])
    assert plan["paths"]["activity"] == "data/operator_activity.json"
    evidence_titles = {row["title"] for row in plan["evidence_rows"]}
    assert "Note task activity ledger" in evidence_titles
    api_paths = {row["path"] for row in plan["api_actions"]}
    assert "/api/operator/note-task-draft" in api_paths
    assert "/api/operator/activity" in api_paths

    empty = run_operator_note_task_draft("alice", notes=[], now=now)
    empty_titles = {row["title"] for row in empty["alert_rows"]}
    assert empty["summary"]["note_task_alert_count"] == 2
    assert empty["summary"]["critical_note_task_alert_count"] == 0
    assert empty["summary"]["entry_route_count"] == 5
    assert empty["summary"]["entry_route_ready_count"] == 3
    assert "No local notes available for task draft" in empty_titles
    assert "Task save requires review" in empty_titles


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
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["change_alert_count"] == 1
    assert plan["summary"]["critical_change_alert_count"] == 0
    assert plan["summary"]["creates_changes"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["workspace_rows"][0]["id"] == "repo-1"
    assert plan["changed_workspace_rows"][0]["id"] == "repo-1"
    assert plan["activity_rows"][0]["id"] == "act-1"
    assert plan["alert_rows"][0]["title"] == "Exact git evidence requires Code Workspace"
    assert plan["alert_rows"][0]["requires_approval"] is False
    assert "git status --short" in commands
    assert "git diff --stat" in commands
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "explain-changes-since-yesterday" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
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
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-backup-verify-plan"
    assert plan["summary"]["encrypted_export_sections"] == 6
    assert plan["summary"]["full_snapshot_items"] == 12
    assert plan["summary"]["audit_count"] == 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 5
    assert plan["summary"]["backup_alert_count"] == 3
    assert plan["summary"]["critical_backup_alert_count"] == 0
    assert plan["summary"]["creates_backup"] is False
    assert plan["summary"]["restores_data"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["requires_export_approval"] is True
    assert plan["verification_packet"]["approval_required"] is True
    assert plan["verification_packet"]["executes"] is False
    assert plan["verification_packet"]["writes"] is False
    assert plan["verification_packet"]["restores_data"] is False
    assert plan["verification_packet"]["runs_shell"] is False
    assert plan["verification_packet"]["uses_network"] is False
    assert any(item["id"] == "encrypted-export" for item in plan["verification_packet"]["expected_artifacts"])
    assert any(item["id"] == "restore-dry-run" for item in plan["verification_packet"]["verification_checks"])
    assert "snapshot verify command succeeds" in plan["verification_packet"]["pass_criteria"]
    assert "store backup passwords in activity logs" in plan["verification_packet"]["disallowed_actions"]
    assert plan["alert_rows"][0]["title"] == "Encrypted export approval required"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert any(row["title"] == "Restore drill approval required" for row in plan["alert_rows"])
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "prepare-backup" for row in plan["entry_rows"])
    assert all(row["approval_command_id"] == "request-backup-export" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "backup-scope-selection-handoff",
        "backup-encrypted-export-handoff",
        "backup-full-snapshot-handoff",
        "backup-snapshot-verify-handoff",
        "backup-restore-drill-handoff",
        "backup-password-custody-handoff",
        "backup-storage-location-handoff",
        "backup-activity-ledger-handoff",
    }
    assert handoffs["backup-encrypted-export-handoff"]["target_api"] == "/api/backup/encrypted/export"
    assert handoffs["backup-restore-drill-handoff"]["target_api"] == "/api/backup/encrypted/import?dry_run=true"
    assert handoffs["backup-full-snapshot-handoff"]["target_api"] == "scripts/cleverly-backup snapshot --pretty"
    assert handoffs["backup-scope-selection-handoff"]["requires_approval"] is False
    assert handoffs["backup-activity-ledger-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["creates_backup"] is False for row in plan["handoff_rows"])
    assert all(row["verifies_backup"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["reads_backup"] is False for row in plan["handoff_rows"])
    assert all(row["reads_password"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["moves_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_files"] is False for row in plan["handoff_rows"])
    assert all(row["uploads_backup"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["backup-encrypted-export-handoff"]["gated_operation"]["creates_backup"] is True
    assert handoffs["backup-encrypted-export-handoff"]["gated_operation"]["reads_password"] is True
    assert handoffs["backup-snapshot-verify-handoff"]["gated_operation"]["verifies_backup"] is True
    assert handoffs["backup-snapshot-verify-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["backup-restore-drill-handoff"]["gated_operation"]["restores_data"] is True
    assert handoffs["backup-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
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
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-file-ops-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["root_count"] == 5
    assert plan["summary"]["existing_root_count"] == 4
    assert plan["summary"]["missing_required_count"] == 1
    assert plan["summary"]["sensitive_root_count"] == 1
    assert plan["summary"]["file_alert_count"] == 4
    assert plan["summary"]["critical_file_alert_count"] == 1
    assert plan["summary"]["direct_file_count"] >= 3
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 2
    assert plan["summary"]["executes"] is False
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
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-local-data-map" for row in plan["entry_rows"])
    assert all(row["trust_command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["backup_command_id"] == "open-backup-preflight" for row in plan["entry_rows"])
    assert all(row["activity_command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["file_ops_api"] == "/api/operator/file-ops-plan" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["reads_file_contents"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert all(row["copies_files"] is False for row in plan["entry_rows"])
    assert all(row["moves_files"] is False for row in plan["entry_rows"])
    assert all(row["deletes_files"] is False for row in plan["entry_rows"])
    assert all(row["uploads_files"] is False for row in plan["entry_rows"])
    assert all(row["imports_files"] is False for row in plan["entry_rows"])
    assert all(row["indexes_files"] is False for row in plan["entry_rows"])
    assert all(row["exports_files"] is False for row in plan["entry_rows"])
    assert all(row["restores_files"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "file-read-scope-handoff",
        "file-write-import-handoff",
        "file-delete-restore-handoff",
        "file-backup-snapshot-handoff",
        "file-index-library-handoff",
        "file-activity-recovery-handoff",
    }
    assert handoffs["file-read-scope-handoff"]["state"] == "ok"
    assert handoffs["file-delete-restore-handoff"]["state"] == "error"
    assert handoffs["file-backup-snapshot-handoff"]["approval_api"] == "/api/backup/encrypted/export"
    assert handoffs["file-delete-restore-handoff"]["approval_api"] == "/api/backup/encrypted/import"
    assert handoffs["file-index-library-handoff"]["target_api"] == "/api/operator/document-search-plan"
    assert all(row["requires_approval"] is True for row in plan["handoff_rows"])
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["reads_file_contents"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["copies_files"] is False for row in plan["handoff_rows"])
    assert all(row["moves_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_files"] is False for row in plan["handoff_rows"])
    assert all(row["uploads_files"] is False for row in plan["handoff_rows"])
    assert all(row["imports_files"] is False for row in plan["handoff_rows"])
    assert all(row["indexes_files"] is False for row in plan["handoff_rows"])
    assert all(row["exports_files"] is False for row in plan["handoff_rows"])
    assert all(row["restores_files"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["title"] == "Delete/restore gate" for row in plan["operation_rows"])
    assert any(row["title"] == "Missing required root: data/missing" for row in plan["alert_rows"])
    assert any(row["title"] == "Sensitive root mapped: data/auth.json" for row in plan["alert_rows"])
    assert any(row.get("destructive") is True for row in plan["alert_rows"])
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
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-activity-timeline-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["record_count"] == 2
    assert plan["summary"]["event_count"] == 2
    assert plan["summary"]["detail_count"] == 2
    assert plan["summary"]["trust_count"] == 1
    assert plan["summary"]["log_evidence_count"] == 2
    assert plan["summary"]["retryable_count"] == 2
    assert plan["summary"]["rollback_ready_count"] == 1
    assert plan["summary"]["failure_count"] == 1
    assert plan["summary"]["missing_trust_count"] == 1
    assert plan["summary"]["activity_alert_count"] == 5
    assert plan["summary"]["critical_activity_alert_count"] == 2
    assert plan["summary"]["action_affordance_count"] == 2
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 3
    assert plan["summary"]["writes_activity"] is False
    assert plan["summary"]["deletes_activity"] is False
    assert plan["summary"]["retries_commands"] is False
    assert plan["summary"]["runs_commands"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/activity-plan" in api_paths
    assert "/api/operator/activity/{activity_id}" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["activity_api"] == "/api/operator/activity" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["writes_activity"] is False for row in plan["entry_rows"])
    assert all(row["deletes_activity"] is False for row in plan["entry_rows"])
    assert all(row["retries_commands"] is False for row in plan["entry_rows"])
    assert all(row["runs_commands"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "activity-details-handoff",
        "activity-copy-log-handoff",
        "activity-retry-checkpoint-handoff",
        "activity-recovery-rollback-handoff",
        "activity-ledger-write-handoff",
        "activity-delete-clear-handoff",
        "activity-trust-review-handoff",
    }
    assert handoffs["activity-details-handoff"]["state"] == "ok"
    assert handoffs["activity-copy-log-handoff"]["state"] == "ok"
    assert handoffs["activity-retry-checkpoint-handoff"]["requires_approval"] is True
    assert handoffs["activity-delete-clear-handoff"]["target_api"] == "/api/operator/activity/{activity_id}"
    assert handoffs["activity-recovery-rollback-handoff"]["target_api"] == "/api/operator/recovery-plan"
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["retries_commands"] is False for row in plan["handoff_rows"])
    assert all(row["runs_commands"] is False for row in plan["handoff_rows"])
    assert all(row["approves_actions"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert plan["gap_rows"]
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    assert "Failed activity records need review" in alert_titles
    assert "Activity records missing trust tags" in alert_titles
    assert any(row["retryable"] is True and row["log_ready"] is True for row in plan["action_rows"])
    assert any(row["rollback_ready"] is False and row["needs_recovery"] is True for row in plan["action_rows"])
    assert any(row["detail_action"] == "activity-detail:a1" for row in plan["action_rows"])
    assert any(row["copy_log_action"] == "copy-activity-log:a1" and row["copy_requires_approval"] is False for row in plan["action_rows"])
    assert any(row["retry_action"] == "retry-activity:a1" and row["retry_requires_approval"] is True for row in plan["action_rows"])
    assert any(row["recovery_action"] == "open-recovery-map" and row["recovery_requires_approval"] is False for row in plan["action_rows"])
    assert any(row.get("destructive") is True for row in plan["alert_rows"])
    assert "does not write records" in plan["approval"]["policy"]


def test_operator_recovery_plan_maps_retry_and_rollback_without_execution(tmp_path):
    from src.operator_recovery import run_operator_recovery_plan

    data_root = tmp_path / "data"
    logs_root = tmp_path / "logs"
    (data_root / "code-workspaces").mkdir(parents=True)
    logs_root.mkdir()
    records = [
        {
            "id": "a1",
            "owner": "alice",
            "command_id": "run-tests",
            "title": "Run Tests",
            "status": "success",
            "detail": "Tests passed",
            "recovery_hint": "Retry through activity.",
        },
        {
            "id": "a2",
            "owner": "alice",
            "command_id": "repair-container",
            "title": "Repair Container",
            "status": "failed",
            "detail": "Docker restart failed",
        },
        {
            "id": "b1",
            "owner": "bob",
            "command_id": "other",
            "status": "failed",
        },
    ]

    plan = run_operator_recovery_plan(
        "alice",
        records=records,
        activity_path=tmp_path / "operator_activity.json",
        data_root=data_root,
        logs_root=logs_root,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    entries = {row["entry"] for row in plan["entry_rows"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}

    assert plan["mode"] == "read-only-recovery-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["record_count"] == 2
    assert plan["summary"]["retryable_count"] == 2
    assert plan["summary"]["failure_count"] == 1
    assert plan["summary"]["recovery_needed_count"] == 2
    assert plan["summary"]["rollback_ready_count"] == 1
    assert plan["summary"]["recovery_row_count"] == 8
    assert plan["summary"]["recovery_alert_count"] == 4
    assert plan["summary"]["critical_recovery_alert_count"] == 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["retries_commands"] is False
    assert plan["summary"]["restores_data"] is False
    assert plan["summary"]["repairs_services"] is False
    assert plan["summary"]["deletes_files"] is False
    assert plan["summary"]["exports_data"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/recovery-plan" in api_paths
    assert "/api/operator/activity-plan" in api_paths
    assert "/api/operator/backup-plan" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-recovery-map" for row in plan["entry_rows"])
    assert all(row["recovery_api"] == "/api/operator/recovery-plan" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["retries_commands"] is False for row in plan["entry_rows"])
    assert all(row["restores_data"] is False for row in plan["entry_rows"])
    assert all(row["repairs_services"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert "Failure review queue" in alert_titles
    assert "Rollback and recovery hints" in alert_titles
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert "does not retry commands" in plan["approval"]["policy"]


def test_operator_activity_store_upserts_owner_scoped_records(tmp_path):
    from src.operator_activity import upsert_operator_activity_record

    path = tmp_path / "operator_activity.json"
    first = upsert_operator_activity_record(
        {
            "id": "run-1",
            "command_id": "run-tests",
            "title": "Code Workspace Command Passed",
            "status": "success",
            "stdout": "ok",
            "retryable": True,
            "retry_command_id": "run-tests",
            "retry_requires_approval": True,
            "deletable": True,
            "clear_requires_approval": True,
            "recovery_hint": "This command can be replayed through the current command route and trust policy.",
            "rollback_hint": "",
            "events": [{"status": "success", "detail": "done"}],
        },
        owner="alice",
        activity_path=path,
    )
    second = upsert_operator_activity_record(
        {
            "id": "run-1",
            "command_id": "run-tests",
            "title": "Code Workspace Command Failed",
            "status": "error",
            "stderr": "failed",
        },
        owner="alice",
        activity_path=path,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    records = data["records"]

    assert first["owner"] == "alice"
    assert first["retryable"] is True
    assert first["retry_command_id"] == "run-tests"
    assert first["retry_requires_approval"] is True
    assert first["deletable"] is True
    assert first["clear_requires_approval"] is True
    assert "current command route" in first["recovery_hint"]
    assert second["owner"] == "alice"
    assert len(records) == 1
    assert records[0]["id"] == "run-1"
    assert records[0]["status"] == "error"
    assert records[0]["stderr"] == "failed"
    assert records[0]["updated_at"]


def test_operator_activity_endpoint_normalizes_action_affordances(monkeypatch, tmp_path):
    import routes.operator_routes as operator_routes

    monkeypatch.setattr(operator_routes, "ACTIVITY_FILE", str(tmp_path / "operator_activity.json"))
    router = operator_routes.setup_operator_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/activity" and "POST" in route.methods
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))

    result = endpoint(
        request,
        {
            "record": {
                "id": "activity-affordance",
                "command_id": "run-tests",
                "title": "Run Tests",
                "status": "success",
                "retryable": 1,
                "retry_command_id": "run-tests",
                "retry_requires_approval": "yes",
                "deletable": True,
                "clear_requires_approval": "yes",
                "recovery_hint": "x" * 900,
                "rollback_hint": "Use backup before destructive follow-up.",
            }
        },
    )

    activity = result["activity"]
    assert result["ok"] is True
    assert activity["owner"] == "alice"
    assert activity["retryable"] is True
    assert activity["retry_command_id"] == "run-tests"
    assert activity["retry_requires_approval"] is True
    assert activity["deletable"] is True
    assert activity["clear_requires_approval"] is True
    assert len(activity["recovery_hint"]) == 700
    assert activity["rollback_hint"] == "Use backup before destructive follow-up."


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
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-code-test-plan"
    assert plan["summary"]["workspace_count"] == 1
    assert plan["summary"]["candidate_command_count"] == 2
    assert plan["summary"]["runner"] == "worker"
    assert plan["summary"]["code_alert_count"] == 2
    assert plan["summary"]["critical_code_alert_count"] == 0
    assert plan["summary"]["route_count"] == 5
    assert plan["summary"]["route_ready_count"] == 5
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 4
    assert plan["summary"]["runs_tests"] is False
    assert plan["summary"]["changes_files"] is False
    assert plan["summary"]["creates_snapshot"] is False
    assert plan["summary"]["requires_run_approval"] is True
    assert plan["workspace_rows"][0]["id"] == "repo-1"
    assert "npm test" in commands
    assert "python -m pytest -q" in commands
    assert plan["alert_rows"][0]["title"] == "Snapshot approval required"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert any(row["title"] == "Test run approval required" for row in plan["alert_rows"])
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert any(row["entry"] == "voice" and row["command_id"] == "run-tests" for row in plan["route_rows"])
    assert all(row["executes"] is False and row["requires_approval"] is True for row in plan["route_rows"])
    assert all(row["command_id"] == "run-tests" for row in plan["entry_rows"])
    assert all(row["run_api"] == "/api/code-workspaces/{workspace_id}/run" for row in plan["entry_rows"])
    assert all(row["snapshot_api"] == "/api/code-workspaces/{workspace_id}/snapshots" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["runs_tests"] is False for row in plan["entry_rows"])
    assert all(row["changes_files"] is False for row in plan["entry_rows"])
    assert all(row["creates_snapshot"] is False for row in plan["entry_rows"])
    assert all(row["applies_diffs"] is False for row in plan["entry_rows"])
    assert all(row["restores_snapshots"] is False for row in plan["entry_rows"])
    assert all(row["commits_changes"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "code-workspace-selection-handoff",
        "code-status-diff-handoff",
        "code-snapshot-checkpoint-handoff",
        "code-exact-command-approval-handoff",
        "code-runner-isolation-handoff",
        "code-activity-output-handoff",
        "code-recovery-rollback-handoff",
    }
    assert handoffs["code-exact-command-approval-handoff"]["target_api"] == "/api/code-workspaces/{workspace_id}/run"
    assert handoffs["code-snapshot-checkpoint-handoff"]["target_api"] == "/api/code-workspaces/{workspace_id}/snapshots"
    assert handoffs["code-runner-isolation-handoff"]["requires_approval"] is False
    assert handoffs["code-activity-output-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["runs_tests"] is False for row in plan["handoff_rows"])
    assert all(row["changes_files"] is False for row in plan["handoff_rows"])
    assert all(row["creates_snapshot"] is False for row in plan["handoff_rows"])
    assert all(row["reads_diff"] is False for row in plan["handoff_rows"])
    assert all(row["restores_snapshots"] is False for row in plan["handoff_rows"])
    assert all(row["commits_changes"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["code-status-diff-handoff"]["gated_operation"]["reads_diff"] is True
    assert handoffs["code-snapshot-checkpoint-handoff"]["gated_operation"]["creates_snapshot"] is True
    assert handoffs["code-exact-command-approval-handoff"]["gated_operation"]["runs_tests"] is True
    assert handoffs["code-exact-command-approval-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["code-activity-output-handoff"]["gated_operation"]["writes_activity"] is True
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
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-build-watch-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["workspace_count"] == 1
    assert plan["summary"]["candidate_command_count"] == 2
    assert plan["summary"]["runner"] == "worker"
    assert plan["summary"]["build_alert_count"] == 3
    assert plan["summary"]["critical_build_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 4
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
    assert plan["alert_rows"][0]["title"] == "Recovery snapshot approval required"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert any(row["title"] == "Build Watch loop approval required" for row in plan["alert_rows"])
    assert "/api/code-workspaces/{workspace_id}/run" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "watch-build-until-green" for row in plan["entry_rows"])
    assert all(row["approval_command_id"] == "request-build-watch-loop" for row in plan["entry_rows"])
    assert all(row["plan_api"] == "/api/operator/build-watch-plan" for row in plan["entry_rows"])
    assert all(row["run_api"] == "/api/code-workspaces/{workspace_id}/run" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["starts_loop"] is False for row in plan["entry_rows"])
    assert all(row["runs_build"] is False for row in plan["entry_rows"])
    assert all(row["edits_files"] is False for row in plan["entry_rows"])
    assert all(row["creates_snapshot"] is False for row in plan["entry_rows"])
    assert all(row["restores_snapshot"] is False for row in plan["entry_rows"])
    assert all(row["installs_dependencies"] is False for row in plan["entry_rows"])
    assert all(row["commits_changes"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "build-watch-workspace-selection-handoff",
        "build-watch-status-diff-handoff",
        "build-watch-snapshot-checkpoint-handoff",
        "build-watch-loop-approval-handoff",
        "build-watch-runner-build-handoff",
        "build-watch-repair-iteration-handoff",
        "build-watch-recovery-rollback-handoff",
        "build-watch-activity-ledger-handoff",
    }
    assert handoffs["build-watch-loop-approval-handoff"]["target_api"] == "/api/operator/workflows"
    assert handoffs["build-watch-snapshot-checkpoint-handoff"]["target_api"] == "/api/code-workspaces/{workspace_id}/snapshots"
    assert handoffs["build-watch-runner-build-handoff"]["target_api"] == "/api/code-workspaces/{workspace_id}/run"
    assert handoffs["build-watch-runner-build-handoff"]["requires_approval"] is False
    assert handoffs["build-watch-activity-ledger-handoff"]["requires_approval"] is False
    assert all(row["approval_command_id"] == "request-build-watch-loop" for row in plan["handoff_rows"])
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["starts_loop"] is False for row in plan["handoff_rows"])
    assert all(row["runs_build"] is False for row in plan["handoff_rows"])
    assert all(row["edits_files"] is False for row in plan["handoff_rows"])
    assert all(row["creates_snapshot"] is False for row in plan["handoff_rows"])
    assert all(row["reads_diff"] is False for row in plan["handoff_rows"])
    assert all(row["restores_snapshot"] is False for row in plan["handoff_rows"])
    assert all(row["installs_dependencies"] is False for row in plan["handoff_rows"])
    assert all(row["commits_changes"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["build-watch-status-diff-handoff"]["gated_operation"]["reads_diff"] is True
    assert handoffs["build-watch-snapshot-checkpoint-handoff"]["gated_operation"]["creates_snapshot"] is True
    assert handoffs["build-watch-loop-approval-handoff"]["gated_operation"]["starts_loop"] is True
    assert handoffs["build-watch-runner-build-handoff"]["gated_operation"]["runs_build"] is True
    assert handoffs["build-watch-runner-build-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["build-watch-repair-iteration-handoff"]["gated_operation"]["edits_files"] is True
    assert handoffs["build-watch-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
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
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-document-search-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["document_count"] == 1
    assert plan["summary"]["chunk_count"] == 1
    assert plan["summary"]["directory_count"] == 2
    assert plan["summary"]["vector_ready"] is True
    assert plan["summary"]["keyword_ready"] is True
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["document_alert_count"] == 1
    assert plan["summary"]["critical_document_alert_count"] == 0
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 6
    assert plan["summary"]["runs_search"] is False
    assert plan["summary"]["reads_query"] is False
    assert plan["summary"]["indexes_files"] is False
    assert plan["summary"]["changes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert plan["summary"]["activity_metadata_only"] is True
    assert plan["summary"]["route_command_id"] == "search-local-documents"
    evidence_titles = {row["title"] for row in plan["evidence_rows"]}
    assert "/api/personal/search" in api_paths
    assert "/api/personal/reload" in api_paths
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "search-local-documents" for row in plan["entry_rows"])
    assert all(row["requires_query"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["route_rows"])
    assert all(row["executes"] is False for row in plan["guard_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert set(handoffs) == {
        "document-query-review-handoff",
        "document-vector-keyword-handoff",
        "document-index-refresh-handoff",
        "document-directory-scope-handoff",
        "document-exclusion-handoff",
        "document-research-escalation-handoff",
        "document-activity-handoff",
    }
    assert handoffs["document-query-review-handoff"]["target_api"] == "/api/personal/search"
    assert handoffs["document-index-refresh-handoff"]["requires_approval"] is True
    assert handoffs["document-research-escalation-handoff"]["target_api"] == "/api/operator/research-plan"
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["runs_search"] is False for row in plan["handoff_rows"])
    assert all(row["reads_query"] is False for row in plan["handoff_rows"])
    assert all(row["reads_result_snippets"] is False for row in plan["handoff_rows"])
    assert all(row["indexes_files"] is False for row in plan["handoff_rows"])
    assert all(row["changes_files"] is False for row in plan["handoff_rows"])
    assert all(row["adds_directories"] is False for row in plan["handoff_rows"])
    assert all(row["excludes_files"] is False for row in plan["handoff_rows"])
    assert all(row["rebuilds_rag"] is False for row in plan["handoff_rows"])
    assert all(row["starts_research"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["requires_approval"] is True for row in plan["guard_rows"])
    assert plan["alert_rows"][0]["title"] == "Index changes require review"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert "Search activity ledger" in evidence_titles
    assert "does not run a query" in plan["approval"]["policy"]
    assert plan["paths"]["personal_docs"] == str(personal_dir)
    assert plan["paths"]["activity"] == "data/operator_activity.json"

    empty = run_operator_document_search_plan(
        "alice",
        personal_docs_manager=None,
        rag_manager=None,
        data_root=tmp_path,
        personal_dir=personal_dir,
    )
    empty_titles = {row["title"] for row in empty["alert_rows"]}
    assert empty["summary"]["document_alert_count"] == 4
    assert empty["summary"]["critical_document_alert_count"] == 0
    assert empty["summary"]["entry_route_count"] == 5
    assert empty["summary"]["entry_route_ready_count"] == 3
    assert "Local document index is empty" in empty_titles
    assert "Vector search unavailable" in empty_titles
    assert "Keyword fallback not ready" in empty_titles
    assert "Index changes require review" in empty_titles


def test_operator_research_plan_is_read_only_and_network_gated():
    from src.operator_research import run_operator_research_plan

    plan = run_operator_research_plan(
        "alice",
        features={"deep_research": True, "web_search": True, "network_integrations": True},
        settings={
            "research_search_provider": "searxng",
            "search_provider": "searxng",
            "search_url": "http://searxng:8080",
            "research_model": "llama3",
            "search_fallback_chain": ["duckduckgo"],
        },
        reports=[{"id": "r1", "owner": "alice", "query": "local models", "sources": [{"url": "x"}], "status": "done"}],
        active_jobs=[],
        offline=False,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}

    assert plan["mode"] == "read-only-research-operations-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["deep_research_enabled"] is True
    assert plan["summary"]["web_search_enabled"] is True
    assert plan["summary"]["provider"] == "searxng"
    assert plan["summary"]["provider_ready"] is True
    assert plan["summary"]["source_gathering_ready"] is True
    assert plan["summary"]["report_count"] == 1
    assert plan["summary"]["research_alert_count"] == 1
    assert plan["summary"]["critical_research_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 5
    assert plan["summary"]["handoff_ready_count"] == 5
    assert plan["summary"]["runs_search"] is False
    assert plan["summary"]["starts_research"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["writes_reports"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-research-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-research" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/research/start" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["starts_research"] is False for row in plan["entry_rows"])
    assert all(row["runs_search"] is False for row in plan["entry_rows"])
    assert all(row["writes_reports"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    handoff_ids = {row["id"] for row in plan["handoff_rows"]}
    assert "research-local-document-handoff" in handoff_ids
    assert "research-saved-report-handoff" in handoff_ids
    assert "research-gallery-evidence-handoff" in handoff_ids
    assert "research-workspace-synthesis-handoff" in handoff_ids
    assert "research-web-source-approval-handoff" in handoff_ids
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["starts_research"] is False for row in plan["handoff_rows"])
    assert all(row["starts_jobs"] is False for row in plan["handoff_rows"])
    assert all(row["runs_search"] is False for row in plan["handoff_rows"])
    assert all(row["writes_reports"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["target_api"] == "/api/operator/document-search-plan" for row in plan["handoff_rows"])
    assert any(row.get("approval_api") == "/api/research/start" and row["requires_approval"] is True for row in plan["handoff_rows"])
    assert "Research start approval required" in alert_titles
    assert "/api/operator/research-plan" in api_paths
    assert "/api/research/start" in api_paths
    assert "/api/search" in api_paths
    assert any(row["starts_job"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["uses_network"] is True for row in plan["api_actions"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert "does not start research" in plan["approval"]["policy"]
    assert plan["paths"]["reports"] == "data/deep_research"

    weak = run_operator_research_plan(
        "alice",
        features={"deep_research": False, "web_search": False, "network_integrations": False},
        settings={
            "research_search_provider": "disabled",
            "search_provider": "disabled",
            "research_model": "",
            "default_model": "",
            "research_endpoint_id": "",
            "default_endpoint_id": "",
        },
        reports=[{"id": "bad", "owner": "alice", "query": "bad", "status": "failed"}],
        active_jobs=[{"session_id": "a", "query": "running", "status": "running"}],
        offline=True,
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["research_alert_count"] == 8
    assert weak["summary"]["critical_research_alert_count"] == 2
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert weak["summary"]["handoff_count"] == 5
    assert weak["summary"]["handoff_ready_count"] == 3
    assert "Deep Research feature disabled" in weak_titles
    assert "Research source gathering blocked" in weak_titles
    assert "Research search provider disabled" in weak_titles
    assert "Research model route missing" in weak_titles
    assert "Active research jobs running" in weak_titles
    assert "Failed research reports need review" in weak_titles


def test_operator_gallery_plan_is_read_only_and_media_gated(tmp_path):
    from src.operator_gallery import run_operator_gallery_plan

    data_root = tmp_path / "data"
    upload_root = tmp_path / "uploads"
    for name in ("generated_images", "gallery", "gallery_uploads"):
        (data_root / name).mkdir(parents=True)
    upload_root.mkdir()
    (data_root / "generated_images" / "one.png").write_bytes(b"png")

    plan = run_operator_gallery_plan(
        "alice",
        data_root=data_root,
        upload_root=upload_root,
        features={"gallery": True, "external_model_endpoints": False},
        settings={"image_model": "local-image", "image_gen_enabled": True, "vision_enabled": True},
        upload_stats={"files": 1},
        offline=True,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-gallery-media-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["media_file_count"] == 1
    assert plan["summary"]["gallery_enabled"] is True
    assert plan["summary"]["image_generation_enabled"] is True
    assert plan["summary"]["vision_enabled"] is True
    assert plan["summary"]["gallery_alert_count"] == 1
    assert plan["summary"]["critical_gallery_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 7
    assert plan["summary"]["uploads_files"] is False
    assert plan["summary"]["generates_images"] is False
    assert plan["summary"]["edits_media"] is False
    assert plan["summary"]["deletes_media"] is False
    assert plan["summary"]["exports_media"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-library-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-gallery" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/gallery/upload" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["uploads_files"] is False for row in plan["entry_rows"])
    assert all(row["generates_images"] is False for row in plan["entry_rows"])
    assert all(row["edits_media"] is False for row in plan["entry_rows"])
    assert all(row["deletes_media"] is False for row in plan["entry_rows"])
    assert all(row["exports_media"] is False for row in plan["entry_rows"])
    assert all(row["refreshes_vision"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "gallery-upload-import-handoff",
        "gallery-generate-image-handoff",
        "gallery-edit-transform-handoff",
        "gallery-delete-archive-handoff",
        "gallery-export-download-handoff",
        "gallery-vision-refresh-handoff",
        "gallery-network-provider-handoff",
    }
    assert handoffs["gallery-upload-import-handoff"]["target_api"] == "/api/gallery/upload"
    assert handoffs["gallery-generate-image-handoff"]["target_api"] == "/api/gallery/style-transfer"
    assert handoffs["gallery-delete-archive-handoff"]["method"] == "DELETE"
    assert handoffs["gallery-network-provider-handoff"]["requires_approval"] is False
    assert all(row["requires_approval"] is True for row in plan["handoff_rows"] if row["id"] != "gallery-network-provider-handoff")
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["uploads_files"] is False for row in plan["handoff_rows"])
    assert all(row["generates_images"] is False for row in plan["handoff_rows"])
    assert all(row["edits_media"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_media"] is False for row in plan["handoff_rows"])
    assert all(row["exports_media"] is False for row in plan["handoff_rows"])
    assert all(row["refreshes_vision"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["gallery-upload-import-handoff"]["gated_operation"]["uploads_files"] is True
    assert handoffs["gallery-generate-image-handoff"]["gated_operation"]["generates_images"] is True
    assert handoffs["gallery-edit-transform-handoff"]["gated_operation"]["edits_media"] is True
    assert handoffs["gallery-delete-archive-handoff"]["gated_operation"]["deletes_media"] is True
    assert handoffs["gallery-export-download-handoff"]["gated_operation"]["exports_media"] is True
    assert handoffs["gallery-vision-refresh-handoff"]["gated_operation"]["refreshes_vision"] is True
    assert plan["alert_rows"][0]["title"] == "Media write/delete gates require review"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert "/api/operator/gallery-plan" in api_paths
    assert "/api/gallery/upload" in api_paths
    assert "/api/gallery/style-transfer" in api_paths
    assert "/api/gallery/ai-upscale" in api_paths
    assert "/api/gallery/{image_id}" in api_paths
    assert any(row["deletes"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert "does not upload files" in plan["approval"]["policy"]
    assert plan["paths"]["uploads"] == str(upload_root)

    weak = run_operator_gallery_plan(
        "alice",
        data_root=tmp_path / "missing-data",
        upload_root=tmp_path / "missing-uploads",
        features={"gallery": False, "external_model_endpoints": True},
        settings={"image_model": "", "image_gen_enabled": False, "vision_enabled": False},
        upload_stats={"files": 0},
        offline=False,
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["gallery_alert_count"] == 11
    assert weak["summary"]["critical_gallery_alert_count"] == 4
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert weak["summary"]["handoff_count"] == 7
    assert weak["summary"]["handoff_ready_count"] == 0
    assert "Media root missing: Generated image files" in weak_titles
    assert "Media root missing: Chat upload files" in weak_titles
    assert "Gallery feature disabled" in weak_titles
    assert "Image generation disabled" in weak_titles
    assert "Vision captioning disabled" in weak_titles
    assert "External image endpoints enabled" in weak_titles
    assert "Upload index empty" in weak_titles


def test_operator_workspace_plan_unifies_local_workbench_without_execution():
    from src.operator_workspace import run_operator_workspace_plan

    plan = run_operator_workspace_plan(
        "alice",
        code_plan={
            "mode": "read-only-code-test-plan",
            "summary": {"workspace_count": 1, "candidate_command_count": 2, "runs_tests": False, "state": "ok"},
        },
        build_watch_plan={
            "mode": "read-only-build-watch-plan",
            "summary": {"workspace_count": 1, "starts_loop": False, "runs_build": False, "state": "ok"},
        },
        document_plan={
            "mode": "read-only-document-search-plan",
            "summary": {"document_count": 3, "runs_search": False, "uses_network": False, "state": "ok"},
        },
        research_plan={
            "mode": "read-only-research-operations-plan",
            "summary": {"active_job_count": 0, "saved_report_count": 2, "starts_research": False, "uses_network": False, "state": "ok"},
        },
        gallery_plan={
            "mode": "read-only-gallery-media-plan",
            "summary": {"image_count": 4, "uploads_files": False, "generates_images": False, "state": "ok"},
        },
        file_ops_plan={
            "mode": "read-only-file-ops-plan",
            "summary": {"root_count": 5, "writes_files": False, "deletes_files": False, "state": "ok"},
        },
        data_plan={
            "mode": "read-only-data-plan",
            "summary": {"data_root_count": 6, "uses_network": False, "state": "ok"},
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-local-workspace-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["workspace_row_count"] == 6
    assert plan["summary"]["workspace_ready_count"] == 6
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 8
    assert plan["summary"]["workspace_alert_count"] == 0
    assert plan["summary"]["runs_tests"] is False
    assert plan["summary"]["runs_build"] is False
    assert plan["summary"]["starts_build_watch"] is False
    assert plan["summary"]["runs_search"] is False
    assert plan["summary"]["starts_research"] is False
    assert plan["summary"]["uploads_files"] is False
    assert plan["summary"]["generates_images"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["deletes_files"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["workspace_api"] == "/api/operator/workspace-plan" for row in plan["entry_rows"])
    assert all(row["runs_tests"] is False for row in plan["entry_rows"])
    assert all(row["runs_search"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "workspace-code-test-handoff",
        "workspace-build-watch-handoff",
        "workspace-document-search-handoff",
        "workspace-research-escalation-handoff",
        "workspace-media-operation-handoff",
        "workspace-file-data-handoff",
        "workspace-backup-recovery-handoff",
        "workspace-activity-handoff",
    }
    assert handoffs["workspace-code-test-handoff"]["target_api"] == "/api/code-workspaces/{id}/run"
    assert handoffs["workspace-research-escalation-handoff"]["target_api"] == "/api/research/start"
    assert handoffs["workspace-activity-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["runs_tests"] is False for row in plan["handoff_rows"])
    assert all(row["runs_search"] is False for row in plan["handoff_rows"])
    assert all(row["starts_research"] is False for row in plan["handoff_rows"])
    assert all(row["uploads_files"] is False for row in plan["handoff_rows"])
    assert all(row["generates_images"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_files"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["workspace-code-test-handoff"]["gated_operation"]["runs_tests"] is True
    assert handoffs["workspace-code-test-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["workspace-build-watch-handoff"]["gated_operation"]["starts_build_watch"] is True
    assert handoffs["workspace-document-search-handoff"]["gated_operation"]["runs_search"] is True
    assert handoffs["workspace-research-escalation-handoff"]["gated_operation"]["starts_research"] is True
    assert handoffs["workspace-research-escalation-handoff"]["gated_operation"]["uses_network"] is True
    assert handoffs["workspace-media-operation-handoff"]["gated_operation"]["uploads_files"] is True
    assert handoffs["workspace-media-operation-handoff"]["gated_operation"]["generates_images"] is True
    assert handoffs["workspace-file-data-handoff"]["gated_operation"]["writes_files"] is True
    assert handoffs["workspace-file-data-handoff"]["gated_operation"]["deletes_files"] is True
    assert "/api/operator/workspace-plan" in api_paths
    assert "/api/operator/code-test-plan" in api_paths
    assert "/api/operator/document-search-plan" in api_paths
    assert "/api/operator/research-plan" in api_paths
    assert "/api/operator/gallery-plan" in api_paths
    assert "/api/operator/file-ops-plan" in api_paths
    assert any(row["path"] == "/api/files/delete" and row["requires_approval"] is True for row in plan["api_actions"])
    assert all(row["runs_shell"] is False for row in plan["api_actions"])
    assert "does not run tests" in plan["approval"]["policy"]

    weak = run_operator_workspace_plan("alice")
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["workspace_alert_count"] == 6
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert weak["summary"]["handoff_count"] == 8
    assert weak["summary"]["handoff_ready_count"] == 1
    assert "Code workspaces" in weak_titles
    assert "File and local data boundary" in weak_titles


def test_operator_notes_plan_is_read_only_and_reminder_gated():
    from src.operator_notes import run_operator_notes_plan

    notes = [
        {
            "id": "n1",
            "owner": "alice",
            "title": "Follow up",
            "content": "todo call client",
            "items": [{"text": "call", "done": False}],
            "pinned": True,
            "archived": False,
            "due_date": "2026-06-22T10:00:00Z",
            "updated_at": "2026-06-22T09:00:00Z",
        },
        {
            "id": "n2",
            "owner": "alice",
            "title": "Done",
            "items": [{"text": "done", "done": True}],
            "archived": False,
            "updated_at": "2026-06-21T09:00:00Z",
        },
        {
            "id": "n3",
            "owner": "alice",
            "title": "Old",
            "archived": True,
            "updated_at": "2026-06-20T09:00:00Z",
        },
    ]
    plan = run_operator_notes_plan(
        "alice",
        notes=notes,
        settings={"reminder_channel": "browser"},
        features={},
        offline=True,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-notes-operations-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["note_count"] == 3
    assert plan["summary"]["active_note_count"] == 2
    assert plan["summary"]["archived_note_count"] == 1
    assert plan["summary"]["pinned_note_count"] == 1
    assert plan["summary"]["checklist_note_count"] == 2
    assert plan["summary"]["open_checklist_item_count"] == 1
    assert plan["summary"]["due_note_count"] == 1
    assert plan["summary"]["task_candidate_count"] == 1
    assert plan["summary"]["notes_alert_count"] == 3
    assert plan["summary"]["critical_notes_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 2
    assert plan["summary"]["creates_notes"] is False
    assert plan["summary"]["updates_notes"] is False
    assert plan["summary"]["archives_notes"] is False
    assert plan["summary"]["deletes_notes"] is False
    assert plan["summary"]["fires_reminders"] is False
    assert plan["summary"]["creates_tasks"] is False
    assert plan["summary"]["uses_network"] is False
    assert "Note reminders need review" in alert_titles
    assert "Notes ready for task draft" in alert_titles
    assert "Note write/delete gates require review" in alert_titles
    assert "/api/operator/notes-plan" in api_paths
    assert "/api/notes/fire-reminder" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-work-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-notes" for row in plan["entry_rows"])
    assert all(row["draft_command_id"] == "draft-task-from-note" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/notes" for row in plan["entry_rows"])
    assert all(row["reminder_api"] == "/api/notes/fire-reminder" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["creates_notes"] is False for row in plan["entry_rows"])
    assert all(row["updates_notes"] is False for row in plan["entry_rows"])
    assert all(row["deletes_notes"] is False for row in plan["entry_rows"])
    assert all(row["fires_reminders"] is False for row in plan["entry_rows"])
    assert all(row["creates_tasks"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "notes-create-update-handoff",
        "notes-archive-delete-handoff",
        "notes-checklist-handoff",
        "notes-reminder-notification-handoff",
        "notes-task-draft-handoff",
        "notes-search-export-handoff",
        "notes-activity-recovery-handoff",
    }
    assert handoffs["notes-create-update-handoff"]["target_api"] == "/api/notes"
    assert handoffs["notes-task-draft-handoff"]["target_api"] == "/api/operator/note-task-draft"
    assert handoffs["notes-search-export-handoff"]["state"] == "ok"
    assert handoffs["notes-activity-recovery-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["creates_notes"] is False for row in plan["handoff_rows"])
    assert all(row["updates_notes"] is False for row in plan["handoff_rows"])
    assert all(row["archives_notes"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_notes"] is False for row in plan["handoff_rows"])
    assert all(row["toggles_checklist_items"] is False for row in plan["handoff_rows"])
    assert all(row["fires_reminders"] is False for row in plan["handoff_rows"])
    assert all(row["creates_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["exports_notes"] is False for row in plan["handoff_rows"])
    assert all(row["indexes_notes"] is False for row in plan["handoff_rows"])
    assert all(row["sends_notifications"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["deletes"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert "does not create notes" in plan["approval"]["policy"]
    assert plan["paths"]["notes"] == "data/app.db:notes"

    weak = run_operator_notes_plan(
        "alice",
        notes=[],
        settings={"reminder_channel": "email"},
        features={"email": False, "network_notifications": False},
        offline=True,
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["notes_alert_count"] == 3
    assert weak["summary"]["critical_notes_alert_count"] == 0
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 5
    assert "No active notes visible" in weak_titles
    assert "Network reminder channel blocked" in weak_titles
    assert "Note write/delete gates require review" in weak_titles


def test_operator_calendar_plan_is_read_only_and_sync_gated():
    from src.operator_calendar import run_operator_calendar_plan

    current = dt.datetime(2026, 6, 22, 8, 0, tzinfo=dt.timezone.utc)
    events = [
        {
            "uid": "e1",
            "owner": "alice",
            "summary": "Standup",
            "dtstart": "2026-06-22T09:00:00Z",
            "dtend": "2026-06-22T09:30:00Z",
            "reminder_minutes": 10,
            "status": "confirmed",
        },
        {
            "uid": "e2",
            "owner": "alice",
            "summary": "Planning day",
            "dtstart": "2026-06-23T00:00:00Z",
            "dtend": "2026-06-24T00:00:00Z",
            "all_day": True,
            "rrule": "FREQ=WEEKLY",
        },
        {
            "uid": "e3",
            "owner": "bob",
            "summary": "Other owner",
            "dtstart": "2026-06-22T11:00:00Z",
            "dtend": "2026-06-22T12:00:00Z",
        },
    ]
    plan = run_operator_calendar_plan(
        "alice",
        events=events,
        settings={"caldav": {}},
        features={"calendar": True, "network_integrations": True},
        offline=True,
        now=current,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-calendar-operations-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["event_count"] == 2
    assert plan["summary"]["today_event_count"] == 1
    assert plan["summary"]["upcoming_event_count"] == 2
    assert plan["summary"]["all_day_event_count"] == 1
    assert plan["summary"]["reminder_event_count"] == 1
    assert plan["summary"]["recurring_event_count"] == 1
    assert plan["summary"]["calendar_sync_configured"] is False
    assert plan["summary"]["calendar_alert_count"] == 5
    assert plan["summary"]["critical_calendar_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 2
    assert plan["summary"]["creates_events"] is False
    assert plan["summary"]["updates_events"] is False
    assert plan["summary"]["deletes_events"] is False
    assert plan["summary"]["imports_calendars"] is False
    assert plan["summary"]["exports_calendars"] is False
    assert plan["summary"]["syncs_calendars"] is False
    assert plan["summary"]["sends_notifications"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-work-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-calendar" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/calendar/events" for row in plan["entry_rows"])
    assert all(row["sync_api"] == "/api/calendar/sync" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["creates_events"] is False for row in plan["entry_rows"])
    assert all(row["syncs_calendars"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "calendar-create-update-handoff",
        "calendar-delete-lifecycle-handoff",
        "calendar-import-export-handoff",
        "calendar-sync-egress-handoff",
        "calendar-reminder-notification-handoff",
        "calendar-recurring-rule-handoff",
        "calendar-activity-recovery-handoff",
    }
    assert handoffs["calendar-create-update-handoff"]["target_api"] == "/api/calendar/events"
    assert handoffs["calendar-sync-egress-handoff"]["state"] == "ok"
    assert handoffs["calendar-import-export-handoff"]["target_api"] == "/api/calendar/import"
    assert handoffs["calendar-activity-recovery-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["creates_events"] is False for row in plan["handoff_rows"])
    assert all(row["updates_events"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_events"] is False for row in plan["handoff_rows"])
    assert all(row["imports_calendars"] is False for row in plan["handoff_rows"])
    assert all(row["exports_calendars"] is False for row in plan["handoff_rows"])
    assert all(row["syncs_calendars"] is False for row in plan["handoff_rows"])
    assert all(row["tests_remote_connection"] is False for row in plan["handoff_rows"])
    assert all(row["sends_notifications"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert "Calendar events due today" in alert_titles
    assert "Upcoming calendar window active" in alert_titles
    assert "Calendar reminders need review" in alert_titles
    assert "Recurring calendar rules present" in alert_titles
    assert "Calendar write/delete gates require review" in alert_titles
    assert "/api/operator/calendar-plan" in api_paths
    assert "/api/calendar/import" in api_paths
    assert "/api/calendar/sync" in api_paths
    assert any(row["deletes"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["uses_network"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert "does not create calendar events" in plan["approval"]["policy"]
    assert plan["paths"]["events"] == "data/app.db:calendar_events"

    weak = run_operator_calendar_plan(
        "alice",
        events=[],
        settings={"caldav": {"url": "https://calendar.example"}},
        features={"calendar": False, "network_integrations": False},
        offline=True,
        now=current,
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["calendar_alert_count"] == 4
    assert weak["summary"]["critical_calendar_alert_count"] == 0
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert "No local calendar events visible" in weak_titles
    assert "Calendar feature disabled" in weak_titles
    assert "Calendar sync blocked by local policy" in weak_titles
    assert "Calendar write/delete gates require review" in weak_titles


def test_operator_tasks_plan_is_read_only_and_trigger_gated():
    from src.operator_tasks import run_operator_tasks_plan

    current = dt.datetime(2026, 6, 22, 8, 0, tzinfo=dt.timezone.utc)
    tasks = [
        {
            "id": "t1",
            "owner": "alice",
            "name": "Morning summary",
            "status": "active",
            "task_type": "llm",
            "trigger_type": "schedule",
            "schedule": "daily",
            "next_run": "2026-06-22T07:00:00Z",
            "notifications_enabled": True,
            "output_target": "notification",
        },
        {
            "id": "t2",
            "owner": "alice",
            "name": "Webhook task",
            "status": "active",
            "task_type": "llm",
            "trigger_type": "webhook",
            "output_target": "session",
        },
        {
            "id": "t3",
            "owner": "alice",
            "name": "Shell task",
            "status": "active",
            "task_type": "action",
            "action": "run_local",
            "trigger_type": "schedule",
            "schedule": "daily",
            "next_run": "2026-06-23T09:00:00Z",
        },
        {
            "id": "t4",
            "owner": "alice",
            "name": "Paused",
            "status": "paused",
            "trigger_type": "schedule",
        },
        {
            "id": "t5",
            "owner": "bob",
            "name": "Other owner",
            "status": "active",
        },
    ]
    runs = [
        {"id": "r1", "owner": "alice", "task_id": "t1", "task_name": "Morning summary", "status": "error", "error": "bad", "started_at": "2026-06-22T07:01:00Z"},
        {"id": "r2", "owner": "alice", "task_id": "t2", "task_name": "Webhook task", "status": "running", "started_at": "2026-06-22T07:02:00Z"},
        {"id": "r3", "owner": "bob", "task_id": "t5", "task_name": "Other owner", "status": "error"},
    ]
    plan = run_operator_tasks_plan(
        "alice",
        tasks=tasks,
        runs=runs,
        settings={"tasks_enabled": True},
        features={"webhooks": False, "network_integrations": False, "email": True},
        offline=True,
        now=current,
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-task-automation-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["task_count"] == 4
    assert plan["summary"]["active_task_count"] == 3
    assert plan["summary"]["paused_task_count"] == 1
    assert plan["summary"]["schedule_task_count"] == 2
    assert plan["summary"]["webhook_task_count"] == 1
    assert plan["summary"]["overdue_task_count"] == 1
    assert plan["summary"]["due_today_count"] == 1
    assert plan["summary"]["run_count"] == 2
    assert plan["summary"]["active_run_count"] == 1
    assert plan["summary"]["failed_run_count"] == 1
    assert plan["summary"]["notification_task_count"] == 1
    assert plan["summary"]["shell_action_task_count"] == 1
    assert plan["summary"]["task_alert_count"] == 8
    assert plan["summary"]["critical_task_alert_count"] == 3
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 1
    assert plan["summary"]["creates_tasks"] is False
    assert plan["summary"]["updates_tasks"] is False
    assert plan["summary"]["deletes_tasks"] is False
    assert plan["summary"]["runs_tasks"] is False
    assert plan["summary"]["stops_tasks"] is False
    assert plan["summary"]["changes_webhooks"] is False
    assert plan["summary"]["sends_notifications"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-work-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-tasks" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/tasks" for row in plan["entry_rows"])
    assert all(row["run_api"] == "/api/tasks/{task_id}/run" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["creates_tasks"] is False for row in plan["entry_rows"])
    assert all(row["runs_tasks"] is False for row in plan["entry_rows"])
    assert all(row["changes_webhooks"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "tasks-create-update-handoff",
        "tasks-lifecycle-delete-handoff",
        "tasks-run-stop-retry-handoff",
        "tasks-webhook-handoff",
        "tasks-notification-output-handoff",
        "tasks-shell-admin-handoff",
        "tasks-activity-recovery-handoff",
    }
    assert handoffs["tasks-create-update-handoff"]["target_api"] == "/api/tasks"
    assert handoffs["tasks-run-stop-retry-handoff"]["state"] == "error"
    assert handoffs["tasks-webhook-handoff"]["target_api"] == "/api/tasks/{task_id}/webhook/{token}"
    assert handoffs["tasks-shell-admin-handoff"]["state"] == "error"
    assert handoffs["tasks-activity-recovery-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["creates_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["updates_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["runs_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["stops_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["changes_webhooks"] is False for row in plan["handoff_rows"])
    assert all(row["clears_cache"] is False for row in plan["handoff_rows"])
    assert all(row["sends_notifications"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert "Scheduled tasks overdue" in alert_titles
    assert "Tasks due today" in alert_titles
    assert "Task runs failed" in alert_titles
    assert "Task runs active" in alert_titles
    assert "Webhook task triggers blocked by policy" in alert_titles
    assert "Task notification delivery needs review" in alert_titles
    assert "Shell-capable task actions require admin review" in alert_titles
    assert "Task write/run gates require review" in alert_titles
    assert "/api/operator/tasks-plan" in api_paths
    assert "/api/tasks/{task_id}/run" in api_paths
    assert "/api/tasks/{task_id}/webhook/{token}" in api_paths
    assert any(row["starts_job"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["deletes"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert "does not create tasks" in plan["approval"]["policy"]
    assert plan["paths"]["tasks"] == "data/app.db:scheduled_tasks"

    weak = run_operator_tasks_plan(
        "alice",
        tasks=[],
        runs=[],
        settings={"tasks_enabled": False},
        features={"tasks": False, "webhooks": False, "network_integrations": False},
        offline=True,
        now=current,
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["task_alert_count"] == 3
    assert weak["summary"]["critical_task_alert_count"] == 0
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert "Tasks feature disabled" in weak_titles
    assert "No scheduled tasks visible" in weak_titles
    assert "Task write/run gates require review" in weak_titles


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
    assert plan["summary"]["activity_metadata_only"] is True
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["voice_alert_count"] == 2
    assert plan["summary"]["critical_voice_alert_count"] == 0
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 5
    assert plan["summary"]["route_command_id"] == "start-voice-command"
    assert plan["summary"]["setup_command_id"] == "enable-browser-voice-mode"
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert {row["id"] for row in plan["handoff_rows"]} == {
        "voice-permission-handoff",
        "voice-transcript-route-handoff",
        "voice-trust-gate-handoff",
        "voice-activity-ledger-handoff",
        "voice-output-review-handoff",
        "voice-endpoint-privacy-handoff",
    }
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    assert "Microphone permission required" in alert_titles
    assert "Voice start requires user activation" in alert_titles
    assert {row["command_id"] for row in plan["entry_rows"]} == {"open-voice-preflight", "start-voice-command"}
    assert all(row["requires_permission"] is True for row in plan["entry_rows"])
    assert all(row["requires_user_activation"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["starts_microphone"] is False for row in plan["entry_rows"])
    assert all(row["records_audio"] is False for row in plan["entry_rows"])
    assert all(row["speaks_audio"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["alert_rows"])
    assert all(row["starts_microphone"] is False for row in plan["alert_rows"])
    assert all(row["records_audio"] is False for row in plan["alert_rows"])
    assert all(row["uploads_audio"] is False for row in plan["alert_rows"])
    assert all(row["transcribes_audio"] is False for row in plan["alert_rows"])
    assert all(row["synthesizes_audio"] is False for row in plan["alert_rows"])
    assert all(row["speaks_audio"] is False for row in plan["alert_rows"])
    assert all(row["changes_settings"] is False for row in plan["alert_rows"])
    assert all(row["routes_commands"] is False for row in plan["alert_rows"])
    assert all(row["runs_shell"] is False for row in plan["alert_rows"])
    assert all(row["uses_network"] is False for row in plan["alert_rows"])
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["starts_microphone"] is False for row in plan["handoff_rows"])
    assert all(row["records_audio"] is False for row in plan["handoff_rows"])
    assert all(row["uploads_audio"] is False for row in plan["handoff_rows"])
    assert all(row["transcribes_audio"] is False for row in plan["handoff_rows"])
    assert all(row["synthesizes_audio"] is False for row in plan["handoff_rows"])
    assert all(row["speaks_audio"] is False for row in plan["handoff_rows"])
    assert all(row["changes_settings"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert "/api/operator/voice-plan" in api_paths
    assert "/api/stt/transcribe" in api_paths
    assert "/api/tts/synthesize" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["permission_rows"])
    assert any(row["requires_approval"] is True for row in plan["input_rows"])
    assert any(row["id"] == "activity-ledger" and row["state"] == "ok" for row in plan["routing_rows"])
    assert "does not start the microphone" in plan["approval"]["policy"]
    assert plan["paths"]["tts_cache"] == str(tmp_path / "tts_cache")
    assert plan["paths"]["activity"] == "data/operator_activity.json"


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
    entries = {row["entry"] for row in plan["entry_rows"]}

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
    assert plan["summary"]["decision_mode_count"] == 4
    assert plan["summary"]["decision_mode_ready_count"] == 3
    assert plan["summary"]["permission_checkpoint_count"] == 5
    assert plan["summary"]["permission_checkpoint_ready_count"] == 4
    assert plan["summary"]["automation_alert_count"] == 3
    assert plan["summary"]["critical_automation_alert_count"] == 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 7
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
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-automation-map" for row in plan["entry_rows"])
    assert all(row["trust_command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["activity_command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["palette_command_id"] == "open-command-palette" for row in plan["entry_rows"])
    assert all(row["route_api"] == "/api/operator/route" for row in plan["entry_rows"])
    assert all(row["routes_api"] == "/api/operator/routes" for row in plan["entry_rows"])
    assert all(row["policy_api"] == "/api/operator/policy" for row in plan["entry_rows"])
    assert all(row["workflows_api"] == "/api/operator/workflows" for row in plan["entry_rows"])
    assert all(row["activity_api"] == "/api/operator/activity" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["routes_commands"] is False for row in plan["entry_rows"])
    assert all(row["executes_commands"] is False for row in plan["entry_rows"])
    assert all(row["approves_commands"] is False for row in plan["entry_rows"])
    assert all(row["retries_commands"] is False for row in plan["entry_rows"])
    assert all(row["starts_workflows"] is False for row in plan["entry_rows"])
    assert all(row["changes_policy"] is False for row in plan["entry_rows"])
    assert all(row["deletes_activity"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["modifies_files"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    decision_modes = {row["mode"]: row for row in plan["decision_mode_rows"]}
    assert {"suggest", "ask", "execute", "auto-execute"} == set(decision_modes)
    assert decision_modes["suggest"]["state"] == "ok"
    assert decision_modes["ask"]["state"] == "ok"
    assert decision_modes["execute"]["state"] == "ok"
    assert decision_modes["auto-execute"]["state"] == "warn"
    assert all(row["executes"] is False for row in plan["decision_mode_rows"])
    assert all(row["routes_commands"] is False for row in plan["decision_mode_rows"])
    assert all(row["uses_network"] is False for row in plan["decision_mode_rows"])
    checkpoint_ids = {row["id"] for row in plan["permission_checkpoint_rows"]}
    assert "checkpoint-suggest-route-preview" in checkpoint_ids
    assert "checkpoint-ask-evidence-review" in checkpoint_ids
    assert "checkpoint-execute-ledger" in checkpoint_ids
    assert "checkpoint-auto-local-scope" in checkpoint_ids
    assert "checkpoint-workflow-handoff" in checkpoint_ids
    assert all(row["executes"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["routes_commands"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["executes_commands"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["approves_commands"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["retries_commands"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["starts_workflows"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["changes_policy"] is False for row in plan["permission_checkpoint_rows"])
    assert all(row["uses_network"] is False for row in plan["permission_checkpoint_rows"])
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "autonomy-route-preview-handoff",
        "autonomy-ask-approval-handoff",
        "autonomy-workflow-start-handoff",
        "autonomy-activity-ledger-handoff",
        "autonomy-retry-recovery-handoff",
        "autonomy-trust-policy-handoff",
        "autonomy-network-offline-handoff",
        "autonomy-safety-boundary-handoff",
    }
    assert handoffs["autonomy-route-preview-handoff"]["target_api"] == "/api/operator/route"
    assert handoffs["autonomy-ask-approval-handoff"]["target_api"] == "/api/operator/approval-plan"
    assert handoffs["autonomy-workflow-start-handoff"]["target_api"] == "/api/operator/loops-plan"
    assert handoffs["autonomy-activity-ledger-handoff"]["target_api"] == "/api/operator/activity-plan"
    assert handoffs["autonomy-retry-recovery-handoff"]["target_api"] == "/api/operator/recovery-plan"
    assert handoffs["autonomy-trust-policy-handoff"]["target_api"] == "/api/operator/policy"
    assert handoffs["autonomy-network-offline-handoff"]["target_api"] == "/api/operator/safety-plan"
    assert handoffs["autonomy-safety-boundary-handoff"]["target_api"] == "/api/operator/safety-plan"
    assert handoffs["autonomy-route-preview-handoff"]["requires_approval"] is False
    assert handoffs["autonomy-ask-approval-handoff"]["requires_approval"] is True
    assert handoffs["autonomy-retry-recovery-handoff"]["state"] == "warn"
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["approves_commands"] is False for row in plan["handoff_rows"])
    assert all(row["retries_commands"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["changes_policy"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["modifies_files"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["autonomy-route-preview-handoff"]["gated_operation"]["routes_commands"] is True
    assert handoffs["autonomy-ask-approval-handoff"]["gated_operation"]["approves_commands"] is True
    assert handoffs["autonomy-workflow-start-handoff"]["gated_operation"]["starts_workflows"] is True
    assert handoffs["autonomy-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
    assert handoffs["autonomy-retry-recovery-handoff"]["gated_operation"]["retries_commands"] is True
    assert handoffs["autonomy-trust-policy-handoff"]["gated_operation"]["changes_policy"] is True
    assert handoffs["autonomy-network-offline-handoff"]["gated_operation"]["uses_network"] is True
    assert handoffs["autonomy-safety-boundary-handoff"]["gated_operation"]["runs_shell"] is True
    assert any(row["requires_approval"] is True for row in plan["permission_rows"])
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    assert "Pending automation approvals" in alert_titles
    assert "Automation command failures" in alert_titles
    assert "Retryable command records" in alert_titles
    assert "does not route commands" in plan["approval"]["policy"]


def test_operator_automation_plan_unifies_local_automation_without_execution():
    from src.operator_automation import run_operator_automation_plan

    commands = [
        {
            "id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "category": "Automation",
            "trust": "approval",
            "workflow": True,
            "keywords": ["watch build", "build passes"],
        },
        {
            "id": "open-automation-map",
            "title": "Open Automation Map",
            "category": "Automation",
            "trust": "local",
        },
    ]
    workflows = [
        {
            "id": "watch-build-until-green",
            "phrase": "Watch this repo until the build passes.",
            "commandId": "request-build-watch-loop",
            "approvalId": "request-build-watch-loop",
            "expectedRouteId": "request-build-watch-loop",
            "state": "ok",
        }
    ]
    activity = [
        {
            "id": "auto-1",
            "command_id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "status": "pending_approval",
        },
        {
            "id": "auto-2",
            "command_id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "status": "failed",
            "detail": "Build command failed",
        },
    ]

    plan = run_operator_automation_plan(
        "alice",
        commands=commands,
        workflows=workflows,
        loops=[{"id": "build-watch", "title": "Build Until Green"}],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        activity=activity,
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-automation-operations-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["automation_command_count"] == 2
    assert plan["summary"]["ask_automation_count"] == 1
    assert plan["summary"]["workflow_count"] == 1
    assert plan["summary"]["loop_count"] == 1
    assert plan["summary"]["route_match_count"] == 1
    assert plan["summary"]["route_match_ready_count"] == 1
    assert plan["summary"]["pending_count"] == 1
    assert plan["summary"]["failure_count"] == 1
    assert plan["summary"]["activity_count"] == 2
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["automation_alert_count"] >= 1
    assert plan["summary"]["critical_automation_alert_count"] >= 1
    assert plan["summary"]["starts_automation"] is False
    assert plan["summary"]["starts_loops"] is False
    assert plan["summary"]["runs_tasks"] is False
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["approves_commands"] is False
    assert plan["summary"]["writes_activity"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert "/api/operator/automation-plan" in api_paths
    assert "/api/operator/tasks-plan" in api_paths
    assert "/api/operator/build-watch-plan" in api_paths
    assert any(row["id"] == "build-watch-boundary" for row in plan["automation_rows"])
    assert any(row["state"] == "error" for row in plan["activity_rows"])
    assert any(
        row["id"] == "auto-2"
        and row["detail_action"] == "activity-detail:auto-2"
        and row["copy_log_action"] == "copy-activity-log:auto-2"
        and row["retry_action"] == "retry-activity:auto-2"
        and row["recovery_action"] == "open-recovery-map"
        and row["log_ready"] is True
        and row["retryable"] is True
        and row["needs_recovery"] is True
        and row["retries_commands"] is False
        for row in plan["activity_rows"]
    )
    assert any(row["rollback_ready"] is False for row in plan["activity_rows"])
    assert all(row["starts_automation"] is False for row in plan["entry_rows"])
    assert all(row["executes_commands"] is False for row in plan["route_rows"])
    assert all(row["runs_shell"] is False for row in plan["api_actions"])
    assert "does not start automation" in plan["approval"]["policy"]


def test_operator_loops_plan_audits_agent_loops_without_execution():
    from src.operator_loops import run_operator_loops_plan

    commands = [
        {
            "id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "category": "Automation",
            "trust": "approval",
            "workflow": True,
            "keywords": ["watch build", "build passes"],
        },
        {
            "id": "open-trust-controls",
            "title": "Trust Controls",
            "category": "Safety",
            "trust": "local",
        },
    ]
    loops = [
        {
            "id": "build-watch",
            "title": "Build Until Green",
            "category": "Code",
            "mode": "Approval",
            "goal": "Watch the repo until the build passes",
            "check": "npm run build",
            "exit": "build passes",
            "steps": ["snapshot", "run build", "report"],
            "actionIds": ["request-build-watch-loop"],
        }
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
            "id": "loop-1",
            "owner": "alice",
            "command_id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "status": "pending_approval",
        },
        {
            "id": "loop-2",
            "owner": "alice",
            "command_id": "request-build-watch-loop",
            "title": "Start Build Watch Loop",
            "status": "failed",
            "detail": "Build command failed",
        },
    ]

    plan = run_operator_loops_plan(
        "alice",
        loops=loops,
        workflows=workflows,
        commands=commands,
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        activity=activity,
        configured={"commands": True, "workflows": True, "policy": True},
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    alert_titles = {row["title"] for row in plan["alert_rows"]}
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-agent-loops-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["loop_count"] == 1
    assert plan["summary"]["workflow_count"] == 1
    assert plan["summary"]["workflow_command_count"] == 1
    assert plan["summary"]["loop_action_count"] == 1
    assert plan["summary"]["loop_command_count"] == 1
    assert plan["summary"]["approval_gated_count"] == 1
    assert plan["summary"]["route_ready_count"] == 1
    assert plan["summary"]["pending_count"] == 1
    assert plan["summary"]["failure_count"] == 1
    assert plan["summary"]["loop_alert_count"] == 2
    assert plan["summary"]["critical_loop_alert_count"] == 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["starts_loops"] is False
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_jobs"] is False
    assert plan["summary"]["writes_files"] is False
    assert plan["summary"]["uses_network"] is False
    assert "/api/operator/loops-plan" in api_paths
    assert "/api/operator/workflows" in api_paths
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-automation-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-loops" for row in plan["entry_rows"])
    assert all(row["map_command_id"] == "open-automation-map" for row in plan["entry_rows"])
    assert all(row["approval_command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["activity_command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["starts_loops"] is False for row in plan["entry_rows"])
    assert all(row["routes_commands"] is False for row in plan["entry_rows"])
    assert all(row["starts_jobs"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["permission_rows"])
    assert "Loop activity pending" in alert_titles
    assert "Loop activity failures" in alert_titles
    assert "does not start loops" in plan["approval"]["policy"]


def test_operator_memory_plan_is_read_only_and_profiles_local_memory():
    from src.operator_memory import run_operator_memory_plan

    memories = [
        {
            "id": "m1",
            "text": "User prefers local-first tools by default.",
            "category": "preference",
            "owner": "alice",
            "pinned": True,
            "updated_at": "2026-01-01T00:00:00Z",
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
            "category": "model_choice",
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
        {
            "id": "m6",
            "text": "User decided external model endpoints stay disabled unless explicitly enabled.",
            "category": "decision",
            "owner": "alice",
        },
        {
            "id": "m7",
            "text": "Every weekday morning, summarize tasks, calendar, model status, and alerts.",
            "category": "recurring_task",
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
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-memory-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["memory_count"] == 7
    assert plan["summary"]["recent_memory_count"] == 7
    assert plan["summary"]["latest_memory_at"] == "2026-01-01T00:00:00Z"
    assert plan["summary"]["note_count"] == 1
    assert plan["summary"]["pinned_count"] == 1
    assert plan["summary"]["profile_complete_count"] == 7
    assert plan["summary"]["profile_gap_count"] == 0
    assert plan["summary"]["model_choice_count"] == 2
    assert plan["summary"]["remembered_model_choice_count"] == 1
    assert plan["summary"]["default_model_preference_ready"] is True
    assert plan["summary"]["memory_alert_count"] == 1
    assert plan["summary"]["critical_memory_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["memory_enabled"] is True
    assert plan["summary"]["auto_memory"] is True
    assert plan["summary"]["skills_enabled"] is True
    assert plan["summary"]["default_model"] == "llama3.2:3b"
    assert bucket_counts["identity"] == 1
    assert bucket_counts["preferences"] == 1
    assert bucket_counts["projects"] == 1
    assert bucket_counts["decisions"] == 1
    assert bucket_counts["model_choices"] == 1
    assert bucket_counts["recurring_tasks"] == 1
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
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-memory-preflight" for row in plan["entry_rows"])
    assert all(row["profile_command_id"] == "open-memory-profile" for row in plan["entry_rows"])
    assert all(row["seed_command_id"] == "seed-memory-profile" for row in plan["entry_rows"])
    assert all(row["memory_api"] == "/api/memory" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["writes_memories"] is False for row in plan["entry_rows"])
    assert all(row["adds_memories"] is False for row in plan["entry_rows"])
    assert all(row["imports_files"] is False for row in plan["entry_rows"])
    assert all(row["extracts_with_model"] is False for row in plan["entry_rows"])
    assert all(row["audits_with_model"] is False for row in plan["entry_rows"])
    assert all(row["deletes_memories"] is False for row in plan["entry_rows"])
    assert all(row["changes_notes"] is False for row in plan["entry_rows"])
    assert all(row["runs_automation"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert len(plan["recent_rows"]) == 7
    assert len(plan["model_choice_rows"]) == 2
    assert any(row["id"] == "default-model-preference" and row["model"] == "llama3.2:3b" for row in plan["model_choice_rows"])
    assert any(row["id"] == "m3" and row["source"] == "memory" for row in plan["model_choice_rows"])
    assert all(row["sets_primary_model"] is False for row in plan["model_choice_rows"])
    assert all(row["writes_memories"] is False for row in plan["model_choice_rows"])
    assert all(row["uses_network"] is False for row in plan["model_choice_rows"])
    assert any(
        row["bucket"] == "preferences"
        and "local-first tools" in row["detail"]
        and row["writes_memories"] is False
        and row["uses_network"] is False
        for row in plan["recent_rows"]
    )
    assert any(row["bucket"] == "model_choices" and row["pinned"] is False for row in plan["recent_rows"])
    assert any(row["requires_approval"] is True for row in plan["guard_rows"])
    assert plan["alert_rows"][0]["title"] == "Model-assisted memory writes require review"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert "does not add memories" in plan["approval"]["policy"]

    weak = run_operator_memory_plan(
        "alice",
        memories=[],
        notes=[],
        prefs={
            "memory_enabled": False,
            "auto_memory": False,
            "skills_enabled": False,
        },
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["memory_alert_count"] == 7
    assert weak["summary"]["critical_memory_alert_count"] == 0
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["model_choice_count"] == 0
    assert weak["summary"]["default_model_preference_ready"] is False
    assert "Memory store is empty" in weak_titles
    assert "Operator profile has gaps" in weak_titles
    assert "Memory recall disabled" in weak_titles
    assert "Auto memory extraction disabled" in weak_titles
    assert "Skill recall disabled" in weak_titles
    assert "Default model preference missing" in weak_titles


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
    entries = {row["entry"] for row in plan["entry_rows"]}

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
    assert plan["summary"]["alert_count"] == 5
    assert plan["summary"]["critical_alert_count"] == 2
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
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
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "summarize-today" for row in plan["entry_rows"])
    assert all(row["work_command_id"] == "open-work-preflight" for row in plan["entry_rows"])
    assert all(row["tasks_command_id"] == "open-tasks" for row in plan["entry_rows"])
    assert all(row["calendar_command_id"] == "open-calendar" for row in plan["entry_rows"])
    assert all(row["notes_command_id"] == "open-notes" for row in plan["entry_rows"])
    assert all(row["note_task_command_id"] == "draft-task-from-note" for row in plan["entry_rows"])
    assert all(row["activity_command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["trust_command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["workday_api"] == "/api/operator/workday-plan" for row in plan["entry_rows"])
    assert all(row["briefing_api"] == "/api/operator/briefing" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["creates_tasks"] is False for row in plan["entry_rows"])
    assert all(row["updates_tasks"] is False for row in plan["entry_rows"])
    assert all(row["runs_tasks"] is False for row in plan["entry_rows"])
    assert all(row["creates_calendar_events"] is False for row in plan["entry_rows"])
    assert all(row["syncs_calendar"] is False for row in plan["entry_rows"])
    assert all(row["edits_notes"] is False for row in plan["entry_rows"])
    assert all(row["sends_notifications"] is False for row in plan["entry_rows"])
    assert all(row["runs_automation"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["title"] == "Workday write boundary" for row in plan["work_rows"])
    assert any(row["title"] == "Overdue follow-up" and row["badge"] == "late" for row in plan["alert_rows"])
    assert any(row["title"] == "Morning review" and row["badge"] == "run" for row in plan["alert_rows"])
    assert any(row["title"] == "Note ready for task draft" and row["requires_approval"] is True for row in plan["alert_rows"])
    assert "does not create tasks" in plan["approval"]["policy"]
    assert plan["paths"]["tasks"] == "data/app.db:scheduled_tasks"


def test_operator_work_ops_plan_unifies_local_work_without_execution():
    from src.operator_work_ops import run_operator_work_ops_plan

    plan = run_operator_work_ops_plan(
        "alice",
        briefing_plan={
            "mode": "read-only-local",
            "summary": {"state": "warn", "due_today_count": 2, "today_event_count": 1, "briefing_alert_count": 1},
            "alert_rows": [{"id": "brief", "state": "warn", "badge": "brief", "title": "Briefing alert", "detail": "review today"}],
        },
        workday_plan={
            "mode": "read-only-workday-plan",
            "summary": {"state": "warn", "active_task_count": 3, "today_event_count": 1, "note_task_candidate_count": 1, "alert_count": 1},
            "alert_rows": [{"id": "work", "state": "warn", "badge": "work", "title": "Workday alert", "detail": "task due"}],
        },
        tasks_plan={
            "mode": "read-only-task-automation-plan",
            "summary": {"state": "error", "task_count": 4, "active_task_count": 3, "due_today_count": 2, "failed_run_count": 1, "task_alert_count": 1},
            "alert_rows": [{"id": "task", "state": "error", "badge": "task", "title": "Task failed", "detail": "run failed"}],
        },
        notes_plan={
            "mode": "read-only-notes-operations-plan",
            "summary": {"state": "ok", "note_count": 5, "active_note_count": 4, "task_candidate_count": 2, "due_note_count": 1, "notes_alert_count": 0},
        },
        calendar_plan={
            "mode": "read-only-calendar-operations-plan",
            "summary": {"state": "warn", "event_count": 6, "today_event_count": 1, "calendar_alert_count": 1, "calendar_sync_configured": True},
            "alert_rows": [{"id": "cal", "state": "warn", "badge": "cal", "title": "Calendar today", "detail": "event today"}],
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    entries = {row["entry"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-work-operations-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["state"] == "error"
    assert plan["summary"]["task_count"] == 4
    assert plan["summary"]["active_task_count"] == 3
    assert plan["summary"]["due_today_count"] == 2
    assert plan["summary"]["failed_run_count"] == 1
    assert plan["summary"]["today_event_count"] == 1
    assert plan["summary"]["calendar_event_count"] == 6
    assert plan["summary"]["note_count"] == 5
    assert plan["summary"]["task_candidate_count"] == 2
    assert plan["summary"]["work_ops_alert_count"] == 4
    assert plan["summary"]["critical_work_ops_alert_count"] == 1
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 0
    assert plan["summary"]["handoff_count"] == 5
    assert plan["summary"]["handoff_ready_count"] == 2
    assert plan["summary"]["summarize_today_ready"] is True
    assert plan["summary"]["note_task_draft_ready"] is True
    assert plan["summary"]["calendar_review_ready"] is True
    assert plan["summary"]["task_review_ready"] is True
    assert plan["summary"]["creates_tasks"] is False
    assert plan["summary"]["updates_tasks"] is False
    assert plan["summary"]["runs_tasks"] is False
    assert plan["summary"]["creates_notes"] is False
    assert plan["summary"]["updates_notes"] is False
    assert plan["summary"]["fires_reminders"] is False
    assert plan["summary"]["creates_calendar_events"] is False
    assert plan["summary"]["updates_calendar_events"] is False
    assert plan["summary"]["syncs_calendars"] is False
    assert plan["summary"]["runs_automation"] is False
    assert plan["summary"]["writes_activity"] is False
    assert plan["summary"]["sends_notifications"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["work_ops_api"] == "/api/operator/work-ops-plan" for row in plan["entry_rows"])
    assert all(row["briefing_api"] == "/api/operator/briefing" for row in plan["entry_rows"])
    assert all(row["workday_api"] == "/api/operator/workday-plan" for row in plan["entry_rows"])
    assert all(row["tasks_api"] == "/api/operator/tasks-plan" for row in plan["entry_rows"])
    assert all(row["notes_api"] == "/api/operator/notes-plan" for row in plan["entry_rows"])
    assert all(row["calendar_api"] == "/api/operator/calendar-plan" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["creates_tasks"] is False for row in plan["entry_rows"])
    assert all(row["runs_tasks"] is False for row in plan["entry_rows"])
    assert all(row["fires_reminders"] is False for row in plan["entry_rows"])
    assert all(row["syncs_calendars"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert "/api/operator/work-ops-plan" in api_paths
    assert "/api/operator/briefing" in api_paths
    assert "/api/operator/note-task-draft" in api_paths
    assert "/api/tasks/{task_id}/run" in api_paths
    assert "/api/notes/fire-reminder" in api_paths
    assert "/api/calendar/sync" in api_paths
    assert any(row["starts_job"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["sends_notification"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["uses_network"] is True and row["requires_approval"] is True for row in plan["api_actions"])
    assert all(row["title"] for row in plan["work_ops_rows"])
    assert any(row["id"] == "handoff-summarize-today" and row["state"] == "ok" for row in plan["handoff_rows"])
    assert any(row["id"] == "handoff-note-task-draft" and row["requires_approval"] is True for row in plan["handoff_rows"])
    assert any(row["id"] == "handoff-calendar-sync-gate" and row["network_after_approval"] is True for row in plan["handoff_rows"])
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["creates_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["runs_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["fires_reminders"] is False for row in plan["handoff_rows"])
    assert all(row["creates_calendar_events"] is False for row in plan["handoff_rows"])
    assert all(row["syncs_calendars"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["title"] == "Task failed" and row["state"] == "error" for row in plan["alert_rows"])
    assert any(row["title"] == "Read-only aggregate" for row in plan["guard_rows"])
    assert "does not create tasks" in plan["approval"]["policy"]
    assert plan["paths"]["notes"] == "data/app.db:notes"


def test_operator_model_snapshot_summarizes_local_training(monkeypatch, tmp_path):
    from src import operator_models

    primary_manifest = tmp_path / "cleverly-primary-model.json"
    primary_manifest.write_text(json.dumps({"primary_model": "llama3.2:3b", "source": "test"}), encoding="utf-8")
    training_root = tmp_path / "training"

    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
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
    assert snapshot["summary"]["model_snapshot_alert_count"] == 0
    assert snapshot["summary"]["critical_model_snapshot_alert_count"] == 0
    assert snapshot["summary"]["executes"] is False
    assert snapshot["summary"]["uses_network"] is False

    primary_manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(operator_models, "load_features", lambda: {"external_model_endpoints": True})
    monkeypatch.setattr(
        operator_models,
        "_endpoint_rows",
        lambda: [
            {
                "id": "remote",
                "name": "Remote",
                "base_url": "https://models.example.test/v1",
                "is_enabled": True,
                "local": False,
                "scope": "external",
                "model_count": 1,
                "models": ["external-model"],
            }
        ],
    )
    monkeypatch.setattr(operator_models, "list_datasets", lambda: [])
    monkeypatch.setattr(operator_models, "list_artifacts", lambda: [])
    monkeypatch.setattr(
        operator_models,
        "finetune_status",
        lambda: {
            "dependencies": {"available": False, "missing": ["peft"]},
            "trainable_models": [],
            "ollama_models": [],
            "jobs": [{"job_id": "job-failed", "status": "failed"}],
        },
    )

    weak = operator_models.run_operator_model_snapshot()
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["readiness"]["state"] == "error"
    assert weak["summary"]["model_snapshot_alert_count"] == 8
    assert weak["summary"]["critical_model_snapshot_alert_count"] == 2
    assert "Primary model not selected" in weak_titles
    assert "External model endpoint enabled" in weak_titles
    assert "Fine-tuning job failed" in weak_titles
    assert all(row["executes"] is False for row in weak["alert_rows"])
    assert all(row["starts_training"] is False for row in weak["alert_rows"])
    assert all(row["downloads_models"] is False for row in weak["alert_rows"])
    assert all(row["runs_shell"] is False for row in weak["alert_rows"])


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
    assert plan["summary"]["model_alert_count"] == 3
    assert plan["summary"]["critical_model_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 3
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
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-model-preflight" for row in plan["entry_rows"])
    assert all(row["start_command_id"] == "open-model-routing-map" for row in plan["entry_rows"])
    assert all(row["approval_api"] == "/api/offline-control/models/primary" for row in plan["entry_rows"])
    assert all(row["download_api"] == "/api/model/download" for row in plan["entry_rows"])
    assert all(row["serve_api"] == "/api/model/serve" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["sets_primary_model"] is False for row in plan["entry_rows"])
    assert all(row["downloads_models"] is False for row in plan["entry_rows"])
    assert all(row["starts_serving"] is False for row in plan["entry_rows"])
    assert all(row["starts_training"] is False for row in plan["entry_rows"])
    assert all(row["starts_finetune"] is False for row in plan["entry_rows"])
    assert all(row["changes_settings"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    handoff_ids = {row["id"] for row in plan["handoff_rows"]}
    assert "model-primary-review-handoff" in handoff_ids
    assert "model-endpoint-routing-handoff" in handoff_ids
    assert "model-serving-download-handoff" in handoff_ids
    assert "model-training-handoff" in handoff_ids
    assert "model-context-retrieval-handoff" in handoff_ids
    assert "model-network-policy-handoff" in handoff_ids
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["sets_primary_model"] is False for row in plan["handoff_rows"])
    assert all(row["registers_endpoints"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_endpoints"] is False for row in plan["handoff_rows"])
    assert all(row["pulls_models"] is False for row in plan["handoff_rows"])
    assert all(row["downloads_models"] is False for row in plan["handoff_rows"])
    assert all(row["starts_serving"] is False for row in plan["handoff_rows"])
    assert all(row["benchmarks_models"] is False for row in plan["handoff_rows"])
    assert all(row["starts_training"] is False for row in plan["handoff_rows"])
    assert all(row["starts_finetune"] is False for row in plan["handoff_rows"])
    assert all(row["changes_settings"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert any(row["approval_api"] == "/api/training/train" and row["requires_approval"] is True for row in plan["handoff_rows"])
    assert any(row["id"] == "model-network-policy-handoff" and row["network_after_approval"] is True for row in plan["handoff_rows"])
    assert "/api/operator/model-ops-plan" in api_paths
    assert "/api/offline-control/models/primary" in api_paths
    assert "/api/model/download" in api_paths
    assert "/api/model/serve" in api_paths
    assert "/api/training/finetune/jobs" in api_paths
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    assert any(row["title"] == "Model operation boundary" for row in plan["operation_rows"])
    assert plan["alert_rows"][0]["title"] == "External model endpoints enabled"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert any(row["title"] == "Model operation approval required" for row in plan["alert_rows"])
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
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 6
    assert plan["summary"]["handoff_ready_count"] == 5
    assert plan["summary"]["training_alert_count"] == 1
    assert plan["summary"]["critical_training_alert_count"] == 0
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
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["route_rows"])
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["creates_dataset"] is False for row in plan["handoff_rows"])
    assert all(row["starts_training"] is False for row in plan["handoff_rows"])
    assert all(row["creates_model"] is False for row in plan["handoff_rows"])
    assert all(row["runs_finetune"] is False for row in plan["handoff_rows"])
    assert all(row["pulls_models"] is False for row in plan["handoff_rows"])
    assert all(row["changes_endpoints"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert all(row["executes"] is False for row in plan["api_actions"])
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-training-run-plan" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert any(row["requires_approval"] is True for row in plan["api_actions"])
    handoff_ids = {row["id"] for row in plan["handoff_rows"]}
    assert "training-dataset-review-handoff" in handoff_ids
    assert "training-approval-checkpoint-handoff" in handoff_ids
    assert "training-job-monitor-handoff" in handoff_ids
    assert "training-artifact-sampling-handoff" in handoff_ids
    assert "training-model-routing-handoff" in handoff_ids
    assert "training-activity-evidence-handoff" in handoff_ids
    assert any(row.get("approval_api") == "/api/training/train" and row["requires_approval"] is True for row in plan["handoff_rows"])
    assert plan["alert_rows"][0]["title"] == "Training run approval required"
    assert plan["alert_rows"][0]["requires_approval"] is True
    assert plan["approval"]["required"] is True
    assert "does not create datasets" in plan["approval"]["policy"]
    assert plan["paths"]["datasets"] == "data/training/datasets"

    blocked = run_operator_training_plan(
        "alice",
        model_snapshot={
            "primary": {"model": "", "configured": False},
            "endpoints": {"counts": {"local_enabled": 0}},
            "training": {"dataset_count": 0, "artifact_count": 0, "datasets": [], "artifacts": []},
            "finetune": {
                "dependencies": {"available": False, "missing": ["peft"]},
                "trainable_models": [],
                "trainable_count": 0,
                "jobs": [{"job_id": "job-2", "status": "failed"}],
                "job_counts": {"total": 1, "active": 0, "failed": 1, "complete": 0},
            },
            "readiness": {"state": "warn", "ready": False},
        },
    )
    blocked_titles = {row["title"] for row in blocked["alert_rows"]}
    assert blocked["summary"]["training_alert_count"] == 6
    assert blocked["summary"]["critical_training_alert_count"] == 2
    assert blocked["summary"]["handoff_count"] == 6
    assert blocked["summary"]["handoff_ready_count"] == 1
    assert "Dataset required" in blocked_titles
    assert "Failed training jobs need review" in blocked_titles
    assert "Fine-tune dependencies missing" in blocked_titles
    assert "Trainable base weights required" in blocked_titles
    assert "Primary local model not ready" in blocked_titles
    assert blocked["summary"]["entry_route_count"] == 5
    assert blocked["summary"]["entry_route_ready_count"] == 3


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
    assert snapshot["summary"]["read_only"] is True
    assert snapshot["summary"]["due_today_count"] == 1
    assert snapshot["summary"]["workflow_count"] == 9
    assert snapshot["summary"]["route_count"] == 5
    assert snapshot["summary"]["route_ready_count"] == 5
    assert snapshot["summary"]["entry_route_count"] == 5
    assert snapshot["summary"]["entry_route_ready_count"] == 5
    assert snapshot["summary"]["briefing_alert_count"] == 3
    assert snapshot["summary"]["critical_briefing_alert_count"] == 0
    assert snapshot["summary"]["writes_activity"] is False
    assert snapshot["summary"]["runs_shell"] is False
    assert snapshot["summary"]["uses_network"] is False
    titles = {row["title"] for row in snapshot["headline_rows"]}
    assert {"Tasks", "Calendar", "Automation routes", "Models and training"} <= titles
    overview_titles = {row["title"] for row in snapshot["overview_rows"]}
    action_titles = {row["title"] for row in snapshot["action_rows"]}
    risk_titles = {row["title"] for row in snapshot["risk_rows"]}
    route_titles = {row["title"] for row in snapshot["route_rows"]}
    alert_titles = {row["title"] for row in snapshot["alert_rows"]}
    entries = {row["entry"] for row in snapshot["entry_rows"]}
    source_paths = {row["path"] for row in snapshot["data_source_rows"]}
    api_paths = {row["path"] for row in snapshot["api_actions"]}
    assert "Today operating picture" in overview_titles
    assert "Review today's work" in action_titles
    assert "Read-only briefing" in risk_titles
    assert "Today has work to review" in alert_titles
    assert "No local memory visible" in alert_titles
    assert "No local notes visible" in alert_titles
    assert "Typed command route" in route_titles
    assert "Voice command route" in route_titles
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert snapshot["entry_rows"] == snapshot["route_rows"]
    assert all(row["command_id"] == "summarize-today" and row["executes"] is False for row in snapshot["route_rows"])
    assert all(row["routes_commands"] is False for row in snapshot["entry_rows"])
    assert all(row["executes_commands"] is False for row in snapshot["entry_rows"])
    assert all(row["starts_workflows"] is False for row in snapshot["entry_rows"])
    assert all(row["starts_jobs"] is False for row in snapshot["entry_rows"])
    assert all(row["runs_shell"] is False for row in snapshot["entry_rows"])
    assert all(row["writes_activity"] is False for row in snapshot["entry_rows"])
    assert all(row["uses_network"] is False for row in snapshot["entry_rows"])
    assert all(row["executes"] is False for row in snapshot["alert_rows"])
    assert all(row["routes_commands"] is False for row in snapshot["alert_rows"])
    assert all(row["executes_commands"] is False for row in snapshot["alert_rows"])
    assert all(row["starts_workflows"] is False for row in snapshot["alert_rows"])
    assert all(row["starts_jobs"] is False for row in snapshot["alert_rows"])
    assert all(row["runs_shell"] is False for row in snapshot["alert_rows"])
    assert all(row["writes_activity"] is False for row in snapshot["alert_rows"])
    assert all(row["uses_network"] is False for row in snapshot["alert_rows"])
    assert "data/operator_activity.json" in source_paths
    assert "/api/operator/briefing" in api_paths
    assert all(row["executes"] is False for row in snapshot["api_actions"])
    assert snapshot["approval"]["required"] is False
    assert "does not write activity" in snapshot["approval"]["policy"]


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


def test_operator_command_layer_plan_audits_catalog_routes_and_entry_points_without_execution():
    from src.operator_command_layer import run_operator_command_layer_plan

    commands = [
        {
            "id": "summarize-today",
            "title": "Today Briefing",
            "category": "Briefing",
            "trust": "local",
            "priority": 50,
            "keywords": ["summarize today", "cleverly summarize today"],
        },
        {
            "id": "request-container-fix",
            "title": "Ask To Fix Container Health",
            "category": "Safety",
            "trust": "approval",
            "priority": 48,
            "keywords": ["fix anything unhealthy", "restart containers"],
        },
    ]
    workflows = [
        {
            "id": "summarize-today-target",
            "phrase": "Cleverly, summarize today.",
            "title": "Today Briefing",
            "area": "Briefing",
            "commandId": "summarize-today",
            "expectedRouteId": "summarize-today",
            "state": "ok",
        },
        {
            "id": "container-health-target",
            "phrase": "Check the containers and fix anything unhealthy.",
            "title": "Container Repair",
            "area": "Services",
            "commandId": "request-container-fix",
            "approvalId": "request-container-fix",
            "expectedRouteId": "request-container-fix",
            "state": "ok",
        },
    ]
    plan = run_operator_command_layer_plan(
        "alice",
        commands=commands,
        workflows=workflows,
        loops=[{"id": "daily-briefing"}],
        policy={"local": "auto", "approval": "ask", "network": "ask", "danger": "ask"},
        configured={"commands": True, "workflows": True, "policy": True},
    )

    assert plan["mode"] == "read-only-command-layer-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["command_count"] == 2
    assert plan["summary"]["workflow_count"] == 2
    assert plan["summary"]["loop_count"] == 1
    assert plan["summary"]["route_match_count"] == 2
    assert plan["summary"]["route_match_ready_count"] == 2
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 7
    assert plan["summary"]["ask_first_count"] == 1
    assert plan["summary"]["routes_commands"] is False
    assert plan["summary"]["executes_commands"] is False
    assert plan["summary"]["starts_workflows"] is False
    assert plan["summary"]["writes_activity"] is False
    assert plan["summary"]["changes_policy"] is False
    assert plan["summary"]["runs_shell"] is False
    assert plan["summary"]["uses_network"] is False
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert any(row["command_id"] == "request-container-fix" and row["route_ready"] for row in plan["route_rows"])
    assert all(row["executes_commands"] is False for row in plan["entry_rows"])
    assert all(row["routes_commands"] is False for row in plan["route_rows"])
    assert all(row["writes_activity"] is False for row in plan["catalog_rows"])
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "command-layer-text-route-handoff",
        "command-layer-palette-catalog-handoff",
        "command-layer-voice-route-handoff",
        "command-layer-policy-handoff",
        "command-layer-approval-queue-handoff",
        "command-layer-workflow-start-handoff",
        "command-layer-activity-ledger-handoff",
        "command-layer-network-policy-handoff",
    }
    assert handoffs["command-layer-text-route-handoff"]["target_api"] == "/api/operator/route"
    assert handoffs["command-layer-workflow-start-handoff"]["target_api"] == "/api/operator/workflows"
    assert handoffs["command-layer-activity-ledger-handoff"]["target_api"] == "/api/operator/activity"
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["approves_commands"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["changes_policy"] is False for row in plan["handoff_rows"])
    assert all(row["publishes_catalog"] is False for row in plan["handoff_rows"])
    assert all(row["reads_transcript"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["command-layer-text-route-handoff"]["gated_operation"]["routes_commands"] is True
    assert handoffs["command-layer-palette-catalog-handoff"]["gated_operation"]["publishes_catalog"] is True
    assert handoffs["command-layer-voice-route-handoff"]["gated_operation"]["reads_transcript"] is True
    assert handoffs["command-layer-policy-handoff"]["gated_operation"]["changes_policy"] is True
    assert handoffs["command-layer-approval-queue-handoff"]["gated_operation"]["approves_commands"] is True
    assert handoffs["command-layer-workflow-start-handoff"]["gated_operation"]["starts_workflows"] is True
    assert handoffs["command-layer-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
    assert "/api/operator/command-layer-plan" in {row["path"] for row in plan["api_actions"]}
    assert plan["approval"]["required"] is False
    assert "does not route a live request" in plan["approval"]["policy"]


def test_operator_command_text_uses_backend_route_preflight_before_local_fallback():
    from pathlib import Path

    commands_js = Path("static/js/operatorCommands.js").read_text(encoding="utf-8")
    center_js = Path("static/js/commandCenter.js").read_text(encoding="utf-8")
    palette_js = Path("static/js/commandPalette.js").read_text(encoding="utf-8")
    code_workspace_js = Path("static/js/codeWorkspace.js").read_text(encoding="utf-8")
    voice_js = Path("static/js/voiceCommand.js").read_text(encoding="utf-8")
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    index_html = Path("static/index.html").read_text(encoding="utf-8")
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
    assert "function activityActionMetadata(record = {}, preview = null)" in commands_js
    assert "retry_command_id: retryable ? commandId : ''" in commands_js
    assert "retry_requires_approval: elevated || record.requires_approval === true || record.approval_required === true" in commands_js
    assert "clear_requires_approval: true" in commands_js
    assert "Recovery Map before changing local state" in commands_js
    assert "function recordNoteTaskDraftActivity(note, draft = {})" in commands_js
    assert "source: 'note-task-draft-fallback'" in commands_js
    assert "privacy_note: 'Full note text and draft prompt are not stored in the activity ledger.'" in commands_js
    assert "recordNoteTaskDraftActivity(null, draft);" in commands_js
    assert "recordNoteTaskDraftActivity(note, draft);" in commands_js
    assert "function backendBriefingRows" in center_js
    assert "operatorBriefing.overview_rows" in center_js
    assert "operatorBriefing.action_rows" in center_js
    assert "operatorBriefing.alert_rows" in center_js
    assert "Backend suggested actions" in center_js
    assert "Backend briefing alerts" in center_js
    assert "No backend briefing alerts visible" in center_js
    assert "briefing_alert_count" in center_js
    assert "Backend Guardrails:" in center_js
    assert "function operatorRepairApprovalPacketRows" in center_js
    assert "approval_packet" in center_js
    assert "Approval packet" in center_js
    assert "candidate_host_commands" in center_js
    assert "function backupVerificationPacketRows" in center_js
    assert "verification_packet" in center_js
    assert "Verification packet" in center_js
    assert "expected_artifacts" in center_js
    assert "function recordLocalDocumentSearchActivity" in center_js
    assert "privacy_note: 'Result snippets are not stored in the activity ledger.'" in center_js
    assert "recordLocalDocumentSearchActivity(cleanQuery, data);" in center_js
    assert "recordLocalDocumentSearchActivity(cleanQuery, {}, error);" in center_js
    assert "No document search request route proof visible" in center_js
    assert "Search my local documents for this" in center_js
    assert "requiresQuery: row.requires_query === true" in center_js
    assert "function recordNoteTaskDraftActivity(note, draft = {})" in center_js
    assert "privacy_note: 'Full note text and draft prompt are not stored in the activity ledger.'" in center_js
    assert "command_id: 'draft-task-from-note'" in center_js
    assert "No note task request route proof visible" in center_js
    assert "Create a task from this note" in center_js
    assert "requiresReview: row.requires_review === true" in center_js
    assert "Note task alerts" in center_js
    assert "No backend note task alerts visible" in center_js
    assert "requiresApproval: row.requires_approval === true" in center_js
    assert "recordNoteTaskDraftActivity(note, draft);" in center_js
    assert "const alertRows = asArray(workdayPlan.alert_rows)" in center_js
    assert "const workdayEntryRows = asArray(workdayPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Local alert queue" in center_js
    assert "Workday request route proof" in center_js
    assert "No workday request route proof visible" in center_js
    assert "plural(Number(workdaySummary.alert_count || 0), 'alert')" in center_js
    assert "'Alerts:'" in center_js
    assert "const fileAlertRows = asArray(fileOpsPlan.alert_rows)" in center_js
    assert "const fileOpsEntryRows = asArray(fileOpsPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "operatorDataPlan: '/api/operator/data-plan'" in center_js
    assert "operatorRecoveryPlan: '/api/operator/recovery-plan'" in center_js
    assert "operatorCommandLayerPlan: '/api/operator/command-layer-plan'" in center_js
    assert "operatorAutomationPlan: '/api/operator/automation-plan'" in center_js
    assert "operatorAiRuntimePlan: '/api/operator/ai-runtime-plan'" in center_js
    assert "operatorWorkspacePlan: '/api/operator/workspace-plan'" in center_js
    assert "operatorDockerRuntimePlan: '/api/operator/docker-runtime-plan'" in center_js
    assert "operatorWorkOpsPlan: '/api/operator/work-ops-plan'" in center_js
    assert "const commandLayerPlan = readData(snapshot || {}, 'operatorCommandLayerPlan')" in center_js
    assert "const automationPlan = readData(source, 'operatorAutomationPlan')" in center_js
    assert "const aiRuntimePlan = readData(source, 'operatorAiRuntimePlan')" in center_js
    assert "const workspacePlan = readData(source, 'operatorWorkspacePlan')" in center_js
    assert "Backend command-layer entry points" in center_js
    assert "Backend command-layer route matrix" in center_js
    assert "Command-layer alerts" in center_js
    assert "Command-layer API gates" in center_js
    assert "No backend command-layer entry proof visible" in center_js
    assert "const commandLayerHandoffRows = asArray(commandLayerPlan.handoff_rows)" in center_js
    assert "Command-layer handoffs" in center_js
    assert "No command-layer handoffs visible" in center_js
    assert "command-layer handoff evidence" in center_js
    assert "Layer Handoffs" in center_js
    assert "row.routes_commands === true" in center_js
    assert "row.starts_workflows === true" in center_js
    assert "critical_command_layer_alert_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "Backend automation operations" in center_js
    assert "Backend automation request routes" in center_js
    assert "No backend automation operations plan visible" in center_js
    assert "critical_automation_alert_count" in center_js
    assert "Local AI runtime" in center_js
    assert "AI runtime request routes" in center_js
    assert "No backend local AI runtime plan visible" in center_js
    assert "critical_ai_runtime_alert_count" in center_js
    assert "const aiRuntimeHandoffRows = asArray(aiRuntimePlan.handoff_rows)" in center_js
    assert "AI runtime handoffs" in center_js
    assert "No AI runtime handoffs visible" in center_js
    assert "local AI runtime handoff evidence" in center_js
    assert "const modelHandoffRows = asArray(modelOpsPlan.handoff_rows)" in center_js
    assert "Model operation handoffs" in center_js
    assert "No model operation handoffs visible" in center_js
    assert "model operation handoff evidence" in center_js
    assert "Backend workspace operations" in center_js
    assert "Workspace alert queue" in center_js
    assert "Workspace request routes" in center_js
    assert "Workspace API gates" in center_js
    assert "No backend workspace operations plan visible" in center_js
    assert "No workspace request route proof visible" in center_js
    assert "const workspaceHandoffRows = workspacePlanOk ? asArray(workspacePlan.handoff_rows)" in center_js
    assert "Workspace handoffs" in center_js
    assert "No workspace handoffs visible" in center_js
    assert "local workspace handoff evidence" in center_js
    assert "Workspace Handoffs" in center_js
    assert "row.starts_research === true" in center_js
    assert "workspace_alert_count" in center_js
    assert "critical_workspace_alert_count" in center_js
    assert "Docker runtime operations" in center_js
    assert "Docker runtime alerts" in center_js
    assert "Docker runtime request routes" in center_js
    assert "Docker deployment gates" in center_js
    assert "No backend Docker runtime plan visible" in center_js
    assert "No Docker runtime request route proof visible" in center_js
    assert "No Docker deployment gates visible" in center_js
    assert "builds_images" in center_js
    assert "recreates_services" in center_js
    assert "docker_runtime_alert_count" in center_js
    assert "critical_docker_runtime_alert_count" in center_js
    assert "Backend work operations" in center_js
    assert "Work operations alert queue" in center_js
    assert "Work operations request route proof" in center_js
    assert "Work operations handoffs" in center_js
    assert "Work operations guard rails" in center_js
    assert "No backend work operations rows visible" in center_js
    assert "No work operations request route proof visible" in center_js
    assert "No work operations handoffs visible" in center_js
    assert "workOpsHandoffRows" in center_js
    assert "handoff_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "work_ops_alert_count" in center_js
    assert "critical_work_ops_alert_count" in center_js
    assert "Backend identity proof" in center_js
    assert "No backend identity rows visible" in center_js
    assert "backendIdentity" in center_js
    assert "identity_count" in center_js
    assert "identity_ready_count" in center_js
    assert "const dataPlan = readData(source, 'operatorDataPlan')" in center_js
    assert "const dataEntryRows = asArray(dataPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "const dataHandoffRows = asArray(dataPlan.handoff_rows)" in center_js
    assert "Backend local data map plan" in center_js
    assert "Local data alert queue" in center_js
    assert "Local Data Map request route proof" in center_js
    assert "Local data handoffs" in center_js
    assert "No Local Data Map request route proof visible" in center_js
    assert "No local data handoffs visible" in center_js
    assert "local data handoff evidence" in center_js
    assert "data_alert_count" in center_js
    assert "critical_data_alert_count" in center_js
    assert "handoff_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "Data Routes" in center_js
    assert "const recoveryPlan = readData(source, 'operatorRecoveryPlan')" in center_js
    assert "Backend recovery plan" in center_js
    assert "Recovery alert queue" in center_js
    assert "Recovery request route proof" in center_js
    assert "No Recovery request route proof visible" in center_js
    assert "recovery_alert_count" in center_js
    assert "critical_recovery_alert_count" in center_js
    assert "File alert queue" in center_js
    assert "File operation request routes" in center_js
    assert "No file operation request route proof visible" in center_js
    assert "const fileOpsHandoffRows = asArray(fileOpsPlan.handoff_rows)" in center_js
    assert "File operation handoffs" in center_js
    assert "No file operation handoffs visible" in center_js
    assert "file operation handoff evidence" in center_js
    assert "critical_file_alert_count" in center_js
    assert "file_alert_count" in center_js
    assert "File Routes" in center_js
    assert "File Handoffs" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const backendAlertRows = asArray(backendPlan.alert_rows)" in center_js
    assert "Training alert queue" in center_js
    assert "training_alert_count" in center_js
    assert "critical_training_alert_count" in center_js
    assert "const handoffRows = backendOk ? asArray(backendPlan.handoff_rows)" in center_js
    assert "Training handoffs" in center_js
    assert "No backend training handoffs visible" in center_js
    assert "handoff_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "const automationPlanAlertRows = asArray(automationPlan.alert_rows)" in center_js
    assert "const autonomyAlertRows = asArray(autonomyPlan.alert_rows)" in center_js
    assert "const autonomyEntryRows = asArray(autonomyPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "const autonomyHandoffRows = asArray(autonomyPlan.handoff_rows)" in center_js
    assert "Automation alert queue" in center_js
    assert "Automation request route proof" in center_js
    assert "No automation request route proof visible" in center_js
    assert "Autonomy handoffs" in center_js
    assert "No autonomy handoffs visible" in center_js
    assert "autonomy handoff evidence" in center_js
    assert "Autonomy Handoffs" in center_js
    assert "Autonomy Routes" in center_js
    assert "Autonomy decision modes" in center_js
    assert "No autonomy decision mode proof visible" in center_js
    assert "decision_mode_count" in center_js
    assert "decision_mode_ready_count" in center_js
    assert "const backendPermissionCheckpointRows = backendOk ? asArray(backendPlan, ['permission_checkpoint_rows', 'permissionCheckpointRows'])" in center_js
    assert "const backendHandoffRows = backendOk ? asArray(backendPlan.handoff_rows)" in center_js
    assert "Permission checkpoints" in center_js
    assert "No permission checkpoint proof visible" in center_js
    assert "permission_checkpoint_count" in center_js
    assert "permission_checkpoint_ready_count" in center_js
    assert "gated.starts_workflows === true" in center_js
    assert "gated.changes_policy === true" in center_js
    assert "automation_alert_count" in center_js
    assert "critical_automation_alert_count" in center_js
    assert "const approvalAlertRows = asArray(approvalPlan.alert_rows)" in center_js
    assert "Approval alert queue" in center_js
    assert "approval_alert_count" in center_js
    assert "critical_approval_alert_count" in center_js
    assert "const approvalEntryRows = asArray(approvalPlan.entry_rows)" in center_js
    assert "Approval request route proof" in center_js
    assert "No approval request route proof visible" in center_js
    assert "Approval decision checkpoints" in center_js
    assert "No approval decision checkpoints visible" in center_js
    assert "approvalCheckpointRows" in center_js
    assert "decision_checkpoint_count" in center_js
    assert "decision_checkpoint_ready_count" in center_js
    assert "const approvalHandoffRows = asArray(approvalPlan.handoff_rows)" in center_js
    assert "Approval handoffs" in center_js
    assert "No approval handoffs visible" in center_js
    assert "approval posture handoff evidence" in center_js
    assert "Approval Handoffs" in center_js
    assert "row.network_after_approval === true" in center_js
    assert "open-trust-controls approval route evidence" in center_js
    assert "row.approves_commands === true" in center_js
    assert "Approval Routes" in center_js
    assert "pending_approval_count" in center_js
    assert "Backend approval queue" in center_js
    assert "const memoryAlertRows = asArray(memoryPlan.alert_rows)" in center_js
    assert "const memoryEntryRows = asArray(memoryPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Memory alert queue" in center_js
    assert "Memory request routes" in center_js
    assert "Backend recent memory" in center_js
    assert "No backend recent memory rows visible" in center_js
    assert "Backend model choices" in center_js
    assert "No backend model choice rows visible" in center_js
    assert "backendModelChoiceRows" in center_js
    assert "model_choice_count" in center_js
    assert "default_model_preference_ready" in center_js
    assert "backendRecentMemoryRows" in center_js
    assert "recent_memory_count" in center_js
    assert "No Memory request route proof visible" in center_js
    assert "Backend memory bucket coverage" in center_js
    assert "No backend memory bucket proof visible" in center_js
    assert "backendBucketRows" in center_js
    assert "memory_alert_count" in center_js
    assert "critical_memory_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "Model Choices" in center_js
    assert "Recurring Tasks" in center_js
    assert "model_choices" in center_js
    assert "recurring_tasks" in center_js
    assert "const documentAlertRows = asArray(documentSearchPlan.alert_rows)" in center_js
    assert "Library alert queue" in center_js
    assert "const documentHandoffRows = asArray(documentSearchPlan.handoff_rows)" in center_js
    assert "Document search handoffs" in center_js
    assert "No document search handoffs visible" in center_js
    assert "document search handoff evidence" in center_js
    assert "Doc Handoffs" in center_js
    assert "document_alert_count" in center_js
    assert "critical_document_alert_count" in center_js
    assert "const runtimeAlertRows = asArray(runtimePlan.alert_rows)" in center_js
    assert "const runtimeEntryRows = asArray(runtimePlan, ['entry_rows', 'entryRows'])" in center_js
    assert "const sealedRuntimeRows = asArray(runtimePlan, ['sealed_runtime_rows', 'sealedRuntimeRows'])" in center_js
    assert "Runtime alert queue" in center_js
    assert "Runtime request routes" in center_js
    assert "Sealed runtime matrix" in center_js
    assert "No Runtime request route proof visible" in center_js
    assert "No sealed runtime matrix visible" in center_js
    assert "runtime_alert_count" in center_js
    assert "critical_runtime_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "sealed_runtime_count" in center_js
    assert "sealed_volume_count" in center_js
    assert "support_service_count" in center_js
    assert "const repairAlertRows = asArray(backendRepairPlan.alert_rows)" in center_js
    assert "Repair alert queue" in center_js
    assert "repair_alert_count" in center_js
    assert "critical_repair_alert_count" in center_js
    assert "const repairEntryRows = asArray(backendRepairPlan.entry_rows)" in center_js
    assert "No repair request route proof visible" in center_js
    assert "open-container-repair-plan request route evidence" in center_js
    assert "row.repairs_services === true" in center_js
    assert "const repairHandoffRows = asArray(backendRepairPlan.handoff_rows)" in center_js
    assert "Repair handoffs" in center_js
    assert "No repair handoffs visible" in center_js
    assert "container repair handoff evidence" in center_js
    assert "Repair Handoffs" in center_js
    assert "row.runs_docker === true" in center_js
    assert "row.pulls_images === true" in center_js
    assert "const backupAlertRows = asArray(backendPlan.alert_rows)" in center_js
    assert "Backup alert queue" in center_js
    assert "const backupHandoffRows = backendOk ? asArray(backendPlan.handoff_rows)" in center_js
    assert "Backup handoffs" in center_js
    assert "No backup handoffs visible" in center_js
    assert "backup handoff evidence" in center_js
    assert "Backup Handoffs" in center_js
    assert "row.creates_backup === true" in center_js
    assert "row.verifies_backup === true" in center_js
    assert "backup_alert_count" in center_js
    assert "critical_backup_alert_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "backendPlan.entry_rows" in center_js
    assert "No backup request route proof visible" in center_js
    assert "Prepare a backup and verify it" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const changeAlertRows = asArray(backendChange.alert_rows)" in center_js
    assert "Change alert queue" in center_js
    assert "explain-changes-since-yesterday" in center_js
    assert "No Change Brief request route proof visible" in center_js
    assert "Explain what changed since yesterday" in center_js
    assert "explain-changes-since-yesterday request route evidence" in center_js
    assert "const activityAlertRows = backendOk ? asArray(backendPlan.alert_rows)" in center_js
    assert "const activityEntryRows = backendOk ? asArray(backendPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Activity alert queue" in center_js
    assert "Activity request routes" in center_js
    assert "No Activity request route proof visible" in center_js
    assert "const activityHandoffRows = backendOk ? asArray(backendPlan.handoff_rows)" in center_js
    assert "Activity handoffs" in center_js
    assert "No Activity handoffs visible" in center_js
    assert "activity handoff evidence" in center_js
    assert "handoff_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "activity_alert_count" in center_js
    assert "critical_activity_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const codeAlertRows = backendOk ? asArray(backendPlan.alert_rows)" in center_js
    assert "const codeEntryRows = backendOk ? asArray(backendPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Code alert queue" in center_js
    assert "Code request routes" in center_js
    assert "const codeHandoffRows = backendOk ? asArray(backendPlan.handoff_rows)" in center_js
    assert "Code test handoffs" in center_js
    assert "No code test handoffs visible" in center_js
    assert "code test handoff evidence" in center_js
    assert "Code Handoffs" in center_js
    assert "row.runs_shell === true" in center_js
    assert "code_alert_count" in center_js
    assert "critical_code_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const buildAlertRows = backendOk ? asArray(backendPlan.alert_rows)" in center_js
    assert "const buildEntryRows = backendOk ? asArray(backendPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Build alert queue" in center_js
    assert "Build Watch request routes" in center_js
    assert "No Build Watch request route proof visible" in center_js
    assert "const buildHandoffRows = backendOk ? asArray(backendPlan.handoff_rows)" in center_js
    assert "Build Watch handoffs" in center_js
    assert "No Build Watch handoffs visible" in center_js
    assert "build-watch handoff evidence" in center_js
    assert "Build Handoffs" in center_js
    assert "row.starts_loop === true" in center_js
    assert "row.runs_build === true" in center_js
    assert "build_alert_count" in center_js
    assert "critical_build_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "const safetyAlertRows = safetyOk" in center_js
    assert "const safetyEntryRows = safetyOk ? asArray(safetyPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Safety alert queue" in center_js
    assert "Safety request route proof" in center_js
    assert "No safety request route proof visible" in center_js
    assert "const safetyHandoffRows = safetyOk ? asArray(safetyPlan.handoff_rows)" in center_js
    assert "Safety handoffs" in center_js
    assert "No safety handoffs visible" in center_js
    assert "safety boundary handoff evidence" in center_js
    assert "Safety Handoffs" in center_js
    assert "row.network_after_approval === true" in center_js
    assert "Safety Routes" in center_js
    assert "safety_alert_count" in center_js
    assert "critical_safety_alert_count" in center_js
    assert "const consoleAlertRows = asArray(backendPlan.alert_rows)" in center_js
    assert "const backendAlertFeedRows = asArray(backendPlan.alert_feed_rows)" in center_js
    assert "const backendEntryRows = asArray(backendPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "const consoleHandoffRows = asArray(backendPlan.handoff_rows)" in center_js
    assert "Console alert queue" in center_js
    assert "Console alert feeds" in center_js
    assert "Console handoffs" in center_js
    assert "No console alert feed proof visible" in center_js
    assert "No console handoffs visible" in center_js
    assert "console handoff evidence" in center_js
    assert "Console Handoffs" in center_js
    assert "gated.routes_commands === true" in center_js
    assert "gated.writes_activity === true" in center_js
    assert "gated.creates_backup === true" in center_js
    assert "gated.uses_network === true" in center_js
    assert "Console request route proof" in center_js
    assert "No console request route proof visible" in center_js
    assert "Entry Routes" in center_js
    assert "alert_feed_count" in center_js
    assert "alert_feed_ready_count" in center_js
    assert "console_alert_count" in center_js
    assert "critical_console_alert_count" in center_js
    assert "goal_alert_count" in center_js
    assert "critical_goal_alert_count" in center_js
    assert "Backend goal alerts" in center_js
    assert "Goal request route proof" in center_js
    assert "No backend goal request route proof visible" in center_js
    assert "function goalPlanHandoffRows" in center_js
    assert "asArray(backendPlan.handoff_rows)" in center_js
    assert "Goal handoffs" in center_js
    assert "No goal handoffs visible" in center_js
    assert "goal handoff evidence" in center_js
    assert "gated.starts_workflows === true" in center_js
    assert "gated.runs_shell === true" in center_js
    assert "gated.runs_docker === true" in center_js
    assert "Backend capability coverage" in center_js
    assert "No backend capability rows visible" in center_js
    assert "capability_rows" in center_js
    assert "capability_count" in center_js
    assert "capability_ready_count" in center_js
    assert "const toolchainAlertRows = asArray(backendPlan.alert_rows)" in center_js
    assert "const toolchainEntryRows = asArray(backendPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Toolchain request route proof" in center_js
    assert "No toolchain request route proof visible" in center_js
    assert "Toolchain inventory" in center_js
    assert "const toolchainHandoffRows = asArray(toolchainPlan.handoff_rows)" in center_js
    assert "Toolchain handoffs" in center_js
    assert "No toolchain handoffs visible" in center_js
    assert "toolchain handoff evidence" in center_js
    assert "Tool Handoffs" in center_js
    assert "gated.starts_models === true" in center_js
    assert "gated.starts_training === true" in center_js
    assert "gated.runs_shell === true" in center_js
    assert "gated.runs_docker === true" in center_js
    assert "Toolchain safety boundary" in center_js
    assert "Toolchain" in center_js
    assert "toolchain_alert_count" in center_js
    assert "critical_toolchain_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const toolAccessAlertRows = asArray(toolAccessPlan.alert_rows)" in center_js
    assert "const toolAccessEntryRows = asArray(toolAccessPlan, ['entry_rows', 'entryRows'])" in center_js
    assert "Tool access alerts" in center_js
    assert "Tool access request routes" in center_js
    assert "Tool access inventory" in center_js
    assert "Tool access safety boundary" in center_js
    assert "Tool access API gates" in center_js
    assert "tool_access_alert_count" in center_js
    assert "critical_tool_access_alert_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const backendAlertSources = [" in center_js
    assert "operatorWorkOpsPlan" in center_js
    assert "operatorWorkdayPlan" in center_js
    assert "operatorFileOpsPlan" in center_js
    assert "operatorTrainingPlan" in center_js
    assert "operatorAutonomyPlan" in center_js
    assert "operatorApprovalPlan" in center_js
    assert "operatorLoopsPlan" in center_js
    assert "operatorMemoryPlan" in center_js
    assert "operatorServicesPlan" in center_js
    assert "operatorDockerRuntimePlan" in center_js
    assert "operatorCredentialsPlan" in center_js
    assert "operatorDocumentSearchPlan" in center_js
    assert "operatorResearchPlan" in center_js
    assert "operatorGalleryPlan" in center_js
    assert "operatorRuntimePlan" in center_js
    assert "operatorRepairPlan" in center_js
    assert "operatorModelOpsPlan" in center_js
    assert "operatorBackupPlan" in center_js
    assert "operatorChangeBrief" in center_js
    assert "operatorActivityPlan" in center_js
    assert "operatorCodeTestPlan" in center_js
    assert "operatorBuildWatchPlan" in center_js
    assert "operatorWorkspacePlan" in center_js
    assert "operatorSafetyPlan" in center_js
    assert "operatorConsolePlan" in center_js
    assert "operatorToolchainPlan" in center_js
    assert "operatorToolAccessPlan" in center_js
    assert "operatorExperiencePlan" in center_js
    assert "operatorNotesPlan" in center_js
    assert "operatorCalendarPlan" in center_js
    assert "operatorTasksPlan" in center_js
    assert "experience_alert_count" in center_js
    assert "critical_experience_alert_count" in center_js
    assert "const experienceRouteMatchRows = asArray(experiencePlan, ['route_match_rows', 'routeMatchRows'])" in center_js
    assert "Target route matches" in center_js
    assert "No target route match evidence visible" in center_js
    assert "route_match_count" in center_js
    assert "route_match_ready_count" in center_js
    assert "const experienceHandoffRows = asArray(experiencePlan.handoff_rows)" in center_js
    assert "Target handoffs" in center_js
    assert "No target handoffs visible" in center_js
    assert "target-experience handoff evidence" in center_js
    assert "Target Handoffs" in center_js
    assert "gated.starts_training === true" in center_js
    assert "gated.runs_docker === true" in center_js
    assert "gated.creates_backup === true" in center_js
    assert "const experienceEntryRows = asArray(experiencePlan, ['entry_rows', 'entryRows'])" in center_js
    assert "experienceEntryRows.length" in center_js
    assert "backend target proof; matches" in center_js
    assert "Global command palette" in center_js
    assert "Voice command route" in center_js
    assert "const entryRows = voicePlanOk ? asArray(voicePlan.entry_rows)" in center_js
    assert "const alertRows = voicePlanOk ? asArray(voicePlan.alert_rows)" in center_js
    assert "const handoffRows = voicePlanOk ? asArray(voicePlan.handoff_rows)" in center_js
    assert "Voice alert queue" in center_js
    assert "No backend voice alerts visible" in center_js
    assert "Voice transcript handoffs" in center_js
    assert "No voice transcript handoffs visible" in center_js
    assert "voice_alert_count" in center_js
    assert "critical_voice_alert_count" in center_js
    assert "handoff_count" in center_js
    assert "handoff_ready_count" in center_js
    assert "No voice request route proof visible" in center_js
    assert "requiresPermission: row.requires_permission === true" in center_js
    assert "entry_route_ready_count" in center_js
    assert "const notesAlertRows = asArray(notesPlan.alert_rows)" in center_js
    assert "Notes alert queue" in center_js
    assert "notes_alert_count" in center_js
    assert "critical_notes_alert_count" in center_js
    assert "const notesEntryRows = asArray(notesPlan.entry_rows)" in center_js
    assert "Notes request route proof" in center_js
    assert "No notes request route proof visible" in center_js
    assert "open-work-preflight notes route evidence" in center_js
    assert "row.fires_reminders === true" in center_js
    assert "const notesHandoffRows = asArray(notesPlan.handoff_rows)" in center_js
    assert "Notes handoffs" in center_js
    assert "No notes handoffs visible" in center_js
    assert "notes handoff evidence" in center_js
    assert "Note Handoffs" in center_js
    assert "const calendarAlertRows = asArray(calendarPlan.alert_rows)" in center_js
    assert "Calendar alert queue" in center_js
    assert "calendar_alert_count" in center_js
    assert "critical_calendar_alert_count" in center_js
    assert "const calendarEntryRows = asArray(calendarPlan.entry_rows)" in center_js
    assert "Calendar request route proof" in center_js
    assert "No calendar request route proof visible" in center_js
    assert "const calendarHandoffRows = asArray(calendarPlan.handoff_rows)" in center_js
    assert "Calendar handoffs" in center_js
    assert "No calendar handoffs visible" in center_js
    assert "calendar handoff evidence" in center_js
    assert "Cal Handoffs" in center_js
    assert "const taskAlertRows = asArray(tasksPlan.alert_rows)" in center_js
    assert "Tasks alert queue" in center_js
    assert "task_alert_count" in center_js
    assert "critical_task_alert_count" in center_js
    assert "const taskEntryRows = asArray(tasksPlan.entry_rows)" in center_js
    assert "Task request route proof" in center_js
    assert "No task request route proof visible" in center_js
    assert "const taskHandoffRows = asArray(tasksPlan.handoff_rows)" in center_js
    assert "Task handoffs" in center_js
    assert "No task handoffs visible" in center_js
    assert "task handoff evidence" in center_js
    assert "Task Handoffs" in center_js
    assert "const loopAlertRows = asArray(loopsPlan.alert_rows)" in center_js
    assert "Loop alert queue" in center_js
    assert "loop_alert_count" in center_js
    assert "critical_loop_alert_count" in center_js
    assert "const loopEntryRows = asArray(loopsPlan.entry_rows)" in center_js
    assert "Agent Loop request route proof" in center_js
    assert "No Agent Loop request route proof visible" in center_js
    assert "open-automation-preflight Agent Loop route evidence" in center_js
    assert "row.starts_loops === true" in center_js
    assert "Loop Routes" in center_js
    assert "const serviceAlertRows = asArray(servicesPlan.alert_rows)" in center_js
    assert "const serviceSnapshotAlertRows = asArray(serviceSnapshot.alert_rows)" in center_js
    assert "const checksAlertRows = [" in center_js
    assert "Service alert queue" in center_js
    assert "Service snapshot alerts" in center_js
    assert "Operator check alerts" in center_js
    assert "No backend service snapshot alerts visible" in center_js
    assert "No operator check alerts visible" in center_js
    assert "service_alert_count" in center_js
    assert "critical_service_alert_count" in center_js
    assert "service_snapshot_alert_count" in center_js
    assert "critical_service_snapshot_alert_count" in center_js
    assert "const serviceEntryRows = asArray(servicesPlan.entry_rows)" in center_js
    assert "Service request route proof" in center_js
    assert "No service request route proof visible" in center_js
    assert "open-local-services-map service route evidence" in center_js
    assert "row.runs_docker === true" in center_js
    assert "const serviceHandoffRows = asArray(servicesPlan.handoff_rows)" in center_js
    assert "Service handoffs" in center_js
    assert "No service handoffs visible" in center_js
    assert "local service handoff evidence" in center_js
    assert "Service Handoffs" in center_js
    assert "row.starts_services === true" in center_js
    assert "const dockerRuntimeHandoffRows = dockerRuntimeOk ? asArray(dockerRuntimePlan.handoff_rows)" in center_js
    assert "Docker runtime handoffs" in center_js
    assert "No Docker runtime handoffs visible" in center_js
    assert "docker runtime handoff evidence" in center_js
    assert "Docker Handoffs" in center_js
    assert "container_status_rows" in center_js
    assert "Container status evidence" in center_js
    assert "container_status_visible_count" in center_js
    assert "unhealthy_container_count" in center_js
    assert "const credentialAlertRows = asArray(credentialsPlan.alert_rows)" in center_js
    assert "Credential alert queue" in center_js
    assert "credential_alert_count" in center_js
    assert "critical_credential_alert_count" in center_js
    assert "const credentialEntryRows = asArray(credentialsPlan.entry_rows)" in center_js
    assert "const credentialHandoffRows = asArray(credentialsPlan.handoff_rows)" in center_js
    assert "Credential request route proof" in center_js
    assert "No credential request route proof visible" in center_js
    assert "Credential handoffs" in center_js
    assert "No credential handoffs visible" in center_js
    assert "credential posture handoff evidence" in center_js
    assert "open-local-data-map credential route evidence" in center_js
    assert "row.reads_secrets === true" in center_js
    assert "Credential Routes" in center_js
    assert "Credential Handoffs" in center_js
    assert "const researchAlertRows = asArray(backendPlan.alert_rows)" in center_js
    assert "Research alert queue" in center_js
    assert "research_alert_count" in center_js
    assert "critical_research_alert_count" in center_js
    assert "const researchEntryRows = asArray(backendPlan.entry_rows)" in center_js
    assert "No research request route proof visible" in center_js
    assert "open-research-preflight request route evidence" in center_js
    assert "row.runs_search === true" in center_js
    assert "const researchHandoffRows = asArray(backendPlan.handoff_rows)" in center_js
    assert "Research/library handoffs" in center_js
    assert "No backend research handoffs visible" in center_js
    assert "research/library handoff evidence" in center_js
    assert "network_after_approval" in center_js
    assert "const galleryAlertRows = asArray(galleryPlan.alert_rows)" in center_js
    assert "Media alert queue" in center_js
    assert "gallery_alert_count" in center_js
    assert "critical_gallery_alert_count" in center_js
    assert "const galleryEntryRows = asArray(galleryPlan.entry_rows)" in center_js
    assert "Media request route proof" in center_js
    assert "No media request route proof visible" in center_js
    assert "row.uploads_files === true" in center_js
    assert "const galleryHandoffRows = asArray(galleryPlan.handoff_rows)" in center_js
    assert "Media handoffs" in center_js
    assert "No media handoffs visible" in center_js
    assert "gallery media handoff evidence" in center_js
    assert "Media Handoffs" in center_js
    assert "row.refreshes_vision === true" in center_js
    assert "const modelAlertRows = asArray(modelOpsPlan.alert_rows)" in center_js
    assert "const modelSnapshotAlertRows = asArray(operatorModels.alert_rows)" in center_js
    assert "Model alert queue" in center_js
    assert "Model snapshot alerts" in center_js
    assert "No model snapshot alerts returned" in center_js
    assert "model_alert_count" in center_js
    assert "critical_model_alert_count" in center_js
    assert "model_snapshot_alert_count" in center_js
    assert "critical_model_snapshot_alert_count" in center_js
    assert "const modelEntryRows = asArray(modelOpsPlan.entry_rows)" in center_js
    assert "Model request route proof" in center_js
    assert "No model operation request route proof visible" in center_js
    assert "row.starts_serving === true" in center_js
    assert "const recoveryHint = String(activity.recovery_hint || '').trim();" in center_js
    assert "const rollbackHint = String(activity.rollback_hint || '').trim();" in center_js
    assert "activity.retryable === true" in center_js
    assert "recoveryHint || 'Retry re-runs this command through the current trust policy" in center_js
    assert "const backendActionRows = backendOk ? asArray(backendPlan.action_rows)" in center_js
    assert "Activity action affordances" in center_js
    assert "row.copy_log_action || row.copyLogAction" in center_js
    assert "row.retry_action || row.retryAction" in center_js
    assert "row.recovery_action || row.recoveryAction" in center_js
    assert "log_evidence_count" in center_js
    assert "rollback_ready_count" in center_js
    assert "Automation activity handoffs" in center_js
    assert "No automation activity handoffs visible" in center_js
    assert "copy-activity-log:" in center_js
    assert "retry-activity:" in center_js
    assert "if (handleDashboardInternalAction(commandId))" in center_js
    assert "openActivityRetryCheckpoint(action.slice('retry-activity:'.length), 'activity-preflight')" in center_js
    assert "openActivityRetryCheckpoint(action.slice('retry-activity:'.length), 'automation-map')" in center_js
    assert "copyActivityLogById(action.slice('copy-activity-log:'.length))" in center_js
    assert "asArray(operatorBriefing, ['entry_rows', 'entryRows'])" in center_js
    assert "Backend route proof" in center_js
    assert "route_ready_count" in center_js
    assert "entry_route_ready_count" in center_js
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
    assert "20260626-operator-console" in app_js
    assert "app.js?v=20260626-operator-console" in index_html
    assert "import operatorCommands from './operatorCommands.js?v=20260626-operator-console';" in code_workspace_js
    assert "import operatorCommands from './operatorCommands.js?v=20260626-operator-console';" in center_js
    assert "import voiceCommand from './voiceCommand.js?v=20260626-operator-console';" in center_js
    assert "import operatorCommands from './operatorCommands.js?v=20260626-operator-console';" in palette_js
    assert "import operatorCommands from './operatorCommands.js?v=20260626-operator-console';" in voice_js
    assert "function recordVoiceActivity(status, state, detail, patch = {})" in voice_js
    assert "command_id: 'start-voice-command'" in voice_js
    assert "source: 'voice-command'" in voice_js
    assert "privacy_note: 'Voice controller activity stores provider/status metadata only; no audio is stored.'" in voice_js
    assert "recordVoiceActivity('no_speech', 'warn'" in voice_js
    assert "const routedStatus = result?.cancelled ? 'cancelled' : 'routed';" in voice_js
    assert "export async function open(options = {})" in code_workspace_js
    assert "async function stageRunCommand(options = {})" in code_workspace_js
    assert "input.value = command;" in code_workspace_js
    assert "Nothing has executed yet." in code_workspace_js
    assert "function recordRunActivity(command, data = {}, error = null)" in code_workspace_js
    assert "source: 'code-workspace-run'" in code_workspace_js
    assert "run_command: trimmed" in code_workspace_js
    assert "stdout," in code_workspace_js
    assert "stderr," in code_workspace_js
    assert "function syncBackendRunActivity(record)" in code_workspace_js
    assert "operatorCommands.setBackendActivity([record, ...existing], { emit: true });" in code_workspace_js
    assert "if (data.activity)" in code_workspace_js
    assert "syncBackendRunActivity(data.activity);" in code_workspace_js
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
    assert "backendPlan.route_rows" in center_js
    assert "backendPlan.entry_rows" in center_js
    assert "Request route proof" in center_js
    assert "route_ready_count" in center_js
    assert "entry_route_ready_count" in center_js
    assert "Train a small model on this dataset" in center_js


def test_code_workspace_run_endpoint_records_backend_activity(monkeypatch):
    import routes.code_workspace_routes as code_workspace_routes

    captured = {}

    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "run_command",
        lambda workspace_id, command, owner="", timeout_seconds=120: {
            "stdout": "tests passed",
            "stderr": "",
            "exit_code": 0,
            "runner": "worker",
        },
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "get_workspace",
        lambda workspace_id, owner="": {"id": workspace_id, "name": "Repo"},
    )

    def fake_upsert(record, *, owner="local", activity_path=None):
        captured["record"] = record
        captured["owner"] = owner
        return {"id": "activity-1", "owner": owner, **record}

    monkeypatch.setattr(code_workspace_routes, "upsert_operator_activity_record", fake_upsert)

    router = code_workspace_routes.setup_code_workspace_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/code-workspaces/{workspace_id}/run"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    result = endpoint(
        "ws1",
        code_workspace_routes.RunRequest(command="pytest -q", timeout_seconds=30),
        request,
    )

    assert result["ok"] is True
    assert result["activity"]["id"] == "activity-1"
    assert captured["owner"] == "alice"
    assert captured["record"]["command_id"] == "run-tests"
    assert captured["record"]["source"] == "code-workspace-api"
    assert captured["record"]["workspace"] == "Repo"
    assert captured["record"]["run_command"] == "pytest -q"
    assert captured["record"]["exit_code"] == 0
    assert captured["record"]["runner"] == "worker"
    assert captured["record"]["stdout"] == "tests passed"
    assert captured["record"]["status"] == "success"
    assert captured["record"]["events"][0]["status"] == "success"


def test_code_workspace_run_endpoint_records_blocked_activity(monkeypatch):
    import routes.code_workspace_routes as code_workspace_routes

    captured = {}

    def blocked_run(*args, **kwargs):
        raise code_workspace_routes.code_workspace.CodeWorkspaceError("Command is blocked")

    monkeypatch.setattr(code_workspace_routes.code_workspace, "run_command", blocked_run)
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "get_workspace",
        lambda workspace_id, owner="": {"id": workspace_id, "name": "Repo"},
    )

    def fake_upsert(record, *, owner="local", activity_path=None):
        captured["record"] = record
        captured["owner"] = owner
        return {"id": "activity-blocked", "owner": owner, **record}

    monkeypatch.setattr(code_workspace_routes, "upsert_operator_activity_record", fake_upsert)

    router = code_workspace_routes.setup_code_workspace_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/code-workspaces/{workspace_id}/run"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))

    with pytest.raises(HTTPException) as exc:
        endpoint(
            "ws1",
            code_workspace_routes.RunRequest(command="pip install package", timeout_seconds=30),
            request,
        )

    assert exc.value.status_code == 400
    assert captured["owner"] == "alice"
    assert captured["record"]["title"] == "Code Workspace Command Blocked"
    assert captured["record"]["status"] == "blocked"
    assert captured["record"]["state"] == "error"
    assert captured["record"]["source"] == "code-workspace-api"
    assert captured["record"]["runner"] == "validation"
    assert captured["record"]["stderr"] == "Command is blocked"
    assert captured["record"]["events"][0]["status"] == "blocked"


def test_code_workspace_agent_endpoint_records_backend_activity(monkeypatch):
    import routes.code_workspace_routes as code_workspace_routes

    captured = {}

    async def fake_agent(*args, **kwargs):
        return {
            "ok": True,
            "model": "llama3.2:3b",
            "plan": "Patch file",
            "snapshot": {"id": "snap1"},
            "selected_paths": ["src/app.py"],
            "proposed_diff": "diff --git a/src/app.py b/src/app.py\n",
            "applied": False,
            "test_result": {"stdout": "ok", "stderr": "", "exit_code": 0},
            "steps": [{"phase": "draft", "round": 1, "exit_code": 0}],
            "exit_code": 0,
        }

    monkeypatch.setattr(code_workspace_routes.code_workspace_agent, "run_agent", fake_agent)
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "get_workspace",
        lambda workspace_id, owner="": {"id": workspace_id, "name": "Repo"},
    )
    monkeypatch.setattr(code_workspace_routes, "append_audit", lambda *args, **kwargs: None)

    def fake_upsert(record, *, owner="local", activity_path=None):
        captured["record"] = record
        captured["owner"] = owner
        return {"id": "agent-activity", "owner": owner, **record}

    monkeypatch.setattr(code_workspace_routes, "upsert_operator_activity_record", fake_upsert)

    router = code_workspace_routes.setup_code_workspace_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/code-workspaces/{workspace_id}/agent"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    result = asyncio.run(endpoint(
        "ws1",
        code_workspace_routes.AgentRequest(task="Fix the failing test", selected_paths=["src/app.py"]),
        request,
    ))

    assert result["ok"] is True
    assert result["activity"]["id"] == "agent-activity"
    assert captured["owner"] == "alice"
    assert captured["record"]["title"] == "Code Workspace Agent Completed"
    assert captured["record"]["source"] == "code-workspace-agent-api"
    assert captured["record"]["agent_task"] == "Fix the failing test"
    assert captured["record"]["model"] == "llama3.2:3b"
    assert captured["record"]["selected_paths"] == ["src/app.py"]
    assert captured["record"]["snapshot_id"] == "snap1"
    assert captured["record"]["has_proposed_diff"] is True
    assert captured["record"]["stdout"] == "ok"
    assert captured["record"]["steps"][0]["phase"] == "draft"


def test_code_workspace_agent_endpoint_records_blocked_activity(monkeypatch):
    import routes.code_workspace_routes as code_workspace_routes

    captured = {}

    async def blocked_agent(*args, **kwargs):
        raise code_workspace_routes.code_workspace.CodeWorkspaceError("Set Code Workspace model key before running the coding agent")

    monkeypatch.setattr(code_workspace_routes.code_workspace_agent, "run_agent", blocked_agent)
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "get_workspace",
        lambda workspace_id, owner="": {"id": workspace_id, "name": "Repo"},
    )

    def fake_upsert(record, *, owner="local", activity_path=None):
        captured["record"] = record
        captured["owner"] = owner
        return {"id": "agent-blocked", "owner": owner, **record}

    monkeypatch.setattr(code_workspace_routes, "upsert_operator_activity_record", fake_upsert)

    router = code_workspace_routes.setup_code_workspace_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/code-workspaces/{workspace_id}/agent"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(
            "ws1",
            code_workspace_routes.AgentRequest(task="Fix the failing test"),
            request,
        ))

    assert exc.value.status_code == 400
    assert captured["owner"] == "alice"
    assert captured["record"]["title"] == "Code Workspace Agent Blocked"
    assert captured["record"]["status"] == "blocked"
    assert captured["record"]["state"] == "error"
    assert captured["record"]["source"] == "code-workspace-agent-api"
    assert captured["record"]["stderr"] == "Set Code Workspace model key before running the coding agent"
    assert captured["record"]["events"][0]["status"] == "blocked"


def test_code_workspace_validate_diff_records_activity(monkeypatch):
    import routes.code_workspace_routes as code_workspace_routes

    captured = {}

    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "create_snapshot",
        lambda workspace_id, label, owner="": {"id": "snap1"},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "apply_unified_diff",
        lambda workspace_id, diff, owner="", allowed_paths=None: {"stdout": "patch ok", "stderr": "", "exit_code": 0},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "run_command",
        lambda workspace_id, command, owner="", timeout_seconds=120: {"stdout": "tests ok", "stderr": "", "exit_code": 0},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "restore_snapshot",
        lambda workspace_id, snapshot_id, owner="": {"restored": True},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "get_workspace",
        lambda workspace_id, owner="": {"id": workspace_id, "name": "Repo"},
    )
    monkeypatch.setattr(code_workspace_routes, "append_audit", lambda *args, **kwargs: None)

    def fake_upsert(record, *, owner="local", activity_path=None):
        captured["record"] = record
        captured["owner"] = owner
        return {"id": "validation-activity", "owner": owner, **record}

    monkeypatch.setattr(code_workspace_routes, "upsert_operator_activity_record", fake_upsert)

    router = code_workspace_routes.setup_code_workspace_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/code-workspaces/{workspace_id}/validate-diff"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    result = endpoint(
        "ws1",
        code_workspace_routes.ValidateDiffRequest(diff="diff --git a/a b/a\n", test_command="pytest -q"),
        request,
    )

    assert result["ok"] is True
    assert result["valid"] is True
    assert result["activity"]["id"] == "validation-activity"
    assert captured["owner"] == "alice"
    assert captured["record"]["title"] == "Code Workspace Diff Validation Passed"
    assert captured["record"]["source"] == "code-workspace-validate-api"
    assert captured["record"]["snapshot_id"] == "snap1"
    assert captured["record"]["patch_exit_code"] == 0
    assert captured["record"]["test_exit_code"] == 0
    assert captured["record"]["stdout"] == "tests ok"
    assert captured["record"]["valid"] is True


def test_code_workspace_validate_diff_records_patch_failure_activity(monkeypatch):
    import routes.code_workspace_routes as code_workspace_routes

    captured = {}

    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "create_snapshot",
        lambda workspace_id, label, owner="": {"id": "snap1"},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "apply_unified_diff",
        lambda workspace_id, diff, owner="", allowed_paths=None: {"stdout": "", "stderr": "patch failed", "exit_code": 1},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "restore_snapshot",
        lambda workspace_id, snapshot_id, owner="": {"restored": True},
    )
    monkeypatch.setattr(
        code_workspace_routes.code_workspace,
        "get_workspace",
        lambda workspace_id, owner="": {"id": workspace_id, "name": "Repo"},
    )

    def fake_upsert(record, *, owner="local", activity_path=None):
        captured["record"] = record
        captured["owner"] = owner
        return {"id": "validation-failed", "owner": owner, **record}

    monkeypatch.setattr(code_workspace_routes, "upsert_operator_activity_record", fake_upsert)

    router = code_workspace_routes.setup_code_workspace_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/code-workspaces/{workspace_id}/validate-diff"
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    result = endpoint(
        "ws1",
        code_workspace_routes.ValidateDiffRequest(diff="bad diff", test_command="pytest -q"),
        request,
    )

    assert result["ok"] is True
    assert result["valid"] is False
    assert result["activity"]["id"] == "validation-failed"
    assert captured["record"]["title"] == "Code Workspace Diff Validation Failed"
    assert captured["record"]["status"] == "error"
    assert captured["record"]["patch_exit_code"] == 1
    assert captured["record"]["test_exit_code"] is None
    assert captured["record"]["stderr"] == "patch failed"
    assert captured["record"]["valid"] is False


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
    assert plan["summary"]["route_match_count"] == 9
    assert plan["summary"]["route_match_ready_count"] == 9
    assert plan["summary"]["approval_target_count"] == 3
    assert plan["summary"]["approval_ready_count"] == 3
    assert plan["summary"]["experience_alert_count"] == 1
    assert plan["summary"]["critical_experience_alert_count"] == 0
    assert plan["summary"]["handoff_count"] == 9
    assert plan["summary"]["handoff_ready_count"] == 9
    assert plan["summary"]["entry_path_count"] == 5
    assert plan["summary"]["entry_path_ready_count"] == 5
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["dashboard_entry_ready"] is True
    assert plan["summary"]["text_entry_ready"] is True
    assert plan["summary"]["palette_entry_ready"] is True
    assert plan["summary"]["voice_entry_ready"] is True
    assert plan["summary"]["workflow_entry_ready"] is True
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
    route_match_titles = {row["title"] for row in plan["route_match_rows"]}
    assert "Route match: Container health and repair request" in route_match_titles
    assert "Route match: Training run plan" in route_match_titles
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "experience-summarize-today-handoff",
        "experience-container-health-handoff",
        "experience-code-tests-handoff",
        "experience-train-small-model-handoff",
        "experience-watch-build-handoff",
        "experience-note-task-handoff",
        "experience-local-doc-search-handoff",
        "experience-change-brief-handoff",
        "experience-backup-verify-handoff",
    }
    assert handoffs["experience-summarize-today-handoff"]["target_api"] == "/api/operator/briefing"
    assert handoffs["experience-container-health-handoff"]["target_api"] == "/api/operator/repair-plan"
    assert handoffs["experience-code-tests-handoff"]["target_api"] == "/api/operator/code-test-plan"
    assert handoffs["experience-train-small-model-handoff"]["target_api"] == "/api/operator/training-plan"
    assert handoffs["experience-watch-build-handoff"]["target_api"] == "/api/operator/build-watch-plan"
    assert handoffs["experience-note-task-handoff"]["target_api"] == "/api/operator/note-task-draft"
    assert handoffs["experience-local-doc-search-handoff"]["target_api"] == "/api/operator/document-search-plan"
    assert handoffs["experience-change-brief-handoff"]["target_api"] == "/api/operator/change-brief"
    assert handoffs["experience-backup-verify-handoff"]["target_api"] == "/api/operator/backup-plan"
    assert handoffs["experience-container-health-handoff"]["requires_approval"] is True
    assert handoffs["experience-watch-build-handoff"]["requires_approval"] is True
    assert handoffs["experience-backup-verify-handoff"]["requires_approval"] is True
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["starts_jobs"] is False for row in plan["handoff_rows"])
    assert all(row["starts_training"] is False for row in plan["handoff_rows"])
    assert all(row["runs_search"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["creates_tasks"] is False for row in plan["handoff_rows"])
    assert all(row["creates_backup"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["exports_data"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_records"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["experience-container-health-handoff"]["gated_operation"]["runs_docker"] is True
    assert handoffs["experience-code-tests-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["experience-train-small-model-handoff"]["gated_operation"]["starts_training"] is True
    assert handoffs["experience-watch-build-handoff"]["gated_operation"]["starts_workflows"] is True
    assert handoffs["experience-note-task-handoff"]["gated_operation"]["creates_tasks"] is True
    assert handoffs["experience-local-doc-search-handoff"]["gated_operation"]["runs_search"] is True
    assert handoffs["experience-backup-verify-handoff"]["gated_operation"]["creates_backup"] is True
    assert all(row["executes"] is False for row in plan["target_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["route_match_rows"])
    assert all(row["routes_commands"] is False for row in plan["route_match_rows"])
    assert all(row["starts_workflows"] is False for row in plan["route_match_rows"])
    assert all(row["runs_shell"] is False for row in plan["route_match_rows"])
    assert all(row["uses_network"] is False for row in plan["route_match_rows"])
    assert {row["id"] for row in plan["entry_rows"]} == {
        "dashboard",
        "text-command",
        "command-palette",
        "voice-command",
        "agent-workflows",
    }
    assert any(row["requires_approval"] is True for row in plan["target_rows"])
    assert plan["alert_rows"][0]["title"] == "Target workflow catalog missing"
    assert "does not route commands" in plan["approval"]["policy"]
    assert plan["paths"]["commands"] == "data/operator_commands.json"

    weak = run_operator_experience_plan(
        "alice",
        commands=[],
        workflows=[],
        policy={},
        configured={"commands": False, "workflows": False, "policy": False},
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["experience_alert_count"] == 12
    assert weak["summary"]["critical_experience_alert_count"] == 10
    assert weak["summary"]["entry_path_count"] == 5
    assert weak["summary"]["entry_path_ready_count"] == 0
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert weak["summary"]["handoff_count"] == 9
    assert weak["summary"]["handoff_ready_count"] == 0
    assert "Target phrase not ready: Today briefing" in weak_titles
    assert "Target phrase not ready: Code workspace test plan" in weak_titles
    assert "Target command catalog missing" in weak_titles
    assert "Target trust policy evidence missing" in weak_titles


def test_operator_console_plan_maps_dashboard_sections_without_execution():
    from src.operator_console import run_operator_console_plan

    action_ids = [
        "open-operator-runbook",
        "open-model-routing-map",
        "open-offline",
        "open-operations-queue",
        "open-memory-profile",
        "open-local-data-map",
        "open-work-preflight",
        "open-code-workspace-map",
        "open-library-preflight",
        "open-training-run-plan",
        "open-activity-preflight",
        "open-command-palette",
        "summarize-today",
        "open-voice-preflight",
        "open-automation-map",
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
    entry_ids = {row["id"] for row in plan["entry_rows"]}

    assert plan["mode"] == "read-only-console-readiness-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["section_count"] == 12
    assert plan["summary"]["ready_count"] == 12
    assert plan["summary"]["entry_count"] == 5
    assert plan["summary"]["entry_ready_count"] == 5
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["console_alert_count"] == 0
    assert plan["summary"]["critical_console_alert_count"] == 0
    assert plan["summary"]["alert_feed_count"] == 43
    assert plan["summary"]["alert_feed_ready_count"] == 43
    assert plan["summary"]["handoff_count"] == 8
    assert plan["summary"]["handoff_ready_count"] == 8
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
        "local-data-map",
        "tasks",
        "calendar",
        "code-workspaces",
        "research-library",
        "training-jobs",
        "alerts",
    } == section_ids
    assert {
        "dashboard",
        "command-palette",
        "text-command",
        "voice-command",
        "agent-workflows",
    } == entry_ids
    assert "/api/operator/console-plan" in api_paths
    assert "/api/operator/data-plan" in api_paths
    assert "/api/operator/runtime-plan" in api_paths
    assert "/api/operator/model-ops-plan" in api_paths
    assert "/api/operator/research-plan" in api_paths
    assert "/api/operator/gallery-plan" in api_paths
    assert "/api/operator/document-search-plan" in api_paths
    feed_ids = {row["id"] for row in plan["alert_feed_rows"]}
    assert "alert-feed-service-snapshot" in feed_ids
    assert "alert-feed-container-checks" in feed_ids
    assert "alert-feed-docker-runtime" in feed_ids
    assert "alert-feed-models" in feed_ids
    assert "alert-feed-ai-runtime" in feed_ids
    assert "alert-feed-command-layer" in feed_ids
    assert "alert-feed-automation-plan" in feed_ids
    assert "alert-feed-workspace" in feed_ids
    assert "alert-feed-work-ops" in feed_ids
    assert "alert-feed-voice" in feed_ids
    assert "alert-feed-activity" in feed_ids
    assert "alert-feed-recovery" in feed_ids
    assert "alert-feed-data" in feed_ids
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "console-dashboard-status-handoff",
        "console-command-layer-handoff",
        "console-alert-feed-handoff",
        "console-approval-queue-handoff",
        "console-activity-ledger-handoff",
        "console-recovery-handoff",
        "console-backup-readiness-handoff",
        "console-safety-policy-handoff",
    }
    assert handoffs["console-command-layer-handoff"]["target_api"] == "/api/operator/command-layer-plan"
    assert handoffs["console-activity-ledger-handoff"]["target_api"] == "/api/operator/activity"
    assert handoffs["console-backup-readiness-handoff"]["target_api"] == "/api/operator/backup-plan"
    assert handoffs["console-safety-policy-handoff"]["target_api"] == "/api/operator/safety-plan"
    assert handoffs["console-dashboard-status-handoff"]["requires_approval"] is False
    assert handoffs["console-activity-ledger-handoff"]["requires_approval"] is False
    assert handoffs["console-recovery-handoff"]["requires_approval"] is True
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["starts_jobs"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["approves_actions"] is False for row in plan["handoff_rows"])
    assert all(row["creates_backup"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["changes_policy"] is False for row in plan["handoff_rows"])
    assert all(row["exports_data"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_records"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["console-command-layer-handoff"]["gated_operation"]["routes_commands"] is True
    assert handoffs["console-activity-ledger-handoff"]["gated_operation"]["writes_activity"] is True
    assert handoffs["console-approval-queue-handoff"]["gated_operation"]["approves_actions"] is True
    assert handoffs["console-backup-readiness-handoff"]["gated_operation"]["creates_backup"] is True
    assert handoffs["console-recovery-handoff"]["gated_operation"]["restores_data"] is True
    assert handoffs["console-safety-policy-handoff"]["gated_operation"]["changes_policy"] is True
    assert handoffs["console-safety-policy-handoff"]["gated_operation"]["uses_network"] is True
    assert all(row["executes"] is False for row in plan["section_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["alert_feed_rows"])
    assert all(row["routes_commands"] is False for row in plan["alert_feed_rows"])
    assert all(row["starts_jobs"] is False for row in plan["alert_feed_rows"])
    assert all(row["runs_shell"] is False for row in plan["alert_feed_rows"])
    assert all(row["uses_network"] is False for row in plan["alert_feed_rows"])
    assert all(row["uses_network"] is False for row in plan["section_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "logs/" in plan["paths"]["data_paths"]

    weak = run_operator_console_plan(
        "alice",
        commands=[],
        workflows=[],
        policy={},
        configured={"commands": False, "workflows": False, "policy": False},
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["console_alert_count"] == 20
    assert weak["summary"]["critical_console_alert_count"] == 4
    assert weak["summary"]["handoff_count"] == 8
    assert weak["summary"]["handoff_ready_count"] == 1
    assert "Dashboard section action missing: System status" in weak_titles
    assert "Dashboard section action missing: Alerts" in weak_titles
    assert "Command catalog missing" in weak_titles


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
        "open-capability-map",
        "open-command-palette",
        "open-automation-map",
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
    assert plan["summary"]["entry_count"] == 5
    assert plan["summary"]["entry_ready_count"] == 5
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["network_capable_count"] == 7
    assert plan["summary"]["toolchain_alert_count"] == 0
    assert plan["summary"]["critical_toolchain_alert_count"] == 0
    assert plan["summary"]["handoff_count"] == 11
    assert plan["summary"]["handoff_ready_count"] == 11
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
    assert {
        "dashboard",
        "text-command",
        "command-palette",
        "voice-command",
        "agent-workflows",
    } == {row["id"] for row in plan["entry_rows"]}
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "toolchain-command-layer-handoff",
        "toolchain-ai-runtime-handoff",
        "toolchain-knowledge-rag-handoff",
        "toolchain-research-network-handoff",
        "toolchain-code-build-handoff",
        "toolchain-training-handoff",
        "toolchain-work-automation-handoff",
        "toolchain-memory-profile-handoff",
        "toolchain-docker-services-handoff",
        "toolchain-backup-recovery-handoff",
        "toolchain-tool-access-safety-handoff",
    }
    assert handoffs["toolchain-command-layer-handoff"]["target_api"] == "/api/operator/command-layer-plan"
    assert handoffs["toolchain-ai-runtime-handoff"]["target_api"] == "/api/operator/ai-runtime-plan"
    assert handoffs["toolchain-knowledge-rag-handoff"]["target_api"] == "/api/operator/document-search-plan"
    assert handoffs["toolchain-research-network-handoff"]["target_api"] == "/api/operator/research-plan"
    assert handoffs["toolchain-code-build-handoff"]["target_api"] == "/api/operator/workspace-plan"
    assert handoffs["toolchain-training-handoff"]["target_api"] == "/api/operator/training-plan"
    assert handoffs["toolchain-docker-services-handoff"]["target_api"] == "/api/operator/docker-runtime-plan"
    assert handoffs["toolchain-backup-recovery-handoff"]["target_api"] == "/api/operator/backup-plan"
    assert handoffs["toolchain-tool-access-safety-handoff"]["target_api"] == "/api/operator/tool-access-plan"
    assert handoffs["toolchain-command-layer-handoff"]["requires_approval"] is False
    assert handoffs["toolchain-knowledge-rag-handoff"]["requires_approval"] is False
    assert handoffs["toolchain-research-network-handoff"]["requires_approval"] is True
    assert handoffs["toolchain-docker-services-handoff"]["requires_approval"] is True
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["starts_jobs"] is False for row in plan["handoff_rows"])
    assert all(row["starts_models"] is False for row in plan["handoff_rows"])
    assert all(row["starts_training"] is False for row in plan["handoff_rows"])
    assert all(row["runs_search"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["changes_settings"] is False for row in plan["handoff_rows"])
    assert all(row["approves_actions"] is False for row in plan["handoff_rows"])
    assert all(row["downloads_models"] is False for row in plan["handoff_rows"])
    assert all(row["creates_backup"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["exports_data"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_records"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["toolchain-command-layer-handoff"]["gated_operation"]["routes_commands"] is True
    assert handoffs["toolchain-ai-runtime-handoff"]["gated_operation"]["starts_models"] is True
    assert handoffs["toolchain-research-network-handoff"]["gated_operation"]["runs_search"] is True
    assert handoffs["toolchain-code-build-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["toolchain-training-handoff"]["gated_operation"]["starts_training"] is True
    assert handoffs["toolchain-work-automation-handoff"]["gated_operation"]["starts_workflows"] is True
    assert handoffs["toolchain-docker-services-handoff"]["gated_operation"]["runs_docker"] is True
    assert handoffs["toolchain-backup-recovery-handoff"]["gated_operation"]["creates_backup"] is True
    assert handoffs["toolchain-tool-access-safety-handoff"]["gated_operation"]["changes_settings"] is True
    assert handoffs["toolchain-tool-access-safety-handoff"]["gated_operation"]["uses_network"] is True
    assert "does not route commands" in plan["approval"]["policy"]
    assert "cleverly-chromadb-data:/data" in plan["paths"]["data_paths"]

    weak = run_operator_toolchain_plan(
        "alice",
        commands=[],
        workflows=[],
        policy={},
        configured={"commands": False, "workflows": False, "policy": False},
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert weak["summary"]["toolchain_alert_count"] == 24
    assert weak["summary"]["critical_toolchain_alert_count"] == 5
    assert weak["summary"]["handoff_count"] == 11
    assert weak["summary"]["handoff_ready_count"] == 0
    assert "Toolchain module route missing: Offline Control" in weak_titles
    assert "Toolchain module route missing: Docker support services" in weak_titles
    assert "Toolchain entry point missing: Agent workflow handoff" in weak_titles
    assert "Command catalog missing" in weak_titles


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
    entries = {row["entry"] for row in plan["entry_rows"]}
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}

    assert plan["mode"] == "read-only-safety-boundary-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["risk_count"] == 5
    assert plan["summary"]["ready_count"] == 5
    assert plan["summary"]["ask_ready_count"] == 6
    assert plan["summary"]["ask_total"] == 6
    assert plan["summary"]["network_capable_count"] == 1
    assert plan["summary"]["safety_alert_count"] == 0
    assert plan["summary"]["critical_safety_alert_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["handoff_count"] == 7
    assert plan["summary"]["handoff_ready_count"] == 7
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
    assert entries == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["command_id"] == "open-trust-controls" for row in plan["entry_rows"])
    assert all(row["safety_command_id"] == "open-autonomy-map" for row in plan["entry_rows"])
    assert all(row["offline_command_id"] == "open-offline" for row in plan["entry_rows"])
    assert all(row["activity_command_id"] == "open-activity-preflight" for row in plan["entry_rows"])
    assert all(row["safety_api"] == "/api/operator/safety-plan" for row in plan["entry_rows"])
    assert all(row["policy_api"] == "/api/operator/policy" for row in plan["entry_rows"])
    assert all(row["requires_approval"] is True for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["routes_commands"] is False for row in plan["entry_rows"])
    assert all(row["executes_commands"] is False for row in plan["entry_rows"])
    assert all(row["approves_actions"] is False for row in plan["entry_rows"])
    assert all(row["starts_workflows"] is False for row in plan["entry_rows"])
    assert all(row["starts_jobs"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["runs_docker"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert all(row["reads_credentials"] is False for row in plan["entry_rows"])
    assert all(row["exports_data"] is False for row in plan["entry_rows"])
    assert all(row["deletes_records"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert set(handoffs) == {
        "safety-destructive-recovery-handoff",
        "safety-network-egress-handoff",
        "safety-credential-secret-handoff",
        "safety-filesystem-boundary-handoff",
        "safety-shell-docker-handoff",
        "safety-backup-recovery-handoff",
        "safety-activity-ledger-handoff",
    }
    assert handoffs["safety-destructive-recovery-handoff"]["approval_command_id"] == "request-container-fix"
    assert handoffs["safety-network-egress-handoff"]["network_after_approval"] is True
    assert handoffs["safety-credential-secret-handoff"]["target_api"] == "/api/operator/credentials-plan"
    assert handoffs["safety-shell-docker-handoff"]["approval_command_id"] == "run-tests"
    assert handoffs["safety-activity-ledger-handoff"]["requires_approval"] is False
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["approves_actions"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["starts_jobs"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["reads_credentials"] is False for row in plan["handoff_rows"])
    assert all(row["exports_data"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_records"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["safety-destructive-recovery-handoff"]["gated_operation"]["restarts_services"] is True
    assert handoffs["safety-network-egress-handoff"]["gated_operation"]["uses_network"] is True
    assert handoffs["safety-credential-secret-handoff"]["gated_operation"]["reads_credentials"] is True
    assert handoffs["safety-filesystem-boundary-handoff"]["gated_operation"]["writes_files"] is True
    assert handoffs["safety-shell-docker-handoff"]["gated_operation"]["runs_shell"] is True
    assert all(row["executes"] is False for row in plan["risk_rows"])
    assert all(row["uses_network"] is False for row in plan["risk_rows"])
    assert any(row["network_capable"] is True for row in plan["risk_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "data/auth.json" in plan["paths"]["data_paths"]

    weak = run_operator_safety_plan(
        "alice",
        commands=[],
        workflows=[],
        policy={"local": "auto", "approval": "auto", "network": "auto", "danger": "auto"},
        configured={"commands": False, "workflows": False, "policy": False},
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["safety_alert_count"] == 8
    assert weak["summary"]["critical_safety_alert_count"] == 4
    assert weak["summary"]["handoff_count"] == 7
    assert weak["summary"]["handoff_ready_count"] == 1
    assert "Safety boundary not ask-gated: Destructive and recovery actions" in weak_titles
    assert "Safety boundary not ask-gated: Shell, Docker, tests, and loops" in weak_titles
    assert "Command safety catalog missing" in weak_titles


def test_operator_goal_plan_maps_operating_console_goal_without_execution():
    from pathlib import Path

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
        "open-voice-preflight",
        "open-automation-map",
        "summarize-today",
        "open-operations-queue",
        "open-code-workspace-map",
        "open-model-routing-map",
        "open-training-run-plan",
        "open-research-preflight",
        "open-work-preflight",
        "prepare-backup",
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
        configured={
            "commands": True,
            "workflows": True,
            "policy": True,
            "docker_runtime_verified": True,
            "command_center_ui_verified": True,
            "command_route_examples_verified": True,
            "permission_gate_ui_verified": True,
            "activity_timeline_verified": True,
            "operator_route_smokes_passed": True,
            "focused_tests_passed": True,
            "ui_inspection_passed": True,
            "clean_commit_pushed": True,
        },
    )

    api_paths = {row["path"] for row in plan["api_actions"]}
    all_rows = [*plan["principle_rows"], *plan["definition_rows"], *plan["release_gate_rows"], *plan["evidence_rows"], *plan["capability_rows"]]

    assert plan["mode"] == "read-only-goal-readiness-plan"
    assert plan["owner"] == "alice"
    assert plan["summary"]["principle_count"] == 7
    assert plan["summary"]["identity_count"] == 4
    assert plan["summary"]["identity_ready_count"] == 4
    assert plan["summary"]["definition_count"] == 4
    assert plan["summary"]["release_gate_count"] == 9
    assert plan["summary"]["release_gate_ready_count"] == 9
    assert plan["summary"]["evidence_count"] == 9
    assert plan["summary"]["capability_count"] == 13
    assert plan["summary"]["capability_ready_count"] == 13
    assert plan["summary"]["requirement_count"] == 42
    assert plan["summary"]["ready_count"] == 42
    assert plan["summary"]["issue_count"] == 0
    assert plan["summary"]["entry_route_count"] == 5
    assert plan["summary"]["entry_route_ready_count"] == 5
    assert plan["summary"]["goal_alert_count"] == 0
    assert plan["summary"]["critical_goal_alert_count"] == 0
    assert plan["summary"]["handoff_count"] == 10
    assert plan["summary"]["handoff_ready_count"] == 10
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
    assert all(row["executes"] is False for row in plan["identity_rows"])
    assert all(row["uses_network"] is False for row in plan["identity_rows"])
    assert {row["entry"] for row in plan["entry_rows"]} == {"dashboard", "text", "palette", "voice", "workflow"}
    assert all(row["goal_api"] == "/api/operator/goal-plan" for row in plan["entry_rows"])
    assert all(row["executes"] is False for row in plan["entry_rows"])
    assert all(row["routes_commands"] is False for row in plan["entry_rows"])
    assert all(row["executes_commands"] is False for row in plan["entry_rows"])
    assert all(row["starts_workflows"] is False for row in plan["entry_rows"])
    assert all(row["starts_jobs"] is False for row in plan["entry_rows"])
    assert all(row["runs_shell"] is False for row in plan["entry_rows"])
    assert all(row["writes_files"] is False for row in plan["entry_rows"])
    assert all(row["uses_network"] is False for row in plan["entry_rows"])
    assert all(row["approves_actions"] is False for row in plan["entry_rows"])
    handoffs = {row["id"]: row for row in plan["handoff_rows"]}
    assert set(handoffs) == {
        "goal-console-readiness-handoff",
        "goal-command-layer-handoff",
        "goal-autonomy-approval-handoff",
        "goal-memory-profile-handoff",
        "goal-practical-control-handoff",
        "goal-activity-recovery-handoff",
        "goal-safety-boundary-handoff",
        "goal-docker-runtime-handoff",
        "goal-target-experience-handoff",
        "goal-completion-audit-handoff",
    }
    assert handoffs["goal-console-readiness-handoff"]["target_api"] == "/api/operator/console-plan"
    assert handoffs["goal-command-layer-handoff"]["target_api"] == "/api/operator/command-layer-plan"
    assert handoffs["goal-autonomy-approval-handoff"]["target_api"] == "/api/operator/autonomy-plan"
    assert handoffs["goal-memory-profile-handoff"]["target_api"] == "/api/operator/memory-plan"
    assert handoffs["goal-practical-control-handoff"]["target_api"] == "/api/operator/toolchain-plan"
    assert handoffs["goal-activity-recovery-handoff"]["target_api"] == "/api/operator/activity-plan"
    assert handoffs["goal-safety-boundary-handoff"]["target_api"] == "/api/operator/safety-plan"
    assert handoffs["goal-docker-runtime-handoff"]["target_api"] == "/api/operator/docker-runtime-plan"
    assert handoffs["goal-target-experience-handoff"]["target_api"] == "/api/operator/experience-plan"
    assert handoffs["goal-completion-audit-handoff"]["target_api"] == "/api/operator/goal-plan"
    assert handoffs["goal-console-readiness-handoff"]["requires_approval"] is False
    assert handoffs["goal-command-layer-handoff"]["requires_approval"] is False
    assert handoffs["goal-autonomy-approval-handoff"]["requires_approval"] is True
    assert handoffs["goal-safety-boundary-handoff"]["requires_approval"] is True
    assert all(row["executes"] is False for row in plan["handoff_rows"])
    assert all(row["routes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["executes_commands"] is False for row in plan["handoff_rows"])
    assert all(row["starts_workflows"] is False for row in plan["handoff_rows"])
    assert all(row["starts_jobs"] is False for row in plan["handoff_rows"])
    assert all(row["starts_models"] is False for row in plan["handoff_rows"])
    assert all(row["starts_training"] is False for row in plan["handoff_rows"])
    assert all(row["runs_search"] is False for row in plan["handoff_rows"])
    assert all(row["runs_shell"] is False for row in plan["handoff_rows"])
    assert all(row["runs_docker"] is False for row in plan["handoff_rows"])
    assert all(row["writes_files"] is False for row in plan["handoff_rows"])
    assert all(row["writes_activity"] is False for row in plan["handoff_rows"])
    assert all(row["changes_policy"] is False for row in plan["handoff_rows"])
    assert all(row["approves_actions"] is False for row in plan["handoff_rows"])
    assert all(row["creates_backup"] is False for row in plan["handoff_rows"])
    assert all(row["restores_data"] is False for row in plan["handoff_rows"])
    assert all(row["exports_data"] is False for row in plan["handoff_rows"])
    assert all(row["deletes_records"] is False for row in plan["handoff_rows"])
    assert all(row["restarts_services"] is False for row in plan["handoff_rows"])
    assert all(row["uses_network"] is False for row in plan["handoff_rows"])
    assert handoffs["goal-command-layer-handoff"]["gated_operation"]["routes_commands"] is True
    assert handoffs["goal-autonomy-approval-handoff"]["gated_operation"]["starts_workflows"] is True
    assert handoffs["goal-autonomy-approval-handoff"]["gated_operation"]["approves_actions"] is True
    assert handoffs["goal-practical-control-handoff"]["gated_operation"]["starts_models"] is True
    assert handoffs["goal-practical-control-handoff"]["gated_operation"]["runs_shell"] is True
    assert handoffs["goal-activity-recovery-handoff"]["gated_operation"]["writes_activity"] is True
    assert handoffs["goal-safety-boundary-handoff"]["gated_operation"]["changes_policy"] is True
    assert handoffs["goal-docker-runtime-handoff"]["gated_operation"]["runs_docker"] is True
    assert handoffs["goal-docker-runtime-handoff"]["gated_operation"]["restarts_services"] is True
    assert handoffs["goal-target-experience-handoff"]["gated_operation"]["routes_commands"] is True
    assert any(row["id"] == "safety-by-default" for row in plan["principle_rows"])
    assert any(row["id"] == "docker-runtime-reliability" for row in plan["definition_rows"])
    assert {row["id"] for row in plan["release_gate_rows"]} == {
        "docker-runtime-started",
        "command-center-default-screen",
        "command-route-examples",
        "permission-gates-visible",
        "activity-timeline-proofed",
        "operator-plan-route-smokes",
        "focused-tests-js-checks",
        "responsive-ui-inspection",
        "clean-commit-push",
    }
    assert all(row["release_gate"] is True for row in plan["release_gate_rows"])
    assert all(row["proof_ready"] is True for row in plan["release_gate_rows"])
    assert all(row["executes"] is False for row in plan["release_gate_rows"])
    assert all(row["uses_network"] is False for row in plan["release_gate_rows"])
    release_gates = {row["id"]: row for row in plan["release_gate_rows"]}
    assert "ci/operator_route_smoke.py" in release_gates["operator-plan-route-smokes"]["paths"]
    assert "ci/smoke-operator-routes.ps1" in release_gates["operator-plan-route-smokes"]["paths"]
    assert "ci/command_center_ui_smoke.py" in release_gates["responsive-ui-inspection"]["paths"]
    assert "ci/smoke-command-center-ui.ps1" in release_gates["responsive-ui-inspection"]["paths"]
    assert "tests/bombadil-spec.ts" in release_gates["responsive-ui-inspection"]["paths"]
    assert "dist/command-center-ui-smoke.json" in release_gates["responsive-ui-inspection"]["paths"]
    route_smoke_py = Path("ci/operator_route_smoke.py").read_text(encoding="utf-8")
    route_smoke_ps1 = Path("ci/smoke-operator-routes.ps1").read_text(encoding="utf-8")
    ui_smoke_py = Path("ci/command_center_ui_smoke.py").read_text(encoding="utf-8")
    ui_smoke_ps1 = Path("ci/smoke-command-center-ui.ps1").read_text(encoding="utf-8")
    bombadil_spec = Path("tests/bombadil-spec.ts").read_text(encoding="utf-8")
    assert 'OPERATOR_PREFIX = "/api/operator"' in route_smoke_py
    assert "/console-plan" in route_smoke_py
    assert "/goal-plan" in route_smoke_py
    assert "/command-layer-plan" in route_smoke_py
    assert "/docker-runtime-plan" in route_smoke_py
    assert "/activity-plan" in route_smoke_py
    assert "ROUTE_EXAMPLES" in route_smoke_py
    assert '"request-build-watch-loop"' in route_smoke_py
    assert '"example_ok"' in route_smoke_py
    assert "operator_route_smoke.py" in route_smoke_ps1
    assert "desktop-command-center" in ui_smoke_py
    assert "mobile-command-center" in ui_smoke_py
    assert "--require-live" in ui_smoke_py
    assert "CLEVERLY_BOMBADIL_USERNAME" in ui_smoke_py
    assert "command_center_ui_smoke.py" in ui_smoke_ps1
    assert "commandCenterBecomesReady" in bombadil_spec
    assert "commandCenterDoesNotStayLoading" in bombadil_spec
    assert "commandCenterHasResponsiveClearance" in bombadil_spec
    assert "commandCenterTargetRoutesVisible" in bombadil_spec
    assert any(row["id"] == "goal-readiness-proof" for row in plan["evidence_rows"])
    assert any(row["id"] == "chat-reasoning" for row in plan["capability_rows"])
    assert any(row["id"] == "docker-services" for row in plan["capability_rows"])
    assert any(row["id"] == "cleverly-name" and row["value"] == "Cleverly" for row in plan["identity_rows"])
    assert any(row["id"] == "cleverly-privacy" and "local-first" in row["detail"] for row in plan["identity_rows"])
    assert "does not route commands" in plan["approval"]["policy"]
    assert "cleverly-chromadb-data:/data" in plan["paths"]["data_paths"]

    weak = run_operator_goal_plan(
        "alice",
        commands=[],
        workflows=[],
        policy={"local": "auto", "approval": "auto", "network": "auto", "danger": "auto"},
        configured={"commands": False, "workflows": False, "policy": False},
    )
    weak_titles = {row["title"] for row in weak["alert_rows"]}
    assert weak["summary"]["entry_route_count"] == 5
    assert weak["summary"]["entry_route_ready_count"] == 0
    assert weak["summary"]["release_gate_count"] == 9
    assert weak["summary"]["release_gate_ready_count"] == 0
    assert weak["summary"]["goal_alert_count"] == 50
    assert weak["summary"]["critical_goal_alert_count"] == 31
    assert weak["summary"]["handoff_count"] == 10
    assert weak["summary"]["handoff_ready_count"] == 0
    assert "Goal requirement not ready: Local-first" in weak_titles
    assert "Goal requirement not ready: Code workspace operations" in weak_titles
    assert "Goal requirement not ready: Docker runtime starts" in weak_titles
    assert "Goal requirement not ready: Work is committed and pushed cleanly" in weak_titles
    assert "Goal entry point missing: Goal prompt dashboard route" in weak_titles
    assert "Goal command catalog missing" in weak_titles


def test_operator_routes_use_builtin_v1_target_catalog_when_unconfigured(monkeypatch, tmp_path):
    from routes import operator_routes
    from src.operator_command_router import resolve_operator_route, resolve_operator_route_matrix

    monkeypatch.setattr(operator_routes, "COMMANDS_FILE", str(tmp_path / "operator_commands.json"))
    monkeypatch.setattr(operator_routes, "WORKFLOWS_FILE", str(tmp_path / "operator_workflows.json"))
    monkeypatch.setattr(operator_routes, "POLICY_FILE", str(tmp_path / "operator_policy.json"))

    context = operator_routes._operator_route_context("fresh-owner")

    assert context["configured"]["commands"] is False
    assert context["configured"]["workflows"] is False
    assert context["paths"]["commands"] == "data/operator_commands.json"
    assert len(context["commands"]) >= 9
    assert len(context["workflows"]) >= 9

    examples = [
        ("Summarize today.", "summarize-today", False),
        ("Check containers and fix anything unhealthy.", "request-container-fix", True),
        ("Open my code workspace and run the tests.", "run-tests", False),
        ("Train a small model on this dataset.", "open-training-run-plan", False),
        ("Watch this repo until the build passes.", "request-build-watch-loop", True),
        ("Create a task from this note.", "draft-task-from-note", False),
        ("Search my local documents for this.", "search-local-documents", False),
        ("Explain what changed since yesterday.", "explain-changes-since-yesterday", False),
        ("Prepare a backup and verify it.", "prepare-backup", False),
    ]
    for phrase, expected_id, approval_required in examples:
        result = resolve_operator_route(
            phrase,
            context["commands"],
            context["workflows"],
            context["policy"],
            limit=3,
        )
        assert result["selected"]["id"] == expected_id
        assert result["selected"]["approval_required"] is approval_required

    matrix = resolve_operator_route_matrix(context["commands"], context["workflows"], context["policy"])
    rows = {row["phrase"]: row for row in matrix["rows"]}
    assert matrix["summary"]["total"] >= 9
    for phrase, expected_id, approval_required in examples:
        row = rows[phrase]
        assert row["selected_id"] == expected_id
        assert row["route_ready"] is True
        if approval_required:
            assert row["approval_ready"] is True
            assert row["approval_mode"] == "ask"


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
    loops = [
        {
            "id": "summarize-today-loop",
            "title": "Summarize Today",
            "category": "Briefing",
            "mode": "Manual",
            "goal": "Prepare the local daily snapshot",
            "actionIds": ["summarize-today"],
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
        lambda: {"version": 1, "owners": {"alice": {"loops": loops, "workflows": workflows}}},
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
        "run_operator_research_plan",
        lambda owner: {
            "mode": "read-only-research-operations-plan",
            "owner": owner,
            "summary": {"runs_search": False, "starts_research": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_gallery_plan",
        lambda owner: {
            "mode": "read-only-gallery-media-plan",
            "owner": owner,
            "summary": {"uploads_files": False, "generates_images": False, "uses_network": False},
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
        "run_operator_data_plan",
        lambda owner: {
            "mode": "read-only-local-data-map-plan",
            "owner": owner,
            "summary": {"writes_files": False, "deletes_files": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_workspace_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-local-workspace-plan",
            "owner": owner,
            "summary": {
                "runs_tests": False,
                "starts_build_watch": False,
                "runs_search": False,
                "starts_research": False,
                "uploads_files": False,
                "generates_images": False,
                "writes_files": False,
                "deletes_files": False,
                "runs_shell": False,
                "uses_network": False,
            },
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
        "run_operator_recovery_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-recovery-plan",
            "owner": owner,
            "summary": {"retries_commands": False, "restores_data": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_services_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-local-services-plan",
            "owner": owner,
            "summary": {"restarts_services": False, "runs_docker": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_checks",
        lambda: {
            "mode": "read-only-operator-checks",
            "summary": {"runs_docker": False, "uses_network": False},
            "container_plan": {"summary": {"checks_container_alert_count": 0}},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_service_snapshot",
        lambda: {
            "mode": "read-only-operator-service-snapshot",
            "summary": {"service_snapshot_alert_count": 0},
            "services": [],
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_repair_plan",
        lambda: {
            "mode": "read-only-local-repair-plan",
            "summary": {"restarts_services": False, "runs_docker": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_docker_runtime_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-docker-runtime-plan",
            "owner": owner,
            "summary": {
                "restarts_services": False,
                "starts_services": False,
                "repairs_services": False,
                "builds_images": False,
                "recreates_services": False,
                "pulls_images": False,
                "runs_docker": False,
                "runs_shell": False,
                "writes_files": False,
                "deletes_volumes": False,
                "uses_network": False,
            },
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_credentials_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-credential-posture-plan",
            "owner": owner,
            "summary": {"reads_secrets": False, "writes_credentials": False, "uses_network": False},
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
        "run_operator_tool_access_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-tool-access-plan",
            "owner": owner,
            "summary": {
                "executes_tools": False,
                "runs_shell": False,
                "writes_files": False,
                "changes_settings": False,
                "uses_network": False,
            },
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
        "run_operator_notes_plan",
        lambda owner: {
            "mode": "read-only-notes-operations-plan",
            "owner": owner,
            "summary": {"creates_notes": False, "updates_notes": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_calendar_plan",
        lambda owner: {
            "mode": "read-only-calendar-operations-plan",
            "owner": owner,
            "summary": {"creates_events": False, "syncs_calendars": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_tasks_plan",
        lambda owner: {
            "mode": "read-only-task-automation-plan",
            "owner": owner,
            "summary": {"creates_tasks": False, "runs_tasks": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_briefing_snapshot",
        lambda owner: {
            "mode": "read-only-local",
            "owner": owner,
            "summary": {"runs_tasks": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_work_ops_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-work-operations-plan",
            "owner": owner,
            "summary": {
                "creates_tasks": False,
                "updates_tasks": False,
                "runs_tasks": False,
                "creates_calendar_events": False,
                "fires_reminders": False,
                "runs_shell": False,
                "uses_network": False,
            },
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
        "run_operator_approval_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-approval-queue-plan",
            "owner": owner,
            "summary": {
                "routes_commands": False,
                "executes_commands": False,
                "approves_commands": False,
                "changes_policy": False,
                "writes_activity": False,
                "uses_network": False,
            },
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_loops_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-agent-loops-plan",
            "owner": owner,
            "summary": {"starts_loops": False, "routes_commands": False, "executes_commands": False},
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
        "run_operator_model_snapshot",
        lambda: {
            "mode": "read-only-operator-model-snapshot",
            "summary": {"starts_models": False, "starts_training": False, "uses_network": False},
        },
    )
    monkeypatch.setattr(
        operator_routes,
        "run_operator_ai_runtime_plan",
        lambda owner, **kwargs: {
            "mode": "read-only-local-ai-runtime-plan",
            "owner": owner,
            "summary": {
                "starts_models": False,
                "starts_training": False,
                "downloads_models": False,
                "starts_services": False,
                "runs_shell": False,
                "uses_network": False,
            },
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
    command_layer_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/command-layer-plan"
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
    research_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/research-plan"
    )
    gallery_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/gallery-plan"
    )
    file_ops_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/file-ops-plan"
    )
    workspace_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/workspace-plan"
    )
    runtime_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/runtime-plan"
    )
    recovery_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/recovery-plan"
    )
    services_plan_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/services-plan"
    )
    docker_runtime_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/docker-runtime-plan"
    )
    credentials_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/credentials-plan"
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
    tool_access_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/tool-access-plan"
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
    notes_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/notes-plan"
    )
    calendar_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/calendar-plan"
    )
    tasks_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/tasks-plan"
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
    automation_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/automation-plan"
    )
    approval_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/approval-plan"
    )
    loops_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/loops-plan"
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
    ai_runtime_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/ai-runtime-plan"
    )
    workday_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/workday-plan"
    )
    work_ops_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/operator/work-ops-plan"
    )
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(state=SimpleNamespace(personal_docs_manager=None, rag_manager=None)),
    )

    routed = route_endpoint(request, {"text": "Cleverly, summarize today."})
    matrix = matrix_endpoint(request)
    command_layer_plan = command_layer_endpoint(request)
    brief = change_endpoint(request)
    backup = backup_endpoint(request)
    activity_plan = activity_endpoint(request)
    code_plan = code_endpoint(request)
    build_plan = build_endpoint(request)
    document_search_plan = document_search_endpoint(request)
    research_plan = research_endpoint(request)
    gallery_plan = gallery_endpoint(request)
    file_ops_plan = file_ops_endpoint(request)
    workspace_plan = workspace_endpoint(request)
    runtime_plan = runtime_endpoint(request)
    recovery_plan = recovery_endpoint(request)
    services_plan = services_plan_endpoint(request)
    docker_runtime_plan = docker_runtime_endpoint(request)
    credentials_plan = credentials_endpoint(request)
    console_plan = console_endpoint(request)
    toolchain_plan = toolchain_endpoint(request)
    tool_access_plan = tool_access_endpoint(request)
    safety_plan = safety_endpoint(request)
    goal_plan = goal_endpoint(request)
    experience_plan = experience_endpoint(request)
    notes_plan = notes_endpoint(request)
    calendar_plan = calendar_endpoint(request)
    tasks_plan = tasks_endpoint(request)
    training_plan = training_endpoint(request)
    voice_plan = voice_endpoint(request)
    autonomy_plan = autonomy_endpoint(request)
    automation_plan = automation_endpoint(request)
    approval_plan = approval_endpoint(request)
    loops_plan = loops_endpoint(request)
    memory_plan = memory_endpoint(request)
    model_ops_plan = model_ops_endpoint(request)
    ai_runtime_plan = ai_runtime_endpoint(request)
    workday_plan = workday_endpoint(request)
    work_ops_plan = work_ops_endpoint(request)

    assert routed["ok"] is True
    assert routed["selected"]["id"] == "summarize-today"
    assert routed["selected"]["approval_required"] is False
    assert routed["paths"]["commands"] == "data/operator_commands.json"
    assert matrix["summary"]["ready"] == 1
    assert matrix["rows"][0]["selected_id"] == "summarize-today"
    assert command_layer_plan["ok"] is True
    assert command_layer_plan["owner"] == "alice"
    assert command_layer_plan["summary"]["command_count"] == 1
    assert command_layer_plan["summary"]["route_match_ready_count"] == 1
    assert command_layer_plan["summary"]["routes_commands"] is False
    assert command_layer_plan["summary"]["executes_commands"] is False
    assert command_layer_plan["summary"]["starts_workflows"] is False
    assert command_layer_plan["summary"]["writes_activity"] is False
    assert command_layer_plan["summary"]["uses_network"] is False
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
    assert research_plan["ok"] is True
    assert research_plan["owner"] == "alice"
    assert research_plan["summary"]["runs_search"] is False
    assert research_plan["summary"]["starts_research"] is False
    assert gallery_plan["ok"] is True
    assert gallery_plan["owner"] == "alice"
    assert gallery_plan["summary"]["uploads_files"] is False
    assert gallery_plan["summary"]["generates_images"] is False
    assert file_ops_plan["ok"] is True
    assert file_ops_plan["owner"] == "alice"
    assert file_ops_plan["summary"]["writes_files"] is False
    assert file_ops_plan["summary"]["deletes_files"] is False
    assert workspace_plan["ok"] is True
    assert workspace_plan["owner"] == "alice"
    assert workspace_plan["summary"]["runs_tests"] is False
    assert workspace_plan["summary"]["starts_build_watch"] is False
    assert workspace_plan["summary"]["runs_search"] is False
    assert workspace_plan["summary"]["starts_research"] is False
    assert workspace_plan["summary"]["uploads_files"] is False
    assert workspace_plan["summary"]["generates_images"] is False
    assert workspace_plan["summary"]["writes_files"] is False
    assert workspace_plan["summary"]["deletes_files"] is False
    assert workspace_plan["summary"]["runs_shell"] is False
    assert workspace_plan["summary"]["uses_network"] is False
    assert runtime_plan["ok"] is True
    assert runtime_plan["owner"] == "alice"
    assert runtime_plan["summary"]["starts_jobs"] is False
    assert runtime_plan["summary"]["runs_shell"] is False
    assert recovery_plan["ok"] is True
    assert recovery_plan["owner"] == "alice"
    assert recovery_plan["summary"]["retries_commands"] is False
    assert recovery_plan["summary"]["restores_data"] is False
    assert recovery_plan["summary"]["uses_network"] is False
    assert services_plan["ok"] is True
    assert services_plan["owner"] == "alice"
    assert services_plan["summary"]["restarts_services"] is False
    assert services_plan["summary"]["runs_docker"] is False
    assert services_plan["summary"]["uses_network"] is False
    assert docker_runtime_plan["ok"] is True
    assert docker_runtime_plan["owner"] == "alice"
    assert docker_runtime_plan["summary"]["restarts_services"] is False
    assert docker_runtime_plan["summary"]["starts_services"] is False
    assert docker_runtime_plan["summary"]["repairs_services"] is False
    assert docker_runtime_plan["summary"]["builds_images"] is False
    assert docker_runtime_plan["summary"]["recreates_services"] is False
    assert docker_runtime_plan["summary"]["pulls_images"] is False
    assert docker_runtime_plan["summary"]["runs_docker"] is False
    assert docker_runtime_plan["summary"]["runs_shell"] is False
    assert docker_runtime_plan["summary"]["writes_files"] is False
    assert docker_runtime_plan["summary"]["deletes_volumes"] is False
    assert docker_runtime_plan["summary"]["uses_network"] is False
    assert credentials_plan["ok"] is True
    assert credentials_plan["owner"] == "alice"
    assert credentials_plan["summary"]["reads_secrets"] is False
    assert credentials_plan["summary"]["writes_credentials"] is False
    assert credentials_plan["summary"]["uses_network"] is False
    assert console_plan["ok"] is True
    assert console_plan["owner"] == "alice"
    assert console_plan["summary"]["executes_commands"] is False
    assert console_plan["summary"]["starts_jobs"] is False
    assert toolchain_plan["ok"] is True
    assert toolchain_plan["owner"] == "alice"
    assert toolchain_plan["summary"]["executes_commands"] is False
    assert toolchain_plan["summary"]["starts_jobs"] is False
    assert tool_access_plan["ok"] is True
    assert tool_access_plan["owner"] == "alice"
    assert tool_access_plan["summary"]["executes_tools"] is False
    assert tool_access_plan["summary"]["runs_shell"] is False
    assert tool_access_plan["summary"]["writes_files"] is False
    assert tool_access_plan["summary"]["changes_settings"] is False
    assert tool_access_plan["summary"]["uses_network"] is False
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
    assert notes_plan["ok"] is True
    assert notes_plan["owner"] == "alice"
    assert notes_plan["summary"]["creates_notes"] is False
    assert notes_plan["summary"]["updates_notes"] is False
    assert calendar_plan["ok"] is True
    assert calendar_plan["owner"] == "alice"
    assert calendar_plan["summary"]["creates_events"] is False
    assert calendar_plan["summary"]["syncs_calendars"] is False
    assert tasks_plan["ok"] is True
    assert tasks_plan["owner"] == "alice"
    assert tasks_plan["summary"]["creates_tasks"] is False
    assert tasks_plan["summary"]["runs_tasks"] is False
    assert work_ops_plan["ok"] is True
    assert work_ops_plan["owner"] == "alice"
    assert work_ops_plan["summary"]["creates_tasks"] is False
    assert work_ops_plan["summary"]["updates_tasks"] is False
    assert work_ops_plan["summary"]["runs_tasks"] is False
    assert work_ops_plan["summary"]["creates_calendar_events"] is False
    assert work_ops_plan["summary"]["fires_reminders"] is False
    assert work_ops_plan["summary"]["runs_shell"] is False
    assert work_ops_plan["summary"]["uses_network"] is False
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
    assert automation_plan["ok"] is True
    assert automation_plan["owner"] == "alice"
    assert automation_plan["summary"]["starts_automation"] is False
    assert automation_plan["summary"]["starts_loops"] is False
    assert automation_plan["summary"]["runs_tasks"] is False
    assert automation_plan["summary"]["routes_commands"] is False
    assert automation_plan["summary"]["executes_commands"] is False
    assert automation_plan["summary"]["approves_commands"] is False
    assert automation_plan["summary"]["writes_activity"] is False
    assert automation_plan["summary"]["uses_network"] is False
    assert approval_plan["ok"] is True
    assert approval_plan["owner"] == "alice"
    assert approval_plan["summary"]["routes_commands"] is False
    assert approval_plan["summary"]["executes_commands"] is False
    assert approval_plan["summary"]["approves_commands"] is False
    assert approval_plan["summary"]["changes_policy"] is False
    assert approval_plan["summary"]["writes_activity"] is False
    assert approval_plan["summary"]["uses_network"] is False
    assert loops_plan["ok"] is True
    assert loops_plan["owner"] == "alice"
    assert loops_plan["summary"]["starts_loops"] is False
    assert loops_plan["summary"]["routes_commands"] is False
    assert loops_plan["summary"]["executes_commands"] is False
    assert memory_plan["ok"] is True
    assert memory_plan["owner"] == "alice"
    assert memory_plan["summary"]["writes_memories"] is False
    assert memory_plan["summary"]["adds_memories"] is False
    assert model_ops_plan["ok"] is True
    assert model_ops_plan["owner"] == "alice"
    assert model_ops_plan["summary"]["sets_primary_model"] is False
    assert model_ops_plan["summary"]["starts_serving"] is False
    assert ai_runtime_plan["ok"] is True
    assert ai_runtime_plan["owner"] == "alice"
    assert ai_runtime_plan["summary"]["starts_models"] is False
    assert ai_runtime_plan["summary"]["starts_training"] is False
    assert ai_runtime_plan["summary"]["downloads_models"] is False
    assert ai_runtime_plan["summary"]["starts_services"] is False
    assert ai_runtime_plan["summary"]["runs_shell"] is False
    assert ai_runtime_plan["summary"]["uses_network"] is False
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
