import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest

from src import code_workspace as cw
from src import code_workspace_worker
from src.settings import DEFAULT_SETTINGS


GIT_AVAILABLE = shutil.which("git") is not None


def test_model_key_must_be_explicitly_set():
    assert DEFAULT_SETTINGS["code_workspace_model_key"] == ""


def test_code_workspace_round_trip_file_operations(tmp_path):
    meta = cw.create_workspace("Demo Repo", owner="alice", root=tmp_path)

    written = cw.write_file(
        meta["id"],
        "src/app.py",
        "print('hi')\n",
        owner="alice",
        root=tmp_path,
    )
    assert written["exit_code"] == 0
    assert written["path"] == "src/app.py"

    loaded = cw.read_file(meta["id"], "src/app.py", owner="alice", root=tmp_path)
    assert loaded["content"] == "print('hi')\n"

    tree = cw.list_tree(meta["id"], "", owner="alice", root=tmp_path)
    assert {"name": "src", "path": "src", "type": "dir", "size": (tmp_path / meta["id"] / "src").stat().st_size} in tree["entries"]


@pytest.mark.parametrize("bad_path", [
    "../outside.txt",
    "/absolute.txt",
    r"C:\absolute.txt",
    ".git/config",
    "src/.git/config",
])
def test_code_workspace_rejects_unsafe_paths(tmp_path, bad_path):
    meta = cw.create_workspace("Unsafe Paths", owner="alice", root=tmp_path)

    with pytest.raises(cw.CodeWorkspaceError):
        cw.write_file(meta["id"], bad_path, "x", owner="alice", root=tmp_path)


@pytest.mark.parametrize("command", [
    "curl https://example.com",
    "git pull",
    "python -m pip install requests",
    "npm install",
    "docker ps",
])
def test_code_workspace_blocks_network_or_host_commands(tmp_path, command):
    meta = cw.create_workspace("Blocked Commands", owner="alice", root=tmp_path)

    with pytest.raises(cw.CodeWorkspaceError, match="blocked"):
        cw.run_command(meta["id"], command, owner="alice", root=tmp_path)


@pytest.mark.skipif(not GIT_AVAILABLE, reason="git is required for patch application")
def test_code_workspace_applies_unified_diff(tmp_path):
    meta = cw.create_workspace("Patch Demo", owner="alice", root=tmp_path)
    cw.write_file(meta["id"], "app.txt", "old\n", owner="alice", root=tmp_path)

    diff = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-old
