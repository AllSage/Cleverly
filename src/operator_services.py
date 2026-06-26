"""Read-only local service readiness plan for the Cleverly operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.operator_checks import run_operator_checks, run_operator_service_snapshot


SERVICE_STATES = {"ok", "warn", "error", "loading"}
CORE_SERVICE_IDS = {"cleverly-api", "data-dir", "code-worker-queue"}
SUPPORT_SERVICE_IDS = {"ollama", "chromadb", "searxng", "ntfy"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _state(value: Any) -> str:
    text = _trim(value, 80).lower()
    return text if text in SERVICE_STATES else "warn"


def _as_records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)] if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    *,
    action: str = "open-local-services-map",
    action_label: str = "Services",
    requires_approval: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": _state(state),
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "executes": False,
        "requires_approval": requires_approval,
    }


def _api_action(
    action_id: str,
    method: str,
    path: str,
    *,
    risk: str,
    requires_approval: bool,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "method": method,
        "path": path,
        "risk": risk,
        "executes": False,
        "requires_approval": requires_approval,
    }


def _entry_rows(*, service_count: int, compose_count: int, status_visible: bool) -> list[dict[str, Any]]:
    state = "ok" if service_count and compose_count and status_visible else "warn"
    common = {
        "command_id": "open-local-services-map",
        "repair_command_id": "open-container-repair-plan",
        "approval_command_id": "request-container-fix",
        "activity_command_id": "open-activity-preflight",
        "services_api": "/api/operator/services-plan",
        "repair_api": "/api/operator/repair-plan",
        "checks_api": "/api/operator/checks",
        "requires_approval": True,
        "executes": False,
        "restarts_services": False,
        "starts_services": False,
        "pulls_images": False,
        "runs_docker": False,
        "runs_shell": False,
        "writes_files": False,
        "sends_notifications": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "services-dashboard-route",
            "entry": "dashboard",
            "state": state,
            "badge": "dash",
            "title": "Dashboard services preflight",
            "detail": "The System panel opens read-only local service and container posture before any repair, restart, pull, or host command.",
            "action": "open-local-services-map",
            "actionLabel": "Services",
        },
        {
            **common,
            "id": "services-text-route",
            "entry": "text",
            "state": state,
            "badge": "text",
            "title": "Typed container request route",
            "detail": "Typed requests such as check the containers route to Local Services Map and Repair Plan before any Docker action.",
            "action": "open-local-services-map",
            "actionLabel": "Services",
        },
        {
            **common,
            "id": "services-palette-route",
            "entry": "palette",
            "state": state,
            "badge": "cmd",
            "title": "Palette services route",
            "detail": "The command palette separates service review from approval-gated container repair commands.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "services-voice-route",
            "entry": "voice",
            "state": state,
            "badge": "voice",
            "title": "Voice services route",
            "detail": "Voice mode can open service preflight without running Docker, restarting services, or using network access.",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "services-workflow-route",
            "entry": "workflow",
            "state": state,
            "badge": "flow",
            "title": "Workflow services handoff",
            "detail": "Automation handoffs can inspect service alerts and captured container status, but repairs stay owner-approved.",
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
    approval_command_id: str = "request-container-fix",
    requires_approval: bool = False,
    restarts_services: bool = False,
    starts_services: bool = False,
    repairs_services: bool = False,
    pulls_images: bool = False,
    runs_docker: bool = False,
    runs_shell: bool = False,
    writes_files: bool = False,
    deletes_data: bool = False,
    sends_notifications: bool = False,
    writes_activity: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": _state(state),
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "target_api": target_api,
        "approval_command_id": approval_command_id,
        "requires_approval": requires_approval,
        "executes": False,
        "restarts_services": False,
        "starts_services": False,
        "repairs_services": False,
        "pulls_images": False,
        "runs_docker": False,
        "runs_shell": False,
        "writes_files": False,
        "deletes_data": False,
        "sends_notifications": False,
        "writes_activity": False,
        "uses_network": False,
        "gated_operation": {
            "restarts_services": restarts_services,
            "starts_services": starts_services,
            "repairs_services": repairs_services,
            "pulls_images": pulls_images,
            "runs_docker": runs_docker,
            "runs_shell": runs_shell,
            "writes_files": writes_files,
            "deletes_data": deletes_data,
            "sends_notifications": sends_notifications,
            "writes_activity": writes_activity,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    *,
    service_count: int,
    status_visible_count: int,
    required_issue_count: int,
    optional_issue_count: int,
    unhealthy_container_count: int,
    approval_gated_command_count: int,
) -> list[dict[str, Any]]:
    service_state = "ok" if service_count else "warn"
    status_state = "ok" if status_visible_count else "loading"
    issue_state = "error" if required_issue_count else ("warn" if optional_issue_count or unhealthy_container_count else "ok")
    return [
        _handoff_row(
            "services-snapshot-read-handoff",
            service_state,
            "svc",
            "Service snapshot handoff",
            f"{service_count} local service probe(s) are visible before any host repair command is considered.",
            "open-local-services-map",
            "Services",
            target_api="/api/operator/services",
        ),
        _handoff_row(
            "services-container-status-handoff",
            status_state,
            "dock",
            "Container status handoff",
            f"{status_visible_count} captured container status row(s) are available through read-only host status evidence.",
            "check-containers",
            "System",
            target_api="/api/operator/checks",
            runs_docker=True,
            runs_shell=True,
        ),
        _handoff_row(
            "services-repair-plan-handoff",
            issue_state,
            "fix",
            "Repair plan handoff",
            "Service alerts route to the read-only Container Repair Plan before any restart, recreate, support start, or host command.",
            "open-container-repair-plan",
            "Repair",
            target_api="/api/operator/repair-plan",
            repairs_services=True,
            restarts_services=True,
            runs_docker=True,
            runs_shell=True,
        ),
        _handoff_row(
            "services-data-backup-boundary-handoff",
            "error" if required_issue_count else "ok",
            "data",
            "Data and backup boundary handoff",
            "Required data/path service issues must route through Local Data Map and Backup before service repair.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/backup-plan",
            writes_files=True,
            deletes_data=True,
        ),
        _handoff_row(
            "services-host-command-approval-handoff",
            "warn" if approval_gated_command_count else "ok",
            "cmd",
            "Host command approval handoff",
            f"{approval_gated_command_count} approval-gated host command(s) are listed as evidence only.",
            "open-trust-controls",
            "Trust",
            target_api="/api/operator/repair-plan",
            requires_approval=bool(approval_gated_command_count),
            runs_docker=True,
            runs_shell=True,
        ),
        _handoff_row(
            "services-support-start-handoff",
            "warn" if optional_issue_count else "ok",
            "opt",
            "Optional support start handoff",
            "Optional support services can stay offline unless needed; approved starts must use prepared local images without pulls.",
            "request-container-fix",
            "Ask",
            target_api="docker compose --profile support up -d --no-build --pull never",
            requires_approval=bool(optional_issue_count),
            starts_services=True,
            runs_docker=True,
            runs_shell=True,
        ),
        _handoff_row(
            "services-offline-no-pull-handoff",
            "ok",
            "safe",
            "Offline/no-pull policy handoff",
            "Image pulls, network diagnostics, and broad host changes stay outside the local service audit by default.",
            "open-offline",
            "Policy",
            target_api="/api/operator/safety-plan",
            pulls_images=True,
            uses_network=True,
        ),
        _handoff_row(
            "services-activity-ledger-handoff",
            "ok",
            "log",
            "Activity ledger handoff",
            "Service alerts, repair requests, selected host command evidence, approvals, retries, and outcomes stay in the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            writes_activity=True,
        ),
    ]


def _service_title(service: dict[str, Any]) -> str:
    return _trim(service.get("label") or service.get("id") or "Local service", 180)


def _service_badge(service: dict[str, Any]) -> str:
    return _trim(service.get("kind") or ("core" if service.get("required") else "svc"), 24)


def _service_recommendation(service: dict[str, Any]) -> tuple[str, str, bool]:
    service_id = _trim(service.get("id"), 160)
    state = _state(service.get("state"))
    required = service.get("required") is True
    if state == "ok":
        return "No service action needed.", "open-local-services-map", False
    if service_id == "data-dir":
        return "Verify the Docker data volume mount before restarting services.", "open-backup-preflight", True
    if required or service_id in CORE_SERVICE_IDS:
        return "Inspect logs and repair only the affected core service after approval.", "open-container-repair-plan", True
    if service_id in SUPPORT_SERVICE_IDS:
        return "Optional support service can stay offline unless its feature is needed.", "open-container-repair-plan", True
    return "Review service configuration before repair.", "open-local-services-map", state == "error"


def _service_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for service in _as_records(snapshot.get("services")):
        state = _state(service.get("state"))
        recommendation, action, approval = _service_recommendation(service)
        target = _trim(service.get("target"), 240)
        detail = _trim(service.get("detail"), 500)
        if target:
            detail = f"{detail}; target={target}" if detail else f"target={target}"
        rows.append({
            "id": _trim(service.get("id"), 160),
            "state": state,
            "badge": _service_badge(service),
            "title": _service_title(service),
            "detail": detail or recommendation,
            "kind": _trim(service.get("kind"), 80),
            "target": target,
            "required": service.get("required") is True,
            "latency_ms": service.get("latency_ms"),
            "recommendation": recommendation,
            "action": action,
            "actionLabel": "Repair" if action == "open-container-repair-plan" else ("Backup" if action == "open-backup-preflight" else "Services"),
            "executes": False,
            "requires_approval": approval,
        })
    return rows


def _compose_rows(checks: dict[str, Any]) -> list[dict[str, Any]]:
    container_plan = _as_dict(checks.get("container_plan"))
    rows: list[dict[str, Any]] = []
    for service in _as_records(container_plan.get("services")):
        rows.append({
            "id": _trim(service.get("compose_service") or service.get("container_name"), 160),
            "state": "ok",
            "badge": "core" if service.get("required") else "opt",
            "title": _trim(service.get("compose_service") or "Compose service", 180),
            "detail": f"{_trim(service.get('container_name'), 180)} - {_trim(service.get('role'), 300)}",
            "container_name": _trim(service.get("container_name"), 180),
            "required": service.get("required") is True,
            "profile": _trim(service.get("profile"), 80),
            "source": _trim(service.get("source") or container_plan.get("source"), 160),
            "action": "open-local-services-map",
            "actionLabel": "Services",
            "executes": False,
            "requires_approval": False,
        })
    return rows


def _host_command_rows(checks: dict[str, Any]) -> list[dict[str, Any]]:
    container_plan = _as_dict(checks.get("container_plan"))
    rows: list[dict[str, Any]] = []
    for command in _as_records(container_plan.get("host_commands")):
        risk = _trim(command.get("risk") or "approval-required", 80).lower()
        requires_approval = risk != "read-only"
        rows.append({
            "id": _trim(command.get("label") or command.get("command"), 160).lower().replace(" ", "-"),
            "state": "warn" if requires_approval else "ok",
            "badge": "ask" if requires_approval else "read",
            "title": _trim(command.get("label") or "Host command", 180),
            "detail": _trim(command.get("command"), 700),
            "risk": "approval-required" if requires_approval else "read-only",
            "action": "request-container-fix" if requires_approval else "check-containers",
            "actionLabel": "Ask" if requires_approval else "System",
            "executes": False,
            "requires_approval": requires_approval,
        })
    return rows


def _container_status_records(checks: dict[str, Any]) -> list[dict[str, Any]]:
    container_plan = _as_dict(checks.get("container_plan"))
    rows = _as_records(container_plan.get("container_status"))
    if rows:
        return rows
    return _as_records(checks.get("container_status"))


def _container_status_state(record: dict[str, Any] | None, required: bool, status_visible: bool) -> tuple[str, str]:
    if not record:
        if status_visible:
            return ("error" if required else "warn", "No matching container status row was captured for this expected service.")
        return ("loading", "Host Docker status has not been captured; use the read-only docker ps command before repair.")
    status = _trim(record.get("status") or record.get("state") or record.get("health"), 240)
    text = status.lower()
    if any(term in text for term in ("unhealthy", "restarting", "restart", "paused")):
        return "warn", status or "Container needs review."
    if any(term in text for term in ("exited", "dead", "created", "removing")):
        return ("error" if required else "warn"), status or "Container is not running."
    if any(term in text for term in ("up", "running", "healthy")):
        return "ok", status or "Container is running."
    return "warn", status or "Container status is unknown."


def _container_status_rows(compose_rows: list[dict[str, Any]], checks: dict[str, Any]) -> list[dict[str, Any]]:
    records = _container_status_records(checks)
    status_visible = bool(records)
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in ("name", "names", "container", "container_name", "compose_service", "service"):
            value = _trim(record.get(key), 180)
            if value:
                by_name[value] = record
    rows: list[dict[str, Any]] = []
    for service in compose_rows:
        container_name = _trim(service.get("container_name"), 180)
        compose_service = _trim(service.get("id") or service.get("title"), 180)
        record = by_name.get(container_name) or by_name.get(compose_service)
        state, detail = _container_status_state(record, service.get("required") is True, status_visible)
        rows.append({
            "id": f"container-{container_name or compose_service or 'service'}",
            "state": state,
            "badge": "dock",
            "title": container_name or compose_service or "Expected container",
            "detail": detail,
            "compose_service": compose_service,
            "container_name": container_name,
            "image": _trim((record or {}).get("image"), 240),
            "status": _trim((record or {}).get("status") or (record or {}).get("state") or (record or {}).get("health"), 240),
            "visible": record is not None,
            "required": service.get("required") is True,
            "action": "open-container-repair-plan" if state in {"warn", "error"} else "open-local-services-map",
            "actionLabel": "Repair" if state in {"warn", "error"} else "Services",
            "executes": False,
            "requires_approval": state in {"warn", "error"},
        })
    return rows


def _alert_rows(
    service_rows: list[dict[str, Any]],
    compose_rows: list[dict[str, Any]],
    container_rows: list[dict[str, Any]],
    command_rows: list[dict[str, Any]],
    checks: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    required_issues = [row for row in service_rows if row["required"] and row["state"] in {"warn", "error", "loading"}]
    optional_errors = [row for row in service_rows if not row["required"] and row["state"] == "error"]
    optional_pending = [row for row in service_rows if not row["required"] and row["state"] in {"warn", "loading"}]
    if required_issues:
        names = ", ".join(row["title"] for row in required_issues[:3])
        rows.append(_row(
            "required-service-issues",
            "error",
            "core",
            "Required service needs review",
            f"{len(required_issues)} required service signal(s) need repair review: {names}.",
            action="open-container-repair-plan",
            action_label="Repair",
            requires_approval=True,
        ))
    if optional_errors:
        names = ", ".join(row["title"] for row in optional_errors[:3])
        rows.append(_row(
            "optional-service-errors",
            "warn",
            "svc",
            "Optional support service unavailable",
            f"{len(optional_errors)} optional support service(s) failed local probes: {names}.",
            action="open-local-services-map",
            action_label="Services",
            requires_approval=True,
        ))
    if optional_pending:
        rows.append(_row(
            "optional-service-pending",
            "warn",
            "opt",
            "Optional support service pending",
            f"{len(optional_pending)} optional service signal(s) are warning or loading; this is acceptable when the related feature is unused.",
            action="open-local-services-map",
            action_label="Services",
        ))
    if compose_rows and not any(row.get("visible") for row in container_rows):
        rows.append(_row(
            "host-container-status-missing",
            "warn",
            "dock",
            "Host container status not captured",
            "Expected Compose services are mapped, but no read-only docker ps status evidence is attached.",
            action="open-container-repair-plan",
            action_label="Repair",
        ))
    unhealthy_containers = [row for row in container_rows if row["state"] in {"warn", "error"}]
    if unhealthy_containers:
        rows.append(_row(
            "container-status-issues",
            "error" if any(row["required"] and row["state"] == "error" for row in unhealthy_containers) else "warn",
            "dock",
            "Container status needs review",
            f"{len(unhealthy_containers)} expected container status row(s) are missing, unhealthy, or not running.",
            action="open-container-repair-plan",
            action_label="Repair",
            requires_approval=True,
        ))
    if not compose_rows:
        rows.append(_row(
            "compose-service-map-missing",
            "warn",
            "dock",
            "Compose service map missing",
            "No expected Docker/Compose service map is visible in operator checks.",
            action="open-container-repair-plan",
            action_label="Repair",
        ))
    if not any(row.get("risk") == "read-only" for row in command_rows):
        rows.append(_row(
            "read-only-host-check-missing",
            "warn",
            "read",
            "Read-only host check command missing",
            "The service plan cannot show a read-only host Docker status command.",
            action="open-container-repair-plan",
            action_label="Repair",
        ))
    if _as_dict(checks.get("container_plan")).get("docker_socket_mounted"):
        rows.append(_row(
            "docker-socket-mounted",
            "warn",
            "dock",
            "Docker socket is mounted",
            "Docker socket access increases repair blast radius; continue to require approval before restarts or recreates.",
            action="open-container-repair-plan",
            action_label="Repair",
            requires_approval=True,
        ))
    return rows[:12]


def run_operator_services_plan(
    owner: str = "local",
    *,
    service_snapshot: dict[str, Any] | None = None,
    checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return read-only local service readiness, alerts, and approval gates."""
    owner = owner or "local"
    snapshot = service_snapshot if isinstance(service_snapshot, dict) else run_operator_service_snapshot()
    check_data = checks if isinstance(checks, dict) else run_operator_checks()
    service_rows = _service_rows(snapshot)
    compose_rows = _compose_rows(check_data)
    container_rows = _container_status_rows(compose_rows, check_data)
    host_command_rows = _host_command_rows(check_data)
    alert_rows = _alert_rows(service_rows, compose_rows, container_rows, host_command_rows, check_data)
    entry_rows = _entry_rows(
        service_count=len(service_rows),
        compose_count=len(compose_rows),
        status_visible=any(row.get("visible") for row in container_rows),
    )
    required_issues = [row for row in service_rows if row["required"] and row["state"] in {"warn", "error", "loading"}]
    optional_issues = [row for row in service_rows if not row["required"] and row["state"] in {"warn", "error", "loading"}]
    critical = [row for row in alert_rows if row["state"] == "error"]
    unhealthy_container_count = len([row for row in container_rows if row["state"] in {"warn", "error"}])
    approval_gated_command_count = len([row for row in host_command_rows if row["requires_approval"]])
    handoff_rows = _handoff_rows(
        service_count=len(service_rows),
        status_visible_count=len([row for row in container_rows if row.get("visible")]),
        required_issue_count=len(required_issues),
        optional_issue_count=len(optional_issues),
        unhealthy_container_count=unhealthy_container_count,
        approval_gated_command_count=approval_gated_command_count,
    )
    state = "error" if required_issues or critical else ("warn" if optional_issues or alert_rows else "ok")
    snapshot_summary = _as_dict(snapshot.get("summary"))
    api_actions = [
        _api_action("services-plan", "GET", "/api/operator/services-plan", risk="read-only", requires_approval=False),
        _api_action("services", "GET", "/api/operator/services", risk="read-only-local-probes", requires_approval=False),
        _api_action("checks", "GET", "/api/operator/checks", risk="read-only", requires_approval=False),
        _api_action("repair-plan", "GET", "/api/operator/repair-plan", risk="read-only-repair-plan", requires_approval=False),
        _api_action("runtime-plan", "GET", "/api/operator/runtime-plan", risk="read-only", requires_approval=False),
        _api_action("activity", "GET", "/api/operator/activity", risk="read-only", requires_approval=False),
    ]
    return {
        "mode": "read-only-local-services-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "service_count": len(service_rows),
            "ok_count": int(snapshot_summary.get("ok") or sum(1 for row in service_rows if row["state"] == "ok")),
            "warn_count": int(snapshot_summary.get("warn") or sum(1 for row in service_rows if row["state"] == "warn")),
            "error_count": int(snapshot_summary.get("error") or sum(1 for row in service_rows if row["state"] == "error")),
            "loading_count": int(snapshot_summary.get("loading") or sum(1 for row in service_rows if row["state"] == "loading")),
            "required_count": int(snapshot_summary.get("required") or sum(1 for row in service_rows if row["required"])),
            "required_issue_count": len(required_issues),
            "optional_issue_count": len(optional_issues),
            "compose_service_count": len(compose_rows),
            "container_status_count": len(container_rows),
            "container_status_visible_count": len([row for row in container_rows if row.get("visible")]),
            "running_container_count": len([row for row in container_rows if row["state"] == "ok"]),
            "unhealthy_container_count": unhealthy_container_count,
            "host_command_count": len(host_command_rows),
            "approval_gated_command_count": approval_gated_command_count,
            "service_alert_count": len(alert_rows),
            "critical_service_alert_count": len(critical),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "probes_local_only": True,
            "restarts_services": False,
            "starts_services": False,
            "pulls_images": False,
            "runs_docker": False,
            "runs_shell": False,
            "writes_files": False,
            "uses_network": False,
            "next_action": "Open Local Services Map or Repair Plan to review service alerts and approval-gated host commands before changing containers.",
        },
        "service_rows": service_rows,
        "compose_rows": compose_rows,
        "container_status_rows": container_rows,
        "host_command_rows": host_command_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Local service audit only",
            "policy": "This endpoint only audits local service probes, expected Docker services, host-command evidence, and approval gates. It does not restart services, start services, pull images, run Docker, run shell commands, write files, send notifications, or use network access.",
            "disallowed_by_default": [
                "restart services",
                "start services",
                "pull images",
                "run Docker",
                "run shell",
                "write files",
                "send notifications",
                "use network",
            ],
        },
        "paths": {
            "service_snapshot": "/api/operator/services",
            "checks": "/api/operator/checks",
            "repair_plan": "/api/operator/repair-plan",
        },
    }
