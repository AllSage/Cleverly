"""Networkless Code Workspace command worker.

The main app writes command jobs into DATA_DIR/code-workspaces/.worker. This
process polls that directory, runs one workspace command, and writes a result
file back. In Docker Compose the worker uses network_mode: none, so workspace
tests/builds cannot egress even if command code tries to open sockets.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src import code_workspace


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("code_workspace_worker")


def _queue_root() -> Path:
    root = code_workspace.workspace_root()
    return code_workspace._worker_root(root)  # internal queue helper, shared with producer


def _claim_job(queue: Path) -> Path | None:
    for job in sorted((queue / "pending").glob("*.json")):
        target = queue / "running" / job.name
        try:
            job.replace(target)
            return target
        except OSError:
            continue
    return None


def _write_result(queue: Path, job_id: str, result: dict) -> None:
    result_path = queue / "results" / f"{job_id}.json"
    code_workspace._atomic_write_json(result_path, result)


def _run_job(queue: Path, job_path: Path) -> None:
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job_id = str(job.get("id") or job_path.stem)
        workspace = Path(job.get("workspace") or "").resolve()
        command = str(job.get("command") or "").strip()
        timeout = int(job.get("timeout") or 120)
        root = Path(job.get("root") or code_workspace.workspace_root()).resolve()
        if root not in workspace.parents and workspace != root:
            raise code_workspace.CodeWorkspaceError("Worker job escaped workspace root")
        if code_workspace.DENIED_COMMAND_RE.search(command):
            raise code_workspace.CodeWorkspaceError("Command is blocked in offline code workspace mode")
        result = code_workspace._run_workspace_shell(workspace, command, timeout)
        result["runner"] = "worker"
        _write_result(queue, job_id, result)
    except Exception as exc:
        fallback_id = job_path.stem
        try:
            fallback_id = str(json.loads(job_path.read_text(encoding="utf-8")).get("id") or fallback_id)
        except Exception:
            pass
        _write_result(queue, fallback_id, {"stdout": "", "stderr": str(exc), "exit_code": 1, "runner": "worker"})
    finally:
        try:
            job_path.unlink()
        except OSError:
            pass


def main() -> None:
    queue = _queue_root()
    log.info("Code Workspace worker watching %s", queue)
    while True:
        job = _claim_job(queue)
        if job:
            _run_job(queue, job)
            continue
        time.sleep(code_workspace.WORKER_POLL_SECONDS)


if __name__ == "__main__":
    main()
