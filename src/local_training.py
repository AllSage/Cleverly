"""Offline-only starter language-model training utilities.

This module intentionally avoids network clients, shell command execution, and
optional ML dependencies. It trains a small character n-gram model from local
text so users can experiment with dataset/model workflows inside an air-gapped
Cleverly container.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.atomic_io import atomic_write_json
from src.constants import DATA_DIR


DEFAULT_ORDER = 3
MIN_ORDER = 1
MAX_ORDER = 5
MAX_DATASET_CHARS = 512_000
MAX_GENERATE_CHARS = 1_000

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


class LocalTrainingError(ValueError):
    """Raised for user-fixable training-lab input errors."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else Path(DATA_DIR) / "training"


def _datasets_dir(root: str | Path | None = None) -> Path:
    return _root(root) / "datasets"


def _artifacts_dir(root: str | Path | None = None) -> Path:
    return _root(root) / "artifacts"


def _finetune_dir(root: str | Path | None = None) -> Path:
    return _root(root) / "finetune"


def _slug(value: str, fallback: str) -> str:
    value = (value or "").strip().lower()
    value = _SLUG_RE.sub("-", value).strip(".-_")
    return (value or fallback)[:48]


def _validate_id(value: str, kind: str) -> str:
    value = (value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(value):
        raise LocalTrainingError(f"Invalid {kind} id")
    return value


def _metadata_path(item_dir: Path) -> Path:
    return item_dir / "metadata.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sorted_metadata(items_dir: Path) -> list[dict[str, Any]]:
    if not items_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for item in items_dir.iterdir():
        if not item.is_dir():
            continue
        meta_path = _metadata_path(item)
        if not meta_path.exists():
            continue
        try:
            rows.append(_read_json(meta_path))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda row: str(row.get("created_at", "")), reverse=True)


def ensure_training_dirs(root: str | Path | None = None) -> Path:
    base = _root(root)
    _datasets_dir(base).mkdir(parents=True, exist_ok=True)
    _artifacts_dir(base).mkdir(parents=True, exist_ok=True)
    (_finetune_dir(base) / "jobs").mkdir(parents=True, exist_ok=True)
    (_finetune_dir(base) / "adapters").mkdir(parents=True, exist_ok=True)
    (_finetune_dir(base) / "base-models").mkdir(parents=True, exist_ok=True)
    return base


def list_datasets(root: str | Path | None = None) -> list[dict[str, Any]]:
    ensure_training_dirs(root)
    return _sorted_metadata(_datasets_dir(root))


def list_artifacts(root: str | Path | None = None) -> list[dict[str, Any]]:
    ensure_training_dirs(root)
    return _sorted_metadata(_artifacts_dir(root))


