import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from routes.auth_routes import SESSION_COOKIE, setup_auth_routes
from routes.code_workspace_routes import setup_code_workspace_routes
from routes.operator_routes import setup_operator_routes
from src import code_workspace as cw
from src import code_workspace_agent, code_workspace_worker


class _TestAuthManager:
    signup_enabled = False
    is_configured = True
    users = {"admin": {"is_admin": True}}

    @staticmethod
    def verify_password(username, password):
        return username == "admin" and password == "password123"

    @staticmethod
    def totp_enabled(_username):
        return False

    @staticmethod
    def totp_verify(_username, _code):
        return True

    @staticmethod
    def create_session(username, password):
        if username == "admin" and password == "password123":
            return "test-session-token"
        return None

    @staticmethod
    def validate_token(token):
        return token == "test-session-token"

    @staticmethod
    def get_username_for_token(token):
        return "admin" if token == "test-session-token" else None

    @staticmethod
    def is_admin(username):
        return username == "admin"

    @staticmethod
    def status(token):
        user = "admin" if token == "test-session-token" else None
        return {
            "configured": True,
            "authenticated": bool(user),
            "username": user,
            "is_admin": user == "admin",
        }

    @staticmethod
    def get_privileges(_username):
        return {}


def _build_authed_app(auth_manager) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = auth_manager

    @app.middleware("http")
    async def auth_cookie_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/auth/"):
            return await call_next(request)
        token = request.cookies.get(SESSION_COOKIE)
        if not auth_manager.validate_token(token):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        request.state.current_user = auth_manager.get_username_for_token(token)
        return await call_next(request)

    app.include_router(setup_auth_routes(auth_manager))
    app.include_router(setup_code_workspace_routes())
    app.include_router(setup_operator_routes())
    return app


def test_authenticated_code_workspace_review_flow_and_worker_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("CODE_WORKSPACE_DIR", str(tmp_path / "code-workspaces"))
    monkeypatch.setenv("CODE_WORKSPACE_RUNNER", "worker")
    monkeypatch.setenv("CODE_WORKSPACE_WORKER_DIR", str(tmp_path / "code-workspaces" / ".worker"))
    monkeypatch.setattr(code_workspace_agent, "resolve_model_key", lambda *_args, **_kwargs: ("http://ollama:11434/v1/chat/completions", "GLM-5.2", {}))

    diff = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-old
+new
"""

    async def fake_llm_call(*_args, **_kwargs):
        return diff

    monkeypatch.setattr(code_workspace_agent, "llm_call_async", fake_llm_call)

    auth = _TestAuthManager()
    app = _build_authed_app(auth)
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    assert login.status_code == 200

    create = client.post("/api/code-workspaces", json={"name": "E2E Repo"})
    assert create.status_code == 200
    workspace_id = create.json()["workspace"]["id"]

    write = client.put(
        f"/api/code-workspaces/{workspace_id}/file",
        json={"path": "app.txt", "content": "old\n"},
    )
    assert write.status_code == 200

    draft = client.post(
        f"/api/code-workspaces/{workspace_id}/agent",
        json={
            "task": "Change old to new",
            "model_key": "GLM-5.2",
            "selected_paths": ["app.txt"],
            "apply_changes": False,
        },
    )
    assert draft.status_code == 200
    draft_data = draft.json()
    assert draft_data["applied"] is False
    assert "proposed_diff" in draft_data

    unchanged = client.get(f"/api/code-workspaces/{workspace_id}/file", params={"path": "app.txt"})
    assert unchanged.json()["content"] == "old\n"

    snapshot = client.post(f"/api/code-workspaces/{workspace_id}/snapshots", json={"label": "Before review apply"})
    assert snapshot.status_code == 200

    apply = client.post(f"/api/code-workspaces/{workspace_id}/patch", json={"diff": draft_data["proposed_diff"]})
    assert apply.status_code == 200

    changed = client.get(f"/api/code-workspaces/{workspace_id}/file", params={"path": "app.txt"})
    assert changed.json()["content"] == "new\n"

    def worker_once():
        queue = cw._worker_root(Path(tmp_path / "code-workspaces"))
        deadline = time.time() + 5
        while time.time() < deadline:
            job = code_workspace_worker._claim_job(queue)
            if job:
                code_workspace_worker._run_job(queue, job)
                return
            time.sleep(0.05)

    thread = threading.Thread(target=worker_once)
    thread.start()
    run = client.post(
        f"/api/code-workspaces/{workspace_id}/run",
        json={"command": 'python -c "print(123)"', "timeout_seconds": 10},
    )
    thread.join(timeout=5)
    assert run.status_code == 200
    assert run.json()["runner"] == "worker"

    blocked = client.post(
        f"/api/code-workspaces/{workspace_id}/run",
        json={"command": "curl https://example.com", "timeout_seconds": 5},
    )
    assert blocked.status_code == 400

    export = client.get(f"/api/code-workspaces/{workspace_id}/export")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/zip")

    checks = client.get("/api/operator/checks")
    assert checks.status_code == 200
    check_ids = {item["id"]: item for item in checks.json()["checks"]}
    assert check_ids["offline-mode"]["status"] == "ok"
    assert check_ids["code-worker"]["status"] in {"ok", "warn"}
