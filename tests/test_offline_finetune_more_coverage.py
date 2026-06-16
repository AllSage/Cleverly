import json
from pathlib import Path

import pytest

from src import offline_finetune
from src.local_training import LocalTrainingError, create_dataset


def _make_trainable_model(
    model_dir: Path,
    *,
    config: str = '{"model_type":"llama"}',
    root: Path | None = None,
) -> dict | None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.json").write_text(config, encoding="utf-8")
    (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (model_dir / "pytorch_model-00001.bin").write_bytes(b"weights")
    if root is None:
        return None
    return offline_finetune.discover_trainable_models(root)[0]


def test_dependency_status_and_model_discovery_edges(tmp_path, monkeypatch):
    assert offline_finetune._now().endswith("Z")

    def fake_find_spec(module):
        return object() if module == "present_pkg" else None

    monkeypatch.setattr(offline_finetune.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(
        offline_finetune,
        "REQUIRED_FINETUNE_DEPS",
        {"present": "present_pkg", "missing": "missing_pkg"},
    )
    status = offline_finetune.dependency_status()
    assert status["available"] is False
    assert status["missing"] == ["missing"]

    root = tmp_path / "training"
    good = root / "finetune" / "base-models" / "good"
    bad_json = root / "finetune" / "base-models" / "badjson"
    missing_tokenizer = root / "finetune" / "base-models" / "missing-tokenizer"
    outside_env = tmp_path.parent / "outside-finetune-env"
    inside_env = tmp_path / "huggingface" / "inside"
    _make_trainable_model(good)
    _make_trainable_model(bad_json, config="{bad json")
    missing_tokenizer.mkdir(parents=True)
    (missing_tokenizer / "config.json").write_text("{}", encoding="utf-8")
    (missing_tokenizer / "model.safetensors").write_bytes(b"weights")
    outside_env.mkdir()
    _make_trainable_model(inside_env)

    monkeypatch.setenv("CLEVERLY_FINETUNE_MODEL_DIR", str(outside_env))
    roots = [path.resolve() for path in offline_finetune._candidate_model_roots(root)]
    assert outside_env.resolve() not in roots

    monkeypatch.setenv("CLEVERLY_FINETUNE_MODEL_DIR", str(inside_env))
    models = offline_finetune.discover_trainable_models(root)
    names = {model["name"] for model in models}
    assert {"good", "badjson", "inside"} <= names
    assert "missing-tokenizer" not in names
    outside_model_dir = tmp_path.parent / f"{tmp_path.name}-outside-model"
    outside_model = _make_trainable_model(outside_model_dir)
    outside_info = offline_finetune._model_info(outside_model_dir, root=root)
    assert outside_model is None
    assert outside_info["display_path"] == str(outside_model_dir)

    real_rglob = Path.rglob

    def bad_rglob(self, pattern):
        if self.name == "base-models":
            raise OSError("scan failed")
        return real_rglob(self, pattern)

    root_with_scan_error = tmp_path / "isolated" / "training"
    (root_with_scan_error / "finetune" / "base-models").mkdir(parents=True)
    monkeypatch.setattr(Path, "rglob", bad_rglob)
    assert offline_finetune.discover_trainable_models(root_with_scan_error) == []
    monkeypatch.setattr(Path, "rglob", real_rglob)

    with pytest.raises(LocalTrainingError, match="Invalid model id"):
        offline_finetune._resolve_model("../bad", root)
    with pytest.raises(LocalTrainingError, match="Trainable model not found"):
        offline_finetune._resolve_model("missing-model", root)


def test_ollama_discovery_variants_and_missing_manifest(tmp_path):
    assert offline_finetune.discover_ollama_models(tmp_path / "training") == []

    data_root = tmp_path
    manifests = data_root / "ollama" / "models" / "manifests"
    simple = manifests / "single"
    library = manifests / "registry.ollama.ai" / "library" / "llama3.2" / "3b"
    simple.parent.mkdir(parents=True)
    library.parent.mkdir(parents=True)
    simple.write_text("{}", encoding="utf-8")
    library.write_text("{}", encoding="utf-8")

    rows = offline_finetune.discover_ollama_models(data_root / "training")
    assert [row["name"] for row in rows] == ["llama3.2:3b", "single"]
    assert all(row["trainable"] is False for row in rows)


def test_ollama_discovery_skips_oserror_entries(tmp_path, monkeypatch):
    manifests = tmp_path / "ollama" / "models" / "manifests"
    item = manifests / "single"
    item.parent.mkdir(parents=True)
    item.write_text("{}", encoding="utf-8")
    real_relative_to = Path.relative_to

    def broken_relative_to(self, *other):
        if self == item:
            raise OSError("rel failed")
        return real_relative_to(self, *other)

    monkeypatch.setattr(Path, "relative_to", broken_relative_to)
    assert offline_finetune.discover_ollama_models(tmp_path / "training") == []


def test_start_lora_job_validates_inputs_and_launches_offline(tmp_path, monkeypatch):
    dataset = create_dataset("Corpus", "abcdefghijklmnopqrstuvwxyz " * 4, root=tmp_path)
    model = _make_trainable_model(tmp_path / "finetune" / "base-models" / "tiny", root=tmp_path)
    assert model is not None
    popen_calls = []

    class FakeProcess:
        pid = 2468

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(offline_finetune, "dependency_status", lambda: {"available": True, "missing": []})
    monkeypatch.setattr(offline_finetune.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(offline_finetune, "_now", lambda: "2026-06-16T00:00:00Z")

    with pytest.raises(LocalTrainingError, match="Dataset not found"):
        offline_finetune.start_lora_job(
            dataset_id="missing",
            model_id=model["id"],
            output_name="Adapter",
            root=tmp_path,
        )

    with pytest.raises(LocalTrainingError, match="Target modules are required"):
        offline_finetune.start_lora_job(
            dataset_id=dataset["id"],
            model_id=model["id"],
            output_name="Adapter",
            target_modules=" , ",
            root=tmp_path,
        )

    job = offline_finetune.start_lora_job(
        dataset_id=dataset["id"],
        model_id=model["id"],
        output_name="Adapter Name",
        max_steps="3",
        epochs="2",
        batch_size="1",
        learning_rate="0.001",
        max_length="128",
        lora_rank="4",
        target_modules=" q_proj, v_proj ",
        root=tmp_path,
    )
    assert job["status"] == "running"
    assert job["pid"] == 2468
    assert job["target_modules"] == "q_proj,v_proj"
    command, kwargs = popen_calls[-1]
    assert command[:3] == [offline_finetune.sys.executable, "-m", "src.offline_finetune_runner"]
    assert kwargs["shell"] is False
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"

    job_dir = tmp_path / "finetune" / "jobs" / job["id"]
    assert (job_dir / "job.json").exists()
    assert json.loads((job_dir / "command.json").read_text(encoding="utf-8"))["offline_env"] is True


@pytest.mark.parametrize(
    ("func", "value", "message"),
    [
        (offline_finetune._normalize_int, "nope", "Steps must be a number"),
        (offline_finetune._normalize_int, 0, "Steps must be between 1 and 2"),
        (offline_finetune._normalize_float, "bad", "Rate must be a number"),
        (offline_finetune._normalize_float, 3, "Rate must be between 0.1 and 1.0"),
    ],
)
def test_normalizers_reject_bad_values(func, value, message):
    with pytest.raises(LocalTrainingError, match=message):
        func(value, "Steps" if func is offline_finetune._normalize_int else "Rate", 1 if func is offline_finetune._normalize_int else 0.1, 2 if func is offline_finetune._normalize_int else 1.0)


def test_read_job_updates_stale_running_jobs_and_tails_logs(tmp_path, monkeypatch):
    jobs = tmp_path / "finetune" / "jobs"
    done_dir = jobs / "done"
    failed_dir = jobs / "failed"
    done_dir.mkdir(parents=True)
    failed_dir.mkdir()
    monkeypatch.setattr(offline_finetune, "pid_alive", lambda _pid: False)
    monkeypatch.setattr(offline_finetune, "_now", lambda: "2026-06-16T01:00:00Z")

    offline_finetune._write_json(done_dir / "status.json", {"id": "done", "status": "running", "pid": 1})
    offline_finetune._write_json(done_dir / "result.json", {"status": "succeeded", "exit_code": 0})
    (done_dir / "train.log").write_text("x" * 20 + "tail", encoding="utf-8")
    done = offline_finetune.read_job("done", root=tmp_path, log_chars=6)
    assert done["status"] == "succeeded"
    assert done["log_tail"] == "xxtail"

    offline_finetune._write_json(failed_dir / "status.json", {"id": "failed", "status": "running", "pid": 2})
    failed = offline_finetune.read_job("failed", root=tmp_path)
    assert failed["status"] == "failed"
    assert "without writing a result" in failed["error"]
    assert failed["log_tail"] == ""

    with pytest.raises(LocalTrainingError, match="Fine-tune job not found"):
        offline_finetune.read_job("missing", root=tmp_path)


def test_list_jobs_skips_bad_entries_and_status_composes_sections(tmp_path, monkeypatch):
    jobs = tmp_path / "finetune" / "jobs"
    good = jobs / "good"
    bad = jobs / "bad"
    good.mkdir(parents=True)
    bad.mkdir()
    offline_finetune._write_json(good / "status.json", {"id": "good", "status": "succeeded", "created_at": "z"})
    (bad / "status.json").write_text("{bad", encoding="utf-8")
    (jobs / "not-a-dir").write_text("skip", encoding="utf-8")

    listed = offline_finetune.list_jobs(tmp_path)
    assert [job["id"] for job in listed] == ["good"]

    monkeypatch.setattr(offline_finetune, "dependency_status", lambda: {"available": True})
    status = offline_finetune.finetune_status(tmp_path)
    assert status["dependencies"] == {"available": True}
    assert status["jobs"][0]["id"] == "good"
    assert status["max_steps"] == offline_finetune.MAX_FINETUNE_STEPS
