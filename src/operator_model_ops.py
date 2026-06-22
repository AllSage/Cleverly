"""Read-only local model operations planning for the operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_ROWS = 8


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _count(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first(item: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return _trim(value, 180)
    return default


def _state_from_counts(error: int = 0, warn: int = 0, ok: bool = True) -> str:
    if error:
        return "error"
    if warn or not ok:
        return "warn"
    return "ok"


def _api_action(
    method: str,
    path: str,
    title: str,
    *,
    writes: bool = False,
    executes: bool = False,
    requires_approval: bool = False,
    uses_network: bool = False,
    destructive: bool = False,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "executes": False,
        "would_execute": executes,
        "requires_approval": requires_approval,
        "uses_network": uses_network,
        "destructive": destructive,
    }


def _job_counts(finetune: dict[str, Any]) -> dict[str, int]:
    counts = _as_dict(finetune.get("job_counts"))
    jobs = _as_list(finetune.get("jobs"))
    if counts:
        return {
            "total": _count(counts.get("total"), len(jobs)),
            "active": _count(counts.get("active")),
            "failed": _count(counts.get("failed")),
            "complete": _count(counts.get("complete")),
        }
    def has(job: Any, tokens: tuple[str, ...]) -> bool:
        row = _as_dict(job)
        text = f"{row.get('status') or ''} {row.get('state') or ''} {row.get('phase') or ''}".lower()
        return any(token in text for token in tokens)

    return {
        "total": len(jobs),
        "active": sum(1 for job in jobs if has(job, ("running", "queued", "pending"))),
        "failed": sum(1 for job in jobs if has(job, ("fail", "error", "dead"))),
        "complete": sum(1 for job in jobs if has(job, ("complete", "success", "done"))),
    }


def run_operator_model_ops_plan(
    owner: str = "local",
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return read-only model operation gates without changing model state."""
    if snapshot is None:
        from src.operator_models import run_operator_model_snapshot

        snapshot = run_operator_model_snapshot()

    owner = owner or "local"
    snap = _as_dict(snapshot)
    primary = _as_dict(snap.get("primary"))
    endpoints = _as_dict(snap.get("endpoints"))
    endpoint_counts = _as_dict(endpoints.get("counts"))
    endpoint_items = [_as_dict(item) for item in _as_list(endpoints.get("items"))]
    training = _as_dict(snap.get("training"))
    finetune = _as_dict(snap.get("finetune"))
    features = _as_dict(snap.get("features"))
    readiness = _as_dict(snap.get("readiness"))

    primary_model = _trim(primary.get("model"), 180)
    local_enabled = _count(endpoint_counts.get("local_enabled"))
    external_enabled = _count(endpoint_counts.get("external_enabled"))
    endpoint_count = _count(endpoint_counts.get("total"), len(endpoint_items))
    enabled_count = _count(endpoint_counts.get("enabled"))
    model_count = _count(endpoint_counts.get("models"))
    dataset_count = _count(training.get("dataset_count"), len(_as_list(training.get("datasets"))))
    artifact_count = _count(training.get("artifact_count"), len(_as_list(training.get("artifacts"))))
    trainable_count = _count(finetune.get("trainable_count"), len(_as_list(finetune.get("trainable_models"))))
    ollama_runtime_count = _count(finetune.get("ollama_runtime_count"), len(_as_list(finetune.get("ollama_models"))))
    job_counts = _job_counts(finetune)
    external_allowed = features.get("external_model_endpoints") is not False
    offline_mode = bool(features.get("offline"))
    deps = _as_dict(finetune.get("dependencies"))
    deps_ready = deps.get("available") is not False
    blockers = list(readiness.get("blockers") or [])
    warnings = list(readiness.get("warnings") or [])
    if not primary_model:
        blockers.append("primary model is not selected")
    if local_enabled < 1:
        warnings.append("no enabled local model endpoint is visible")
    if external_enabled and external_allowed and not offline_mode:
        warnings.append("external model endpoints are enabled")
    if job_counts["failed"]:
        blockers.append("fine-tuning job failure needs review")

    local_endpoint_rows = [row for row in endpoint_items if row.get("local")]
    external_endpoint_rows = [row for row in endpoint_items if not row.get("local")]
    operation_rows = [
        {
            "state": "ok" if primary_model else "warn",
            "badge": "model",
            "title": "Primary model route",
            "detail": (
                f"{primary_model}; manifest {primary.get('path') or 'data/cleverly-primary-model.json'}"
                if primary_model
                else "No primary local model selected"
            ),
            "action": "verify-model" if primary_model else "open-cookbook",
            "actionLabel": "Verify" if primary_model else "Choose",
        },
        {
            "state": "ok" if local_enabled else "warn",
            "badge": "local",
            "title": "Local endpoint inventory",
            "detail": f"{local_enabled} enabled local endpoint(s); {model_count} visible model(s) across {endpoint_count} endpoint(s)",
            "action": "open-model-routing-map",
            "actionLabel": "Routes",
        },
        {
            "state": _state_from_counts(warn=external_enabled if external_allowed and not offline_mode else 0),
            "badge": "egress",
            "title": "External endpoint gate",
            "detail": (
                "Offline mode blocks external endpoints"
                if offline_mode
                else (
                    f"{external_enabled} external endpoint(s) enabled; review before autonomous work"
                    if external_enabled and external_allowed
                    else "External model endpoints disabled or absent"
                )
            ),
            "action": "open-offline",
            "actionLabel": "Policy",
        },
        {
            "state": _state_from_counts(error=job_counts["failed"], warn=job_counts["active"]),
            "badge": "jobs",
            "title": "Model job ledger",
            "detail": f"{job_counts['active']} active; {job_counts['failed']} failed; {job_counts['total']} fine-tune job(s) tracked",
            "action": "open-training-run-plan" if job_counts["failed"] else "open-training",
            "actionLabel": "Training",
        },
        {
            "state": "ok" if dataset_count and artifact_count else "warn",
            "badge": "train",
            "title": "Training Lab data",
            "detail": f"{dataset_count} dataset(s); {artifact_count} tiny artifact(s); {trainable_count} trainable base model(s)",
            "action": "open-training-run-plan",
            "actionLabel": "Plan",
        },
        {
            "state": "ok" if deps_ready else "warn",
            "badge": "lora",
            "title": "LoRA dependency gate",
            "detail": (
                f"Dependencies ready; {ollama_runtime_count} Ollama runtime model(s) visible"
                if deps_ready
                else f"Limited: missing {', '.join(str(item) for item in deps.get('missing') or []) or 'optional dependencies'}"
            ),
            "action": "open-training-run-plan",
            "actionLabel": "LoRA",
        },
        {
            "state": "ok",
            "badge": "safe",
            "title": "Model operation boundary",
            "detail": "This plan does not pull models, serve models, change endpoints, set defaults, benchmark, train, or use network access.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
    ]

    endpoint_rows = []
    for row in (local_endpoint_rows + external_endpoint_rows)[:MAX_ROWS]:
        endpoint_rows.append({
            "state": "ok" if row.get("is_enabled") and row.get("local") else ("warn" if row.get("is_enabled") else "loading"),
            "badge": "local" if row.get("local") else "ext",
            "title": _first(row, ("name", "id"), "Model endpoint"),
            "detail": f"{row.get('scope') or 'unknown'}; {_count(row.get('model_count'))} model(s); {_trim(row.get('base_url'), 160)}",
            "action": "open-model-routing-map",
            "actionLabel": "Route",
        })

    guard_rows = [
        {
            "state": "ok",
            "title": "Primary model changes require review",
            "detail": "Selecting or auto-selecting a primary model writes settings and the local manifest only after an explicit action.",
        },
        {
            "state": "ok",
            "title": "Endpoint changes are admin-gated",
            "detail": "Endpoint create, test, probe, register, delete, and default-route changes stay outside this read-only plan.",
        },
        {
            "state": "ok",
            "title": "Model downloads and serving are separate",
            "detail": "Cookbook model downloads, pulls, and serve jobs are long-running actions that require operator approval.",
        },
        {
            "state": "ok",
            "title": "Training starts only from Training Lab",
            "detail": "Tiny training and LoRA jobs require explicit dataset, output, and base-model review before start.",
        },
        {
            "state": "ok",
            "title": "Network stays explicit",
            "detail": "External endpoints, model downloads, web search, and remote probes remain blocked by offline/features policy unless enabled.",
        },
    ]
    api_actions = [
        _api_action("GET", "/api/operator/model-ops-plan", "Read model operation plan"),
        _api_action("GET", "/api/operator/models", "Read operator model snapshot"),
        _api_action("GET", "/api/offline-control/models/primary", "Read primary model manifest"),
        _api_action("POST", "/api/offline-control/models/primary", "Set primary model", writes=True, requires_approval=True),
        _api_action("POST", "/api/offline-control/models/primary/auto", "Auto-select primary model", writes=True, requires_approval=True),
        _api_action("GET", "/api/offline-control/models/primary/verify", "Verify primary model and record audit", writes=True, requires_approval=True),
        _api_action("GET", "/api/offline-control/models/local", "Read local model roots"),
        _api_action("POST", "/api/offline-control/models/register", "Register local model endpoint", writes=True, requires_approval=True),
        _api_action("POST", "/api/offline-control/models/benchmark", "Benchmark local model", executes=True, requires_approval=True),
        _api_action("GET", "/api/models", "Read model picker inventory"),
        _api_action("GET", "/api/model-endpoints", "Read endpoint inventory"),
        _api_action("POST", "/api/model-endpoints", "Create or probe model endpoint", writes=True, requires_approval=True, uses_network=True),
        _api_action("DELETE", "/api/model-endpoints/{ep_id}", "Delete model endpoint", writes=True, requires_approval=True, destructive=True),
        _api_action("POST", "/api/model/download", "Download or pull model", writes=True, executes=True, requires_approval=True, uses_network=True),
        _api_action("POST", "/api/model/serve", "Start model serving job", writes=True, executes=True, requires_approval=True),
        _api_action("POST", "/api/training/train", "Start tiny local training", writes=True, executes=True, requires_approval=True),
        _api_action("POST", "/api/training/finetune/jobs", "Start LoRA fine-tune job", writes=True, executes=True, requires_approval=True),
    ]
    evidence_rows = [
        {"label": "Primary model", "path": primary.get("path") or "data/cleverly-primary-model.json", "detail": primary_model or "unset"},
        {"label": "Settings", "path": "data/settings.json", "detail": "default model, default endpoint, and fallback route settings"},
        {"label": "Model endpoints", "path": "data/app.db:model_endpoints", "detail": f"{endpoint_count} endpoint row(s) visible"},
        {"label": "Training", "path": "data/training", "detail": "datasets, artifacts, fine-tune jobs, adapters, and base models"},
        {"label": "Ollama store", "path": "data/ollama or /root/.ollama", "detail": f"{ollama_runtime_count} runtime model manifest(s) visible"},
        {"label": "Activity", "path": "data/operator_activity.json", "detail": "verification, recovery, and command results"},
    ]
    state = "error" if blockers else ("warn" if warnings else "ok")
    return {
        "mode": "read-only-model-ops-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "primary_model": primary_model,
            "primary_configured": bool(primary_model),
            "endpoint_count": endpoint_count,
            "enabled_endpoint_count": enabled_count,
            "local_enabled_count": local_enabled,
            "external_enabled_count": external_enabled,
            "model_count": model_count,
            "dataset_count": dataset_count,
            "artifact_count": artifact_count,
            "trainable_count": trainable_count,
            "ollama_runtime_count": ollama_runtime_count,
            "finetune_job_count": job_counts["total"],
            "active_job_count": job_counts["active"],
            "failed_job_count": job_counts["failed"],
            "external_model_endpoints_enabled": bool(external_allowed),
            "offline": bool(offline_mode),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "sets_primary_model": False,
            "auto_selects_primary_model": False,
            "registers_endpoints": False,
            "tests_endpoints": False,
            "deletes_endpoints": False,
            "pulls_models": False,
            "downloads_models": False,
            "starts_serving": False,
            "benchmarks_models": False,
            "starts_training": False,
            "starts_finetune": False,
            "changes_settings": False,
            "uses_network": False,
            "runs_shell": False,
            "requires_action_approval": True,
        },
        "blockers": [_trim(item, 240) for item in blockers[:MAX_ROWS]],
        "warnings": [_trim(item, 240) for item in warnings[:MAX_ROWS]],
        "operation_rows": operation_rows,
        "endpoint_rows": endpoint_rows,
        "guard_rows": guard_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "approval": {
            "required": True,
            "policy": (
                "This endpoint only reads model operation evidence. It does not set the primary model, auto-select "
                "models, register or delete endpoints, pull or download models, start serving, benchmark models, "
                "start training, start fine-tuning, change settings, run shell commands, or use network access."
            ),
        },
        "paths": {
            "primary_model_manifest": primary.get("path") or "data/cleverly-primary-model.json",
            "settings": "data/settings.json",
            "model_endpoints": "data/app.db:model_endpoints",
            "training": "data/training",
            "training_datasets": "data/training/datasets",
            "training_artifacts": "data/training/artifacts",
            "finetune_jobs": "data/training/finetune/jobs",
            "finetune_adapters": "data/training/finetune/adapters",
            "finetune_base_models": "data/training/finetune/base-models",
            "ollama": "data/ollama or /root/.ollama",
            "activity": "data/operator_activity.json",
        },
    }
