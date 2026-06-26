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


def _model_alert_rows(
    primary_model: str,
    local_enabled: int,
    external_enabled: int,
    external_allowed: bool,
    offline_mode: bool,
    job_counts: dict[str, int],
    dataset_count: int,
    artifact_count: int,
    deps_ready: bool,
    trainable_count: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not primary_model:
        rows.append(
            {
                "id": "primary-model-required",
                "state": "warn",
                "badge": "model",
                "title": "Primary local model not selected",
                "detail": "Choose and verify a local primary model before approving autonomous model-backed work.",
                "action": "open-cookbook",
                "actionLabel": "Choose",
                "requires_approval": False,
            }
        )
    if local_enabled < 1:
        rows.append(
            {
                "id": "local-endpoint-required",
                "state": "error",
                "badge": "local",
                "title": "No enabled local model endpoint",
                "detail": "Enable or register a local endpoint before relying on Cleverly as a local-first operator.",
                "action": "open-model-routing-map",
                "actionLabel": "Routes",
                "requires_approval": False,
            }
        )
    if external_enabled and external_allowed and not offline_mode:
        rows.append(
            {
                "id": "external-model-endpoints-enabled",
                "state": "warn",
                "badge": "egress",
                "title": "External model endpoints enabled",
                "detail": f"{external_enabled} external endpoint(s) can leave local-only mode; review Offline Control before autonomous routing.",
                "action": "open-offline",
                "actionLabel": "Policy",
                "requires_approval": True,
                "uses_network": True,
            }
        )
    if _count(job_counts.get("failed"), 0):
        rows.append(
            {
                "id": "failed-model-jobs",
                "state": "error",
                "badge": "fail",
                "title": "Failed model jobs need review",
                "detail": f"{job_counts.get('failed', 0)} fine-tune job(s) failed; inspect local logs before retrying or changing routes.",
                "action": "open-training-run-plan",
                "actionLabel": "Jobs",
                "requires_approval": True,
            }
        )
    if _count(job_counts.get("active"), 0):
        rows.append(
            {
                "id": "active-model-jobs",
                "state": "warn",
                "badge": "run",
                "title": "Model job already active",
                "detail": f"{job_counts.get('active', 0)} model job(s) are running or queued; avoid overlapping heavy local work unless intentional.",
                "action": "open-training",
                "actionLabel": "Jobs",
                "requires_approval": True,
            }
        )
    if dataset_count < 1:
        rows.append(
            {
                "id": "training-dataset-missing",
                "state": "warn",
                "badge": "data",
                "title": "No local training dataset",
                "detail": "Training and fine-tuning routes need an approved local dataset before a model operation can start.",
                "action": "open-training",
                "actionLabel": "Dataset",
                "requires_approval": False,
            }
        )
    elif artifact_count < 1:
        rows.append(
            {
                "id": "starter-artifact-missing",
                "state": "warn",
                "badge": "tiny",
                "title": "No starter model artifact",
                "detail": "Approve a bounded tiny-model run and sample its output before escalating local training workflows.",
                "action": "open-training-run-plan",
                "actionLabel": "Plan",
                "requires_approval": True,
            }
        )
    if not deps_ready:
        rows.append(
            {
                "id": "finetune-dependencies-limited",
                "state": "warn",
                "badge": "deps",
                "title": "Fine-tune dependencies limited",
                "detail": "LoRA routes remain blocked until optional fine-tuning dependencies are available inside the local runtime.",
                "action": "open-training-run-plan",
                "actionLabel": "LoRA",
                "requires_approval": False,
            }
        )
    if trainable_count < 1:
        rows.append(
            {
                "id": "trainable-base-required",
                "state": "warn",
                "badge": "base",
                "title": "Trainable base weights required",
                "detail": "Fine-tuning needs a local HF-format base model directory; runtime chat manifests are not enough.",
                "action": "open-model-creation-plan",
                "actionLabel": "Models",
                "requires_approval": False,
            }
        )
    rows.append(
        {
            "id": "model-operation-approval-required",
            "state": "warn",
            "badge": "ask",
            "title": "Model operation approval required",
            "detail": "Setting defaults, registering endpoints, pulling models, serving, benchmarking, training, and fine-tuning remain explicit actions.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        }
    )
    return rows[:MAX_ROWS]


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


def _entry_rows() -> list[dict[str, Any]]:
    common = {
        "command_id": "open-model-preflight",
        "start_command_id": "open-model-routing-map",
        "approval_api": "/api/offline-control/models/primary",
        "download_api": "/api/model/download",
        "serve_api": "/api/model/serve",
        "training_api": "/api/training/train",
        "finetune_api": "/api/training/finetune/jobs",
        "requires_approval": True,
        "executes": False,
        "sets_primary_model": False,
        "auto_selects_primary_model": False,
        "registers_endpoints": False,
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
    }
    return [
        {
            **common,
            "id": "model-dashboard-route",
            "entry": "dashboard",
            "state": "ok",
            "badge": "dash",
            "title": "Dashboard model preflight",
            "detail": "The Models card opens read-only model operation posture before any model route, endpoint, serving, or training change.",
            "action": "open-model-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "model-text-route",
            "entry": "text",
            "state": "ok",
            "badge": "text",
            "title": "Typed model request route",
            "detail": "Typed model requests route to Model Operations Preflight before any primary model, endpoint, download, serving, benchmark, or training API.",
            "action": "open-model-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "model-palette-route",
            "entry": "palette",
            "state": "ok",
            "badge": "cmd",
            "title": "Palette model route",
            "detail": "The command palette separates model review from write-capable model, endpoint, serving, download, and training actions.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "model-voice-route",
            "entry": "voice",
            "state": "ok",
            "badge": "voice",
            "title": "Voice model route",
            "detail": "Voice transcripts use the same local route and open model preflight before any model operation can start.",
            "action": "start-voice-command",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "model-workflow-route",
            "entry": "workflow",
            "state": "ok",
            "badge": "flow",
            "title": "Workflow model handoff",
            "detail": "Workflow handoff can review model routes, jobs, endpoints, and training evidence, but execution stays behind explicit approval.",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _handoff_rows(
    *,
    primary_model: str,
    local_enabled: int,
    external_enabled: int,
    external_allowed: bool,
    offline_mode: bool,
    model_count: int,
    dataset_count: int,
    artifact_count: int,
    deps_ready: bool,
    trainable_count: int,
    job_counts: dict[str, int],
) -> list[dict[str, Any]]:
    common = {
        "requires_approval": False,
        "executes": False,
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
        "writes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }
    endpoint_egress = external_enabled and external_allowed and not offline_mode
    job_state = "error" if _count(job_counts.get("failed")) else ("warn" if _count(job_counts.get("active")) else "ok")
    return [
        {
            **common,
            "id": "model-primary-review-handoff",
            "state": "ok" if primary_model else "warn",
            "badge": "model",
            "title": "Primary model review handoff",
            "detail": (
                f"{primary_model} is selected; verification and route changes stay approval-gated."
                if primary_model
                else "Choose a local primary model before approving autonomous model-backed work."
            ),
            "action": "verify-model" if primary_model else "open-cookbook",
            "actionLabel": "Verify" if primary_model else "Choose",
            "target_api": "/api/offline-control/models/primary",
            "approval_api": "/api/offline-control/models/primary",
            "requires_approval": True,
        },
        {
            **common,
            "id": "model-endpoint-routing-handoff",
            "state": "warn" if endpoint_egress else ("ok" if local_enabled else "error"),
            "badge": "route",
            "title": "Endpoint routing handoff",
            "detail": (
                f"{local_enabled} local endpoint(s), {external_enabled} external endpoint(s), and {model_count} model(s) are visible; external routing needs policy review."
                if endpoint_egress
                else f"{local_enabled} local endpoint(s) and {model_count} model(s) are visible for local-first routing review."
            ),
            "action": "open-model-routing-map",
            "actionLabel": "Routes",
            "target_api": "/api/model-endpoints",
            "approval_api": "/api/model-endpoints",
            "requires_approval": endpoint_egress,
            "network_after_approval": endpoint_egress,
        },
        {
            **common,
            "id": "model-serving-download-handoff",
            "state": "ok" if local_enabled and model_count else "warn",
            "badge": "serve",
            "title": "Serving and download approval handoff",
            "detail": "Model pulls, downloads, serving, and benchmarks are long-running local actions that require explicit approval.",
            "action": "open-cookbook",
            "actionLabel": "Cookbook",
            "target_api": "/api/model/serve",
            "approval_api": "/api/model/download",
            "requires_approval": True,
        },
        {
            **common,
            "id": "model-training-handoff",
            "state": job_state if job_state != "ok" else ("ok" if dataset_count and artifact_count and deps_ready and trainable_count else "warn"),
            "badge": "train",
            "title": "Training and fine-tune handoff",
            "detail": f"{dataset_count} dataset(s), {artifact_count} artifact(s), {trainable_count} trainable base model(s), {job_counts.get('active', 0)} active job(s), and {job_counts.get('failed', 0)} failed job(s) are visible before another run.",
            "action": "open-training-run-plan",
            "actionLabel": "Training",
            "target_api": "/api/operator/training-plan",
            "approval_api": "/api/training/train",
            "requires_approval": True,
        },
        {
            **common,
            "id": "model-context-retrieval-handoff",
            "state": "ok",
            "badge": "rag",
            "title": "Context and retrieval handoff",
            "detail": "ChromaDB/RAG and SearXNG evidence stay in separate local preflights before retrieval or research workflows use model context.",
            "action": "open-embedding-preflight",
            "actionLabel": "RAG",
            "target_api": "/api/operator/ai-runtime-plan",
        },
        {
            **common,
            "id": "model-network-policy-handoff",
            "state": "warn" if endpoint_egress else "ok",
            "badge": "net",
            "title": "Offline and network policy handoff",
            "detail": (
                "External model endpoints are enabled while offline mode is not active; owner policy review is required before model egress."
                if endpoint_egress
                else "Offline/features policy keeps model egress explicit; this plan does not use network access."
            ),
            "action": "open-offline",
            "actionLabel": "Policy",
            "target_api": "/api/offline-control/audit",
            "requires_approval": endpoint_egress,
            "network_after_approval": endpoint_egress,
        },
    ]


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
    alert_rows = _model_alert_rows(
        primary_model,
        local_enabled,
        external_enabled,
        external_allowed,
        offline_mode,
        job_counts,
        dataset_count,
        artifact_count,
        deps_ready,
        trainable_count,
    )
    entry_rows = _entry_rows()
    handoff_rows = _handoff_rows(
        primary_model=primary_model,
        local_enabled=local_enabled,
        external_enabled=external_enabled,
        external_allowed=external_allowed,
        offline_mode=offline_mode,
        model_count=model_count,
        dataset_count=dataset_count,
        artifact_count=artifact_count,
        deps_ready=deps_ready,
        trainable_count=trainable_count,
        job_counts=job_counts,
    )
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
            "model_alert_count": len(alert_rows),
            "critical_model_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
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
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
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
