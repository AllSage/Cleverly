import json
import shlex
import subprocess
import sys

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _cached_model_scan_script,
    _append_serve_exit_code_lines,
    _append_serve_preflight_exit_lines,
    _bash_squote,
    _check_serve_binary,
    _local_tooling_path_export,
    _parse_serve_phase,
    _ps_squote,
    _safe_env_prefix,
    _shell_path,
    _ssh,
    _ssh_ps,
    _validate_include,
    _validate_local_dir,
    _validate_gpus,
    _validate_remote_host,
    _validate_repo_id,
    _validate_serve_cmd,
    _validate_serve_model_id,
    _validate_ssh_port,
    _validate_token,
    ModelDownloadRequest,
    ServeRequest,
    WIN_SESSION_DIR,
)


def test_safe_env_prefix_accepts_quoted_venv_path():
    assert (
        _safe_env_prefix("source '~/vllm-env/bin/activate'")
        == '[ -f "$HOME/vllm-env/bin/activate" ] && source "$HOME/vllm-env/bin/activate" || true'
    )


def test_safe_env_prefix_leaves_compound_conda_prefix_unchanged():
    prefix = 'eval "$(conda shell.bash hook)" && conda activate qwen35'
    assert _safe_env_prefix(prefix) == prefix


def test_safe_env_prefix_rejects_freeform_shell():
    with pytest.raises(HTTPException):
        _safe_env_prefix("echo ok; curl https://example.invalid")


def test_safe_env_prefix_accepts_powershell_activation_path():
    assert (
        _safe_env_prefix("& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'")
        == "& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'"
    )


def test_validate_ssh_port_rejects_shell_payload():
    with pytest.raises(HTTPException):
        _validate_ssh_port("22; touch /tmp/pwned")
    assert _validate_ssh_port("2222") == "2222"


def test_validate_gpus_accepts_indexes_only():
    assert _validate_gpus("0,1,2") == "0,1,2"
    with pytest.raises(HTTPException):
        _validate_gpus("0; rm -rf /")


def test_download_input_validators_accept_empty_and_reject_payloads():
    assert _validate_include("") is None
    assert _validate_include("*.gguf") == "*.gguf"
    assert _validate_remote_host(None) is None
    assert _validate_remote_host("alice@gpu-box") == "alice@gpu-box"
    assert _validate_token("") is None
    assert _validate_token("hf_abc.123+/=") == "hf_abc.123+/="
    assert _validate_local_dir(None) is None
    assert _validate_local_dir("~/models/llm") == "~/models/llm"
    assert _validate_local_dir("/") == "/"
    assert _validate_local_dir("////") == "/"
    assert _validate_ssh_port("") is None
    assert _validate_gpus(None) is None

    for func, value in [
        (_validate_include, "$(whoami)"),
        (_validate_remote_host, "gpu-box"),
        (_validate_token, "bad token"),
        (_validate_local_dir, "/tmp/bad path"),
        (_validate_ssh_port, "70000"),
    ]:
        with pytest.raises(HTTPException):
            func(value)


def test_validate_repo_id_stays_strict_for_hf_downloads():
    assert _validate_repo_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    with pytest.raises(HTTPException):
        _validate_repo_id("DeepSeek-R1-UD-IQ4_XS")


def test_validate_serve_model_id_accepts_cached_local_model_names():
    assert _validate_serve_model_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert _validate_serve_model_id("DeepSeek-R1-UD-IQ4_XS") == "DeepSeek-R1-UD-IQ4_XS"
    with pytest.raises(HTTPException):
        _validate_serve_model_id("../escape")
    with pytest.raises(HTTPException):
        _validate_serve_model_id("")


def test_local_tooling_path_export_prepends_interpreter_bin():
    """The cookbook runners must see the venv's bin (where `hf`/`python` live)
    so tmux shells can find them without an activated venv."""
    assert (
        _local_tooling_path_export("/opt/venv/bin/python")
        == 'export PATH="/opt/venv/bin:$PATH"'
    )


def test_local_tooling_path_export_preserves_spaces_and_expands_path():
    line = _local_tooling_path_export("/Users/John Smith/.venv/bin/python3")
    assert line == 'export PATH="/Users/John Smith/.venv/bin:$PATH"'
    assert line.endswith(':$PATH"')  # $PATH stays expandable in double quotes
    assert _local_tooling_path_export("relative/python").endswith('relative:$PATH"')


