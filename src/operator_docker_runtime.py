"""Read-only Docker runtime operations evidence for Cleverly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ENTRY_POINTS = (
    ("dashboard", "dash", "Dashboard Docker runtime control", "open-local-services-map"),
    ("text", "text", "Typed container health request", "open-local-services-map"),
    ("palette", "pal", "Palette Docker runtime route", "open-command-palette"),
    ("voice", "voice", "Voice Docker runtime route", "open-voice-preflight"),
    ("workflow", "flow", "Workflow repair handoff", "open-automation-map"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _count(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state(*states: str) -> str:
    clean = [str(item or "").lower() for item in states if item]
    if "error" in clean:
        return "error"
    if "warn" in clean or "loading" in clean:
        return "warn"
    return "ok"


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(plan.get("summary"))


def _summary_state(plan: dict[str, Any], default: str = "warn") -> str:
    summary = _summary(plan)
    return _trim(summary.get("state") or plan.get("state") or default, 40).lower() or default


def _row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str = "Open",
    *,
    requires_approval: bool | None = None,
) -> dict[str, Any]:
    normalized_state = state if state in {"ok", "warn", "error", "loading"} else "warn"
    return {
        "id": row_id,
        "state": normalized_state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": normalized_state != "ok" if requires_approval is None else requires_approval,
        "restarts_services": False,
        "starts_services": False,
        "repairs_services": False,
        "builds_images": False,
        "recreates_services": False,
        "pulls_images": False,
        "runs_docker": False,
        "runs_shell": False,
        "writes_files": False,
        "deletes_volumes": False,
        "uses_network": False,
    }


def _runtime_row(runtime_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(runtime_plan)
    docker_like = summary.get("docker_like") is True
    offline = summary.get("offline") is True
    state = "ok" if docker_like and offline else _summary_state(runtime_plan, "warn")
    return _row(
        "docker-runtime-mode",
        state,
        "run",
        "Docker and offline runtime mode",
        f"docker_like={docker_like}; offline={offline}; sealed runtime items={_count(summary.get('sealed_runtime_count'))}.",
        "open-machine-preflight",
        "Runtime",
    )


def _services_row(services_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(services_plan)
    required_issues = _count(summary.get("required_issue_count"))
    optional_issues = _count(summary.get("optional_issue_count"))
    state = "error" if required_issues else ("warn" if optional_issues else _summary_state(services_plan, "ok"))
    return _row(
        "docker-local-services",
        state,
        "svc",
        "Local service posture",
        f"{_count(summary.get('service_count'))} service row(s); {required_issues} required issue(s); {optional_issues} optional issue(s).",
        "open-local-services-map",
        "Services",
    )


def _container_row(services_plan: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(services_plan)
    check_summary = _as_dict(_as_dict(checks.get("container_plan")).get("summary"))
    visible = _count(summary.get("container_status_visible_count"))
    unhealthy = _count(summary.get("unhealthy_container_count"))
    alert_count = _count(check_summary.get("checks_container_alert_count"))
    state = "error" if unhealthy else ("warn" if not visible or alert_count else "ok")
    return _row(
        "docker-container-status",
        state,
        "dock",
        "Captured container status",
        f"{visible}/{_count(summary.get('container_status_count'))} expected container status row(s) visible; {unhealthy} unhealthy/missing row(s); {alert_count} check alert(s).",
        "open-container-repair-plan",
        "Repair",
    )


def _host_command_row(services_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(services_plan)
    gated = _count(summary.get("approval_gated_command_count"))
    state = "warn" if gated else "ok"
    return _row(
        "docker-host-command-gates",
        state,
        "ask",
        "Host Docker command gates",
        f"{_count(summary.get('host_command_count'))} candidate host command(s); {gated} require explicit approval.",
        "open-container-repair-plan",
        "Repair",
    )


def _repair_row(repair_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(repair_plan)
    state = _summary_state(repair_plan)
    return _row(
        "docker-repair-plan",
        state,
        "fix",
        "Repair plan and approval packet",
        f"{_count(summary.get('total'))} service row(s); {summary.get('next_action') or 'repair actions stay approval-gated'}.",
        "open-container-repair-plan",
        "Repair",
    )


def _volume_row(runtime_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(runtime_plan)
    sealed_count = _count(summary.get("sealed_runtime_count"))
    sealed_ready = _count(summary.get("sealed_runtime_ready_count"))
    state = "ok" if sealed_count and sealed_ready >= sealed_count else ("warn" if sealed_count else "loading")
    return _row(
        "docker-sealed-volumes",
        state,
        "vol",
        "Sealed volumes and data roots",
        f"{sealed_ready}/{sealed_count} sealed runtime item(s) ready; volume checks are read-only.",
        "open-local-data-map",
        "Data",
    )


def _support_services_row(ai_runtime_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(ai_runtime_plan)
    state = _summary_state(ai_runtime_plan)
    return _row(
        "docker-ai-support-services",
        state,
        "ai",
        "AI support services",
        f"{_count(summary.get('runtime_ready_count'))}/{_count(summary.get('runtime_row_count'))} local AI runtime row(s) ready across Ollama, ChromaDB, SearXNG, models, and training.",
        "open-model-preflight",
        "Models",
    )


def _deployment_row() -> dict[str, Any]:
    row = _row(
        "docker-image-deployment-boundary",
        "warn",
        "img",
        "Backend image deployment boundary",
        (
            "Python backend changes are baked into the Cleverly image and require an approved image rebuild "
            "and app/proxy recreate before new operator routes are visible; static stylesheet overrides may be bind-mounted."
        ),
        "open-container-repair-plan",
        "Deploy",
    )
    row["builds_images"] = False
    row["recreates_services"] = False
    return row


def _handoff_row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    *,
    target_api: str = "/api/operator/docker-runtime-plan",
    approval_command_id: str = "request-container-fix",
    target_host_command: str = "",
    activity_api: str = "/api/operator/activity",
    rollback_api: str = "/api/operator/recovery-plan",
    requires_approval: bool = True,
) -> dict[str, Any]:
    row = _row(
        row_id,
        state,
        badge,
        title,
        detail,
        action,
        action_label,
        requires_approval=requires_approval,
    )
    row.update({
        "target_api": target_api,
        "approval_command_id": approval_command_id,
        "target_host_command": target_host_command,
        "activity_api": activity_api,
        "rollback_api": rollback_api,
    })
    return row


def _handoff_rows(
    *,
    runtime_plan: dict[str, Any],
    services_plan: dict[str, Any],
    repair_plan: dict[str, Any],
    checks: dict[str, Any],
) -> list[dict[str, Any]]:
    runtime_summary = _summary(runtime_plan)
    services_summary = _summary(services_plan)
    repair_summary = _summary(repair_plan)
    check_summary = _as_dict(_as_dict(checks.get("container_plan")).get("summary"))
    expected = _count(services_summary.get("container_status_count"))
    visible = _count(services_summary.get("container_status_visible_count"))
    unhealthy = _count(services_summary.get("unhealthy_container_count"))
    required_issues = _count(services_summary.get("required_issue_count"))
    optional_issues = _count(services_summary.get("optional_issue_count"))
    check_alerts = _count(check_summary.get("checks_container_alert_count"))
    offline = runtime_summary.get("offline") is True
    status_state = "ok" if visible and visible >= expected else "warn"
    repair_state = "error" if unhealthy or required_issues or _summary_state(repair_plan) == "error" else ("warn" if optional_issues or check_alerts else "ok")
    support_state = "error" if required_issues else ("warn" if optional_issues or unhealthy else "ok")
    activity_state = "warn" if unhealthy or repair_state != "ok" else "ok"
    return [
        _handoff_row(
            "docker-status-capture-handoff",
            status_state,
            "ps",
            "Host status capture handoff",
            f"{visible}/{expected} expected container status row(s) are captured; host Docker inspection remains an approved host-side metadata capture.",
            "check-containers",
            "Status",
            target_api="/api/operator/services-plan",
            target_host_command="docker compose ps --format json",
        ),
        _handoff_row(
            "docker-repair-restart-handoff",
            repair_state,
            "fix",
            "Repair and restart handoff",
            f"{unhealthy} unhealthy container row(s), {required_issues} required service issue(s), and {check_alerts} check alert(s) route through the repair plan before restart.",
            "open-container-repair-plan",
            "Repair",
            target_api="/api/operator/repair-plan",
            target_host_command="docker compose up -d --no-build --pull never",
        ),
        _handoff_row(
            "docker-support-start-handoff",
            support_state,
            "svc",
            "Support service start handoff",
            f"{optional_issues} optional support-service issue(s) can be started only through approval-gated local Compose commands.",
            "open-local-services-map",
            "Services",
            target_api="/api/operator/services-plan",
            target_host_command="docker compose --profile support up -d --no-build --pull never",
        ),
        _handoff_row(
            "docker-rebuild-recreate-handoff",
            "warn",
            "img",
            "Image rebuild and recreate handoff",
            "Backend route changes require an approved local image rebuild and app/proxy recreate before they appear in the running Docker runtime.",
            "open-container-repair-plan",
            "Deploy",
            target_host_command="docker compose build cleverly cleverly-proxy && docker compose up -d --no-build --pull never cleverly cleverly-proxy",
        ),
        _handoff_row(
            "docker-image-pull-egress-handoff",
            "warn",
            "net",
            "Image pull and network egress handoff",
            f"Offline mode is {'enabled' if offline else 'not enabled'}; image pulls are blocked unless the user explicitly approves network egress.",
            "open-offline-control",
            "Network",
            target_host_command="docker pull",
        ),
        _handoff_row(
            "docker-volume-delete-handoff",
            "warn",
            "del",
            "Volume deletion handoff",
            "Docker volume deletion is destructive and must show backup, rollback, and explicit approval evidence before any host command is prepared.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            target_host_command="docker volume rm",
        ),
        _handoff_row(
            "docker-activity-rollback-handoff",
            activity_state,
            "log",
            "Activity and rollback handoff",
            "Approved Docker repairs should write activity status, logs, retry state, and rollback pointers before and after host-side execution.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            target_host_command="",
        ),
    ]


def _entry_rows(ready: bool) -> list[dict[str, Any]]:
    rows = []
    for entry, badge, title, action in ENTRY_POINTS:
        rows.append({
            "id": f"docker-runtime-{entry}-route",
            "entry": entry,
            "state": "ok" if ready else "warn",
            "badge": badge,
            "title": title,
            "detail": "Docker runtime requests show runtime mode, service health, captured container status, host command gates, volumes, and repair approval before any Docker action.",
            "action": action,
            "actionLabel": "Open",
            "ready": ready,
            "docker_runtime_api": "/api/operator/docker-runtime-plan",
            "runtime_api": "/api/operator/runtime-plan",
            "services_api": "/api/operator/services-plan",
            "repair_api": "/api/operator/repair-plan",
            "checks_api": "/api/operator/checks",
            "ai_runtime_api": "/api/operator/ai-runtime-plan",
            "requires_approval": not ready,
            "restarts_services": False,
            "starts_services": False,
            "repairs_services": False,
            "builds_images": False,
            "recreates_services": False,
            "pulls_images": False,
            "runs_docker": False,
            "runs_shell": False,
            "writes_files": False,
            "deletes_volumes": False,
            "uses_network": False,
        })
    return rows


def _alert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        if row.get("state") not in {"warn", "error"}:
            continue
        alerts.append({
            "id": f"docker-runtime-alert-{row['id']}",
            "state": row.get("state"),
            "badge": row.get("badge") or "dock",
            "title": row.get("title"),
            "detail": row.get("detail"),
            "action": row.get("action") or "open-local-services-map",
            "actionLabel": "Review",
            "requires_approval": row.get("state") == "error",
            "builds_images": False,
            "recreates_services": False,
            "runs_docker": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return alerts[:12]


def _api_action(path: str, title: str, *, method: str = "GET", approval: bool = False, network: bool = False, destructive: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "method": method,
        "title": title,
        "state": "warn" if approval or network or destructive else "ok",
        "requires_approval": approval or network or destructive,
        "restarts_services": approval and "repair" in path,
        "starts_services": approval and "up -d" in path,
        "repairs_services": approval and "repair" in path,
        "builds_images": approval and "build" in path,
        "recreates_services": approval and "up -d" in path,
        "pulls_images": network,
        "runs_docker": False,
        "runs_shell": False,
        "writes_files": False,
        "deletes_volumes": destructive,
        "uses_network": network,
    }


def run_operator_docker_runtime_plan(
    owner: str = "local",
    *,
    runtime_plan: dict[str, Any] | None = None,
    services_plan: dict[str, Any] | None = None,
    repair_plan: dict[str, Any] | None = None,
    checks: dict[str, Any] | None = None,
    ai_runtime_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one read-only Docker runtime operations view."""
    runtime_plan = _as_dict(runtime_plan)
    services_plan = _as_dict(services_plan)
    repair_plan = _as_dict(repair_plan)
    checks = _as_dict(checks)
    ai_runtime_plan = _as_dict(ai_runtime_plan)
    docker_rows = [
        _runtime_row(runtime_plan),
        _services_row(services_plan),
        _container_row(services_plan, checks),
        _host_command_row(services_plan),
        _repair_row(repair_plan),
        _volume_row(runtime_plan),
        _support_services_row(ai_runtime_plan),
        _deployment_row(),
    ]
    state = _state(*(row.get("state") for row in docker_rows))
    ready = state == "ok"
    entry_rows = _entry_rows(ready)
    handoff_rows = _handoff_rows(
        runtime_plan=runtime_plan,
        services_plan=services_plan,
        repair_plan=repair_plan,
        checks=checks,
    )
    alert_rows = _alert_rows(docker_rows)
    summary = {
        "docker_row_count": len(docker_rows),
        "docker_ready_count": sum(1 for row in docker_rows if row.get("state") == "ok"),
        "entry_route_count": len(entry_rows),
        "entry_route_ready_count": sum(1 for row in entry_rows if row.get("ready") is True),
        "handoff_count": len(handoff_rows),
        "handoff_ready_count": sum(1 for row in handoff_rows if row.get("state") == "ok"),
        "docker_runtime_alert_count": len(alert_rows),
        "critical_docker_runtime_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
        "state": state,
        "restarts_services": False,
        "starts_services": False,
        "repairs_services": False,
        "builds_images": False,
        "recreates_services": False,
        "pulls_images": False,
        "runs_docker": False,
        "runs_shell": False,
        "writes_files": False,
        "deletes_volumes": False,
        "uses_network": False,
    }
    return {
        "mode": "read-only-docker-runtime-plan",
        "owner": owner,
        "generated_at": _utc_now(),
        "state": state,
        "summary": summary,
        "docker_rows": docker_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
        "container_status_rows": _as_list(services_plan.get("container_status_rows")),
        "host_command_rows": _as_list(services_plan.get("host_command_rows")),
        "api_actions": [
            _api_action("/api/operator/docker-runtime-plan", "Read Docker runtime operations readiness"),
            _api_action("/api/operator/runtime-plan", "Read runtime resource plan"),
            _api_action("/api/operator/services-plan", "Read local services and container status"),
            _api_action("/api/operator/repair-plan", "Read repair plan and approval packet"),
            _api_action("/api/operator/checks", "Read operator and container checks"),
            _api_action("/api/operator/ai-runtime-plan", "Read local AI support service gates"),
            _api_action("/api/operator/activity", "Record approved repair activity", method="POST", approval=True),
            _api_action("host:docker compose ps", "Capture read-only container status from host", approval=True),
            _api_action("host:docker compose build cleverly cleverly-proxy", "Build updated app/proxy images after source changes", method="POST", approval=True),
            _api_action("host:docker compose up -d --no-build --pull never cleverly cleverly-proxy", "Recreate approved app/proxy containers from local images", method="POST", approval=True),
            _api_action("host:docker compose up -d --no-build --pull never", "Start/recreate approved local services without image pulls", method="POST", approval=True),
            _api_action("host:docker pull", "Pull container images", method="POST", approval=True, network=True),
            _api_action("host:docker volume rm", "Delete Docker volumes", method="POST", approval=True, destructive=True),
        ],
        "paths": {
            "compose": "docker-compose.yml",
            "docker_overlays": "docker/",
            "data_volume": "cleverly-data:/app/data",
            "logs_volume": "cleverly-logs:/app/logs",
            "ollama_volume": "cleverly-ollama:/root/.ollama",
            "chroma_volume": "cleverly-chromadb-data:/data",
            "searxng_volume": "cleverly-searxng-data:/etc/searxng",
            "container_status": "data/operator_container_status.json",
            "activity": "data/operator_activity.json",
        },
        "approval": {
            "required": False,
            "policy": (
                "This endpoint only audits Docker runtime readiness. It does not restart services, start services, "
                "repair containers, build images, recreate services, pull images, run Docker, run shell commands, "
                "write files, delete volumes, or use network access."
            ),
            "disallowed_actions": [
                "restart services",
                "start services",
                "repair containers",
                "build images",
                "recreate services",
                "pull images",
                "run Docker",
                "run shell commands",
                "write files",
                "delete volumes",
                "use network access",
            ],
        },
    }
