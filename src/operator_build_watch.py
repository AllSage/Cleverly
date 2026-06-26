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


def _entry_rows(workspace_count: int, command_count: int, runner_state: str, max_iterations: int) -> list[dict[str, Any]]:
    state = "ok" if workspace_count and command_count and runner_state == "ok" else ("warn" if workspace_count else "loading")
    common = {
        "command_id": "watch-build-until-green",
        "approval_command_id": "request-build-watch-loop",
        "code_command_id": "open-code",
        "map_command_id": "open-code-workspace-map",
        "loops_command_id": "open-loops",
        "activity_command_id": "open-activity-preflight",
        "route_api": "/api/operator/route",
        "plan_api": "/api/operator/build-watch-plan",
        "run_api": "/api/code-workspaces/{workspace_id}/run",
        "snapshot_api": "/api/code-workspaces/{workspace_id}/snapshots",
        "requires_approval": True,
        "max_iterations": max_iterations,
        "executes": False,
        "starts_loop": False,
        "runs_build": False,
        "edits_files": False,
        "creates_snapshot": False,
        "restores_snapshot": False,
        "installs_dependencies": False,
        "commits_changes": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "build-watch-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard build-watch route",
            "detail": "The Command Center opens Build Watch Plan before any loop start, build command, edit, snapshot, or retry work.",
            "action": "watch-build-until-green",
            "actionLabel": "Plan",
        },
        {
            **common,
            "id": "build-watch-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed build-watch request route",
            "detail": "Typed requests such as watch this repo until the build passes route to read-only build-watch evidence first.",
            "action": "watch-build-until-green",
            "actionLabel": "Review",
        },
        {
            **common,
            "id": "build-watch-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette build-watch route",
            "detail": "The command palette separates opening the plan from the approval-gated Build Until Green start request.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "build-watch-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice build-watch route",
            "detail": "Voice mode can open build-watch preflight without starting loops, running builds, or editing files.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "build-watch-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow build-watch handoff",
            "detail": "Automation handoffs can show command candidates, iteration limits, approval gates, and activity requirements before loop start.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
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
    approval_command_id: str = "request-build-watch-loop",
    requires_approval: bool = True,
    starts_loop: bool = False,
    runs_build: bool = False,
    edits_files: bool = False,
    creates_snapshot: bool = False,
    reads_diff: bool = False,
    restores_snapshot: bool = False,
    installs_dependencies: bool = False,
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
        "starts_loop": False,
        "runs_build": False,
        "edits_files": False,
        "creates_snapshot": False,
        "reads_diff": False,
        "restores_snapshot": False,
        "installs_dependencies": False,
        "commits_changes": False,
        "writes_activity": False,
        "runs_shell": False,
        "uses_network": False,
        "gated_operation": {
            "starts_loop": starts_loop,
            "runs_build": runs_build,
            "edits_files": edits_files,
            "creates_snapshot": creates_snapshot,
            "reads_diff": reads_diff,
            "restores_snapshot": restores_snapshot,
            "installs_dependencies": installs_dependencies,
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
            "build-watch-workspace-selection-handoff",
            workspace_state,
            "repo",
            "Workspace selection handoff",
            f"{workspace_count} sealed workspace(s) are visible; choose the exact repo before any loop can be staged.",
            "open-code",
            "Code",
            target_api="/api/code-workspaces",
            requires_approval=False,
        ),
        _handoff_row(
            "build-watch-status-diff-handoff",
            workspace_state,
            "diff",
            "Status and diff review handoff",
            "Status and diff review should precede Build Watch approval so the operator sees pending local changes.",
            "open-code-workspace-map",
            "Map",
            target_api="/api/code-workspaces/{workspace_id}/diff",
            requires_approval=False,
            reads_diff=True,
        ),
        _handoff_row(
            "build-watch-snapshot-checkpoint-handoff",
            "warn" if workspace_count else "loading",
            "snap",
            "Snapshot checkpoint handoff",
            "Snapshot creation writes rollback evidence before repeated build commands or repair edits.",
            "open-code",
            "Snapshot",
            target_api="/api/code-workspaces/{workspace_id}/snapshots",
            creates_snapshot=True,
            edits_files=True,
        ),
        _handoff_row(
            "build-watch-loop-approval-handoff",
            command_state,
            "ask",
            "Loop approval handoff",
            f"{command_count} candidate command(s) are visible; approve the exact Build Until Green request before a loop starts.",
            "request-build-watch-loop",
            "Approve",
            target_api="/api/operator/workflows",
            starts_loop=True,
        ),
        _handoff_row(
            "build-watch-runner-build-handoff",
            "ok" if runner_ready else "warn",
            "run",
            "Runner build handoff",
            "Worker-sidecar execution is preferred; in-process runner mode needs policy review before build commands run.",
            "open-code-workspace-map",
            "Runner",
            target_api="/api/code-workspaces/{workspace_id}/run",
            requires_approval=not runner_ready,
            runs_build=True,
            runs_shell=True,
        ),
        _handoff_row(
            "build-watch-repair-iteration-handoff",
            command_state,
            "fix",
            "Repair iteration handoff",
            "Each failed build pass must route through approved repair work, rerun evidence, and stop conditions.",
            "open-loops",
            "Loops",
            target_api="/api/code-workspaces/{workspace_id}/run",
            starts_loop=True,
            runs_build=True,
            edits_files=True,
            runs_shell=True,
        ),
        _handoff_row(
            "build-watch-recovery-rollback-handoff",
            "warn" if workspace_count else "loading",
            "recover",
            "Recovery and rollback handoff",
            "Failed loops, generated changes, or unsafe edits should route through Recovery Map before restore or retry.",
            "open-recovery-map",
            "Recovery",
            target_api="/api/operator/recovery-plan",
            restores_snapshot=True,
            edits_files=True,
        ),
        _handoff_row(
            "build-watch-activity-ledger-handoff",
            "ok",
            "log",
            "Activity ledger handoff",
            "Approved loop starts, command output, edits, retries, failures, rollback notes, and final green proof stay in the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
            writes_activity=True,
        ),
    ]