def test_serve_preflight_failure_keeps_tmux_pane_visible():
    """Dependency preflight failures should remain visible in tmux output.

    A bare `exit 127` kills the tmux pane before the browser/status poller can
    capture the helpful error, leaving users with a blank "crashed" card.
    """
    runner_lines = [
        'CLEVERLY_PREFLIGHT_EXIT=""',
        'echo "ERROR: vLLM is not installed. Open Cookbook -> Dependencies and install vllm on this server, then launch again."',
        'CLEVERLY_PREFLIGHT_EXIT=127',
    ]
    _append_serve_preflight_exit_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ERROR: vLLM is not installed" in script
    assert 'CLEVERLY_PREFLIGHT_EXIT=127' in script
    assert 'echo "=== Process exited with code $CLEVERLY_PREFLIGHT_EXIT ==="' in script
    assert 'exec "${SHELL:-/bin/bash}"' in script
    assert "exit 127" not in script


def test_serve_runner_preserves_command_exit_code():
    """The serve wrapper must capture `$?` before any echo resets it."""
    runner_lines = ["vllm serve Qwen/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000"]
    _append_serve_exit_code_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "CLEVERLY_CMD_EXIT=$?" in script
    assert 'echo "=== Process exited with code $CLEVERLY_CMD_EXIT ==="' in script
    assert 'echo "=== Process exited with code $? ==="' not in script


def test_serve_runner_exit_lines_can_close_shell():
    preflight = []
    _append_serve_preflight_exit_lines(preflight, keep_shell_open=False)
    assert '  exit "$CLEVERLY_PREFLIGHT_EXIT"' in preflight

    exit_lines = []
    _append_serve_exit_code_lines(exit_lines, keep_shell_open=False)
    assert exit_lines[-1] == 'echo ""; echo "=== Process exited with code $CLEVERLY_CMD_EXIT ==="'


def test_serve_command_validation_accepts_allowlisted_forms_and_rejects_shell():
    assert _validate_serve_cmd("") is None
    assert _validate_serve_cmd("CUDA_VISIBLE_DEVICES=0 python3 -m vllm.entrypoints.openai.api_server") == (
        "CUDA_VISIBLE_DEVICES=0 python3 -m vllm.entrypoints.openai.api_server"
    )
    multiline = "python3 -m vllm.entrypoints.openai.api_server \\\n+      --model Qwen/Qwen3"
    assert "\n" not in _validate_serve_cmd(multiline)
    prelude = (
        'MODEL_FILE=$(find /models -name "*.gguf" | head -1) && '
        '{ [ -n "$MODEL_FILE" ] && [ -f "$MODEL_FILE" ]; } || '
        '{ echo "ERROR"; exit 1; } && '
        'CUDA_VISIBLE_DEVICES=0 python3 -m llama_cpp.server || llama-server --model "$MODEL_FILE"'
    )
    assert _validate_serve_cmd(prelude) == prelude

    for command in [
        "rm -rf /",
        'python3 -c "unterminated',
        "python3 -m server; curl example.com",
        "python3 -m server && curl example.com",
        "python3 -m server || curl example.com",
        "python3 -m server $(whoami)",
        "python3 -m server\ncurl example.com",
        "python3 -m server `whoami`",
    ]:
        with pytest.raises(HTTPException):
            _validate_serve_cmd(command)

    with pytest.raises(HTTPException):
        _check_serve_binary('"unterminated')
    _check_serve_binary("")


def test_serve_phase_parser_covers_ready_running_and_empty_states():
    assert _parse_serve_phase("", "serve") == {}
    assert _parse_serve_phase("Application startup complete") == {"phase": "ready", "status": "ready"}
    assert _parse_serve_phase('GET /v1/models HTTP/1.1" 200 OK') == {"phase": "idle", "status": "ready"}
    assert _parse_serve_phase("Loading weights took 2.5 seconds") == {"phase": "initializing", "status": "running"}
    assert _parse_serve_phase("GPU KV cache allocated") == {"phase": "warming up", "status": "running"}
    assert _parse_serve_phase("Loading safetensors checkpoint 12%") == {
        "phase": "loading 12%",
        "status": "running",
        "pct": 12,
    }
    assert _parse_serve_phase("Fetching 7 files 44%") == {
        "phase": "downloading 44%",
        "status": "running",
        "pct": 44,
    }
    assert _parse_serve_phase("Downloading incomplete total 55% Fetching 3%")["pct"] == 55
    assert _parse_serve_phase("Avg generation throughput: 9.5 tokens/s, Running: 2 reqs") == {
        "phase": "9.5 tok/s",
        "status": "ready",
        "tps": 9.5,
        "reqs": 2,
    }
    assert _parse_serve_phase("generation throughput: 1.0 tokens/s GPU KV cache usage 99% Running: 0 reqs")["phase"] == "idle"
    assert _parse_serve_phase("something else") == {}


