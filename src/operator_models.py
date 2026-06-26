"""Read-only operator model and training snapshot helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.database import ModelEndpoint, SessionLocal
from src.constants import DATA_DIR
from src.local_training import ensure_training_dirs, list_artifacts, list_datasets
from src.offline_finetune import finetune_status
from src.settings import load_features, load_settings


PRIMARY_MODEL_FILE = Path(DATA_DIR) / "cleverly-primary-model.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _short_list(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items[: max(0, limit)]:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


def _endpoint_models(value: Any) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item)[:240] for item in data if str(item or "").strip()]


def _endpoint_hidden(value: Any) -> set[str]:
    if not value:
        return set()
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return set()
    return {str(item) for item in data if str(item or "").strip()}


def _endpoint_is_local(base_url: str) -> bool:
    parsed = urlparse(base_url or "")
    host = (parsed.hostname or "").lower()
    if host in {"", "localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if host.endswith(".local") or host in {"ollama", "chromadb", "searxng", "host.docker.internal"}:
        return True
    return host.startswith("172.") or host.startswith("10.") or host.startswith("192.168.")


def _endpoint_rows() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.query(ModelEndpoint).order_by(ModelEndpoint.created_at).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            all_models = _endpoint_models(row.cached_models)
            hidden = _endpoint_hidden(row.hidden_models)
            visible = [model for model in all_models if model not in hidden]
            local = _endpoint_is_local(row.base_url)
            result.append({
                "id": row.id,
                "name": row.name,
                "base_url": row.base_url,
                "is_enabled": bool(row.is_enabled),
                "local": local,
                "scope": "local" if local else "external",
                "model_count": len(visible),
                "models": visible[:12],
                "hidden_count": len(hidden),
                "model_type": getattr(row, "model_type", None) or "llm",
                "supports_tools": getattr(row, "supports_tools", None),
                "owner_scope": "shared" if not getattr(row, "owner", None) else "owner",
                "status": "cached" if visible else ("empty" if row.is_enabled else "disabled"),
            })
        return result
    finally:
        db.close()


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "enabled": sum(1 for row in rows if row.get("is_enabled")),
        "local_enabled": sum(1 for row in rows if row.get("is_enabled") and row.get("local")),
        "external_enabled": sum(1 for row in rows if row.get("is_enabled") and not row.get("local")),
        "models": sum(int(row.get("model_count") or 0) for row in rows if row.get("is_enabled")),
    }


def _primary_model(settings: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json(PRIMARY_MODEL_FILE)
    env_model = (os.getenv("OLLAMA_MODEL") or "").strip()
    selected = (
        str(manifest.get("primary_model") or "").strip()
        or str(settings.get("default_model") or "").strip()
        or env_model
    )
    return {
        "model": selected,
        "configured": bool(selected),
        "source": manifest.get("source") or ("settings" if settings.get("default_model") else ("env" if env_model else "")),
        "manifest": manifest,
        "path": "data/cleverly-primary-model.json",
    }


def _job_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    def has_status(job: dict[str, Any], pattern: tuple[str, ...]) -> bool:
        text = f"{job.get('status') or ''} {job.get('state') or ''} {job.get('phase') or ''}".lower()
        return any(item in text for item in pattern)

    return {
        "total": len(jobs),
        "active": sum(1 for job in jobs if has_status(job, ("running", "queued", "pending"))),
        "failed": sum(1 for job in jobs if has_status(job, ("fail", "error", "dead"))),
        "complete": sum(1 for job in jobs if has_status(job, ("complete", "success", "done"))),
    }


def _readiness(
    primary: dict[str, Any],
    endpoints: dict[str, Any],
    datasets: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    finetune: dict[str, Any],
    features: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    deps = finetune.get("dependencies") if isinstance(finetune.get("dependencies"), dict) else {}
    trainable = finetune.get("trainable_models") if isinstance(finetune.get("trainable_models"), list) else []
    jobs = finetune.get("jobs") if isinstance(finetune.get("jobs"), list) else []
    job_counts = _job_counts(jobs)

    if not primary.get("configured"):
        blockers.append("primary model is not selected")
    if endpoints["counts"]["enabled"] < 1:
        blockers.append("no enabled model endpoint is registered")
    if endpoints["counts"]["local_enabled"] < 1:
        warnings.append("no enabled local model endpoint is visible")
    if endpoints["counts"]["external_enabled"] and features.get("external_model_endpoints") is not False:
        warnings.append("external model endpoints are enabled")
    if not datasets:
        warnings.append("no local training dataset is saved")
    if not artifacts:
        warnings.append("no tiny local training artifact is saved")
    if deps.get("available") is False:
        missing = ", ".join(str(item) for item in deps.get("missing") or []) or "optional fine-tuning dependencies"
        warnings.append(f"LoRA fine-tuning is limited: missing {missing}")
    if not trainable:
        warnings.append("no HF-format trainable base model is available for LoRA")
    if job_counts["failed"]:
        blockers.append("at least one fine-tuning job failed")

    state = "error" if blockers else ("warn" if warnings else "ok")
    return {
        "state": state,
        "ready": state == "ok",
        "blockers": blockers,
        "warnings": warnings,
        "job_counts": job_counts,
        "summary": (
            "model and training controls ready"
            if state == "ok"
            else "; ".join(blockers or warnings[:2])
        ),
    }


def _model_alert_row(
    *,
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str = "open-model-preflight",
    action_label: str = "Review",
    requires_approval: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": requires_approval,
        "executes": False,
        "routes_commands": False,
        "starts_models": False,
        "starts_training": False,
        "starts_finetune": False,
        "downloads_models": False,
        "changes_settings": False,
        "runs_shell": False,
        "uses_network": uses_network,
    }


def _alert_rows(
    primary: dict[str, Any],
    endpoints: dict[str, Any],
    datasets: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    finetune: dict[str, Any],
    features: dict[str, Any],
    readiness: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counts = endpoints.get("counts") if isinstance(endpoints.get("counts"), dict) else {}
    deps = finetune.get("dependencies") if isinstance(finetune.get("dependencies"), dict) else {}
    trainable = finetune.get("trainable_models") if isinstance(finetune.get("trainable_models"), list) else []
    job_counts = readiness.get("job_counts") if isinstance(readiness.get("job_counts"), dict) else {}

    if not primary.get("configured"):
        rows.append(_model_alert_row(
            row_id="model-primary-missing",
            state="error",
            badge="model",
            title="Primary model not selected",
            detail="Choose a local primary model before relying on chat, routing, or automation defaults.",
            action="open-model-routing-map",
            action_label="Models",
        ))
    if int(counts.get("enabled") or 0) < 1:
        rows.append(_model_alert_row(
            row_id="model-endpoints-missing",
            state="error",
            badge="endpt",
            title="No enabled model endpoint",
            detail="At least one enabled local endpoint is needed for local-first model operation.",
            action="open-model-routing-map",
            action_label="Models",
        ))
    if int(counts.get("local_enabled") or 0) < 1:
        rows.append(_model_alert_row(
            row_id="model-local-endpoint-missing",
            state="warn",
            badge="local",
            title="No enabled local model endpoint",
            detail="Local-first operation is weaker until Ollama or another local endpoint is enabled.",
            action="open-model-routing-map",
            action_label="Models",
        ))
    if int(counts.get("external_enabled") or 0) and features.get("external_model_endpoints") is not False:
        rows.append(_model_alert_row(
            row_id="model-external-endpoint-enabled",
            state="warn",
            badge="net",
            title="External model endpoint enabled",
            detail=f"{int(counts.get('external_enabled') or 0)} external endpoint(s) are enabled; review privacy and network policy before routing.",
            action="open-offline",
            action_label="Policy",
            requires_approval=True,
            uses_network=True,
        ))
    if not datasets:
        rows.append(_model_alert_row(
            row_id="model-training-dataset-missing",
            state="warn",
            badge="data",
            title="No local training dataset",
            detail="Training Lab has no saved dataset for local model experiments.",
            action="open-training",
            action_label="Training",
        ))
    if not artifacts:
        rows.append(_model_alert_row(
            row_id="model-training-artifact-missing",
            state="warn",
            badge="tiny",
            title="No tiny local training artifact",
            detail="No saved tiny-model artifact is visible in local training storage.",
            action="open-training",
            action_label="Training",
        ))
    if deps.get("available") is False:
        missing = ", ".join(str(item) for item in deps.get("missing") or []) or "optional fine-tuning dependencies"
        rows.append(_model_alert_row(
            row_id="model-finetune-deps-missing",
            state="warn",
            badge="deps",
            title="LoRA fine-tuning dependencies missing",
            detail=f"Fine-tuning is limited until dependencies are available: {missing}.",
            action="open-training-run-plan",
            action_label="Plan",
        ))
    if not trainable:
        rows.append(_model_alert_row(
            row_id="model-trainable-base-missing",
            state="warn",
            badge="base",
            title="No trainable base model",
            detail="Add HF-format base weights before starting a LoRA adapter job.",
            action="open-model-creation-plan",
            action_label="Models",
        ))
    if int(job_counts.get("failed") or 0):
        rows.append(_model_alert_row(
            row_id="model-finetune-job-failed",
            state="error",
            badge="job",
            title="Fine-tuning job failed",
            detail=f"{int(job_counts.get('failed') or 0)} fine-tuning job(s) failed; review logs before retrying.",
            action="open-training-run-plan",
            action_label="Plan",
            requires_approval=True,
        ))
    return rows[:10]


def run_operator_model_snapshot() -> dict[str, Any]:
    """Return read-only model/training evidence for the operator console."""
    settings = load_settings()
    features = load_features()
    training_root = ensure_training_dirs()
    datasets = list_datasets()
    artifacts = list_artifacts()
    finetune = finetune_status()
    endpoint_rows = _endpoint_rows()
    endpoints = {
        "counts": _status_counts(endpoint_rows),
        "items": endpoint_rows,
    }
    primary = _primary_model(settings)
    readiness = _readiness(primary, endpoints, datasets, artifacts, finetune, features)
    alert_rows = _alert_rows(primary, endpoints, datasets, artifacts, finetune, features, readiness)
    jobs = finetune.get("jobs") if isinstance(finetune.get("jobs"), list) else []
    deps = finetune.get("dependencies") if isinstance(finetune.get("dependencies"), dict) else {}

    return {
        "generated_at": _utc_now(),
        "mode": "read-only-local",
        "primary": primary,
        "endpoints": endpoints,
        "training": {
            "root": str(training_root),
            "datasets": _short_list(datasets, 12),
            "artifacts": _short_list(artifacts, 12),
            "dataset_count": len(datasets),
            "artifact_count": len(artifacts),
            "paths": {
                "root": "data/training",
                "datasets": "data/training/datasets",
                "artifacts": "data/training/artifacts",
                "finetune_jobs": "data/training/finetune/jobs",
                "finetune_adapters": "data/training/finetune/adapters",
                "finetune_base_models": "data/training/finetune/base-models",
            },
        },
        "finetune": {
            "dependencies": deps,
            "trainable_models": _short_list(finetune.get("trainable_models") or [], 12),
            "ollama_models": _short_list(finetune.get("ollama_models") or [], 12),
            "jobs": _short_list(jobs, 12),
            "trainable_count": len(finetune.get("trainable_models") or []),
            "ollama_runtime_count": len(finetune.get("ollama_models") or []),
            "job_counts": readiness["job_counts"],
            "base_models_dir": finetune.get("base_models_dir") or "",
            "adapters_dir": finetune.get("adapters_dir") or "",
            "max_steps": finetune.get("max_steps"),
            "default_target_modules": finetune.get("default_target_modules") or "",
        },
        "features": {
            "external_model_endpoints": features.get("external_model_endpoints") is not False,
            "web_search": features.get("web_search") is not False,
            "offline": bool(os.getenv("CLEVERLY_OFFLINE")),
        },
        "readiness": readiness,
        "alert_rows": alert_rows,
        "summary": {
            "state": readiness["state"],
            "model_snapshot_alert_count": len(alert_rows),
            "critical_model_snapshot_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
            "primary_model_configured": bool(primary.get("configured")),
            "enabled_endpoint_count": endpoints["counts"]["enabled"],
            "local_enabled_endpoint_count": endpoints["counts"]["local_enabled"],
            "external_enabled_endpoint_count": endpoints["counts"]["external_enabled"],
            "dataset_count": len(datasets),
            "artifact_count": len(artifacts),
            "trainable_count": len(finetune.get("trainable_models") or []),
            "failed_finetune_count": readiness["job_counts"]["failed"],
            "executes": False,
            "starts_models": False,
            "starts_training": False,
            "starts_finetune": False,
            "downloads_models": False,
            "changes_settings": False,
            "runs_shell": False,
            "uses_network": False,
        },
    }
