import json
import os
import sys
import types
from pathlib import Path

import pytest

from src import offline_finetune_runner as runner


OFFLINE_ENV_KEYS = [
    "CLEVERLY_OFFLINE",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "HF_DATASETS_OFFLINE",
    "WANDB_DISABLED",
    "DISABLE_TELEMETRY",
    "TOKENIZERS_PARALLELISM",
    "NO_PROXY",
    "no_proxy",
]


@pytest.fixture(autouse=True)
def restore_runner_env():
    original = {key: os.environ.get(key) for key in OFFLINE_ENV_KEYS}
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_status_helpers_recover_from_corrupt_status(tmp_path):
    job_dir = tmp_path / "job"
    status_path = job_dir / "status.json"
    status_path.parent.mkdir()
    status_path.write_text("{", encoding="utf-8")

    runner._set_status(job_dir, {"status": "running"})

    assert runner._read_json(status_path)["status"] == "running"
    assert runner._now().endswith("Z")


def test_force_offline_env_and_target_modules(monkeypatch):
    for key in ["CLEVERLY_OFFLINE", "HF_HUB_OFFLINE", "NO_PROXY", "no_proxy"]:
        monkeypatch.delenv(key, raising=False)

    runner._force_offline_env()

    assert sys.modules is not None
    assert runner._target_modules(" q_proj, , v_proj ,, ") == ["q_proj", "v_proj"]
    assert runner.os.environ["CLEVERLY_OFFLINE"] == "1"
    assert runner.os.environ["HF_HUB_OFFLINE"] == "1"
    assert runner.os.environ["NO_PROXY"] == "*"
    assert runner.os.environ["no_proxy"] == "*"


def test_training_arguments_keeps_keyword_only_supported_values():
    class DummyTrainingArguments:
        def __init__(self, output_dir, *, max_steps=-1):
            self.output_dir = output_dir
            self.max_steps = max_steps

    args = runner._training_arguments(
        DummyTrainingArguments,
        output_dir="out",
        max_steps=3,
        ignored=True,
    )

    assert args.output_dir == "out"
    assert args.max_steps == 3
    assert not hasattr(args, "ignored")


class FakeTokenizer:
    token_ids = list(range(9))
    eos_id = 99
    saved_to = None

    def __init__(self):
        self.pad_token = None
        self.eos_token = None
        self.unk_token = None
        self.eos_token_id = self.eos_id
        self.special_tokens = None

    @classmethod
    def from_pretrained(cls, path, **kwargs):
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}
        tok = cls()
        tok.loaded_from = path
        return tok

    def add_special_tokens(self, tokens):
        self.special_tokens = tokens
        self.pad_token = tokens["pad_token"]

    def __call__(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return {"input_ids": list(self.token_ids)}

    def __len__(self):
        return 8

    def save_pretrained(self, path):
        self.__class__.saved_to = path
        Path(path, "tokenizer.saved").write_text("saved", encoding="utf-8")


class FakeModel:
    saved_to = None
    loaded_kwargs = None
    resized_to = None

    def __init__(self):
        self.config = types.SimpleNamespace(use_cache=True)

    @classmethod
    def from_pretrained(cls, path, **kwargs):
        cls.loaded_kwargs = kwargs
        model = cls()
        model.loaded_from = path
        return model

    def get_input_embeddings(self):
        return types.SimpleNamespace(num_embeddings=4)

    def resize_token_embeddings(self, length):
        self.__class__.resized_to = length

    def save_pretrained(self, path):
        self.__class__.saved_to = path
        Path(path, "adapter.saved").write_text("saved", encoding="utf-8")


class FakeTrainingArguments:
    def __init__(self, output_dir=None, max_steps=-1, **kwargs):
        self.output_dir = output_dir
        self.max_steps = max_steps
        self.kwargs = kwargs


class FakeDataCollator:
    def __init__(self, tokenizer, mlm):
        self.tokenizer = tokenizer
        self.mlm = mlm


class FakeTrainer:
    seen_len = None
    seen_item = None

    def __init__(self, model, args, train_dataset, data_collator):
        self.model = model
        self.args = args
        self.train_dataset = train_dataset
        self.data_collator = data_collator

    def train(self):
        self.__class__.seen_len = len(self.train_dataset)
        self.__class__.seen_item = self.train_dataset[0]
        return types.SimpleNamespace(training_loss=0.42)


def install_training_modules(monkeypatch, *, cuda=True, token_ids=None, eos_id=99):
    FakeTokenizer.token_ids = list(range(9)) if token_ids is None else token_ids
    FakeTokenizer.eos_id = eos_id
    FakeTokenizer.saved_to = None
    FakeModel.saved_to = None
    FakeModel.loaded_kwargs = None
    FakeModel.resized_to = None
    FakeTrainer.seen_len = None
    FakeTrainer.seen_item = None

    torch_mod = types.ModuleType("torch")
    torch_mod.float16 = "float16"
    torch_mod.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    torch_utils = types.ModuleType("torch.utils")
    torch_data = types.ModuleType("torch.utils.data")
    torch_data.Dataset = object

    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = FakeTokenizer
    transformers.AutoModelForCausalLM = FakeModel
    transformers.DataCollatorForLanguageModeling = FakeDataCollator
    transformers.Trainer = FakeTrainer
    transformers.TrainingArguments = FakeTrainingArguments

    peft = types.ModuleType("peft")
    peft.LoraConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
    peft.TaskType = types.SimpleNamespace(CAUSAL_LM="causal-lm")
    peft.get_peft_model = lambda model, lora: model

    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "torch.utils", torch_utils)
    monkeypatch.setitem(sys.modules, "torch.utils.data", torch_data)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "peft", peft)