def test_path_quote_ssh_quote_and_request_models():
    assert _shell_path("~") == '"$HOME"'
    assert _shell_path("~/models") == '"$HOME/models"'
    assert _shell_path("/models/qwen") == '"/models/qwen"'
    assert _ps_squote("a'b") == "a''b"
    assert _bash_squote("a'b") == "'\\''".join(["a", "b"])
    assert _ssh("alice@gpu", "echo ok") == "ssh alice@gpu 'echo ok'"
    assert _ssh("alice@gpu", "echo ok", port="2222") == "ssh -p 2222 alice@gpu 'echo ok'"
    assert _ssh("alice@gpu", "echo ok", port="22") == "ssh alice@gpu 'echo ok'"
    assert _ssh_ps("alice@win", "C:/tmp/run.ps1") == (
        'ssh alice@win "powershell -ExecutionPolicy Bypass -File C:/tmp/run.ps1"'
    )
    assert _ssh_ps("alice@win", "C:/tmp/run.ps1", port="2022").startswith("ssh -p 2022")
    assert ModelDownloadRequest(repo_id="Qwen/Qwen3").repo_id == "Qwen/Qwen3"
    assert ServeRequest(repo_id="Qwen/Qwen3", cmd="python3 -m server").cmd == "python3 -m server"
    assert WIN_SESSION_DIR.endswith("cleverly-sessions")


def test_safe_env_prefix_additional_branches():
    assert _safe_env_prefix(None) is None
    assert _safe_env_prefix(". ~/venv/bin/activate") == '[ -f "$HOME/venv/bin/activate" ] && source "$HOME/venv/bin/activate" || true'
    assert _safe_env_prefix("source ~") == '[ -f "$HOME" ] && source "$HOME" || true'
    assert _safe_env_prefix('source "/tmp/a\\"b/activate"') == '[ -f "/tmp/a\\"b/activate" ] && source "/tmp/a\\"b/activate" || true'
    assert _safe_env_prefix("conda activate local-env") == "conda activate local-env"
    assert _safe_env_prefix('eval "$(conda shell.bash hook)" && conda activate "local env"') == (
        'eval "$(conda shell.bash hook)" && conda activate ' + "'local env'"
    )

    for prefix in [
        'source "unterminated',
        'eval "$(conda shell.bash hook)" && conda activate "unterminated',
        'eval "$(conda shell.bash hook)" && conda activate env one',
        "& 'C:\\bad;path\\Activate.ps1'",
        "source /bad/$path",
    ]:
        with pytest.raises(HTTPException):
            _safe_env_prefix(prefix)


def test_safe_env_prefix_handles_inner_conda_parse_failure(monkeypatch):
    calls = []
    real_split = shlex.split

    def split_once_then_fail(value, *args, **kwargs):
        calls.append(value)
        if len(calls) == 1:
            return ["eval", "$(conda shell.bash hook)", "&&", "conda", "activate", "broken"]
        raise ValueError("bad inner env")

    monkeypatch.setattr(shlex, "split", split_once_then_fail)
    with pytest.raises(HTTPException):
        _safe_env_prefix('eval "$(conda shell.bash hook)" && conda activate broken')
    monkeypatch.setattr(shlex, "split", real_split)


def test_cached_model_scan_reports_plain_dir_gguf(tmp_path):
    """Custom download dirs may sit inside the HF hub cache and contain plain
    per-model folders. They must show up in Serve and keep the GGUF signal."""
    plain = tmp_path / "Qwen3.6-27B"
    plain.mkdir()
    (plain / "Qwen3.6-27B-Q4_K_M.gguf").write_bytes(b"gguf")

    hf_internal = tmp_path / "models--Qwen--Qwen3.6-27B"
    (hf_internal / "snapshots" / "abc").mkdir(parents=True)
    (hf_internal / "snapshots" / "abc" / "model.safetensors").write_bytes(b"safe")

    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(_cached_model_scan_script([str(tmp_path)]), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
    )

    by_repo = {m["repo_id"]: m for m in json.loads(proc.stdout)}
    assert "models--Qwen--Qwen3.6-27B" not in by_repo
    assert by_repo["Qwen3.6-27B"]["is_local_dir"] is True
    assert by_repo["Qwen3.6-27B"]["is_gguf"] is True
