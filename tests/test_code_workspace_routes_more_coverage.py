import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import code_workspace_routes as routes


def _endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"missing route {method} {path}")


def _request(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def test_code_workspace_route_success_wrappers(monkeypatch, tmp_path):
    audits = []
    monkeypatch.setattr(routes, "append_audit", lambda event, payload, user=None: audits.append((event, payload, user)))
    cw = routes.code_workspace

    monkeypatch.setattr(cw, "list_workspaces", lambda owner=None: [{"owner": owner}])
    monkeypatch.setattr(cw, "create_workspace", lambda name, owner=None: {"id": "ws1", "name": name, "owner": owner})
    monkeypatch.setattr(cw, "delete_workspace", lambda workspace_id, owner=None: {"deleted": workspace_id, "owner": owner})
    monkeypatch.setattr(cw, "list_tree", lambda workspace_id, path="", owner=None: {"workspace_id": workspace_id, "path": path, "items": []})
    monkeypatch.setattr(cw, "read_file", lambda workspace_id, path, owner=None: {"workspace_id": workspace_id, "path": path, "content": "text"})
    monkeypatch.setattr(cw, "write_file", lambda workspace_id, path, content, owner=None, allowed_paths=None: {"workspace_id": workspace_id, "path": path, "bytes": len(content), "allowed": allowed_paths})
    monkeypatch.setattr(cw, "apply_unified_diff", lambda workspace_id, diff, owner=None, allowed_paths=None: {"workspace_id": workspace_id, "exit_code": 0, "diff": diff})
    monkeypatch.setattr(cw, "run_command", lambda workspace_id, command, owner=None, timeout_seconds=None: {"workspace_id": workspace_id, "command": command, "exit_code": 0, "timeout": timeout_seconds})
    monkeypatch.setattr(cw, "git_status", lambda workspace_id, owner=None: {"workspace_id": workspace_id, "clean": True})
    monkeypatch.setattr(cw, "git_diff", lambda workspace_id, owner=None, staged=False: {"workspace_id": workspace_id, "staged": staged, "diff": ""})
    monkeypatch.setattr(cw, "git_commit", lambda workspace_id, message, owner=None: {"workspace_id": workspace_id, "message": message})
    monkeypatch.setattr(cw, "list_snapshots", lambda workspace_id, owner=None: [{"id": "snap1", "workspace_id": workspace_id}])
    monkeypatch.setattr(cw, "create_snapshot", lambda workspace_id, label, owner=None: {"id": "snap1", "workspace_id": workspace_id, "label": label})
    monkeypatch.setattr(cw, "restore_snapshot", lambda workspace_id, snapshot_id, owner=None: {"workspace_id": workspace_id, "restored": snapshot_id})
    monkeypatch.setattr(cw, "diff_snapshot", lambda workspace_id, snapshot_id, owner=None: {"workspace_id": workspace_id, "snapshot_id": snapshot_id, "diff": "snapshot diff"})
    export_file = tmp_path / "repo.zip"
    export_file.write_bytes(b"zip")
    monkeypatch.setattr(cw, "export_workspace", lambda workspace_id, owner=None: {"path": str(export_file), "filename": "repo.zip", "size": 3})

    router = routes.setup_code_workspace_routes()
    req = _request()

    assert _endpoint(router, "/api/code-workspaces", "GET")(req)["workspaces"] == [{"owner": "alice"}]
    assert _endpoint(router, "/api/code-workspaces", "POST")(routes.CreateWorkspaceRequest(name="Repo"), req)["workspace"]["owner"] == "alice"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}", "DELETE")("ws1", req)["deleted"] == "ws1"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/tree", "GET")("ws1", req, path="src")["path"] == "src"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/file", "GET")("ws1", req, path="app.py")["content"] == "text"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/file", "PUT")(
        "ws1",
        routes.FileWriteRequest(path="app.py", content="hello", allowed_paths=["app.py"]),
        req,
    )["allowed"] == ["app.py"]
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/patch", "POST")(
        "ws1",
        routes.PatchRequest(diff="diff", allowed_paths=["app.py"]),
        req,
    )["exit_code"] == 0
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/run", "POST")(
        "ws1",
        routes.RunRequest(command="pytest -q", timeout_seconds=5),
        req,
    )["timeout"] == 5
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/status", "GET")("ws1", req)["clean"] is True
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/diff", "GET")("ws1", req, staged=True)["staged"] is True
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/commit", "POST")(
        "ws1",
        routes.CommitRequest(message="save"),
        req,
    )["message"] == "save"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/snapshots", "GET")("ws1", req)["snapshots"][0]["id"] == "snap1"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/snapshots", "POST")(
        "ws1",
        routes.SnapshotRequest(label="manual"),
        req,
    )["snapshot"]["label"] == "manual"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/snapshots/{snapshot_id}/restore", "POST")("ws1", "snap1", req)["restored"] == "snap1"
    assert _endpoint(router, "/api/code-workspaces/{workspace_id}/snapshots/{snapshot_id}/diff", "GET")("ws1", "snap1", req)["diff"] == "snapshot diff"
    response = _endpoint(router, "/api/code-workspaces/{workspace_id}/export", "GET")("ws1", req)
    assert response.filename == "repo.zip"

    assert [event for event, _payload, _user in audits] == [
        "code_workspace_patch_applied",
        "code_workspace_snapshot_restored",
        "code_workspace_exported",
    ]


