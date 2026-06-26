"""Read-only training run planning for the Cleverly operator console."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


MAX_ROWS = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _count(value: Any, fallback: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def _first_value(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return _trim(value, 240)
    return ""


def _job_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(jobs), "active": 0, "failed": 0, "complete": 0}
    for job in jobs:
        status = f"{job.get('status') or ''} {job.get('state') or ''} {job.get('phase') or ''}".lower()
        if any(word in status for word in ("running", "queued", "pending")):
            counts["active"] += 1
        if any(word in status for word in ("fail", "error", "dead")):
            counts["failed"] += 1
        if any(word in status for word in ("complete", "success", "done")):
            counts["complete"] += 1
    return counts


def _dataset_rows(datasets: list[dict[str, Any]], dataset_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset in datasets[:MAX_ROWS]:
        title = _first_value(dataset, "name", "title", "id", "dataset_id") or "Training dataset"
        dataset_id = _first_value(dataset, "id", "dataset_id")
        chars = _count(dataset.get("chars"), 0)
        records = _count(dataset.get("records") or dataset.get("rows") or dataset.get("examples"), 0)
        detail_parts = [
            dataset_id or "local dataset",
            f"{chars} chars" if chars else "",
            f"{records} records" if records else "",
            _first_value(dataset, "created_at", "updated_at"),
        ]
        rows.append({
            "id": dataset_id or title,
            "state": "ok",
            "badge": "data",
            "title": title,
            "detail": "; ".join(part for part in detail_parts if part) or "local sealed dataset",
            "dataset_id": dataset_id,
            "action": "open-training",
            "actionLabel": "Open",
        })
    if not rows:
        rows.append({
            "id": "dataset-required",
            "state": "warn",
            "badge": "data",
            "title": "No local dataset selected",
            "detail": "Create or import a local text dataset in Training Lab before approving a model run.",
            "action": "open-training",
            "actionLabel": "Dataset",
        })
    elif dataset_count > len(rows):
        rows.append({
            "id": "dataset-overflow",
            "state": "ok",
            "badge": "more",
            "title": f"{dataset_count - len(rows)} more dataset records",
            "detail": "Open Training Lab for the full local dataset list.",
            "action": "open-training",
            "actionLabel": "Lab",
        })
    return rows[: MAX_ROWS + 1]


def _artifact_rows(artifacts: list[dict[str, Any]], artifact_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in artifacts[:MAX_ROWS]:
        title = _first_value(artifact, "name", "title", "id", "artifact_id") or "Training artifact"
        artifact_id = _first_value(artifact, "id", "artifact_id")
        detail_parts = [
            _first_value(artifact, "type") or "local model artifact",
            artifact_id,
            _first_value(artifact, "dataset_id"),
            _first_value(artifact, "created_at", "updated_at"),
        ]
        rows.append({
            "id": artifact_id or title,
            "state": "ok",
            "badge": "art",
            "title": title,
            "detail": "; ".join(part for part in detail_parts if part),
            "artifact_id": artifact_id,
            "action": "open-training",
            "actionLabel": "Sample",
        })
    if not rows:
        rows.append({
            "id": "artifact-required",
            "state": "loading" if artifact_count else "warn",
            "badge": "art",
            "title": "No starter model artifact yet",
            "detail": "After dataset review, approve a bounded tiny-model run and sample the output before reuse.",
            "action": "open-training",
            "actionLabel": "Lab",
        })
    return rows


def _lora_blockers(
    dataset_count: int,
    deps: dict[str, Any],
    trainable_count: int,
) -> list[str]:
    blockers: list[str] = []
    if dataset_count < 1:
        blockers.append("dataset required")
    if deps.get("available") is False:
        missing = ", ".join(_trim(item, 80) for item in deps.get("missing") or []) or "optional fine-tuning dependencies"
        blockers.append(f"missing {missing}")
    if trainable_count < 1:
        blockers.append("HF-format base weights required")
    return blockers


def _training_routes(
    dataset_count: int,
    artifact_count: int,
    lora_ready: bool,
    lora_blockers: list[str],
    primary_model: str,
) -> list[dict[str, Any]]:
    return [
        {
            "id": "training-status",
            "state": "ok" if dataset_count else "warn",
            "badge": "GET",
            "title": "Inspect Training Lab status",
            "detail": "/api/training/status returns datasets, starter artifacts, and fine-tune readiness.",
            "method": "GET",
            "path": "/api/training/status",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-training",
            "actionLabel": "Status",
        },
        {
            "id": "tiny-train",
            "state": "ok" if dataset_count else "warn",
            "badge": "tiny",
            "title": "Tiny from-scratch starter model",
            "detail": (
                "Dataset is visible; approve /api/training/train only after selecting the exact dataset and output name."
                if dataset_count
                else "Needs a saved local dataset before the starter model route can run."
            ),
            "method": "POST",
            "path": "/api/training/train",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "creates_model": False,
            "action": "open-training",
            "actionLabel": "Lab",
        },
        {
            "id": "sample-artifact",
            "state": "ok" if artifact_count else ("loading" if dataset_count else "warn"),
            "badge": "sample",
            "title": "Sample starter artifact",
            "detail": (
                "Starter artifact exists; generate sample output and keep quality notes before reuse."
                if artifact_count
                else "No starter artifact has been trained yet."
            ),
            "method": "POST",
            "path": "/api/training/generate",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-training",
            "actionLabel": "Sample",
        },
        {
            "id": "lora-job",
            "state": "ok" if lora_ready else "warn",
            "badge": "LoRA",
            "title": "Fine-tune adapter job",
            "detail": (
                "LoRA dependencies, dataset, and trainable base weights are visible; approve a bounded job in Training Lab."
                if lora_ready
                else f"Not ready for adapter training: {'; '.join(lora_blockers) or 'readiness incomplete'}."
            ),
            "method": "POST",
            "path": "/api/training/finetune/jobs",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "runs_finetune": False,
            "action": "open-training",
            "actionLabel": "Review",
        },
        {
            "id": "primary-model-separation",
            "state": "ok" if primary_model else "warn",
            "badge": "chat",
            "title": "Primary operator model stays separate",
            "detail": (
                f"{primary_model} remains the operator/chat model while training outputs are evaluated separately."
                if primary_model
                else "Choose a primary local model before relying on model-created artifacts."
            ),
            "method": "GET",
            "path": "/api/operator/models",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-model-routing-map",
            "actionLabel": "Models",
        },
    ]


def _entry_rows(dataset_count: int, lora_ready: bool) -> list[dict[str, Any]]:
    dataset_detail = (
        "Training Run Plan can open with local dataset candidates visible; explicit approval is still required before any run."
        if dataset_count
        else "Training Run Plan opens first and asks for a local dataset before any run can be approved."
    )
    lora_detail = (
        "Workflow handoff can review tiny training or LoRA readiness, but it cannot start jobs from this endpoint."
        if lora_ready
        else "Workflow handoff stays in review mode until dataset, dependencies, and trainable base weights are ready."
    )
    return [
        {
            "id": "training-dashboard-entry",
            "entry": "dashboard",
            "state": "ok" if dataset_count else "warn",
            "badge": "dash",
            "title": "Command Center dashboard",
            "detail": dataset_detail,
            "command_id": "open-training-run-plan",
            "action": "open-training-run-plan",
            "actionLabel": "Plan",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "training-text-entry",
            "entry": "text",
            "state": "ok",
            "badge": "text",
            "title": "Typed operator command",
            "detail": "The phrase 'Train a small model on this dataset' resolves to a read-only training plan before Training Lab actions.",
            "command_id": "open-training-run-plan",
            "action": "open-training-run-plan",
            "actionLabel": "Plan",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "training-palette-entry",
            "entry": "palette",
            "state": "ok",
            "badge": "cmd",
            "title": "Global command palette",
            "detail": "The palette exposes Open Training Run Plan as an approval-gated command route.",
            "command_id": "open-training-run-plan",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "training-voice-entry",
            "entry": "voice",
            "state": "ok",
            "badge": "voice",
            "title": "Voice command mode",
            "detail": "Voice routing can land on the same Training Run Plan without creating datasets, starting jobs, or speaking approvals.",
            "command_id": "open-training-run-plan",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
            "requires_approval": True,
            "executes": False,
        },
        {
            "id": "training-workflow-entry",
            "entry": "workflow",
            "state": "ok" if dataset_count and lora_ready else "warn",
            "badge": "flow",
            "title": "Automation workflow handoff",
            "detail": lora_detail,
            "command_id": "open-training-run-plan",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
            "requires_approval": True,
            "executes": False,
        },
    ]


def _sequence_rows(
    dataset_count: int,
    artifact_count: int,
    job_counts: dict[str, int],
    lora_ready: bool,
    lora_blockers: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "id": "select-dataset",
            "state": "ok" if dataset_count else "warn",
            "badge": "1",
            "title": "Confirm the target dataset",
            "detail": (
                f"{dataset_count} local dataset{'s' if dataset_count != 1 else ''} visible; select one exact dataset in Training Lab."
                if dataset_count
                else "Save or import local training text before approving any model run."
            ),
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-training",
            "actionLabel": "Choose",
        },
        {
            "id": "approve-starter-run",
            "state": "warn" if dataset_count else "loading",
            "badge": "2",
            "title": "Approve a bounded starter run",
            "detail": "Use the tiny local training route first to prove dataset-to-artifact behavior before LoRA.",
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-training",
            "actionLabel": "Train",
        },
        {
            "id": "sample-output",
            "state": "ok" if artifact_count else "loading",
            "badge": "3",
            "title": "Sample and inspect output",
            "detail": (
                f"{artifact_count} starter artifact{'s' if artifact_count != 1 else ''} available for local sampling."
                if artifact_count
                else "After the run, generate a sample and record whether it learned the intended pattern."
            ),
            "risk": "approval-required",
            "requires_approval": True,
            "executes": False,
            "action": "open-training",
            "actionLabel": "Sample",
        },
        {
            "id": "review-finetune-readiness",
            "state": "ok" if lora_ready else "warn",
            "badge": "4",
            "title": "Escalate to LoRA only if ready",
            "detail": "LoRA route is ready for a bounded job." if lora_ready else f"Resolve first: {'; '.join(lora_blockers) or 'readiness incomplete'}.",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-model-creation-plan",
            "actionLabel": "Plan",
        },
        {
            "id": "check-job-ledger",
            "state": "error" if job_counts.get("failed") else ("warn" if job_counts.get("active") else "ok"),
            "badge": "5",
            "title": "Check existing fine-tune jobs",
            "detail": f"{job_counts.get('total', 0)} jobs tracked; {job_counts.get('active', 0)} active; {job_counts.get('failed', 0)} failed.",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-training",
            "actionLabel": "Jobs",
        },
        {
            "id": "record-evidence",
            "state": "ok",
            "badge": "6",
            "title": "Record run evidence",
            "detail": "Keep dataset id, route, output path, sample output, logs, and pass/fail note in the activity timeline.",
            "risk": "read-only",
            "requires_approval": False,
            "executes": False,
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
    ]


def _handoff_rows(
    *,
    dataset_count: int,
    artifact_count: int,
    job_counts: dict[str, int],
    lora_ready: bool,
    primary_model: str,
) -> list[dict[str, Any]]:
    common = {
        "requires_approval": False,
        "executes": False,
        "creates_dataset": False,
        "starts_training": False,
        "creates_model": False,
        "runs_finetune": False,
        "pulls_models": False,
        "changes_endpoints": False,
        "writes_artifacts": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "training-dataset-review-handoff",
            "state": "ok" if dataset_count else "warn",
            "badge": "data",
            "title": "Dataset review handoff",
            "detail": f"{dataset_count} local dataset(s) visible; select the exact dataset in Training Lab before approval.",
            "action": "open-training",
            "actionLabel": "Dataset",
            "target_api": "/api/training/status",
        },
        {
            **common,
            "id": "training-approval-checkpoint-handoff",
            "state": "warn" if dataset_count else "loading",
            "badge": "ask",
            "title": "Training approval checkpoint",
            "detail": "Tiny training, artifact sampling, and LoRA jobs stay in Training Lab behind explicit owner approval.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "target_api": "/api/training/train",
            "approval_api": "/api/training/train",
            "requires_approval": True,
        },
        {
            **common,
            "id": "training-job-monitor-handoff",
            "state": "error" if _count(job_counts.get("failed"), 0) else ("warn" if _count(job_counts.get("active"), 0) else "ok"),
            "badge": "jobs",
            "title": "Training job monitor handoff",
            "detail": f"{job_counts.get('active', 0)} active and {job_counts.get('failed', 0)} failed job(s) visible before another run.",
            "action": "open-training",
            "actionLabel": "Jobs",
            "target_api": "/api/training/finetune/status",
        },
        {
            **common,
            "id": "training-artifact-sampling-handoff",
            "state": "ok" if artifact_count else "loading",
            "badge": "sample",
            "title": "Artifact sampling handoff",
            "detail": f"{artifact_count} starter artifact(s) visible; sample output and record quality before reuse.",
            "action": "open-training",
            "actionLabel": "Sample",
            "target_api": "/api/training/generate",
            "requires_approval": True,
        },
        {
            **common,
            "id": "training-model-routing-handoff",
            "state": "ok" if primary_model and (artifact_count or lora_ready) else "warn",
            "badge": "route",
            "title": "Model routing review handoff",
            "detail": (
                f"{primary_model} stays primary while trained artifacts are reviewed before any route change."
                if primary_model
                else "Choose a primary local model and review trained artifacts before route changes."
            ),
            "action": "open-model-routing-map",
            "actionLabel": "Models",
            "target_api": "/api/operator/model-ops-plan",
            "requires_approval": True,
        },
        {
            **common,
            "id": "training-activity-evidence-handoff",
            "state": "ok",
            "badge": "log",
            "title": "Activity evidence handoff",
            "detail": "Keep dataset id, run type, output path, logs, sample output, and pass/fail notes in Activity.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
            "target_api": "/api/operator/activity-plan",
        },
    ]


def _api_actions() -> list[dict[str, Any]]:
    return [
        {
            "id": "training-status",
            "method": "GET",
            "path": "/api/training/status",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "create-dataset",
            "method": "POST",
            "path": "/api/training/datasets",
            "risk": "approval-required",
            "executes": False,
            "requires_approval": True,
            "creates_dataset": False,
        },
        {
            "id": "train-tiny-model",
            "method": "POST",
            "path": "/api/training/train",
            "risk": "approval-required",
            "executes": False,
            "requires_approval": True,
            "starts_training": False,
            "creates_model": False,
        },
        {
            "id": "sample-artifact",
            "method": "POST",
            "path": "/api/training/generate",
            "risk": "approval-required",
            "executes": False,
            "requires_approval": True,
        },
        {
            "id": "finetune-status",
            "method": "GET",
            "path": "/api/training/finetune/status",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "start-lora-job",
            "method": "POST",
            "path": "/api/training/finetune/jobs",
            "risk": "approval-required",
            "executes": False,
            "requires_approval": True,
            "runs_finetune": False,
        },
    ]


def _training_alert_rows(
    dataset_count: int,
    artifact_count: int,
    job_counts: dict[str, int],
    deps: dict[str, Any],
    trainable_count: int,
    primary_model: str,
    local_enabled: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if dataset_count < 1:
        rows.append(
            {
                "id": "dataset-required",
                "state": "error",
                "badge": "data",
                "title": "Dataset required",
                "detail": "Create or import a local dataset before approving any training run.",
                "action": "open-training",
                "actionLabel": "Dataset",
                "requires_approval": False,
            }
        )
    if _count(job_counts.get("failed"), 0):
        rows.append(
            {
                "id": "failed-training-jobs",
                "state": "error",
                "badge": "fail",
                "title": "Failed training jobs need review",
                "detail": f"{job_counts.get('failed', 0)} failed job(s) are in the local fine-tune ledger; inspect logs before another run.",
                "action": "open-training",
                "actionLabel": "Jobs",
                "requires_approval": True,
            }
        )
    if _count(job_counts.get("active"), 0):
        rows.append(
            {
                "id": "active-training-jobs",
                "state": "warn",
                "badge": "run",
                "title": "Training job already active",
                "detail": f"{job_counts.get('active', 0)} job(s) are running or queued; avoid overlapping heavy local training unless intentional.",
                "action": "open-training",
                "actionLabel": "Jobs",
                "requires_approval": True,
            }
        )
    if dataset_count and artifact_count < 1:
        rows.append(
            {
                "id": "starter-artifact-missing",
                "state": "warn",
                "badge": "tiny",
                "title": "No starter artifact yet",
                "detail": "Approve a bounded tiny-model run first, then sample the output before escalating to LoRA.",
                "action": "open-training",
                "actionLabel": "Train",
                "requires_approval": True,
            }
        )
    if deps.get("available") is False:
        missing = ", ".join(_trim(item, 80) for item in deps.get("missing") or []) or "optional fine-tuning dependencies"
        rows.append(
            {
                "id": "finetune-dependencies-missing",
                "state": "warn",
                "badge": "deps",
                "title": "Fine-tune dependencies missing",
                "detail": f"LoRA cannot start until {missing} are available inside the local runtime.",
                "action": "open-training",
                "actionLabel": "Deps",
                "requires_approval": False,
            }
        )
    if trainable_count < 1:
        rows.append(
            {
                "id": "base-weights-required",
                "state": "warn",
                "badge": "base",
                "title": "Trainable base weights required",
                "detail": "LoRA needs a local HF-format base model directory; runtime chat manifests are not enough.",
                "action": "open-model-creation-plan",
                "actionLabel": "Models",
                "requires_approval": False,
            }
        )
    if not (primary_model and local_enabled):
        rows.append(
            {
                "id": "primary-model-required",
                "state": "warn",
                "badge": "chat",
                "title": "Primary local model not ready",
                "detail": "Choose an enabled local primary model before relying on trained artifacts in operator workflows.",
                "action": "open-model-routing-map",
                "actionLabel": "Models",
                "requires_approval": False,
            }
        )
    rows.append(
        {
            "id": "run-approval-required",
            "state": "warn",
            "badge": "ask",
            "title": "Training run approval required",
            "detail": "Creating datasets, starting tiny training, sampling artifacts, and starting LoRA jobs remain explicit Training Lab actions.",
            "action": "open-trust-controls",
            "actionLabel": "Trust",
            "requires_approval": True,
        }
    )
    return rows[:MAX_ROWS]


def run_operator_training_plan(
    owner: str = "local",
    *,
    model_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return read-only evidence for a local model training request."""
    owner = owner or "local"
    if model_snapshot is not None:
        snapshot = model_snapshot
    else:
        from src.operator_models import run_operator_model_snapshot

        snapshot = run_operator_model_snapshot()
    training = _as_dict(snapshot.get("training"))
    finetune = _as_dict(snapshot.get("finetune"))
    endpoints = _as_dict(snapshot.get("endpoints"))
    primary = _as_dict(snapshot.get("primary"))
    readiness = _as_dict(snapshot.get("readiness"))
    datasets = _as_list(training.get("datasets"))
    artifacts = _as_list(training.get("artifacts"))
    jobs = _as_list(finetune.get("jobs"))
    deps = _as_dict(finetune.get("dependencies"))
    trainable_models = _as_list(finetune.get("trainable_models"))
    dataset_count = _count(training.get("dataset_count"), len(datasets))
    artifact_count = _count(training.get("artifact_count"), len(artifacts))
    trainable_count = _count(finetune.get("trainable_count"), len(trainable_models))
    raw_job_counts = _as_dict(finetune.get("job_counts"))
    job_counts = raw_job_counts or _job_counts(jobs)
    local_enabled = _count(_as_dict(endpoints.get("counts")).get("local_enabled"), 0)
    primary_model = _trim(primary.get("model"), 160)
    lora_blockers = _lora_blockers(dataset_count, deps, trainable_count)
    lora_ready = not lora_blockers
    paths = {
        "training_root": _trim(_as_dict(training.get("paths")).get("root")) or "data/training",
        "datasets": _trim(_as_dict(training.get("paths")).get("datasets")) or "data/training/datasets",
        "artifacts": _trim(_as_dict(training.get("paths")).get("artifacts")) or "data/training/artifacts",
        "finetune_jobs": _trim(_as_dict(training.get("paths")).get("finetune_jobs")) or "data/training/finetune/jobs",
        "finetune_adapters": _trim(finetune.get("adapters_dir")) or _trim(_as_dict(training.get("paths")).get("finetune_adapters")) or "data/training/finetune/adapters",
        "finetune_base_models": _trim(finetune.get("base_models_dir")) or _trim(_as_dict(training.get("paths")).get("finetune_base_models")) or "data/training/finetune/base-models",
        "primary_model_manifest": _trim(primary.get("path")) or "data/cleverly-primary-model.json",
    }
    state = "error" if _count(job_counts.get("failed"), 0) else ("ok" if dataset_count and primary_model else "warn")
    if readiness.get("state") == "error":
        state = "error"

    sequence_rows = _sequence_rows(dataset_count, artifact_count, job_counts, lora_ready, lora_blockers)
    route_rows = _training_routes(dataset_count, artifact_count, lora_ready, lora_blockers, primary_model)
    entry_rows = _entry_rows(dataset_count, lora_ready)
    handoff_rows = _handoff_rows(
        dataset_count=dataset_count,
        artifact_count=artifact_count,
        job_counts=job_counts,
        lora_ready=lora_ready,
        primary_model=primary_model,
    )
    alert_rows = _training_alert_rows(dataset_count, artifact_count, job_counts, deps, trainable_count, primary_model, local_enabled)
    evidence_rows = [
        {
            "id": "dataset-ledger",
            "state": "ok" if dataset_count else "warn",
            "badge": "data",
            "title": "Dataset ledger",
            "detail": f"{dataset_count} local dataset{'s' if dataset_count != 1 else ''} visible at {paths['datasets']}.",
            "action": "open-training",
            "actionLabel": "Data",
        },
        {
            "id": "artifact-ledger",
            "state": "ok" if artifact_count else "loading",
            "badge": "art",
            "title": "Starter artifact ledger",
            "detail": f"{artifact_count} starter artifact{'s' if artifact_count != 1 else ''} visible at {paths['artifacts']}.",
            "action": "open-training",
            "actionLabel": "Artifacts",
        },
        {
            "id": "finetune-ledger",
            "state": "error" if _count(job_counts.get("failed"), 0) else ("warn" if _count(job_counts.get("active"), 0) else "ok"),
            "badge": "jobs",
            "title": "Fine-tune job ledger",
            "detail": f"{job_counts.get('total', 0)} jobs tracked; {job_counts.get('active', 0)} active; {job_counts.get('failed', 0)} failed; {job_counts.get('complete', 0)} complete.",
            "action": "open-training",
            "actionLabel": "Jobs",
        },
        {
            "id": "dependencies",
            "state": "ok" if deps.get("available") is not False else "warn",
            "badge": "deps",
            "title": "Fine-tune dependencies",
            "detail": "LoRA dependencies available." if deps.get("available") is not False else f"Missing {', '.join(_trim(item, 80) for item in deps.get('missing') or []) or 'optional dependencies'}.",
            "action": "open-training",
            "actionLabel": "Review",
        },
        {
            "id": "primary-model",
            "state": "ok" if primary_model and local_enabled else "warn",
            "badge": "model",
            "title": "Primary local model",
            "detail": f"{primary_model or 'No primary model selected'}; {local_enabled} local enabled endpoint{'s' if local_enabled != 1 else ''}.",
            "action": "open-model-routing-map",
            "actionLabel": "Models",
        },
    ]

    return {
        "mode": "read-only-training-run-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "dataset_count": dataset_count,
            "artifact_count": artifact_count,
            "trainable_count": trainable_count,
            "job_counts": job_counts,
            "primary_model": primary_model,
            "local_model_ready": bool(primary_model and local_enabled),
            "lora_ready": lora_ready,
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "training_alert_count": len(alert_rows),
            "critical_training_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "creates_dataset": False,
            "starts_training": False,
            "creates_model": False,
            "runs_finetune": False,
            "pulls_models": False,
            "changes_endpoints": False,
            "uses_network": False,
            "requires_run_approval": True,
            "next_action": "Select the exact dataset in Training Lab, then approve a bounded starter run." if dataset_count else "Create or import a local dataset in Training Lab first.",
        },
        "dataset_rows": _dataset_rows(datasets, dataset_count),
        "artifact_rows": _artifact_rows(artifacts, artifact_count),
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "route_rows": route_rows,
        "sequence_rows": sequence_rows,
        "alert_rows": alert_rows,
        "evidence_rows": evidence_rows,
        "api_actions": _api_actions(),
        "approval": {
            "required": True,
            "gate": "Training Lab explicit run approval",
            "policy": "This endpoint only plans training. It does not create datasets, start tiny training, start LoRA jobs, pull or download models, change endpoints, use the network, write artifacts, or approve jobs.",
            "disallowed_by_default": [
                "dataset creation",
                "tiny training run",
                "LoRA fine-tune job",
                "model pull/download",
                "endpoint change",
                "network access",
            ],
        },
        "paths": paths,
        "readiness": readiness,
        "lora_blockers": lora_blockers,
    }
