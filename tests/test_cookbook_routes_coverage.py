import asyncio
import json
import sys
import time
import types


def _endpoint(router, path, method="GET"):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"endpoint {method} {path} not found")


class RequestLike:
    def __init__(self, payload=None):
        self._payload = payload

    async def json(self):
        return self._payload


def _install_secret_storage(monkeypatch):
    secret_storage = types.ModuleType("src.secret_storage")
    secret_storage.encrypt = lambda value: f"enc:{value}"
    secret_storage.decrypt = lambda value: str(value).removeprefix("enc:")
    monkeypatch.setitem(sys.modules, "src.secret_storage", secret_storage)


def test_cookbook_state_save_get_masks_tokens_preserves_recent_tasks(monkeypatch, tmp_path):
    import routes.cookbook_routes as cookbook_routes

    _install_secret_storage(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    router = cookbook_routes.setup_cookbook_routes()
    save_state = _endpoint(router, "/api/cookbook/state", "POST")
    get_state = _endpoint(router, "/api/cookbook/state")

    first = asyncio.run(
        save_state(
            RequestLike(
                {
                    "env": {
                        "hfToken": "hf_1234567890",
                        "hfTokenMasked": "old",
                        "hfTokenConfigured": False,
                        "servers": [{"name": "gpu-box"}],
                    },
                    "tasks": [
                        {
                            "sessionId": "task-1",
                            "ts": int(time.time() * 1000),
                            "payload": {"hf_token": "secret", "repo": "model"},
                        }
                    ],
                }
            )
        )
    )
    assert first == {"ok": True, "preserved": 0}

    raw_state = json.loads((tmp_path / "cookbook_state.json").read_text(encoding="utf-8"))
    assert raw_state["env"]["hfToken"] == "enc:hf_1234567890"
    assert "hf_token" not in raw_state["tasks"][0]["payload"]

    client_state = asyncio.run(get_state(RequestLike()))
    assert client_state["env"]["hfTokenConfigured"] is True
    assert client_state["env"]["hfTokenMasked"] == "hf_1...7890"
    assert "hfToken" not in client_state["env"]

    second = asyncio.run(save_state(RequestLike({"env": {}, "tasks": []})))
    assert second == {"ok": True, "preserved": 1}
    merged = json.loads((tmp_path / "cookbook_state.json").read_text(encoding="utf-8"))
    assert merged["env"]["servers"] == [{"name": "gpu-box"}]
    assert merged["tasks"][0]["sessionId"] == "task-1"

    (tmp_path / "cookbook_state.json").write_text("{bad-json", encoding="utf-8")
    assert asyncio.run(get_state(RequestLike())) == {}
    assert asyncio.run(save_state(RequestLike(["not", "dict"])))["ok"] is True


def test_cookbook_hf_latest_filters_and_reports_http_errors(monkeypatch):
    import httpx
    import pytest
    import routes.cookbook_routes as cookbook_routes

    class Response:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload or []

        def json(self):
            return self._payload

    class FakeClient:
        response = Response(
            payload=[
                {
                    "modelId": "Team/Good-7B",
                    "tags": ["text-generation", "awq"],
                    "pipeline_tag": "text-generation",
                    "downloads": 10,
                    "likes": 5,
                    "createdAt": "2026-01-01",
                },
                {
                    "modelId": "Team/TooBig-70B",
                    "tags": ["text-generation"],
                    "pipeline_tag": "text-generation",
                },
                {
                    "modelId": "Team/lora-7B",
                    "tags": ["lora"],
                    "pipeline_tag": "text-generation",
                },
                {
                    "modelId": "Team/Other-8B",
                    "tags": ["fp8"],
                    "pipeline_tag": "text-generation",
                },
                {
                    "modelId": "Team/Wrong-8B",
                    "tags": [],
                    "pipeline_tag": "text-classification",
                },
                {"modelId": "Team/NoSize", "tags": [], "pipeline_tag": "text-generation"},
            ]
        )

        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            self.url = url
            return self.response

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(cookbook_routes, "offline_mode", lambda: False)

    router = cookbook_routes.setup_cookbook_routes()
    hf_latest = _endpoint(router, "/api/cookbook/hf-latest")

    filtered = asyncio.run(hf_latest(vram_gb=24, limit=5, pipeline="text-generation", owner="alice"))
    assert [item["repo_id"] for item in filtered["models"]] == ["Team/Good-7B", "Team/Other-8B"]
    assert filtered["models"][0]["needed_vram_gb"] == 4.5
    assert filtered["models"][1]["est_vram_gb"] == 8.0

    FakeClient.response = Response(status_code=503)
    assert asyncio.run(hf_latest(vram_gb=0, limit=1, pipeline="text-generation", owner="alice")) == {
        "models": [],
        "error": "HF API HTTP 503",
    }

    class BrokenClient(FakeClient):
        async def get(self, url):
            raise RuntimeError("offline")

    monkeypatch.setattr(httpx, "AsyncClient", BrokenClient)
    assert asyncio.run(hf_latest(vram_gb=0, limit=1, pipeline="text-generation", owner="alice")) == {
        "models": [],
        "error": "offline",
    }

    monkeypatch.setattr(cookbook_routes, "offline_mode", lambda: True)
    with pytest.raises(cookbook_routes.HTTPException) as offline_exc:
        asyncio.run(hf_latest(vram_gb=0, limit=1, pipeline="text-generation", owner="alice"))
    assert offline_exc.value.status_code == 403


def test_cookbook_remote_cache_and_gpu_probe_blocked_in_offline_mode(monkeypatch, tmp_path):
    import pytest
    import routes.cookbook_routes as cookbook_routes

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "offline_mode", lambda: True)

    router = cookbook_routes.setup_cookbook_routes()
    model_cached = _endpoint(router, "/api/model/cached")
    list_gpus = _endpoint(router, "/api/cookbook/gpus")
    server_setup = _endpoint(router, "/api/cookbook/setup", "POST")
    kill_pid = _endpoint(router, "/api/cookbook/kill-pid", "POST")

    with pytest.raises(cookbook_routes.HTTPException) as cache_exc:
        asyncio.run(model_cached(RequestLike(), host="user@example.test"))
    assert cache_exc.value.status_code == 403
    assert cache_exc.value.detail == "Remote Cookbook servers are disabled in offline mode"

    with pytest.raises(cookbook_routes.HTTPException) as gpu_exc:
        asyncio.run(list_gpus(RequestLike(), host="user@example.test"))
    assert gpu_exc.value.status_code == 403
    assert gpu_exc.value.detail == "Remote Cookbook servers are disabled in offline mode"

    with pytest.raises(cookbook_routes.HTTPException) as setup_exc:
        asyncio.run(server_setup(RequestLike(), types.SimpleNamespace(host="user@example.test", ssh_port=None)))
    assert setup_exc.value.status_code == 403
    assert setup_exc.value.detail == "Remote Cookbook servers are disabled in offline mode"

    with pytest.raises(cookbook_routes.HTTPException) as kill_exc:
        asyncio.run(
            kill_pid(
                RequestLike(),
                types.SimpleNamespace(pid=1234, host="user@example.test", ssh_port=None, signal="TERM"),
            )
        )
    assert kill_exc.value.status_code == 403
    assert kill_exc.value.detail == "Remote Cookbook servers are disabled in offline mode"