def test_code_workspace_import_validate_and_agent_routes(monkeypatch):
    audits = []
    monkeypatch.setattr(routes, "append_audit", lambda event, payload, user=None: audits.append((event, payload, user)))
    cw = routes.code_workspace
    router = routes.setup_code_workspace_routes()
    req = _request("bob")

    class Upload:
        filename = "repo.tar.gz"

        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

    monkeypatch.setattr(cw, "import_archive", lambda name, path, owner=None: {"id": "imported", "name": name, "owner": owner, "exists": path.exists()})
    imported = asyncio.run(_endpoint(router, "/api/code-workspaces/import", "POST")(req, name="", file=Upload([b"abc"])))
    assert imported["workspace"]["name"] == "repo.tar"
    assert imported["workspace"]["owner"] == "bob"

    snapshot = {"id": "snap-validate"}
    restore_calls = []
    monkeypatch.setattr(cw, "create_snapshot", lambda workspace_id, label, owner=None: snapshot)
    monkeypatch.setattr(cw, "restore_snapshot", lambda workspace_id, snapshot_id, owner=None: restore_calls.append(snapshot_id) or {"restored": snapshot_id})
    monkeypatch.setattr(cw, "apply_unified_diff", lambda workspace_id, diff, owner=None, allowed_paths=None: {"exit_code": 1})
    invalid = _endpoint(router, "/api/code-workspaces/{workspace_id}/validate-diff", "POST")(
        "ws1",
        routes.ValidateDiffRequest(diff="bad", test_command="pytest -q"),
        req,
    )
    assert invalid["valid"] is False
    assert invalid["test"] is None
    assert restore_calls == ["snap-validate"]

    monkeypatch.setattr(cw, "apply_unified_diff", lambda workspace_id, diff, owner=None, allowed_paths=None: {"exit_code": 0})
    monkeypatch.setattr(cw, "run_command", lambda workspace_id, command, owner=None, timeout_seconds=None: {"exit_code": 0, "command": command})
    valid = _endpoint(router, "/api/code-workspaces/{workspace_id}/validate-diff", "POST")(
        "ws1",
        routes.ValidateDiffRequest(diff="good", test_command="pytest -q", allowed_paths=["src"]),
        req,
    )
    assert valid["valid"] is True

    async def fake_agent(*_args, **kwargs):
        return {"model": kwargs["model_key"], "selected_paths": kwargs["selected_paths"], "proposed_diff": "diff", "applied": kwargs["apply_changes"]}

    monkeypatch.setattr(routes.code_workspace_agent, "run_agent", fake_agent)
    agent = asyncio.run(_endpoint(router, "/api/code-workspaces/{workspace_id}/agent", "POST")(
        "ws1",
        routes.AgentRequest(task="change it", model_key="GLM-5.2", selected_paths=["app.py"], apply_changes=True),
        req,
    ))
    assert agent["applied"] is True
    assert agent["model"] == "GLM-5.2"
    assert "code_workspace_agent_draft" in [event for event, _payload, _user in audits]


