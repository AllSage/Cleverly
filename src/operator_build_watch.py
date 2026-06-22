"""Read-only build-watch planning for the Cleverly operator console."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_WORKSPACES = 8
MAX_COMMANDS = 12
DEFAULT_MAX_ITERATIONS = 6


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
        "detail": "networkless code-worker queue" if runner == "worker" else "in-process runner; review host isolation before loop approval",
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


def _package_scripts(root: Path | None) -> dict[str, Any]:
    if root is None:
        return {}
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else {}
    return scripts if isinstance(scripts, dict) else {}


def _detect_build_commands(row: dict[str, Any]) -> list[dict[str, Any]]:
    root = _workspace_path(row)
    workspace_id = _trim(row.get("id"), 160)
    title = _trim(row.get("name") or workspace_id or "Workspace", 160)
    candidates: list[tuple[str, str, str]] = []
    scripts = _package_scripts(root)
    if _trim(scripts.get("build"), 240):
        candidates.append(("node", "npm run build", "package.json build script"))
    if _trim(scripts.get("test"), 240):
        candidates.append(("node", "npm test", "package.json test script fallback"))
    if _has_file(root, "Cargo.toml"):
        candidates.append(("rust", "cargo build", "Cargo.toml present"))
    if _has_file(root, "go.mod"):
        candidates.append(("go", "go test ./...", "go.mod present; test command is the practical green check"))
    if _has_file(root, "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"):
        candidates.append(("python", "python -m pytest -q", "Python test config present; test command is the practical green check"))
    if not candidates:
        candidates.append(("manual", "", "No common local build config detected; choose an exact command in Code Workspace"))

    rows: list[dict[str, Any]] = []
    for index, (kind, command, detail) in enumerate(candidates[:4], start=1):
        rows.append({
            "id": f"{workspace_id or 'workspace'}:{kind}:{index}",
            "workspace_id": workspace_id,
            "workspace": title,
            "state": "warn" if command else "loading",
            "badge": kind,
            "title": command or "Manual build command required",
            "detail": detail,
            "command": command,
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "api": f"/api/code-workspaces/{workspace_id}/run" if workspace_id else "",
        })
    return rows


def _workspace_row(row: dict[str, Any], command_count: int) -> dict[str, Any]:
    workspace_id = _trim(row.get("id"), 160)
    title = _trim(row.get("name") or row.get("title") or workspace_id or "Code workspace", 160)
    path = _trim(row.get("path") or row.get("root") or "sealed local workspace", 300)
    updated = row.get("updated_at") or row.get("created_at") or ""
    return {
        "id": workspace_id,
        "state": "ok" if workspace_id else "warn",
        "badge": "repo",
        "title": title,
        "detail": f"{path}; {command_count} candidate build command{'s' if command_count != 1 else ''}",
        "updated_at": updated,
        "path": path,
        "api": {
            "status": f"/api/code-workspaces/{workspace_id}/status" if workspace_id else "",
            "diff": f"/api/code-workspaces/{workspace_id}/diff" if workspace_id else "",
            "snapshot": f"/api/code-workspaces/{workspace_id}/snapshots" if workspace_id else "",
            "run": f"/api/code-workspaces/{workspace_id}/run" if workspace_id else "",
        },
    }


def _sequence_rows(workspace_count: int, command_count: int, runner_state: str, max_iterations: int) -> list[dict[str, Any]]:
    return [
        {
            "id": "select-workspace",
            "state": "ok" if workspace_count else "warn",
            "badge": "1",
            "title": "Select the target repo",
            "detail": f"{workspace_count} sealed code workspace{'s' if workspace_count != 1 else ''} visible" if workspace_count else "Import a sealed repo before planning a build-watch loop",
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
            "detail": "Inspect workspace status/diff before approving repeated build repair work.",
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
            "title": "Create a recovery snapshot",
            "detail": "Snapshot creation writes recovery evidence before any looped edits or build commands.",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-code",
            "actionLabel": "Snapshot",
        },
        {
            "id": "confirm-build-command",
            "state": "warn" if command_count else "loading",
            "badge": "4",
            "title": "Confirm the exact build command",
            "detail": "The plan only infers candidates; approve one exact command inside the build loop request.",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-code",
            "actionLabel": "Command",
        },
        {
            "id": "approve-loop",
            "state": "warn" if command_count else "loading",
            "badge": "5",
            "title": "Approve Build Until Green loop",
            "detail": f"Start only after reviewing the max {max_iterations} pass limit and rollback evidence.",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "request-build-watch-loop",
            "actionLabel": "Ask",
        },
        {
            "id": "monitor-activity",
            "state": "ok",
            "badge": "6",
            "title": "Monitor activity and stop conditions",
            "detail": "Record each run, change, failure, retry, and final green build in the activity timeline.",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
    ]


def _loop_rows(max_iterations: int) -> list[dict[str, Any]]:
    return [
        {
            "id": "loop-template",
            "state": "ok",
            "badge": "loop",
            "title": "Build Until Green",
            "detail": "Repeat the approved build command, repair the first real failure, and stop when the command exits 0.",
            "action": "open-loops",
            "actionLabel": "Loops",
        },
        {
            "id": "iteration-cap",
            "state": "warn",
            "badge": "max",
            "title": "Iteration limit",
            "detail": f"{max_iterations} maximum passes before Cleverly stops for review.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "id": "exit-condition",
            "state": "ok",
            "badge": "exit",
            "title": "Exit condition",
            "detail": "The selected build/check command exits 0 and the final diff is summarized.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
    ]


def _guard_rows(runner: dict[str, Any], max_iterations: int) -> list[dict[str, Any]]:
    return [
        {
            "id": "natural-language-boundary",
            "state": "ok",
            "badge": "plan",
            "title": "Read-only natural-language route",
            "detail": "Watch/build requests open this plan first; the route does not start loops, run commands, or edit files.",
            "action": "watch-build-until-green",
            "actionLabel": "Plan",
        },
        {
            "id": "approval-route",
            "state": "warn",
            "badge": "ask",
            "title": "Approval-gated start route",
            "detail": "request-build-watch-loop must be approved before the Build Until Green prompt is sent.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "id": "runner-isolation",
            "state": runner["state"],
            "badge": "run",
            "title": "Runner isolation",
            "detail": runner["detail"],
            "action": "open-code-workspace-map",
            "actionLabel": "Map",
        },
        {
            "id": "iteration-stop",
            "state": "ok",
            "badge": "stop",
            "title": "Stop condition",
            "detail": f"Loop stops when green or after {max_iterations} passes for manual review.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            "id": "network-blocklist",
            "state": "ok",
            "badge": "local",
            "title": "Local command boundary",
            "detail": "Workspace command runs block network fetch/install commands such as curl, git pull, pip install, npm install, docker, and kubectl.",
            "action": "open-code-workspace-map",
            "actionLabel": "Map",
        },
    ]


def _api_actions() -> list[dict[str, Any]]:
    return [
        {
            "id": "workspace-status",
            "method": "GET",
            "path_template": "/api/code-workspaces/{workspace_id}/status",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "workspace-diff",
            "method": "GET",
            "path_template": "/api/code-workspaces/{workspace_id}/diff",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "workspace-snapshot",
            "method": "POST",
            "path_template": "/api/code-workspaces/{workspace_id}/snapshots",
            "risk": "approval-required",
            "executes": False,
            "requires_approval": True,
            "creates_snapshot": False,
        },
        {
            "id": "workspace-run",
            "method": "POST",
            "path_template": "/api/code-workspaces/{workspace_id}/run",
            "risk": "approval-required",
            "executes": False,
            "requires_approval": True,
            "runs_build": False,
        },
        {
            "id": "route-proof",
            "method": "POST",
            "path_template": "/api/operator/route",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
    ]


def run_operator_build_watch_plan(
    owner: str = "local",
    *,
    workspaces: list[Any] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> dict[str, Any]:
    """Return read-only evidence for a Build Until Green request."""
    owner = owner or "local"
    raw_workspaces, workspace_error = (workspaces, "") if workspaces is not None else _load_workspaces(owner)
    workspace_records = [
        row for row in raw_workspaces or []
        if isinstance(row, dict) and _owner_matches(row, owner)
    ][:MAX_WORKSPACES]
    runner = _runner()
    try:
        loop_limit = max(1, min(20, int(max_iterations)))
    except (TypeError, ValueError):
        loop_limit = DEFAULT_MAX_ITERATIONS

    workspace_rows: list[dict[str, Any]] = []
    command_rows: list[dict[str, Any]] = []
    for workspace in workspace_records:
        commands = _detect_build_commands(workspace)
        command_rows.extend(commands)
        workspace_rows.append(_workspace_row(workspace, sum(1 for item in commands if item.get("command"))))
    command_rows = command_rows[:MAX_COMMANDS]
    command_count = sum(1 for item in command_rows if item.get("command"))
    workspace_count = len(workspace_rows)
    state = "ok" if workspace_count and command_count else ("warn" if workspace_count else "loading")

    return {
        "mode": "read-only-build-watch-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "workspace_count": workspace_count,
            "candidate_command_count": command_count,
            "runner": runner["runner"],
            "runner_state": runner["state"],
            "max_iterations": loop_limit,
            "starts_loop": False,
            "runs_build": False,
            "edits_files": False,
            "creates_snapshot": False,
            "restores_snapshot": False,
            "uses_network": False,
            "requires_loop_approval": True,
            "route_command_id": "watch-build-until-green",
            "approval_command_id": "request-build-watch-loop",
            "next_action": "Review workspace diff, create a snapshot, then approve the exact Build Until Green request." if workspace_count else "Import a sealed Code Workspace before starting a build-watch loop.",
        },
        "workspace_rows": workspace_rows,
        "candidate_commands": command_rows,
        "loop_rows": _loop_rows(loop_limit),
        "sequence_rows": _sequence_rows(workspace_count, command_count, runner["state"], loop_limit),
        "guard_rows": _guard_rows(runner, loop_limit),
        "evidence_rows": [
            {
                "id": "activity-ledger",
                "state": "loading",
                "badge": "log",
                "title": "Activity timeline",
                "detail": "Every approved loop start, command result, edit summary, failure, retry, and final green result should be recorded.",
                "action": "open-activity-preflight",
                "actionLabel": "Activity",
            },
            {
                "id": "snapshot-evidence",
                "state": "warn" if workspace_count else "loading",
                "badge": "snap",
                "title": "Recovery snapshot",
                "detail": "Keep a pre-loop snapshot or backup checkpoint before any repeated build repair work.",
                "action": "open-code",
                "actionLabel": "Snapshot",
            },
            {
                "id": "final-build",
                "state": "loading",
                "badge": "green",
                "title": "Final build proof",
                "detail": "Keep the final command, exit code, output summary, changed files, and rollback note.",
                "action": "open-code-workspace-map",
                "actionLabel": "Map",
            },
        ],
        "api_actions": _api_actions(),
        "approval": {
            "required": True,
            "gate": "Build Until Green loop approval",
            "policy": "This endpoint only plans repeated build work. It does not start loops, run builds, edit files, create or restore snapshots, install dependencies, use network access, commit changes, or execute shell commands.",
            "disallowed_by_default": [
                "loop start",
                "build command execution",
                "file edit",
                "snapshot create/restore",
                "dependency install",
                "network fetch",
                "commit",
            ],
        },
        "paths": {
            "workspaces": "data/code-workspaces/workspaces.json",
            "workspace_root": "data/code-workspaces",
            "worker_queue": runner["worker_dir"] or "data/code-workspaces/.worker",
            "operator_activity": "data/operator_activity.json",
            "operator_workflows": "data/operator_workflows.json",
        },
        "errors": {
            "workspaces": workspace_error,
        },
    }