def set_runner_argv(monkeypatch, job_dir, dataset_path, model_path, output_dir, *, max_length=4):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "offline_finetune_runner",
            "--job-dir",
            str(job_dir),
            "--dataset-path",
            str(dataset_path),
            "--model-path",
            str(model_path),
            "--output-dir",
            str(output_dir),
            "--max-steps",
            "2",
            "--epochs",
            "1",
            "--batch-size",
            "1",
            "--learning-rate",
            "0.001",
            "--max-length",
            str(max_length),
            "--lora-rank",
            "4",
            "--target-modules",
            "q_proj,v_proj",
        ],
    )


def test_main_success_runs_fake_training_offline(tmp_path, monkeypatch):
    install_training_modules(monkeypatch, cuda=True)
    job_dir = tmp_path / "job"
    dataset_path = tmp_path / "data.txt"
    model_path = tmp_path / "model"
    output_dir = tmp_path / "out"
    dataset_path.write_text("training data " * 8, encoding="utf-8")
    model_path.mkdir()
    set_runner_argv(monkeypatch, job_dir, dataset_path, model_path, output_dir)

    assert runner.main() == 0

    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    adapter_meta = json.loads((output_dir / "cleverly_adapter.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert result["adapter_path"] == str(output_dir)
    assert adapter_meta["train_loss"] == 0.42
    assert adapter_meta["offline"] is True
    assert FakeModel.loaded_kwargs["local_files_only"] is True
    assert FakeModel.loaded_kwargs["torch_dtype"] == "float16"
    assert FakeModel.resized_to == 8
    assert FakeTrainer.seen_len == 3
    assert FakeTrainer.seen_item == {"input_ids": [0, 1, 2, 3]}
    assert (output_dir / "adapter.saved").exists()
    assert (output_dir / "tokenizer.saved").exists()


def run_failure_case(tmp_path, monkeypatch, *, dataset_text, make_dataset=True, make_model=True, token_ids=None, eos_id=99):
    install_training_modules(monkeypatch, cuda=False, token_ids=token_ids, eos_id=eos_id)
    job_dir = tmp_path / "job"
    dataset_path = tmp_path / "data.txt"
    model_path = tmp_path / "model"
    output_dir = tmp_path / "out"
    if make_dataset:
        dataset_path.write_text(dataset_text, encoding="utf-8")
    if make_model:
        model_path.mkdir()
    set_runner_argv(monkeypatch, job_dir, dataset_path, model_path, output_dir)

    assert runner.main() == 1
    return json.loads((job_dir / "result.json").read_text(encoding="utf-8"))


def test_main_failure_dataset_missing(tmp_path, monkeypatch, capsys):
    result = run_failure_case(tmp_path, monkeypatch, dataset_text="", make_dataset=False)

    captured = capsys.readouterr()
    assert result["status"] == "failed"
    assert result["error"] == "Dataset file is missing"
    assert "Dataset file is missing" in captured.err


def test_main_failure_model_missing(tmp_path, monkeypatch):
    result = run_failure_case(tmp_path, monkeypatch, dataset_text="training data " * 8, make_model=False)

    assert result["error"] == "Model path is missing"


def test_main_failure_dataset_too_small(tmp_path, monkeypatch):
    result = run_failure_case(tmp_path, monkeypatch, dataset_text="too small", make_model=True)

    assert result["error"] == "Dataset is too small for fine-tuning"


def test_main_failure_no_trainable_blocks(tmp_path, monkeypatch):
    result = run_failure_case(
        tmp_path,
        monkeypatch,
        dataset_text="training data " * 8,
        make_model=True,
        token_ids=[7],
        eos_id=None,
    )

    assert result["error"] == "Dataset did not produce trainable token blocks"