def test_code_workspace_routes_translate_workspace_errors_and_import_limits(monkeypatch):
    cw = routes.code_workspace
    router = routes.setup_code_workspace_routes()
    req = _request()

    def fail(*_args, **_kwargs):
        raise cw.CodeWorkspaceError("not allowed")

    monkeypatch.setattr(cw, "create_workspace", fail)
    with pytest.raises(HTTPException) as exc:
        _endpoint(router, "/api/code-workspaces", "POST")(routes.CreateWorkspaceRequest(name="Bad"), req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "not allowed"

    class BigUpload:
        filename = "repo.zip"

        async def read(self, _size):
            return b"x" * (cw.MAX_ARCHIVE_BYTES + 1)

    with pytest.raises(HTTPException) as too_big:
        asyncio.run(_endpoint(router, "/api/code-workspaces/import", "POST")(req, name="Big", file=BigUpload()))
    assert too_big.value.status_code == 400
    assert "Archive is too large" in too_big.value.detail


def test_code_workspace_import_ignores_temp_cleanup_failure(monkeypatch):
    cw = routes.code_workspace
    router = routes.setup_code_workspace_routes()
    req = _request()

    class Upload:
        filename = ""

        async def read(self, _size):
            return b""

    class TempPath:
        def __init__(self, name):
            self.name = name

        def unlink(self, missing_ok=True):
            raise RuntimeError("cleanup failed")

        def exists(self):
            return True

    class TempFile:
        name = "temp.zip"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, _chunk):
            return None

    monkeypatch.setattr(routes.tempfile, "NamedTemporaryFile", lambda delete=False, suffix=".zip": TempFile())
    monkeypatch.setattr(routes, "Path", lambda value: TempPath(value) if value == "temp.zip" else SimpleNamespace(suffix="", stem="Workspace"))
    monkeypatch.setattr(cw, "import_archive", lambda name, path, owner=None: {"id": "ws", "name": name, "path_exists": path.exists()})
    monkeypatch.setattr(routes, "append_audit", lambda *_args, **_kwargs: None)

    result = asyncio.run(_endpoint(router, "/api/code-workspaces/import", "POST")(req, name="Named", file=Upload()))
    assert result["workspace"]["name"] == "Named"