def _build_alert_rows(
    workspace_count: int,
    command_count: int,
    runner: dict[str, Any],
    command_rows: list[dict[str, Any]],
    workspace_error: str,
    max_iterations: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if workspace_error:
        rows.append(
            {
                "id": "workspace-source-error",
                "state": "error",
                "badge": "code",
                "title": "Build workspace evidence unavailable",
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
                "detail": "Import a sealed Code Workspace before planning a Build Until Green loop.",
                "action": "open-code",
                "actionLabel": "Code",
                "requires_approval": False,
            }
        )
    if workspace_count and command_count < 1:
        rows.append(
            {
                "id": "manual-build-command-required",
                "state": "warn",
                "badge": "cmd",
                "title": "Manual build command required",
                "detail": "No common local build/check command was detected; choose the exact command before loop approval.",
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
                "detail": runner.get("detail") or "Review runner isolation before approving repeated build work.",
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
                "title": "Manual build candidate present",
                "detail": "At least one workspace needs an explicit operator-selected build/check command.",
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
                "title": "Recovery snapshot approval required",
                "detail": "Create a pre-loop snapshot before repeated build repair work.",
                "action": "open-code",
                "actionLabel": "Snapshot",
                "requires_approval": True,
            },
            {
                "id": "loop-approval-required",
                "state": "warn",
                "badge": "ask",
                "title": "Build Watch loop approval required",
                "detail": f"Starting Build Until Green requires approving the exact request and max {max_iterations} passes.",
                "action": "request-build-watch-loop",
                "actionLabel": "Ask",
                "requires_approval": True,
            },
            {
                "id": "activity-evidence-required",
                "state": "warn",
                "badge": "log",
                "title": "Loop activity evidence required",
                "detail": "Each run, edit, failure, retry, and final green result should be recorded in the activity timeline.",
                "action": "open-activity-preflight",
                "actionLabel": "Activity",
                "requires_approval": False,
            },
        ]
    )
    return rows[:8]


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
    alert_rows = _build_alert_rows(workspace_count, command_count, runner, command_rows, workspace_error, loop_limit)
    entry_rows = _entry_rows(workspace_count, command_count, runner["state"], loop_limit)
    handoff_rows = _handoff_rows(workspace_count, command_count, runner["state"])

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
            "build_alert_count": len(alert_rows),
            "critical_build_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
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
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
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
