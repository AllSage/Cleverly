import asyncio
import importlib
import json
import os
import subprocess
import sys
import types
from types import SimpleNamespace

import pytest


def test_model_discovery_tailscale_cache_success_and_failures(monkeypatch):
    import src.model_discovery as md

    md._hosts_cache = []
    md._hosts_cache_time = 0
    monkeypatch.setattr(md.time, "time", lambda: 100.0)

    payload = {
        "Self": {"TailscaleIPs": ["fd7a::1", "100.64.0.1"]},
        "Peer": {
            "online": {"Online": True, "HostName": "box", "OS": "linux", "TailscaleIPs": ["100.64.0.2"]},
            "offline": {"Online": False, "HostName": "off", "OS": "linux", "TailscaleIPs": ["100.64.0.3"]},
            "funnel": {"Online": True, "HostName": "funnel-ingress-node", "OS": "linux", "TailscaleIPs": ["100.64.0.4"]},
            "phone": {"Online": True, "HostName": "phone", "OS": "android", "TailscaleIPs": ["100.64.0.5"]},
        },
    }

    calls = []

    def run_ok(cmd, capture_output=False, text=False, timeout=None):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    monkeypatch.setattr(md.subprocess, "run", run_ok)
    assert md.discover_tailscale_hosts() == ["100.64.0.1", "100.64.0.2"]
    assert md.discover_tailscale_hosts() == ["100.64.0.1", "100.64.0.2"]
    assert len(calls) == 1

    md._hosts_cache = []
    monkeypatch.setattr(md.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="{}"))
    assert md.discover_tailscale_hosts() == []

    def missing(*args, **kwargs):
        raise FileNotFoundError("tailscale")

    monkeypatch.setattr(md.subprocess, "run", missing)
    assert md.discover_tailscale_hosts() == []

    def broken_status(*args, **kwargs):
        raise RuntimeError("tailscale broke")

    monkeypatch.setattr(md.subprocess, "run", broken_status)
    assert md.discover_tailscale_hosts() == []