def create_dataset(name: str, text: str, root: str | Path | None = None) -> dict[str, Any]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text.strip()) < 32:
        raise LocalTrainingError("Dataset text must be at least 32 characters")
    if len(text) > MAX_DATASET_CHARS:
        raise LocalTrainingError(f"Dataset text is limited to {MAX_DATASET_CHARS} characters")

    ensure_training_dirs(root)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    dataset_id = f"{_slug(name, 'dataset')}-{digest}"
    dataset_id = _validate_id(dataset_id, "dataset")
    item_dir = _datasets_dir(root) / dataset_id
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "text.txt").write_text(text, encoding="utf-8")

    meta = {
        "id": dataset_id,
        "name": (name or "Dataset").strip()[:80],
        "created_at": _now(),
        "type": "text",
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    atomic_write_json(str(_metadata_path(item_dir)), meta, indent=2)
    return meta


def _dataset_text(dataset_id: str, root: str | Path | None = None) -> str:
    dataset_id = _validate_id(dataset_id, "dataset")
    path = dataset_text_path(dataset_id, root)
    if not path.exists():
        raise LocalTrainingError("Dataset not found")
    return path.read_text(encoding="utf-8")


def dataset_text_path(dataset_id: str, root: str | Path | None = None) -> Path:
    dataset_id = _validate_id(dataset_id, "dataset")
    return _datasets_dir(root) / dataset_id / "text.txt"


def train_ngram(
    dataset_id: str,
    model_name: str = "",
    order: int = DEFAULT_ORDER,
    root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        order = int(order)
    except (TypeError, ValueError):
        raise LocalTrainingError("Order must be a number")
    if order < MIN_ORDER or order > MAX_ORDER:
        raise LocalTrainingError(f"Order must be between {MIN_ORDER} and {MAX_ORDER}")

    text = _dataset_text(dataset_id, root)
    if len(text.strip()) < 32:
        raise LocalTrainingError("Dataset is too small to train")

    transitions: dict[str, Counter[str]] = defaultdict(Counter)
    padded = ("\n" * order) + text
    for idx in range(0, len(padded) - order):
        context = padded[idx : idx + order]
        next_char = padded[idx + order]
        transitions[context][next_char] += 1

    safe_model_name = (model_name or f"{dataset_id}-order{order}").strip()[:80]
    model_digest = hashlib.sha256(
        f"{dataset_id}:{safe_model_name}:{order}:{_now()}".encode("utf-8")
    ).hexdigest()[:12]
    artifact_id = f"{_slug(safe_model_name, 'model')}-{model_digest}"
    artifact_id = _validate_id(artifact_id, "artifact")
    item_dir = _artifacts_dir(root) / artifact_id
    item_dir.mkdir(parents=True, exist_ok=True)

    model = {
        "schema": "cleverly.local_training.char_ngram.v1",
        "id": artifact_id,
        "name": safe_model_name,
        "created_at": _now(),
        "dataset_id": _validate_id(dataset_id, "dataset"),
        "order": order,
        "type": "char-ngram",
        "chars": len(text),
        "vocab_size": len(set(text)),
        "contexts": len(transitions),
        "transitions": {
            context: dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))
            for context, counter in transitions.items()
        },
    }
    atomic_write_json(str(item_dir / "model.json"), model, indent=2)
    meta = {key: model[key] for key in (
        "id", "name", "created_at", "dataset_id", "order", "type", "chars", "vocab_size", "contexts"
    )}
    atomic_write_json(str(_metadata_path(item_dir)), meta, indent=2)
    return meta


def _load_model(artifact_id: str, root: str | Path | None = None) -> dict[str, Any]:
    artifact_id = _validate_id(artifact_id, "artifact")
    path = _artifacts_dir(root) / artifact_id / "model.json"
    if not path.exists():
        raise LocalTrainingError("Artifact not found")
    model = _read_json(path)
    if model.get("schema") != "cleverly.local_training.char_ngram.v1":
        raise LocalTrainingError("Unsupported artifact format")
    return model


def _pick(counter: dict[str, int], temperature: float, rng: random.Random) -> str:
    if not counter:
        return ""
    if temperature <= 0:
        return max(counter.items(), key=lambda item: (item[1], item[0]))[0]
    power = 1.0 / max(temperature, 0.05)
    weighted = [(char, math.pow(max(count, 1), power)) for char, count in counter.items()]
    total = sum(weight for _, weight in weighted)
    threshold = rng.random() * total
    running = 0.0
    for char, weight in weighted:
        running += weight
        if running >= threshold:
            return char
    return weighted[-1][0]


def generate_text(
    artifact_id: str,
    prompt: str = "",
    max_chars: int = 240,
    temperature: float = 0.8,
    seed: int | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        raise LocalTrainingError("Max chars must be a number")
    if max_chars < 1 or max_chars > MAX_GENERATE_CHARS:
        raise LocalTrainingError(f"Max chars must be between 1 and {MAX_GENERATE_CHARS}")
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        raise LocalTrainingError("Temperature must be a number")
    if temperature < 0 or temperature > 2:
        raise LocalTrainingError("Temperature must be between 0 and 2")

    model = _load_model(artifact_id, root)
    order = int(model["order"])
    transitions = model["transitions"]
    rng = random.Random(seed)
    prompt = (prompt or "")[:512]
    context = (("\n" * order) + prompt)[-order:]
    generated: list[str] = []

    fallback = transitions.get("\n" * order) or next(iter(transitions.values()), {})
    for _ in range(max_chars):
        counter = transitions.get(context) or fallback
        char = _pick(counter, temperature, rng)
        if not char:
            break
        generated.append(char)
        context = (context + char)[-order:]

    completion = "".join(generated)
    return {
        "artifact_id": model["id"],
        "prompt": prompt,
        "completion": completion,
        "text": prompt + completion,
        "max_chars": max_chars,
        "temperature": temperature,
    }
