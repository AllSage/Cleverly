from pathlib import Path

import pytest

from src.local_training import (
    DEFAULT_ORDER,
    LocalTrainingError,
    create_dataset,
    generate_text,
    list_artifacts,
    list_datasets,
    train_ngram,
)
from src import offline_finetune


def test_local_training_round_trip(tmp_path: Path):
    text = (
        "Cleverly keeps private work local.\n"
        "Cleverly trains only on text the user provides.\n"
        "Offline systems should avoid downloads and network calls.\n"
    )
    dataset = create_dataset("Private Notes", text, root=tmp_path)
    assert dataset["id"].startswith("private-notes-")
    assert dataset["chars"] == len(text)
    assert list_datasets(tmp_path)[0]["id"] == dataset["id"]

    artifact = train_ngram(dataset["id"], "Starter", DEFAULT_ORDER, root=tmp_path)
    assert artifact["type"] == "char-ngram"
    assert artifact["order"] == 3
    assert list_artifacts(tmp_path)[0]["id"] == artifact["id"]

    output = generate_text(artifact["id"], prompt="Cleverly ", max_chars=80, seed=7, root=tmp_path)
    assert output["text"].startswith("Cleverly ")
    assert output["completion"]
    assert len(output["completion"]) <= 80


def test_local_training_rejects_path_like_ids(tmp_path: Path):
    text = "abcdefghijklmnopqrstuvwxyz " * 3
    dataset = create_dataset("Corpus", text, root=tmp_path)

    with pytest.raises(LocalTrainingError):
        train_ngram("../" + dataset["id"], root=tmp_path)

    with pytest.raises(LocalTrainingError):
        generate_text("../../model", root=tmp_path)


def test_finetune_discovers_trainable_and_ollama_models(tmp_path: Path):
    model_dir = tmp_path / "finetune" / "base-models" / "tiny"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text('{"model_type":"llama","architectures":["TinyForCausalLM"]}', encoding="utf-8")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.safetensors").write_bytes(b"not-real-weights")

    trainable = offline_finetune.discover_trainable_models(tmp_path)
    assert len(trainable) == 1
    assert trainable[0]["trainable"] is True
    assert trainable[0]["model_type"] == "llama"

    manifest = tmp_path.parent / "ollama" / "models" / "manifests" / "registry.ollama.ai" / "library" / "llama3.2" / "3b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    ollama = offline_finetune.discover_ollama_models(tmp_path)
    assert ollama[0]["trainable"] is False
    assert "trainable HF-format weights" in ollama[0]["reason"]


def test_finetune_refuses_when_optional_deps_are_missing(tmp_path: Path, monkeypatch):
    dataset = create_dataset("Corpus", "abcdefghijklmnopqrstuvwxyz " * 4, root=tmp_path)
    monkeypatch.setattr(offline_finetune, "REQUIRED_FINETUNE_DEPS", {"missing": "__cleverly_missing_pkg__"})

    with pytest.raises(LocalTrainingError, match="Fine-tuning dependencies missing"):
        offline_finetune.start_lora_job(
            dataset_id=dataset["id"],
            model_id="missing-model",
            output_name="adapter",
            root=tmp_path,
        )


def test_finetune_offline_environment_flags():
    env = offline_finetune._offline_env()
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["HF_DATASETS_OFFLINE"] == "1"
    assert env["NO_PROXY"] == "*"


def test_finetune_training_arguments_filter_unsupported_options():
    from src.offline_finetune_runner import _training_arguments

    class DummyTrainingArguments:
        def __init__(self, output_dir=None, max_steps=-1):
            self.output_dir = output_dir
            self.max_steps = max_steps

    args = _training_arguments(
        DummyTrainingArguments,
        output_dir="out",
        max_steps=1,
        overwrite_output_dir=True,
    )

    assert args.output_dir == "out"
    assert args.max_steps == 1
    assert not hasattr(args, "overwrite_output_dir")
