"""Read-only operator repair planning for local services."""

from __future__ import annotations

import time
from typing import Any

from src.operator_checks import run_operator_checks, run_operator_service_snapshot

SERVICE_STATES = {"ok", "warn", "error", "loading"}
SUPPORT_SERVICE_IDS = {"chromadb", "searxng", "ntfy"}
APP_SERVICE_IDS = {"cleverly-api", "data-dir", "logs-dir", "code-worker-queue"}


def _state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in SERVICE_STATES else "warn"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _host_commands(container_plan: dict[str, Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in _as_list(container_plan.get("host_commands")):
        if not isinstance(item, dict):
            continue
        risk = str(item.get("risk") or "approval-required").strip().lower()
        commands.append(
            {
                "label": str(item.get("label") or "Host command").strip(),
                "risk": "read-only" if risk == "read-only" else "approval-required",
                "command": str(item.get("command") or "").strip(),
                "executes": False,
            }
        )
    return commands


def _command_by_label(commands: list[dict[str, Any]], label: str) -> dict[str, Any]:
    for command in commands:
        if command.get("label") == label:
            return command
    return {}


def _step(
    step_id: str,
    title: str,
    detail: str,
    *,
    state: str,
    badge: str,
    risk: str = "read-only",
    command: dict[str, Any] | None = None,
    action: str = "",
    action_label: str = "",
) -> dict[str, Any]:
    command = command or {}
    risk = "read-only" if risk == "read-only" else "approval-required"
    return {
        "id": step_id,
        "state": _state(state),
        "badge": badge,
        "title": title,
        "detail": detail,
        "risk": risk,
        "approval_required": risk != "read-only",
        "command_label": command.get("label") or "",
        "command": command.get("command") or "",
        "executes": False,
        "action": action,
        "actionLabel": action_label,
    }


def _service_recommendation(service: dict[str, Any]) -> tuple[str, str, str]:
    service_id = str(service.get("id") or "")
    state = _state(service.get("state"))
    required = service.get("required") is True
    if state == "ok":
        return "No repair needed", "read-only", "Service is responding or its local path exists."
    if service_id == "data-dir":
        return (
            "Verify the Docker data volume mount before restarting anything",
            "approval-required",
            "Missing app data is a data-boundary issue; inspect the volume before repair.",
        )
    if service_id in APP_SERVICE_IDS or required:
        return (
            "Inspect core logs, then recreate only the affected app service if approved",
            "approval-required",
            "Core Cleverly services should be repaired one service at a time after log review.",
        )
    if service_id in SUPPORT_SERVICE_IDS:
        return (
            "Start optional support services without pulling images if the feature is needed",
            "approval-required",
            "Support services are optional; use the local profile without network pulls.",
        )
    if service_id == "ollama":
        return (
            "Review the local Ollama endpoint or bundled Ollama overlay",
            "approval-required",
            "Model serving can be local or external; do not pull models automatically.",
        )
    return (
        "Review service configuration before repair",
        "read-only" if state == "loading" else "approval-required",
        "The probe does not map to a specific automatic repair command.",
    )


def _service_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for service in _as_list(snapshot.get("services")):
        if not isinstance(service, dict):
            continue
        state = _state(service.get("state"))
        recommendation, risk, reason = _service_recommendation(service)
        rows.append(
            {
                "id": str(service.get("id") or ""),
                "label": str(service.get("label") or service.get("id") or "Local service"),
                "state": state,
                "badge": str(service.get("kind") or ("core" if service.get("required") else "opt")),
                "required": service.get("required") is True,
                "detail": str(service.get("detail") or ""),
                "target": str(service.get("target") or ""),
                "risk": risk,
                "approval_required": risk != "read-only",
                "recommended_step": recommendation,
                "reason": reason,
                "executes": False,
            }
        )
    return rows


def _summary(service_rows: list[dict[str, Any]], service_snapshot: dict[str, Any]) -> dict[str, Any]:
    required_issues = [
        row for row in service_rows
        if row["required"] and row["state"] in {"warn", "error", "loading"}
    ]
    optional_issues = [
        row for row in service_rows
        if not row["required"] and row["state"] in {"warn", "error", "loading"}
    ]
    hard_errors = [row for row in service_rows if row["state"] == "error"]
    state = "error" if required_issues or hard_errors else ("warn" if optional_issues else "ok")
    if required_issues:
        next_action = "Inspect logs and data mounts before any approved service recreation."
    elif hard_errors:
        next_action = "Review failed optional service probes before enabling dependent features."
    elif optional_issues:
        next_action = "Optional support services can stay offline unless you need their features."
    else:
        next_action = "No repair action is needed from the backend service snapshot."
    existing = _as_dict(service_snapshot.get("summary"))
    return {
        "state": state,
        "ok": int(existing.get("ok") or 0),
        "warn": int(existing.get("warn") or 0),
        "error": int(existing.get("error") or 0),
        "loading": int(existing.get("loading") or 0),
        "required": int(existing.get("required") or 0),
        "total": len(service_rows),
        "required_issues": len(required_issues),
        "optional_issues": len(optional_issues),
        "approval_required": any(row["approval_required"] for row in service_rows if row["state"] != "ok"),
        "next_action": next_action,
    }


def _plan_steps(summary: dict[str, Any], service_rows: list[dict[str, Any]], commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compose_ps = _command_by_label(commands, "List compose service state")
    docker_ps = _command_by_label(commands, "List running containers")
    logs = _command_by_label(commands, "Inspect core service logs")
    recreate = _command_by_label(commands, "Recreate app service only")
    start_support = _command_by_label(commands, "Start optional support services without pulling")
    required_issues = [row for row in service_rows if row["required"] and row["state"] in {"warn", "error", "loading"}]
    support_issues = [row for row in service_rows if row["id"] in SUPPORT_SERVICE_IDS and row["state"] in {"warn", "error", "loading"}]
    ollama_issues = [row for row in service_rows if row["id"] == "ollama" and row["state"] in {"warn", "error", "loading"}]

    steps = [
        _step(
            "status-first",
            "Capture container status first",
            "Run read-only Docker/Compose status checks before choosing a repair.",
            state="ok",
            badge="read",
            command=compose_ps or docker_ps,
            action="check-containers",
            action_label="System",
        ),
        _step(
            "backup-checkpoint",
            "Confirm backup and rollback coverage",
            "Review local backup coverage before any restart, data mount repair, or service recreation.",
            state="warn" if summary["approval_required"] else "ok",
            badge="roll",
            action="open-backup-preflight",
            action_label="Backup",
        ),
        _step(
            "inspect-logs",
            "Inspect logs before repair",
            "Use logs as evidence before restarting or recreating a container.",
            state="warn" if summary["state"] != "ok" else "ok",
            badge="logs",
            command=logs,
            action="open-activity-preflight",
            action_label="Activity",
        ),
    ]
    if required_issues:
        names = ", ".join(row["label"] for row in required_issues[:3])
        steps.append(
            _step(
                "repair-core-service",
                "Repair only the affected core service",
                f"Approval is required before recreating app services. Current core issue candidates: {names}.",
                state="error",
                badge="ask",
                risk="approval-required",
                command=recreate,
                action="request-container-fix",
                action_label="Ask",
            )
        )
    if support_issues:
        names = ", ".join(row["label"] for row in support_issues[:3])
        steps.append(
            _step(
                "start-support-services",
                "Start optional support services without pulls",
                f"Approval is required before starting optional support services. Current candidates: {names}.",
                state="warn",
                badge="ask",
                risk="approval-required",
                command=start_support,
                action="request-container-fix",
                action_label="Ask",
            )
        )
    if ollama_issues:
        steps.append(
            _step(
                "review-ollama",
                "Review local Ollama model service",
                "Verify the configured Ollama endpoint or bundled overlay; model pulls remain explicit.",
                state="warn",
                badge="model",
                risk="approval-required",
                action="verify-model",
                action_label="Models",
            )
        )
    steps.append(
        _step(
            "network-pull-guard",
            "Keep repair offline by default",
            "Do not pull images, download models, use network diagnostics, delete volumes, or change host files unless explicitly approved.",
            state="ok",
            badge="safe",
            action="open-trust-controls",
            action_label="Trust",
        )
    )
    return steps


def _approval_packet(
    summary: dict[str, Any],
    service_rows: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    affected = [row for row in service_rows if row["state"] != "ok"]
    required_affected = [row for row in affected if row["required"]]
    optional_affected = [row for row in affected if not row["required"]]
    command_by_text = {str(command.get("command") or ""): command for command in commands if command.get("command")}
    candidate_commands: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    for step in steps:
        command_text = str(step.get("command") or "")
        if not command_text or command_text in seen_commands:
            continue
        seen_commands.add(command_text)
        command = command_by_text.get(command_text) or {}
        candidate_commands.append({
            "label": command.get("label") or step.get("command_label") or step.get("title") or "Host command",
            "command": command_text,
            "risk": command.get("risk") or step.get("risk") or "approval-required",
            "step_id": step.get("id") or "",
            "executes": False,
            "approval_required": (command.get("risk") or step.get("risk")) != "read-only",
        })

    preflight = [
        {
            "id": "capture-status",
            "state": "ok",
            "title": "Capture current container status",
            "detail": "Use read-only compose/docker status before selecting a repair command.",
            "executes": False,
        },
        {
            "id": "inspect-logs",
            "state": "warn" if summary["state"] != "ok" else "ok",
            "title": "Inspect relevant logs",
            "detail": "Review only the affected service logs before restart or recreation.",
            "executes": False,
        },
        {
            "id": "verify-data-boundary",
            "state": "error" if any(row["id"] == "data-dir" for row in required_affected) else "ok",
            "title": "Verify data volume boundary",
            "detail": "Confirm /app/data and backup posture before any service recreation.",
            "executes": False,
        },
        {
            "id": "repair-one-service",
            "state": "warn" if affected else "ok",
            "title": "Repair one service at a time",
            "detail": "Restart/recreate only the named unhealthy service after explicit approval.",
            "executes": False,
        },
    ]
    disallowed = [
        "pull images",
        "download models",
        "delete volumes",
        "delete host files",
        "change credentials",
        "run network diagnostics",
        "restart healthy services",
        "modify app data without backup review",
    ]
    if required_affected:
        scope = f"{len(required_affected)} required service/path issue(s) require owner approval before repair."
    elif optional_affected:
        scope = f"{len(optional_affected)} optional service issue(s) can stay offline unless the feature is needed."
    else:
        scope = "No affected services are visible; repair is optional and should remain read-only."
    return {
        "state": summary["state"],
        "approval_required": summary["approval_required"],
        "scope": scope,
        "affected_count": len(affected),
        "required_affected_count": len(required_affected),
        "optional_affected_count": len(optional_affected),
        "affected_services": [
            {
                "id": row["id"],
                "label": row["label"],
                "state": row["state"],
                "required": row["required"],
                "recommended_step": row["recommended_step"],
                "reason": row["reason"],
                "target": row["target"],
            }
            for row in affected[:10]
        ],
        "candidate_host_commands": candidate_commands[:8],
        "preflight_checklist": preflight,
        "disallowed_actions": disallowed,
        "operator_prompt": (
            "Ask before applying any repair. Use the read-only status/log commands first, "
            "repair only named unhealthy services, avoid pulls/downloads/deletes, and preserve local data."
        ),
        "executes": False,
        "writes": False,
        "uses_network": False,
    }


def run_operator_repair_plan() -> dict[str, Any]:
    """Return a read-only repair plan for service/container issues."""
    service_snapshot = run_operator_service_snapshot()
    checks = run_operator_checks()
    container_plan = _as_dict(checks.get("container_plan"))
    commands = _host_commands(container_plan)
    service_rows = _service_rows(service_snapshot)
    summary = _summary(service_rows, service_snapshot)
    steps = _plan_steps(summary, service_rows, commands)
    approval_packet = _approval_packet(summary, service_rows, steps, commands)
    return {
        "mode": "read-only-local-repair-plan",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "services": service_rows,
        "steps": steps,
        "approval_packet": approval_packet,
        "host_commands": commands,
        "container_plan": {
            "source": container_plan.get("source") or "",
            "compose_project": container_plan.get("compose_project") or "",
            "docker_socket_mounted": container_plan.get("docker_socket_mounted") is True,
            "services": _as_list(container_plan.get("services")),
        },
        "approval": {
            "required": any(step["approval_required"] for step in steps),
            "gate": "Ask To Fix",
            "policy": "This endpoint never executes host Docker commands. Restarts, starts, pulls, deletes, network use, and host filesystem changes require explicit approval.",
            "disallowed_by_default": [
                "docker pull",
                "model download",
                "volume delete",
                "host file deletion",
                "network diagnostics",
            ],
        },
        "paths": {
            "activity": "data/operator_activity.json",
            "policy": "data/operator_policy.json",
            "data": next((row["target"] for row in service_rows if row["id"] == "data-dir"), ""),
            "logs": next((row["target"] for row in service_rows if row["id"] == "logs-dir"), ""),
        },
        "note": "Read-only plan generation only; no Docker socket, restart, pull, delete, filesystem repair, network action, or host shell command is executed.",
    }
