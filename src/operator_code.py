"""Read-only code test planning for the Cleverly operator console."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_WORKSPACES = 8
MAX_COMMANDS = 18


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _owner_matches(record: dict[str, Any], owner: str) -> bool:
    item_owner = str(record.get("owner") or "local")
    if owner and owner != "local":
        return item_owner == owner
    return item_owner in {"", "local"}


def _load_workspaces(owner: str) -> tuple[list[dict[str, Any]], str]:
    try:
        from src import code_workspace

        return [row for row in code_workspace.list_workspaces(owner=owner) if isinstance(row, dict)], ""
    except Exception as exc:
        return [], _trim(exc, 300)


def _runner() -> dict[str, Any]:
    runner = (os.getenv("CODE_WORKSPACE_RUNNER") or "in-process").strip().lower() or "in-process"
    worker_dir = os.getenv("CODE_WORKSPACE_WORKER_DIR") or ""
    return {
        "runner": runner,
        "worker_dir": worker_dir,
        "state": "ok" if runner == "worker" else "warn",
        "detail": "networkless worker queue" if runner == "worker" else "in-process runner; review host isolation before approving test commands",
    }


def _workspace_path(row: dict[str, Any]) -> Path | None:
    raw = row.get("path") or row.get("root") or row.get("workspace_root")
    if not raw:
        return None
    try:
        path = Path(str(raw)).resolve()
        if path.exists() and path.is_dir():
            return path
    except OSError:
        return None
    return None


def _has_file(root: Path | None, *names: str) -> bool:
    if root is None:
        return False
    return any((root / name).is_file() for name in names)


def _package_test_command(root: Path | None) -> str:
    if root is None:
        return ""
    path = root / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    scripts = data.get("scripts") if isinstance(data, dict) else {}
    if isinstance(scripts, dict) and _trim(scripts.get("test"), 240):
        return "npm test"
    return ""


def _detect_commands(row: dict[str, Any]) -> list[dict[str, Any]]:
    root = _workspace_path(row)
    workspace_id = _trim(row.get("id"), 160)
    title = _trim(row.get("name") or workspace_id or "Workspace", 160)
    candidates: list[tuple[str, str, str]] = []
    package_command = _package_test_command(root)
    if package_command:
        candidates.append(("node", package_command, "package.json test script"))
    if _has_file(root, "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
        candidates.append(("python", "python -m pytest -q", "Python test config present"))
    if _has_file(root, "Cargo.toml"):
        candidates.append(("rust", "cargo test", "Cargo.toml present"))
    if _has_file(root, "go.mod"):
        candidates.append(("go", "go test ./...", "go.mod present"))
    if not candidates:
        candidates.append(("manual", "", "No common local test config detected; choose an exact command in Code Workspace"))
    rows: list[dict[str, Any]] = []
    for index, (kind, command, detail) in enumerate(candidates[:4], start=1):
        rows.append({
            "id": f"{workspace_id or 'workspace'}:{kind}:{index}",
            "workspace_id": workspace_id,
            "workspace": title,
            "state": "warn" if command else "loading",
            "badge": kind,
            "title": command or "Manual test command required",
            "detail": detail,
            "command": command,
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "api": f"/api/code-workspaces/{workspace_id}/run" if workspace_id else "",
        })
    return rows


def _workspace_row(row: dict[str, Any], candidate_count: int) -> dict[str, Any]:
    workspace_id = _trim(row.get("id"), 160)
    title = _trim(row.get("name") or workspace_id or "Code workspace", 160)
    path = _trim(row.get("path") or row.get("root") or "sealed local workspace", 300)
    updated = row.get("updated_at") or row.get("created_at") or ""
    return {
        "id": workspace_id,
        "state": "ok" if workspace_id else "warn",
        "badge": "repo",
        "title": title,
        "detail": f"{path}; {candidate_count} candidate command{'s' if candidate_count != 1 else ''}",
        "updated_at": updated,
        "path": path,
        "api": {
            "status": f"/api/code-workspaces/{workspace_id}/status" if workspace_id else "",
            "diff": f"/api/code-workspaces/{workspace_id}/diff" if workspace_id else "",
            "snapshot": f"/api/code-workspaces/{workspace_id}/snapshots" if workspace_id else "",
            "run": f"/api/code-workspaces/{workspace_id}/run" if workspace_id else "",
        },
    }


def _sequence_rows(workspace_count: int, command_count: int, runner_state: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "select-workspace",
            "state": "ok" if workspace_count else "warn",
            "badge": "1",
            "title": "Select the target workspace",
            "detail": f"{workspace_count} sealed code workspace{'s' if workspace_count != 1 else ''} visible" if workspace_count else "Import a sealed repo before planning tests",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-code",
            "actionLabel": "Code",
        },
        {
            "id": "review-status-diff",
            "state": "ok" if workspace_count else "warn",
            "badge": "2",
            "title": "Review status and diff",
            "detail": "Use Code Workspace Status and Diff before approving a test command.",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-code-workspace-map",
            "actionLabel": "Map",
        },
        {
            "id": "create-snapshot",
            "state": "warn" if workspace_count else "loading",
            "badge": "3",
            "title": "Create a snapshot checkpoint",
            "detail": "Snapshot writes recovery evidence before test/build commands or agent edits.",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-code",
            "actionLabel": "Snapshot",
        },
        {
            "id": "confirm-runner",
            "state": runner_state,
            "badge": "4",
            "title": "Confirm runner isolation",
            "detail": "Docker uses the code-worker sidecar; native runs should review runner policy before approval.",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-code-workspace-map",
            "actionLabel": "Map",
        },
        {
            "id": "approve-command",
            "state": "warn" if command_count else "loading",
            "badge": "5",
            "title": "Approve the exact test command",
            "detail": "The operator plan never runs tests; approve the exact command in Code Workspace after review.",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-code",
            "actionLabel": "Run",
        },
    ]


def run_operator_code_test_plan(
    owner: str = "local",
    *,
    workspaces: list[Any] | None = None,
) -> dict[str, Any]:
    """Return read-only code test plan evidence."""
    owner = owner or "local"
    raw_workspaces, workspace_error = (workspaces, "") if workspaces is not None else _load_workspaces(owner)
    workspace_records = [
        row for row in raw_workspaces or []
        if isinstance(row, dict) and _owner_matches(row, owner)
    ][:MAX_WORKSPACES]
    runner = _runner()
    command_rows: list[dict[str, Any]] = []
    workspace_rows: list[dict[str, Any]] = []
    for workspace in workspace_records:
        candidates = _detect_commands(workspace)
        command_rows.extend(candidates)
        executable_count = sum(1 for item in candidates if item.get("command"))
        workspace_rows.append(_workspace_row(workspace, executable_count))
    command_rows = command_rows[:MAX_COMMANDS]
    candidate_count = sum(1 for item in command_rows if item.get("command"))
    sequence_rows = _sequence_rows(len(workspace_rows), candidate_count, runner["state"])
    return {
        "mode": "read-only-code-test-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "ok" if workspace_rows and candidate_count else ("warn" if workspace_rows else "loading"),
            "workspace_count": len(workspace_rows),
            "candidate_command_count": candidate_count,
            "runner": runner["runner"],
            "runner_state": runner["state"],
            "runs_tests": False,
            "changes_files": False,
            "creates_snapshot": False,
            "requires_run_approval": True,
            "max_command_seconds": 300,
            "next_action": "Review workspace status/diff, create a snapshot, then approve the exact test command." if workspace_rows else "Import a sealed Code Workspace before planning test execution.",
        },
        "runner": runner,
        "workspace_rows": workspace_rows,
        "candidate_commands": command_rows,
        "sequence_rows": sequence_rows,
        "evidence_rows": [
            {
                "id": "activity",
                "state": "loading",
                "badge": "log",
                "title": "Test output evidence",
                "detail": "After approved execution, keep runner output in Code Workspace and the operator activity ledger.",
                "action": "open-activity-preflight",
                "actionLabel": "Activity",
            },
            {
                "id": "denied-commands",
                "state": "ok",
                "badge": "safe",
                "title": "Offline command blocklist",
                "detail": "Network fetch/install commands such as curl, wget, git pull/fetch, pip install, npm install, docker, and kubectl are blocked in workspace runs.",
                "action": "open-code-workspace-map",
                "actionLabel": "Map",
            },
        ],
        "api_actions": [
            {
                "id": "status",
                "method": "GET",
                "path_template": "/api/code-workspaces/{workspace_id}/status",
                "risk": "read-only",
                "executes": False,
                "requires_approval": False,
            },
            {
                "id": "diff",
                "method": "GET",
                "path_template": "/api/code-workspaces/{workspace_id}/diff",
                "risk": "read-only",
                "executes": False,
                "requires_approval": False,
            },
            {
                "id": "snapshot",
                "method": "POST",
                "path_template": "/api/code-workspaces/{workspace_id}/snapshots",
                "risk": "approval-required",
                "executes": False,
                "requires_approval": True,
            },
            {
                "id": "run",
                "method": "POST",
                "path_template": "/api/code-workspaces/{workspace_id}/run",
                "risk": "approval-required",
                "executes": False,
                "requires_approval": True,
            },
        ],
        "approval": {
            "required": True,
            "gate": "Code Workspace Run",
            "policy": "This endpoint only plans test execution. It does not run tests, modify files, apply diffs, create snapshots, restore snapshots, commit, use network access, or execute shell commands.",
            "disallowed_by_default": [
                "network fetch",
                "dependency install",
                "git pull/fetch/push",
                "docker command",
                "snapshot restore",
                "commit",
            ],
        },
        "paths": {
            "workspaces": "data/code-workspaces/workspaces.json",
            "workspace_root": "data/code-workspaces",
            "worker_queue": runner["worker_dir"] or "data/code-workspaces/.worker",
        },
        "errors": {
            "workspaces": workspace_error,
        },
    }
