"""Read-only local AI runtime readiness evidence for Cleverly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


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
    clean = [str(item or "").lower() for item in states]
    if "error" in clean:
        return "error"
    if "warn" in clean or "loading" in clean:
        return "warn"
    return "ok"


def _row(row_id: str, state: str, badge: str, title: str, detail: str, action: str, action_label: str = "Open") -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state if state in {"ok", "warn", "error", "loading"} else "warn",
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": state != "ok",
        "starts_models": False,
        "starts_training": False,
        "downloads_models": False,
        "starts_services": False,
        "restarts_services": False,
        "writes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _summary_state(plan: dict[str, Any], default: str = "warn") -> str:
    summary = _as_dict(plan.get("summary"))
    return _trim(summary.get("state") or plan.get("state") or default, 40).lower() or default


def _model_snapshot_row(model_snapshot: dict[str, Any]) -> dict[str, Any]:
    readiness = _as_dict(model_snapshot.get("readiness"))
    endpoints = _as_dict(model_snapshot.get("endpoints"))
    counts = _as_dict(endpoints.get("counts"))
    training = _as_dict(model_snapshot.get("training"))
    primary = _as_dict(model_snapshot.get("primary"))
    model = _trim(primary.get("model") or _as_dict(model_snapshot.get("primary_model")).get("model"), 180)
    state = _trim(readiness.get("state") or ("ok" if model and _count(counts.get("local_enabled")) else "warn"), 40)
    return _row(
        "ai-runtime-model-snapshot",
        state,
        "model",
        "Model snapshot",
        f"{model or 'No primary model'}; {_count(counts.get('local_enabled'))} local endpoint(s); {_count(counts.get('external_enabled'))} external endpoint(s); {_count(training.get('dataset_count'))} dataset(s).",
        "open-model-routing-map",
        "Models",
    )


def _model_ops_row(model_ops_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(model_ops_plan.get("summary"))
    state = _summary_state(model_ops_plan)
    return _row(
        "ai-runtime-model-ops",
        state,
        "ops",
        "Model operation gates",
        f"{_trim(summary.get('primary_model') or 'No primary model', 180)}; {_count(summary.get('local_enabled_count'))} local endpoint(s); {_count(summary.get('model_alert_count'))} alert(s); no model actions run.",
        "open-model-preflight",
        "Preflight",
    )


def _training_row(training_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(training_plan.get("summary"))
    state = _summary_state(training_plan)
    dataset_count = _count(summary.get("dataset_count"))
    job_count = _count(summary.get("job_count") or summary.get("fine_tune_job_count"))
    failed = _count(summary.get("failed_job_count") or summary.get("failure_count"))
    return _row(
        "ai-runtime-training",
        "error" if failed else state,
        "train",
        "Training and fine-tune readiness",
        f"{dataset_count} dataset(s); {job_count} job(s); {failed} failed job(s); training starts remain approval-gated.",
        "open-training-run-plan",
        "Training",
    )


def _runtime_row(runtime_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(runtime_plan.get("summary"))
    state = _summary_state(runtime_plan)
    return _row(
        "ai-runtime-resources",
        state,
        "run",
        "Runtime resources and sealed volumes",
        f"{_count(summary.get('sealed_runtime_ready_count'))}/{_count(summary.get('sealed_runtime_count'))} sealed runtime item(s) ready; {_count(summary.get('runtime_alert_count'))} alert(s).",
        "open-machine-preflight",
        "Runtime",
    )


def _service_rows(services_plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    source_rows = _as_list(services_plan.get("service_rows")) or _as_list(services_plan.get("probe_rows"))
    wanted = ("ollama", "chromadb", "searxng")
    for service_id in wanted:
        service = next((row for row in source_rows if service_id in _trim(row.get("id") or row.get("title"), 180).lower()), {})
        if service:
            rows.append(_row(
                f"ai-runtime-service-{service_id}",
                _trim(service.get("state") or "warn", 40),
                "svc",
                _trim(service.get("title") or service_id, 180),
                _trim(service.get("detail") or f"{service_id} service evidence", 500),
                "open-local-services-map",
                "Services",
            ))
        else:
            rows.append(_row(
                f"ai-runtime-service-{service_id}",
                "warn",
                "svc",
                f"{service_id.upper()} service",
                f"No {service_id} service row is visible in the local services plan.",
                "open-local-services-map",
                "Services",
            ))
    return rows


def _entry_rows(ready: bool) -> list[dict[str, Any]]:
    rows = []
    for entry, badge, title, action in (
        ("dashboard", "dash", "Dashboard AI runtime control", "open-model-preflight"),
        ("text", "text", "Typed model operation request", "open-model-preflight"),
        ("palette", "pal", "Palette model route", "open-command-palette"),
        ("voice", "voice", "Voice model route", "open-voice-preflight"),
        ("workflow", "flow", "Workflow model handoff", "open-automation-map"),
    ):
        rows.append({
            "id": f"ai-runtime-{entry}-route",
            "entry": entry,
            "state": "ok" if ready else "warn",
            "badge": badge,
            "title": title,
            "detail": "Local AI runtime requests show model, training, service, storage, and network gates before any model operation can start.",
            "action": action,
            "actionLabel": "Open",
            "ready": ready,
            "ai_runtime_api": "/api/operator/ai-runtime-plan",
            "models_api": "/api/operator/models",
            "model_ops_api": "/api/operator/model-ops-plan",
            "training_api": "/api/operator/training-plan",
            "runtime_api": "/api/operator/runtime-plan",
            "services_api": "/api/operator/services-plan",
            "starts_models": False,
            "starts_training": False,
            "downloads_models": False,
            "starts_services": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return rows


def _alert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        if row.get("state") not in {"warn", "error"}:
            continue
        alerts.append({
            "id": f"ai-runtime-alert-{row['id']}",
            "state": row.get("state"),
            "badge": row.get("badge") or "ai",
            "title": row.get("title"),
            "detail": row.get("detail"),
            "action": row.get("action") or "open-model-preflight",
            "actionLabel": "Review",
            "requires_approval": row.get("state") == "error",
        })
    return alerts[:12]


def _find_row(rows: list[dict[str, Any]], token: str) -> dict[str, Any]:
    token = token.lower()
    return next(
        (
            row
            for row in rows
            if token in _trim(row.get("id") or row.get("title") or "", 240).lower()
        ),
        {},
    )


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
    approval_api: str = "",
    requires_approval: bool = False,
    network_after_approval: bool = False,
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
        "approval_api": approval_api,
        "requires_approval": requires_approval,
        "network_after_approval": network_after_approval,
        "starts_models": False,
        "starts_training": False,
        "downloads_models": False,
        "starts_services": False,
        "restarts_services": False,
        "writes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _handoff_rows(runtime_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model_row = _find_row(runtime_rows, "model-snapshot")
    training_row = _find_row(runtime_rows, "training")
    runtime_row = _find_row(runtime_rows, "resources")
    ollama_row = _find_row(runtime_rows, "ollama")
    chroma_row = _find_row(runtime_rows, "chromadb")
    searxng_row = _find_row(runtime_rows, "searxng")
    searxng_state = _trim(searxng_row.get("state") or "warn", 40)
    return [
        _handoff_row(
            "ai-runtime-model-routing-handoff",
            _trim(model_row.get("state") or "warn", 40),
            "model",
            "Model routing handoff",
            "Primary model, local endpoint inventory, and external endpoint posture route to Model Operations before any model setting changes.",
            "open-model-preflight",
            "Models",
            target_api="/api/operator/model-ops-plan",
            approval_api="/api/offline-control/models/primary",
            requires_approval=True,
        ),
        _handoff_row(
            "ai-runtime-training-handoff",
            _trim(training_row.get("state") or "warn", 40),
            "train",
            "Training Lab handoff",
            "Dataset, artifact, and fine-tune job evidence route to Training Run Plan before any training starts.",
            "open-training-run-plan",
            "Training",
            target_api="/api/operator/training-plan",
            approval_api="/api/training/train",
            requires_approval=True,
        ),
        _handoff_row(
            "ai-runtime-ollama-service-handoff",
            _trim(ollama_row.get("state") or "warn", 40),
            "ollama",
            "Ollama service handoff",
            "Ollama runtime evidence routes to Local Services before any serve, pull, restart, or shell-level repair.",
            "open-local-services-map",
            "Services",
            target_api="/api/operator/services-plan",
            approval_api="/api/model/serve",
            requires_approval=_trim(ollama_row.get("state") or "warn", 40) != "ok",
        ),
        _handoff_row(
            "ai-runtime-chromadb-context-handoff",
            _trim(chroma_row.get("state") or "warn", 40),
            "chroma",
            "ChromaDB context handoff",
            "Vector-store readiness routes through Embedding/RAG preflight before model context workflows use local documents.",
            "open-embedding-preflight",
            "RAG",
            target_api="/api/operator/services-plan",
            approval_api="/api/personal/reindex",
            requires_approval=_trim(chroma_row.get("state") or "warn", 40) != "ok",
        ),
        _handoff_row(
            "ai-runtime-searxng-policy-handoff",
            searxng_state,
            "search",
            "SearXNG policy handoff",
            "SearXNG service evidence routes through Research and Offline Control before any web search or network-capable research starts.",
            "open-research-preflight",
            "Research",
            target_api="/api/operator/research-plan",
            approval_api="/api/search",
            requires_approval=searxng_state != "ok",
            network_after_approval=searxng_state != "ok",
        ),
        _handoff_row(
            "ai-runtime-resource-guard-handoff",
            _trim(runtime_row.get("state") or "warn", 40),
            "guard",
            "Runtime resource guard handoff",
            "Sealed volumes, heavy-job resources, and service restart boundaries route to Machine Preflight before local runtime work starts.",
            "open-machine-preflight",
            "Runtime",
            target_api="/api/operator/runtime-plan",
            approval_api="/api/operator/repair-plan",
            requires_approval=_trim(runtime_row.get("state") or "warn", 40) != "ok",
        ),
    ]


def _api_action(path: str, title: str, *, writes: bool = False, starts: bool = False, network: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "method": "GET" if not writes and not starts else "POST",
        "title": title,
        "state": "warn" if writes or starts or network else "ok",
        "writes": writes,
        "starts_models": starts,
        "starts_training": starts,
        "downloads_models": starts or network,
        "starts_services": starts,
        "runs_shell": False,
        "uses_network": network,
        "requires_approval": writes or starts or network,
    }


def run_operator_ai_runtime_plan(
    owner: str = "local",
    *,
    model_snapshot: dict[str, Any] | None = None,
    model_ops_plan: dict[str, Any] | None = None,
    training_plan: dict[str, Any] | None = None,
    runtime_plan: dict[str, Any] | None = None,
    services_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one read-only local AI runtime readiness view."""
    model_snapshot = _as_dict(model_snapshot)
    model_ops_plan = _as_dict(model_ops_plan)
    training_plan = _as_dict(training_plan)
    runtime_plan = _as_dict(runtime_plan)
    services_plan = _as_dict(services_plan)
    runtime_rows = [
        _model_snapshot_row(model_snapshot),
        _model_ops_row(model_ops_plan),
        _training_row(training_plan),
        _runtime_row(runtime_plan),
        *_service_rows(services_plan),
    ]
    state = _state(*(row.get("state") for row in runtime_rows))
    ready = state == "ok"
    entry_rows = _entry_rows(ready)
    alert_rows = _alert_rows(runtime_rows)
    handoff_rows = _handoff_rows(runtime_rows)
    summary = {
        "runtime_row_count": len(runtime_rows),
        "runtime_ready_count": sum(1 for row in runtime_rows if row.get("state") == "ok"),
        "entry_route_count": len(entry_rows),
        "entry_route_ready_count": sum(1 for row in entry_rows if row.get("ready") is True),
        "ai_runtime_alert_count": len(alert_rows),
        "critical_ai_runtime_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
        "handoff_count": len(handoff_rows),
        "handoff_ready_count": sum(1 for row in handoff_rows if row.get("state") == "ok"),
        "starts_models": False,
        "starts_training": False,
        "downloads_models": False,
        "starts_services": False,
        "restarts_services": False,
        "writes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return {
        "mode": "read-only-local-ai-runtime-plan",
        "owner": owner,
        "generated_at": _utc_now(),
        "state": state,
        "summary": summary,
        "runtime_rows": runtime_rows,
        "entry_rows": entry_rows,
        "alert_rows": alert_rows,
        "handoff_rows": handoff_rows,
        "api_actions": [
            _api_action("/api/operator/ai-runtime-plan", "Read local AI runtime readiness"),
            _api_action("/api/operator/models", "Read model and training snapshot"),
            _api_action("/api/operator/model-ops-plan", "Read model operation gates"),
            _api_action("/api/operator/training-plan", "Read training run gates"),
            _api_action("/api/operator/runtime-plan", "Read runtime resource gates"),
            _api_action("/api/operator/services-plan", "Read local support services"),
            _api_action("/api/offline-control/models/primary", "Set primary local model", writes=True),
            _api_action("/api/model/download", "Download model artifact", starts=True, network=True),
            _api_action("/api/model/serve", "Start model server", starts=True),
            _api_action("/api/training/train", "Start local training run", starts=True),
        ],
        "paths": {
            "primary_model": "data/cleverly-primary-model.json",
            "models": "data/models",
            "ollama": "cleverly-ollama:/root/.ollama",
            "training": "data/training",
            "chroma": "cleverly-chromadb-data:/data",
            "searxng": "cleverly-searxng-data:/etc/searxng",
        },
        "approval": {
            "required": False,
            "policy": (
                "This endpoint only audits local AI runtime readiness. It does not set primary models, "
                "download models, start serving, start training, restart services, write files, run shell commands, "
                "pull images, call SearXNG, or use network access."
            ),
            "disallowed_actions": [
                "set primary models",
                "download models",
                "start serving",
                "start training",
                "restart services",
                "write files",
                "run shell commands",
                "pull images",
                "call SearXNG",
                "use network access",
            ],
        },
    }
