import shutil
import zipfile
from pathlib import Path

import pytest

from src import code_workspace as cw
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


def test_code_workspace_is_wired_as_admin_only_offline_tool():
    root = Path(__file__).resolve().parents[1]
    assert "code_workspace" in (root / "src" / "tool_security.py").read_text(encoding="utf-8")
    assert "\"code_workspace\"" in (root / "src" / "tool_execution.py").read_text(encoding="utf-8")
    assert "\"code_workspace\"" in (root / "src" / "tool_schemas.py").read_text(encoding="utf-8")
    assert "Depends(require_admin)" in (root / "routes" / "code_workspace_routes.py").read_text(encoding="utf-8")
    assert "/api/code-workspaces" in (root / "app.py").read_text(encoding="utf-8")
    assert "tool-code-workspace-btn" in (root / "static" / "index.html").read_text(encoding="utf-8")
    assert "code_workspace_model_key" in (root / "static" / "js" / "codeWorkspace.js").read_text(encoding="utf-8")
