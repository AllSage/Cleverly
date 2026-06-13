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