def test_model_discovery_hosts_ports_providers_and_http(monkeypatch):
    import src.model_discovery as md

    discovery = md.ModelDiscovery("127.0.0.1", openai_api_key="sk-test")

    monkeypatch.setenv("LLM_HOSTS", "10.0.0.2, 10.0.0.2, 10.0.0.3")
    assert discovery._get_hosts() == ["127.0.0.1", "10.0.0.2", "10.0.0.2", "10.0.0.3", "host.docker.internal"]

    monkeypatch.setenv("LLM_HOSTS", "host.docker.internal")
    assert discovery._get_hosts() == ["127.0.0.1", "host.docker.internal"]

    monkeypatch.delenv("LLM_HOSTS", raising=False)
    monkeypatch.setattr(md, "discover_tailscale_hosts", lambda: ["100.64.0.2"])
    assert discovery._get_hosts() == ["127.0.0.1", "100.64.0.2", "host.docker.internal"]

    monkeypatch.setattr(md, "discover_tailscale_hosts", lambda: [])
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    monkeypatch.setenv("OLLAMA_URL", "")
    assert discovery._get_hosts() == ["127.0.0.1", "host.docker.internal"]

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:11434")
    monkeypatch.setenv("OLLAMA_URL", "bad host")
    hosts = discovery._get_hosts()
    assert hosts[:2] == ["127.0.0.1", "host.docker.internal"]
    assert "ollama.local" in hosts

    monkeypatch.setattr(md, "urlparse", lambda _raw: (_ for _ in ()).throw(ValueError("bad url")))
    assert discovery._get_hosts() == ["127.0.0.1", "host.docker.internal"]

    class Response:
        is_success = True

        def json(self):
            return {"data": [{"id": "/model-a"}, {}, {"id": "model-b"}]}

    monkeypatch.setattr(md.httpx, "get", lambda url, timeout=3: Response())
    checked = discovery._check_port("host", 8000)
    assert checked["url"] == "http://host:8000/v1/chat/completions"
    assert checked["models"] == ["/model-a", "model-b"]
    assert checked["models_display"] == ["model-a", "model-b"]

    class BadResponse:
        is_success = False

        def json(self):
            raise AssertionError("json should not be read")

    monkeypatch.setattr(md.httpx, "get", lambda url, timeout=3: BadResponse())
    assert discovery._check_port("host", 8000) is None

    monkeypatch.setattr(md.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert discovery._check_port("host", 8000) is None

    monkeypatch.setattr(discovery, "_get_hosts", lambda: ["h2", "h1"])

    def fake_check(host, port):
        if port == 8000:
            return {"host": host, "port": port, "url": f"http://{host}:{port}/v1/chat/completions", "models": ["same"]}
        return None

    monkeypatch.setattr(discovery, "_check_port", fake_check)
    discovered = discovery.discover_models()
    assert discovered["hosts"] == ["h2", "h1"]
    assert len(discovered["items"]) == 1
    assert discovered["items"][0]["host"] in {"h1", "h2"}
    assert discovered["items"][0]["port"] == 8000
    assert discovered["items"][0]["models"] == ["same"]

    providers = discovery.get_providers()["providers"]
    assert providers[0]["provider"] == "vllm"
    assert providers[1]["provider"] == "openai"
    assert "gpt-5.2" in providers[1]["items"][0]["models"]

    no_openai = md.ModelDiscovery("127.0.0.1")
    monkeypatch.setattr(no_openai, "discover_models", lambda: {"hosts": ["h"], "items": []})
    assert no_openai.get_providers()["providers"] == [{"provider": "vllm", "hosts": ["h"], "items": []}]

    monkeypatch.setattr(md, "offline_mode", lambda: True)
    monkeypatch.setattr(md, "is_local_model_url", lambda url: "localhost" in url)
    monkeypatch.setattr(md.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    offline_discovery = md.ModelDiscovery("localhost", openai_api_key="sk-test")
    assert offline_discovery._check_port("api.example.com", 8000) is None
    offline_providers = offline_discovery.get_providers()["providers"]
    assert len(offline_providers) == 1
    assert offline_providers[0]["provider"] == "vllm"


def test_platform_compat_posix_and_windows_branches(monkeypatch, tmp_path):
    import core.platform_compat as pc

    target = tmp_path / "secret.txt"
    target.write_text("x", encoding="utf-8")

    monkeypatch.setattr(pc, "IS_WINDOWS", False)
    chmod_calls = []
    monkeypatch.setattr(pc.os, "chmod", lambda path, mode: chmod_calls.append((path, mode)))
    assert pc.safe_chmod(target, 0o600) is True
    assert chmod_calls == [(target, 0o600)]
    assert pc.detached_popen_kwargs() == {"start_new_session": True}

    alive_calls = []
    monkeypatch.setattr(pc.os, "kill", lambda pid, sig: alive_calls.append((pid, sig)))
    assert pc.pid_alive(123) is True
    assert alive_calls == [(123, 0)]

    kill_calls = []
    monkeypatch.setattr(pc.os, "getpgid", lambda pid: pid + 10, raising=False)
    monkeypatch.setattr(pc.os, "killpg", lambda pgid, sig: kill_calls.append(("pg", pgid, sig)), raising=False)
    pc.kill_process_tree(9)
    assert kill_calls[0][0] == "pg"

    monkeypatch.setattr(pc.os, "chmod", lambda path, mode: (_ for _ in ()).throw(OSError("nope")))
    assert pc.safe_chmod(target, 0o600) is False
    monkeypatch.setattr(pc.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError("missing")))
    assert pc.pid_alive(404) is False

    monkeypatch.setattr(pc, "IS_WINDOWS", True)
    assert pc.safe_chmod(target, 0o600) is False
    detached = pc.detached_popen_kwargs()
    assert detached["creationflags"] & 0x00000200
    assert detached["creationflags"] & 0x00000008

    run_calls = []
    monkeypatch.setattr(pc.subprocess, "run", lambda args, **kwargs: run_calls.append((args, kwargs)))
    pc.kill_process_tree(77)
    assert run_calls[0][0][-1] == "77"


def test_platform_compat_shell_resolution(monkeypatch):
    import core.platform_compat as pc

    monkeypatch.setattr(pc, "IS_WINDOWS", True)
    pc._BASH_CACHE = None
    pc._BASH_PROBED = False

    which_calls = []

    def fake_which(name):
        which_calls.append(name)
        if name == "npm.cmd":
            return "C:/node/npm.cmd"
        return None

    monkeypatch.setattr(pc.shutil, "which", fake_which)
    monkeypatch.setattr(pc.os.path, "exists", lambda path: path.endswith(r"Git\bin\bash.exe"))

    bash = pc.find_bash()
    assert bash.endswith(r"Git\bin\bash.exe")
    assert pc.find_bash() == bash
    assert pc.has_bash() is True
    assert pc.which_tool("npm") == "C:/node/npm.cmd"
    assert pc.run_script_argv("script.sh") == [bash, "script.sh"]

    pc._BASH_CACHE = None
    pc._BASH_PROBED = False
    monkeypatch.setattr(pc.shutil, "which", lambda name: None)
    monkeypatch.setattr(pc.os.path, "exists", lambda path: False)
    monkeypatch.setenv("ComSpec", "C:/Windows/System32/cmd.exe")
    assert pc.run_script_argv("script.bat") == ["C:/Windows/System32/cmd.exe", "/c", "script.bat"]

    monkeypatch.setattr(pc, "IS_WINDOWS", False)
    pc._BASH_CACHE = None
    pc._BASH_PROBED = False
    monkeypatch.setattr(pc.shutil, "which", lambda name: None)
    assert pc.find_bash() is None
    assert pc.run_script_argv("script.sh") == ["sh", "script.sh"]


def test_platform_compat_remaining_process_and_tool_edges(monkeypatch):
    import ctypes
    import core.platform_compat as pc

    assert pc.pid_alive(None) is False
    pc.kill_process_tree(None)

    monkeypatch.setattr(pc, "IS_WINDOWS", True)

    class Kernel32:
        def __init__(self, *, handle=10, exit_ok=True, code=259):
            self.handle = handle
            self.exit_ok = exit_ok
            self.code = code
            self.closed = []

        def OpenProcess(self, *_args):
            return self.handle

        def GetExitCodeProcess(self, _handle, code_ref):
            if self.exit_ok:
                code_ref._obj.value = self.code
                return True
            return False

        def CloseHandle(self, handle):
            self.closed.append(handle)

    kernel = Kernel32()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    assert pc.pid_alive(123) is True
    assert kernel.closed == [10]

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel32(handle=0)), raising=False)
    assert pc.pid_alive(123) is False
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel32(exit_ok=False)), raising=False)
    assert pc.pid_alive(123) is False
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel32(code=0)), raising=False)
    assert pc.pid_alive(123) is False

    monkeypatch.setattr(pc.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("taskkill failed")))
    pc.kill_process_tree(99)

    monkeypatch.setattr(pc, "IS_WINDOWS", False)
    fallback_kills = []
    monkeypatch.setattr(pc.os, "getpgid", lambda _pid: (_ for _ in ()).throw(OSError("no group")), raising=False)
    monkeypatch.setattr(pc.os, "kill", lambda pid, sig: fallback_kills.append((pid, sig)))
    pc.kill_process_tree(88)
    assert fallback_kills and fallback_kills[0][0] == 88

    monkeypatch.setattr(pc.os, "kill", lambda *_args: (_ for _ in ()).throw(OSError("cannot kill")))
    pc.kill_process_tree(88)

    monkeypatch.setattr(pc.shutil, "which", lambda name: "/bin/tool" if name == "tool" else None)
    assert pc.which_tool("tool") == "/bin/tool"
    assert pc.which_tool("missing") is None


