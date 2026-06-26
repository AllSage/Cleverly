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


def _code_alert_rows(
    workspace_count: int,
    command_count: int,
    runner: dict[str, Any],
    command_rows: list[dict[str, Any]],
    workspace_error: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if workspace_error:
        rows.append(
            {
                "id": "workspace-source-error",
                "state": "error",
                "badge": "code",
                "title": "Code workspace evidence unavailable",
                "detail": workspace_error,
                "action": "open-code-workspace-map",
                "actionLabel": "Code",
                "requires_approval": False,
            }
        )
    if workspace_count < 1:
        rows.append(
            {
                "id": "workspace-required",
                "state": "error",
                "badge": "repo",
                "title": "Code workspace required",
                "detail": "Import or open a sealed Code Workspace before planning test execution.",
                "action": "open-code",
                "actionLabel": "Code",
                "requires_approval": False,
            }
        )
    if workspace_count and command_count < 1:
        rows.append(
            {
                "id": "manual-test-command-required",
                "state": "warn",
                "badge": "cmd",
                "title": "Manual test command required",
                "detail": "No common local test command was detected; choose the exact command in Code Workspace.",
                "action": "open-code",
                "actionLabel": "Command",
                "requires_approval": True,
            }
        )
    if runner.get("state") != "ok":
        rows.append(
            {
                "id": "runner-isolation-review",
                "state": "warn",
                "badge": "run",
                "title": "Runner isolation needs review",
                "detail": runner.get("detail") or "Review runner isolation before approving local test commands.",
                "action": "open-code-workspace-map",
                "actionLabel": "Map",
                "requires_approval": False,
            }
        )
    if any(not row.get("command") for row in command_rows):
        rows.append(
            {
                "id": "manual-candidate-present",
                "state": "warn",
                "badge": "manual",
                "title": "Manual command candidate present",
                "detail": "At least one workspace needs an explicit operator-selected test command.",
                "action": "open-code",
                "actionLabel": "Command",
                "requires_approval": True,
            }
        )
    rows.extend(
        [
            {
                "id": "snapshot-approval-required",
                "state": "warn" if workspace_count else "loading",
                "badge": "snap",
                "title": "Snapshot approval required",
                "detail": "Create a recovery snapshot before approving test commands that may inform follow-up edits.",
                "action": "open-code",
                "actionLabel": "Snapshot",
                "requires_approval": True,
            },
            {
                "id": "test-run-approval-required",
                "state": "warn",
                "badge": "ask",
                "title": "Test run approval required",
                "detail": "Running tests stays inside Code Workspace and requires approving the exact command.",
                "action": "open-code",
                "actionLabel": "Run",
                "requires_approval": True,
            },
        ]
    )
    return rows[:8]


def _route_rows() -> list[dict[str, Any]]:
    common = {
        "run_api": "/api/code-workspaces/{workspace_id}/run",
        "snapshot_api": "/api/code-workspaces/{workspace_id}/snapshots",
        "status_api": "/api/code-workspaces/{workspace_id}/status",
        "diff_api": "/api/code-workspaces/{workspace_id}/diff",
        "executes": False,
        "requires_approval": True,
        "runs_tests": False,
        "changes_files": False,
        "creates_snapshot": False,
        "applies_diffs": False,
        "restores_snapshots": False,
        "commits_changes": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "dashboard-code-chip",
            "state": "ok",
            "badge": "dash",
            "title": "Dashboard code test chip",
            "detail": "Command Center code controls route test requests to this read-only Code Test Plan.",
            "entry": "dashboard",
            "command_id": "run-tests",
            "action": "run-tests",
            "actionLabel": "Test Plan",
        },
        {
            **common,
            "id": "text-command",
            "state": "ok",
            "badge": "text",
            "title": "Typed command route",
            "detail": "Text such as 'Open my code workspace and run the tests' opens this plan before any exact command approval.",
            "entry": "text",
            "command_id": "run-tests",
            "action": "run-tests",
            "actionLabel": "Test Plan",
        },
        {
            **common,
            "id": "command-palette",
            "state": "ok",
            "badge": "pal",
            "title": "Command palette route",
            "detail": "The global command palette exposes Run Tests as an approval-tier Code command.",
            "entry": "palette",
            "command_id": "run-tests",
            "action": "run-tests",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "voice-command",
            "state": "ok",
            "badge": "voice",
            "title": "Voice command route",
            "detail": "Voice input follows the same route preflight and resolves the target phrase to run-tests.",
            "entry": "voice",
            "command_id": "run-tests",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "workflow-handoff",
            "state": "ok",
            "badge": "flow",
            "title": "Agent workflow handoff",
            "detail": "Agent and build-watch workflows can hand off to run-tests while execution remains approval-gated in Code Workspace.",
            "entry": "workflow",
            "command_id": "run-tests",
            "action": "open-automation-map",
            "actionLabel": "Automation",
        },
    ]


