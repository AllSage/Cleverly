"""Offline LoRA fine-tuning job management.

This layer only starts jobs when the optional training dependencies and a
trainable local model directory are already present. It never installs
packages or downloads model files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.atomic_io import atomic_write_json
from core.platform_compat import pid_alive
from src.constants import BASE_DIR, DATA_DIR
from src.local_training import LocalTrainingError, dataset_text_path, ensure_training_dirs


REQUIRED_FINETUNE_DEPS = {
    "torch": "torch",
    "transformers": "transformers",
    "peft": "peft",
    "accelerate": "accelerate",
}

MAX_FINETUNE_STEPS = 1000
MAX_FINETUNE_EPOCHS = 10
MAX_FINETUNE_LENGTH = 2048
DEFAULT_TARGET_MODULES = "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else Path(DATA_DIR) / "training"


def _finetune_dir(root: str | Path | None = None) -> Path:
    return _root(root) / "finetune"


def _jobs_dir(root: str | Path | None = None) -> Path:
    return _finetune_dir(root) / "jobs"


def _adapters_dir(root: str | Path | None = None) -> Path:
    return _finetune_dir(root) / "adapters"


def _base_models_dir(root: str | Path | None = None) -> Path:
    return _finetune_dir(root) / "base-models"


def _slug(value: str, fallback: str) -> str:
    value = (value or "").strip().lower()
    value = _SLUG_RE.sub("-", value).strip(".-_")
    return (value or fallback)[:48]


def _validate_id(value: str, kind: str) -> str:
    value = (value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(value):
        raise LocalTrainingError(f"Invalid {kind} id")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), data, indent=2)


def dependency_status() -> dict[str, Any]:
    deps = {
        name: {
            "module": module,
            "available": importlib.util.find_spec(module) is not None,
        }
        for name, module in REQUIRED_FINETUNE_DEPS.items()
    }
    return {
        "available": all(item["available"] for item in deps.values()),
        "required": deps,
        "missing": [name for name, item in deps.items() if not item["available"]],
    }


def _candidate_model_roots(root: str | Path | None = None) -> list[Path]:
    data = Path(DATA_DIR) if root is None else _root(root).parent
    roots = [
        data / "huggingface",
        data / "models",
        _base_models_dir(root),
    ]
    env_dir = os.environ.get("CLEVERLY_FINETUNE_MODEL_DIR", "").strip()
    if env_dir:
        env_path = Path(env_dir)
        try:
            env_path.resolve().relative_to(data.resolve())
            roots.append(env_path)
        except ValueError:
            pass
    seen: set[Path] = set()
    result: list[Path] = []
    for path in roots:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return result


def _has_model_weights(path: Path) -> bool:
    patterns = [
        "model.safetensors",
        "pytorch_model.bin",
        "tf_model.h5",
        "model*.safetensors",
        "pytorch_model*.bin",
        "*.index.json",
    ]
    return any(any(path.glob(pattern)) for pattern in patterns)


def _has_tokenizer(path: Path) -> bool:
    names = {
        "tokenizer.json",
        "tokenizer.model",
        "tokenizer_config.json",
        "vocab.json",
        "spiece.model",
    }
    return any((path / name).exists() for name in names)


def _model_info(path: Path, root: str | Path | None = None) -> dict[str, Any] | None:
    if not (path / "config.json").exists() or not _has_model_weights(path) or not _has_tokenizer(path):
        return None
    try:
        cfg = _read_json(path / "config.json")
    except (OSError, json.JSONDecodeError):
        cfg = {}
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    model_type = str(cfg.get("model_type") or "model")
    name = path.name if path.name != "snapshots" else path.parent.name
    model_id = f"{_slug(name, model_type)}-{digest}"
    data = Path(DATA_DIR) if root is None else _root(root).parent
    try:
        display_path = str(path.resolve().relative_to(data.resolve()))
    except ValueError:
        display_path = str(path)
    return {
        "id": model_id,
        "name": name,
        "path": str(path),
        "display_path": display_path,
        "model_type": model_type,
        "architectures": cfg.get("architectures") or [],
        "trainable": True,
    }


def discover_trainable_models(root: str | Path | None = None) -> list[dict[str, Any]]:
    ensure_training_dirs(root)
    found: dict[str, dict[str, Any]] = {}
    for root_path in _candidate_model_roots(root):
        if not root_path.exists():
            continue
        candidates = [root_path] if (root_path / "config.json").exists() else []
        try:
            candidates.extend(path.parent for path in root_path.rglob("config.json"))
        except OSError:
            continue
        for candidate in candidates[:500]:
            info = _model_info(candidate, root)
            if info:
                found[info["id"]] = info
    return sorted(found.values(), key=lambda item: item["display_path"])


def discover_ollama_models(root: str | Path | None = None) -> list[dict[str, Any]]:
    data = Path(DATA_DIR) if root is None else _root(root).parent
    manifests = data / "ollama" / "models" / "manifests"
    if not manifests.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in manifests.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(manifests)
            parts = rel.parts
            if len(parts) >= 3:
                model_name = f"{parts[-2]}:{parts[-1]}"
            else:
                model_name = path.name
            digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
            rows.append({
                "id": f"ollama-{_slug(model_name, 'model')}-{digest}",
                "name": model_name,
                "display_path": str(path.relative_to(data)),
                "trainable": False,
                "reason": "Ollama runtime model; add matching trainable HF-format weights for LoRA.",
            })
        except OSError:
            continue
    return sorted(rows, key=lambda item: item["name"])


def _resolve_model(model_id: str, root: str | Path | None = None) -> dict[str, Any]:
    model_id = _validate_id(model_id, "model")
    for model in discover_trainable_models(root):
        if model["id"] == model_id:
            return model
    raise LocalTrainingError("Trainable model not found")


def _job_status_path(job_dir: Path) -> Path:
    return job_dir / "status.json"


def _job_config_path(job_dir: Path) -> Path:
    return job_dir / "job.json"


def _job_log_path(job_dir: Path) -> Path:
    return job_dir / "train.log"


def _offline_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "CLEVERLY_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "WANDB_DISABLED": "true",
        "DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "NO_PROXY": "*",
        "no_proxy": "*",
    })
    return env


def _normalize_float(value: float, name: str, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LocalTrainingError(f"{name} must be a number")
    if number < min_value or number > max_value:
        raise LocalTrainingError(f"{name} must be between {min_value} and {max_value}")
    return number


def _normalize_int(value: int, name: str, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise LocalTrainingError(f"{name} must be a number")
    if number < min_value or number > max_value:
        raise LocalTrainingError(f"{name} must be between {min_value} and {max_value}")
    return number


def start_lora_job(
    *,
    dataset_id: str,
    model_id: str,
    output_name: str,
    max_steps: int = 20,
    epochs: int = 1,
    batch_size: int = 1,
    learning_rate: float = 2e-4,
    max_length: int = 512,
    lora_rank: int = 8,
    target_modules: str = DEFAULT_TARGET_MODULES,
    root: str | Path | None = None,
) -> dict[str, Any]:
    deps = dependency_status()
    if not deps["available"]:
        raise LocalTrainingError("Fine-tuning dependencies missing: " + ", ".join(deps["missing"]))

    dataset_id = _validate_id(dataset_id, "dataset")
    text_path = dataset_text_path(dataset_id, root)
    if not text_path.exists():
        raise LocalTrainingError("Dataset not found")
    model = _resolve_model(model_id, root)

    max_steps = _normalize_int(max_steps, "Max steps", 1, MAX_FINETUNE_STEPS)
    epochs = _normalize_int(epochs, "Epochs", 1, MAX_FINETUNE_EPOCHS)
    batch_size = _normalize_int(batch_size, "Batch size", 1, 16)
    max_length = _normalize_int(max_length, "Max length", 64, MAX_FINETUNE_LENGTH)
    lora_rank = _normalize_int(lora_rank, "LoRA rank", 1, 256)
    learning_rate = _normalize_float(learning_rate, "Learning rate", 1e-7, 1.0)
    target_modules = ",".join(
        part.strip()
        for part in (target_modules or DEFAULT_TARGET_MODULES).split(",")
        if part.strip()
    )
    if not target_modules:
        raise LocalTrainingError("Target modules are required")

    ensure_training_dirs(root)
    safe_name = _slug(output_name or f"{dataset_id}-lora", "lora")
    digest = hashlib.sha256(f"{dataset_id}:{model_id}:{safe_name}:{_now()}".encode("utf-8")).hexdigest()[:12]
    job_id = _validate_id(f"{safe_name}-{digest}", "job")
    job_dir = _jobs_dir(root) / job_id
    adapter_dir = _adapters_dir(root) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "id": job_id,
        "type": "lora",
        "created_at": _now(),
        "dataset_id": dataset_id,
        "model_id": model_id,
        "model_name": model["name"],
        "model_path": model["path"],
        "output_name": output_name or safe_name,
        "adapter_path": str(adapter_dir),
        "max_steps": max_steps,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "max_length": max_length,
        "lora_rank": lora_rank,
        "target_modules": target_modules,
    }
    _write_json(_job_config_path(job_dir), config)
    _write_json(_job_status_path(job_dir), {
        **config,
        "status": "queued",
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
    })

    command = [
        sys.executable,
        "-m",
        "src.offline_finetune_runner",
        "--job-dir",
        str(job_dir),
        "--dataset-path",
        str(text_path),
        "--model-path",
        model["path"],
        "--output-dir",
        str(adapter_dir),
        "--max-steps",
        str(max_steps),
        "--epochs",
        str(epochs),
        "--batch-size",
        str(batch_size),
        "--learning-rate",
        str(learning_rate),
        "--max-length",
        str(max_length),
        "--lora-rank",
        str(lora_rank),
        "--target-modules",
        target_modules,
    ]
    _write_json(job_dir / "command.json", {"argv": command, "offline_env": True})
    log_fh = _job_log_path(job_dir).open("ab")
    try:
        proc = subprocess.Popen(
            command,
            cwd=BASE_DIR,
            env=_offline_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            shell=False,
        )
    finally:
        log_fh.close()

    status = {
        **config,
        "status": "running",
        "started_at": _now(),
        "finished_at": None,
        "pid": proc.pid,
        "exit_code": None,
    }
    _write_json(_job_status_path(job_dir), status)
    return status


def read_job(job_id: str, root: str | Path | None = None, log_chars: int = 4000) -> dict[str, Any]:
    job_id = _validate_id(job_id, "job")
    job_dir = _jobs_dir(root) / job_id
    status_path = _job_status_path(job_dir)
    if not status_path.exists():
        raise LocalTrainingError("Fine-tune job not found")
    status = _read_json(status_path)
    if status.get("status") == "running" and status.get("pid") and not pid_alive(status.get("pid")):
        result_path = job_dir / "result.json"
        if result_path.exists():
            status.update(_read_json(result_path))
        else:
            status.update({
                "status": "failed",
                "finished_at": _now(),
                "exit_code": None,
                "error": "Training process exited without writing a result.",
            })
        _write_json(status_path, status)
    log_path = _job_log_path(job_dir)
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        status["log_tail"] = text[-log_chars:]
    else:
        status["log_tail"] = ""
    return status


def list_jobs(root: str | Path | None = None) -> list[dict[str, Any]]:
    ensure_training_dirs(root)
    rows: list[dict[str, Any]] = []
    for job_dir in _jobs_dir(root).iterdir():
        if not job_dir.is_dir():
            continue
        try:
            rows.append(read_job(job_dir.name, root=root, log_chars=1000))
        except (OSError, json.JSONDecodeError, LocalTrainingError):
            continue
    return sorted(rows, key=lambda item: str(item.get("created_at", "")), reverse=True)


def finetune_status(root: str | Path | None = None) -> dict[str, Any]:
    ensure_training_dirs(root)
    return {
        "dependencies": dependency_status(),
        "trainable_models": discover_trainable_models(root),
        "ollama_models": discover_ollama_models(root),
        "jobs": list_jobs(root),
        "base_models_dir": str(_base_models_dir(root)),
        "adapters_dir": str(_adapters_dir(root)),
        "max_steps": MAX_FINETUNE_STEPS,
        "default_target_modules": DEFAULT_TARGET_MODULES,
    }