+new
"""
    result = cw.apply_unified_diff(meta["id"], diff, owner="alice", root=tmp_path)

    assert result["exit_code"] == 0
    assert cw.read_file(meta["id"], "app.txt", owner="alice", root=tmp_path)["content"] == "new\n"


def test_code_workspace_imports_archive_without_network(tmp_path):
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("repo/src/main.py", "print('local')\n")

    meta = cw.import_archive("Imported Repo", archive, owner="alice", root=tmp_path)

    loaded = cw.read_file(meta["id"], "src/main.py", owner="alice", root=tmp_path)
    assert loaded["content"] == "print('local')\n"


@pytest.mark.parametrize("member", [
    "../escape.txt",
    "/absolute.txt",
    "repo/.git/config",
    "repo/src/.git/config",
])
def test_code_workspace_import_rejects_malicious_archive_members(tmp_path, member):
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, "x")

    with pytest.raises(cw.CodeWorkspaceError):
        cw.import_archive("Malicious Repo", archive, owner="alice", root=tmp_path)


def test_code_workspace_snapshots_restore_and_export(tmp_path):
    meta = cw.create_workspace("Snapshot Demo", owner="alice", root=tmp_path)
    cw.write_file(meta["id"], "app.txt", "one\n", owner="alice", root=tmp_path)
    snap = cw.create_snapshot(meta["id"], "before change", owner="alice", root=tmp_path)

    cw.write_file(meta["id"], "app.txt", "two\n", owner="alice", root=tmp_path)
    diff = cw.diff_snapshot(meta["id"], snap["id"], owner="alice", root=tmp_path)
    assert "app.txt" in diff["stdout"]

    restored = cw.restore_snapshot(meta["id"], snap["id"], owner="alice", root=tmp_path)
    assert restored["exit_code"] == 0
    assert cw.read_file(meta["id"], "app.txt", owner="alice", root=tmp_path)["content"] == "one\n"

    exported = cw.export_workspace(meta["id"], owner="alice", root=tmp_path)
    assert exported["filename"].endswith(".zip")
    assert Path(exported["path"]).exists()


def test_code_workspace_worker_runner_executes_from_queue(tmp_path, monkeypatch):
    meta = cw.create_workspace("Worker Demo", owner="alice", root=tmp_path)
    monkeypatch.setenv("CODE_WORKSPACE_RUNNER", "worker")
    monkeypatch.setenv("CODE_WORKSPACE_WORKER_DIR", str(tmp_path / ".worker"))

    def worker_once():
        queue = cw._worker_root(tmp_path)
        deadline = time.time() + 5
        while time.time() < deadline:
            job = code_workspace_worker._claim_job(queue)
            if job:
                code_workspace_worker._run_job(queue, job)
                return
            time.sleep(0.05)

    thread = threading.Thread(target=worker_once)
    thread.start()
    result = cw.run_command(
        meta["id"],
        'python -c "print(\'offline-worker\')"',
        owner="alice",
        root=tmp_path,
        timeout_seconds=10,
    )
    thread.join(timeout=5)

    assert result["runner"] == "worker"
    assert result["exit_code"] == 0
    assert "offline-worker" in result["stdout"]


def test_code_workspace_is_wired_as_admin_only_offline_tool():
    root = Path(__file__).resolve().parents[1]
    routes = (root / "routes" / "code_workspace_routes.py").read_text(encoding="utf-8")
    ui_js = (root / "static" / "js" / "codeWorkspace.js").read_text(encoding="utf-8")
    agent = (root / "src" / "code_workspace_agent.py").read_text(encoding="utf-8")
    assert "code_workspace" in (root / "src" / "tool_security.py").read_text(encoding="utf-8")
    assert "\"code_workspace\"" in (root / "src" / "tool_execution.py").read_text(encoding="utf-8")
    assert "\"code_workspace\"" in (root / "src" / "tool_schemas.py").read_text(encoding="utf-8")
    assert "Depends(require_admin)" in routes
    assert "/api/code-workspaces" in (root / "app.py").read_text(encoding="utf-8")
    operator_routes = (root / "routes" / "operator_routes.py").read_text(encoding="utf-8")
    assert 'prefix="/api/operator"' in operator_routes
    assert '@router.get("/checks")' in operator_routes
    assert '@router.get("/page"' in operator_routes
    assert "tool-code-workspace-btn" in (root / "static" / "index.html").read_text(encoding="utf-8")
    assert "code_workspace_model_key" in ui_js
    assert "code-ws-agent-run" in ui_js
    assert "code-ws-apply-proposed" in ui_js
    assert "ValidateDiffRequest" in routes
    assert '@router.post("/{workspace_id}/validate-diff")' in routes
    assert "Before proposed diff validation" in routes
    assert "code_workspace_diff_validated" in routes
    assert "validateProposedDiff" in ui_js
    assert "_pendingTestPassed" in ui_js
    assert "_lastRunPassed" in ui_js
    assert "Run Tests must pass on the proposed diff before Apply is enabled" in ui_js
    assert "code-ws-review-gate" in ui_js
    assert "Before manual diff apply" in ui_js
    assert "Manual diff validation failed. No permanent changes were applied." in ui_js
    assert "Commit without a passing local test run" in ui_js
    assert "Resolve the pending proposed diff before committing." in ui_js
    assert "code-ws-bottom-actions" in ui_js
    assert "cleverly-code-workspace-safety" in ui_js
    assert "code-ws-safety-level" in ui_js
    assert "Review Only safety level blocks file writes." in ui_js
    assert "Apply With Tests requires a test command before manual diff apply." in ui_js
    assert "Switch Safety Level to Commit Allowed before committing." in ui_js
    assert "steps: list[dict[str, Any]] = [" in agent
    assert '"phase": "plan"' in agent
    assert '"plan": plan' in agent


def test_code_workspace_worker_is_networkless_in_compose():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    worker = compose.split("  cleverly_code_worker:", 1)[1].split("  cleverly_proxy:", 1)[0]
    app_service = compose.split("  cleverly_code_worker:", 1)[0]
    assert "CODE_WORKSPACE_RUNNER: ${CODE_WORKSPACE_RUNNER:-worker}" in app_service
    assert "network_mode: \"none\"" in worker
    assert "cap_drop:" in worker
    assert "no-new-privileges:true" in worker
    assert "read_only: true" in worker