def test_cookbook_tasks_status_parses_tmux_output_and_diagnosis(monkeypatch, tmp_path):
    import routes.cookbook_routes as cookbook_routes

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "IS_WINDOWS", False)

    (tmp_path / "cookbook_state.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"sessionId": "bad;session", "type": "serve", "modelId": "Unsafe/Skip"},
                    {
                        "sessionId": "serve_ok",
                        "type": "serve",
                        "modelId": "Team/Good-7B",
                        "payload": {"_cmd": "vllm serve Team/Good-7B"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    class Completed:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(cmd, timeout=None, capture_output=None, text=False):
        if "capture-pane" in cmd:
            return Completed(
                0,
                "Starting server\n"
                "torch.cuda.OutOfMemoryError: CUDA out of memory\n"
                "=== process exited with code 1 ===",
            )
        return Completed(0)

    monkeypatch.setattr(cookbook_routes.subprocess, "run", fake_run)

    router = cookbook_routes.setup_cookbook_routes()
    status = _endpoint(router, "/api/cookbook/tasks/status")
    result = asyncio.run(status(RequestLike()))

    assert len(result["tasks"]) == 1
    task = result["tasks"][0]
    assert task["session_id"] == "serve_ok"
    assert task["status"] == "error"
    assert "GPU ran out of memory" in task["diagnosis"]["message"]
    assert task["cmd"] == "vllm serve Team/Good-7B"