@pytest.mark.parametrize("module_name", ["src.youtube_handler", "services.youtube.youtube_handler"])
def test_youtube_helpers_and_formatters(monkeypatch, module_name):
    yt = importlib.import_module(module_name)

    assert yt.is_youtube_url("https://youtube.com/watch?v=abc")
    assert yt.is_youtube_url("https://youtu.be/abc")
    assert not yt.is_youtube_url("https://example.com/watch?v=abc")
    assert yt.extract_youtube_id("https://www.youtube.com/watch?v=abc&x=1") == "abc"
    assert yt.extract_youtube_id("https://m.youtube.com/embed/embedid") == "embedid"
    assert yt.extract_youtube_id("https://youtu.be/shortid") == "shortid"
    assert yt.extract_youtube_id("https://example.com/watch?v=abc") is None

    monkeypatch.setattr(yt.Path, "exists", lambda self: True)
    assert yt._find_ytdlp().endswith("yt-dlp")
    monkeypatch.setattr(yt.Path, "exists", lambda self: False)
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    assert yt._find_ytdlp() == "/usr/bin/yt-dlp"
    monkeypatch.setattr(yt.shutil, "which", lambda name: None)
    assert yt._find_ytdlp() == "yt-dlp"

    unavailable = yt.format_transcript_for_context(
        {"success": False, "error": "disabled"},
        "https://youtu.be/x",
        title="Demo",
        channel="Channel",
    )
    assert "Transcript unavailable" in unavailable
    assert "Demo" in unavailable

    transcript = yt.format_transcript_for_context(
        {
            "success": True,
            "transcript": "full text",
            "video_id": "vid",
            "language": "en",
            "is_generated": True,
            "segments": [{"timestamp": "00:01", "text": "hello"}],
        },
        "https://youtu.be/vid",
        title="Title",
        channel="Chan",
    )
    assert "Title: Title" in transcript
    assert "Source: Auto-generated" in transcript
    assert "[00:01] hello" in transcript

    long_segment_context = yt.format_transcript_for_context(
        {
            "success": True,
            "transcript": "plain fallback",
            "video_id": "vid",
            "segments": [{"timestamp": "00:01", "text": "x" * 13000}],
        },
        "url",
    )
    assert "Timestamped Transcript" not in long_segment_context
    assert "Transcript:\nplain fallback" in long_segment_context

    plain = yt.format_transcript_for_context(
        {"success": True, "transcript": "plain", "video_id": "vid", "segments": []},
        "url",
    )
    assert "Transcript:\nplain" in plain

    assert yt.format_comments_for_context({"success": False, "comments": []}, "url") == ""
    comments = yt.format_comments_for_context(
        {"success": True, "comments": [{"author": "A", "likes": 2, "text": "nice"}]},
        "url",
    )
    assert "@A [2 likes]: nice" in comments
    long_comments = yt.format_comments_for_context(
        {"success": True, "comments": [{"author": "A", "likes": 0, "text": "x" * 5000}]},
        "url",
    )
    assert "[Comments truncated]" in long_comments