def test_code_workspace_validate_diff_restore_failure_is_ignored(monkeypatch):
    cw = routes.code_workspace
    router = routes.setup_code_workspace_routes()
    req = _request()

    monkeypatch.setattr(cw, "create_snapshot", lambda *_args, **_kwargs: {"id": "snap"})
    monkeypatch.setattr(cw, "apply_unified_diff", lambda *_args, **_kwargs: {"exit_code": 0})
    monkeypatch.setattr(cw, "run_command", lambda *_args, **_kwargs: {"exit_code": 0})
    monkeypatch.setattr(cw, "restore_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("restore failed")))
    monkeypatch.setattr(routes, "append_audit", lambda *_args, **_kwargs: None)

    result = _endpoint(router, "/api/code-workspaces/{workspace_id}/validate-diff", "POST")(
        "ws1",
        routes.ValidateDiffRequest(diff="diff", test_command="pytest -q"),
        req,
    )
    assert result["valid"] is True


def test_code_workspace_route_error_translation_for_remaining_endpoints(monkeypatch):
    cw = routes.code_workspace
    router = routes.setup_code_workspace_routes()
    req = _request()

    def fail(*_args, **_kwargs):
        raise cw.CodeWorkspaceError("boom")

    cases = [
        ("delete_workspace", "/api/code-workspaces/{workspace_id}", "DELETE", lambda endpoint: endpoint("ws1", req)),
        ("list_tree", "/api/code-workspaces/{workspace_id}/tree", "GET", lambda endpoint: endpoint("ws1", req, path="src")),
        ("read_file", "/api/code-workspaces/{workspace_id}/file", "GET", lambda endpoint: endpoint("ws1", req, path="a.py")),
        ("write_file", "/api/code-workspaces/{workspace_id}/file", "PUT", lambda endpoint: endpoint("ws1", routes.FileWriteRequest(path="a.py", content="x"), req)),
        ("apply_unified_diff", "/api/code-workspaces/{workspace_id}/patch", "POST", lambda endpoint: endpoint("ws1", routes.PatchRequest(diff="diff"), req)),
        ("run_command", "/api/code-workspaces/{workspace_id}/run", "POST", lambda endpoint: endpoint("ws1", routes.RunRequest(command="pytest"), req)),
        ("git_status", "/api/code-workspaces/{workspace_id}/status", "GET", lambda endpoint: endpoint("ws1", req)),
        ("git_diff", "/api/code-workspaces/{workspace_id}/diff", "GET", lambda endpoint: endpoint("ws1", req, staged=False)),
        ("git_commit", "/api/code-workspaces/{workspace_id}/commit", "POST", lambda endpoint: endpoint("ws1", routes.CommitRequest(message="save"), req)),
        ("list_snapshots", "/api/code-workspaces/{workspace_id}/snapshots", "GET", lambda endpoint: endpoint("ws1", req)),
        ("create_snapshot", "/api/code-workspaces/{workspace_id}/snapshots", "POST", lambda endpoint: endpoint("ws1", routes.SnapshotRequest(label="snap"), req)),
        ("restore_snapshot", "/api/code-workspaces/{workspace_id}/snapshots/{snapshot_id}/restore", "POST", lambda endpoint: endpoint("ws1", "snap", req)),
        ("diff_snapshot", "/api/code-workspaces/{workspace_id}/snapshots/{snapshot_id}/diff", "GET", lambda endpoint: endpoint("ws1", "snap", req)),
        ("export_workspace", "/api/code-workspaces/{workspace_id}/export", "GET", lambda endpoint: endpoint("ws1", req)),
    ]

    monkeypatch.setattr(routes, "append_audit", lambda *_args, **_kwargs: None)
    for attr, path, method, caller in cases:
        monkeypatch.setattr(cw, attr, fail)
        with pytest.raises(HTTPException) as exc:
            caller(_endpoint(router, path, method))
        assert exc.value.status_code == 400
        assert exc.value.detail == "boom"


def test_code_workspace_validate_and_agent_errors_translate(monkeypatch):
    cw = routes.code_workspace
    router = routes.setup_code_workspace_routes()
    req = _request()

    monkeypatch.setattr(cw, "create_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(cw.CodeWorkspaceError("validate failed")))
    with pytest.raises(HTTPException) as validate_exc:
        _endpoint(router, "/api/code-workspaces/{workspace_id}/validate-diff", "POST")(
            "ws1",
            routes.ValidateDiffRequest(diff="diff", test_command="pytest"),
            req,
        )
    assert validate_exc.value.detail == "validate failed"

    async def fail_agent(*_args, **_kwargs):
        raise cw.CodeWorkspaceError("agent failed")

    monkeypatch.setattr(routes.code_workspace_agent, "run_agent", fail_agent)
    with pytest.raises(HTTPException) as agent_exc:
        asyncio.run(_endpoint(router, "/api/code-workspaces/{workspace_id}/agent", "POST")(
            "ws1",
            routes.AgentRequest(task="change"),
            req,
        ))
    assert agent_exc.value.detail == "agent failed"