def _handoff_row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    *,
    target_api: str,
    approval_command_id: str = "run-tests",
    requires_approval: bool = True,
    runs_tests: bool = False,
    changes_files: bool = False,
    creates_snapshot: bool = False,
    reads_diff: bool = False,
    restores_snapshots: bool = False,
    commits_changes: bool = False,
    writes_activity: bool = False,
    runs_shell: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state if state in {"ok", "warn", "error", "loading"} else "warn",
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "target_api": target_api,
        "approval_command_id": approval_command_id,
        "requires_approval": requires_approval,
        "executes": False,
        "runs_tests": False,
        "changes_files": False,
        "creates_snapshot": False,
        "reads_diff": False,
        "restores_snapshots": False,
        "commits_changes": False,
        "writes_activity": False,
        "runs_shell": False,
        "uses_network": False,
        "gated_operation": {
            "runs_tests": runs_tests,
            "changes_files": changes_files,
            "creates_snapshot": creates_snapshot,
            "reads_diff": reads_diff,
            "restores_snapshots": restores_snapshots,
            "commits_changes": commits_changes,
            "writes_activity": writes_activity,
            "runs_shell": runs_shell,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(workspace_count: int, command_count: int, runner_state: str) -> list[dict[str, Any]]:
    workspace_state = "ok" if workspace_count else "warn"
    command_state = "warn" if command_count else "loading"
    runner_ready = runner_state == "ok"
    return [
        _handoff_row(
            "code-workspace-selection-handoff",
            workspace_state,
            "repo",
            "Workspace selection handoff",
            f"{workspace_count} sealed workspace(s) are visible; choose the exact repo before any command can be staged.",
            "open-code",
            "Code",
            target_api="/api/code-workspaces",
            requires_approval=False,
        ),
        _handoff_row(
            "code-status-diff-handoff",
            workspace_state,
            "diff",
            "Status and diff review handoff",
            "Status and diff review should precede test approval so the operator sees pending local changes.",
            "open-code-workspace-map",
            "Map",
            target_api="/api/code-workspaces/{workspace_id}/diff",
            requires_approval=False,
            reads_diff=True,
        ),
        _handoff_row(
            "code-snapshot-checkpoint-handoff",
            "warn" if workspace_count else "loading",
            "snap",
            "Snapshot checkpoint handoff",
            "Snapshot creation writes rollback evidence before test commands or follow-up edits.",
            "open-code",
            "Snapshot",
            target_api="/api/code-workspaces/{workspace_id}/snapshots",
            creates_snapshot=True,
            changes_files=True,
        ),
        _handoff_row(
            "code-exact-command-approval-handoff",
            command_state,
            "ask",
            "Exact command approval handoff",
            f"{command_count} candidate command(s) are visible; approve the exact command in Code Workspace before execution.",
            "run-tests",
            "Approve",
            target_api="/api/code-workspaces/{workspace_id}/run",
            runs_tests=True,
            runs_shell=True,
        ),
        _handoff_row(
            "code-runner-isolation-handoff",
            "ok" if runner_ready else "warn",
            "run",
            "Runner isolation handoff",
            "Worker-sidecar execution is preferred; in-process runner mode needs policy review before commands run.",
            "open-code-workspace-map",
            "Runner",
            target_api="/api/operator/runtime-plan",
            requires_approval=not runner_ready,
            runs_shell=True,
        ),
        _handoff_row(
            "code-activity-output-handoff",
            "ok",
            "log",
            "Activity output handoff",
            "Approved test runs should preserve stdout, stderr, status, and recovery notes in the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
            writes_activity=True,
        ),
        _handoff_row(
            "code-recovery-rollback-handoff",
            "warn" if workspace_count else "loading",
            "recover",
            "Recovery and rollback handoff",
            "Failed tests, generated changes, or agent edits should route through Recovery Map before restore or retry.",
            "open-recovery-map",
            "Recovery",
            target_api="/api/operator/recovery-plan",
            restores_snapshots=True,
            changes_files=True,
        ),
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
    alert_rows = _code_alert_rows(len(workspace_rows), candidate_count, runner, command_rows, workspace_error)
    route_rows = _route_rows()
    handoff_rows = _handoff_rows(len(workspace_rows), candidate_count, runner["state"])
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
            "code_alert_count": len(alert_rows),
            "critical_code_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "route_count": len(route_rows),
            "route_ready_count": len([row for row in route_rows if row.get("state") == "ok"]),
            "entry_route_count": len(route_rows),
            "entry_route_ready_count": len([row for row in route_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
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
        "route_rows": route_rows,
        "entry_rows": route_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
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