@pytest.mark.parametrize("module_name", ["src.youtube_handler", "services.youtube.youtube_handler"])
@pytest.mark.asyncio
async def test_youtube_transcript_and_comments(monkeypatch, module_name):
    yt = importlib.import_module(module_name)

    monkeypatch.setattr(yt, "offline_mode", lambda: True)
    blocked_transcript = await yt.extract_transcript_async("url", "vid")
    assert blocked_transcript == {
        "success": False,
        "error": "YouTube transcript fetching is disabled in offline mode",
        "transcript": None,
    }
    monkeypatch.setattr(
        yt.asyncio,
        "create_subprocess_exec",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    blocked_comments = await yt.fetch_youtube_comments("vid")
    assert blocked_comments == {
        "success": False,
        "error": "YouTube comment fetching is disabled in offline mode",
        "comments": [],
    }
    monkeypatch.setattr(yt, "offline_mode", lambda: False)

    yt.YOUTUBE_AVAILABLE = False
    yt.YouTubeTranscriptApi = None
    unavailable = await yt.extract_transcript_async("url", "vid")
    assert unavailable["success"] is False

    class Snippet:
        def __init__(self, text, start, duration):
            self.text = text
            self.start = start
            self.duration = duration

    class Api:
        def fetch(self, video_id):
            assert video_id == "vid"
            return [Snippet(" hello ", 61.2, 2.0), Snippet(" ", 63, 1), Snippet("world", 64, 1)]

    yt.YOUTUBE_AVAILABLE = True
    yt.YouTubeTranscriptApi = Api
    transcript = await yt.extract_transcript_async("url", "vid")
    assert transcript["success"] is True
    assert transcript["transcript"] == "hello world"
    assert transcript["segments"][0]["timestamp"] == "01:01"

    class LongApi:
        def fetch(self, video_id):
            return [Snippet("x" * 9000, 0, 1)]

    yt.YouTubeTranscriptApi = LongApi
    long_transcript = await yt.extract_transcript_async("url", "vid")
    assert long_transcript["transcript"].endswith("... [transcript truncated]")

    class FailingApi:
        def fetch(self, video_id):
            raise RuntimeError("captions off")

    yt.YouTubeTranscriptApi = FailingApi
    original_sleep = asyncio.sleep
    monkeypatch.setattr(yt.asyncio, "sleep", lambda delay: original_sleep(0))
    failed = await yt.extract_transcript_async("url", "vid", max_retries=2)
    assert failed == {"success": False, "error": "Failed after 2 attempts", "transcript": None}

    class CommentProc:
        returncode = 0

        async def communicate(self):
            data = {
                "title": "Video",
                "uploader": "Uploader",
                "comments": [
                    {"author": "Low", "text": "ok", "like_count": 1},
                    {"author": "Skip", "text": "   ", "like_count": 99},
                    {"author": "High", "text": "great", "like_count": 10},
                ],
            }
            return json.dumps(data).encode(), b""

    async def create_exec(*args, **kwargs):
        assert "https://www.youtube.com/watch?v=vid" in args
        return CommentProc()

    monkeypatch.setattr(yt.asyncio, "create_subprocess_exec", create_exec)
    fetched = await yt.fetch_youtube_comments("vid", max_comments=3)
    assert fetched["success"] is True
    assert fetched["title"] == "Video"
    assert fetched["channel"] == "Uploader"
    assert [comment["author"] for comment in fetched["comments"]] == ["High", "Low"]

    class FailedProc:
        returncode = 1

        async def communicate(self):
            return b"", b"boom"

    async def create_failed_exec(*args, **kwargs):
        return FailedProc()

    monkeypatch.setattr(yt.asyncio, "create_subprocess_exec", create_failed_exec)
    failed_comments = await yt.fetch_youtube_comments("vid")
    assert failed_comments["success"] is False
    assert "yt-dlp failed" in failed_comments["error"]

    original_wait_for = asyncio.wait_for

    async def timeout_wait_for(awaitable, timeout=None):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(yt.asyncio, "wait_for", timeout_wait_for)
    timed_out = await yt.fetch_youtube_comments("vid")
    assert timed_out == {"success": False, "error": "Comment fetch timed out", "comments": []}

    monkeypatch.setattr(yt.asyncio, "wait_for", original_wait_for)

    async def missing_exec(*args, **kwargs):
        raise FileNotFoundError("missing yt-dlp")

    monkeypatch.setattr(yt.asyncio, "create_subprocess_exec", missing_exec)
    assert await yt.fetch_youtube_comments("vid") == {"success": False, "error": "yt-dlp not installed", "comments": []}

    async def broken_exec(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(yt.asyncio, "create_subprocess_exec", broken_exec)
    generic_error = await yt.fetch_youtube_comments("vid")
    assert generic_error == {"success": False, "error": "unexpected", "comments": []}


@pytest.mark.parametrize("module_name", ["src.youtube_handler", "services.youtube.youtube_handler"])
def test_youtube_init_import_success_and_failure(monkeypatch, module_name):
    yt = importlib.import_module(module_name)

    fake_module = types.ModuleType("youtube_transcript_api")

    class Api:
        pass

    fake_module.YouTubeTranscriptApi = Api
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)
    yt.init_youtube()
    assert yt.YOUTUBE_AVAILABLE is True
    assert yt.YouTubeTranscriptApi is Api

    monkeypatch.delitem(sys.modules, "youtube_transcript_api", raising=False)
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "youtube_transcript_api":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    yt.init_youtube()
    assert yt.YOUTUBE_AVAILABLE is False
