"""Offline LoRA fine-tuning worker.

Run as a child Python process by src.offline_finetune. All model and tokenizer
loads use local_files_only=True and the process environment forces offline HF
behavior.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.atomic_io import atomic_write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), data, indent=2)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _set_status(job_dir: Path, patch: dict[str, Any]) -> None:
    status_path = job_dir / "status.json"
    try:
        status = _read_json(status_path) if status_path.exists() else {}
    except Exception:
        status = {}
    status.update(patch)
    _write_json(status_path, status)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline Cleverly LoRA runner")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    parser.add_argument("--epochs", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--max-length", type=int, required=True)
    parser.add_argument("--lora-rank", type=int, required=True)
    parser.add_argument("--target-modules", required=True)
    return parser.parse_args()


def _force_offline_env() -> None:
    os.environ.update({
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


def _target_modules(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _training_arguments(cls: type[Any], **kwargs: Any) -> Any:
    signature = inspect.signature(cls.__init__)
    supported = {
        name
        for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return cls(**{name: value for name, value in kwargs.items() if name in supported})


def main() -> int:
    args = _parse_args()
    _force_offline_env()
    job_dir = Path(args.job_dir)
    output_dir = Path(args.output_dir)
    _set_status(job_dir, {"status": "running", "started_at": _now()})

    try:
        import torch
        from torch.utils.data import Dataset
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
        from peft import LoraConfig, TaskType, get_peft_model

        dataset_path = Path(args.dataset_path)
        model_path = Path(args.model_path)
        if not dataset_path.exists():
            raise RuntimeError("Dataset file is missing")
        if not model_path.exists():
            raise RuntimeError("Model path is missing")

        text = dataset_path.read_text(encoding="utf-8")
        if len(text.strip()) < 32:
            raise RuntimeError("Dataset is too small for fine-tuning")

        tokenizer = AutoTokenizer.from_pretrained(
            str(model_path),
            local_files_only=True,
            trust_remote_code=False,
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
        if tokenizer.pad_token is None:
            tokenizer.add_special_tokens({"pad_token": "[PAD]"})

        load_kwargs: dict[str, Any] = {
            "local_files_only": True,
            "trust_remote_code": False,
        }
        if torch.cuda.is_available():
            load_kwargs["torch_dtype"] = torch.float16

        model = AutoModelForCausalLM.from_pretrained(str(model_path), **load_kwargs)
        if len(tokenizer) > model.get_input_embeddings().num_embeddings:
            model.resize_token_embeddings(len(tokenizer))
        if hasattr(model.config, "use_cache"):
            model.config.use_cache = False

        tokenized = tokenizer(text, add_special_tokens=False)["input_ids"]
        if tokenizer.eos_token_id is not None:
            tokenized.append(tokenizer.eos_token_id)
        blocks = [
            tokenized[idx : idx + args.max_length]
            for idx in range(0, len(tokenized), args.max_length)
            if len(tokenized[idx : idx + args.max_length]) >= 2
        ]
        if not blocks:
            raise RuntimeError("Dataset did not produce trainable token blocks")

        class TextDataset(Dataset):
            def __len__(self) -> int:
                return len(blocks)

            def __getitem__(self, idx: int) -> dict[str, Any]:
                return {"input_ids": blocks[idx]}

        lora = LoraConfig(
            r=args.lora_rank,
            lora_alpha=max(args.lora_rank * 2, 2),
            lora_dropout=0.05,
            bias="none",
            task_type=TaskType.CAUSAL_LM,
            target_modules=_target_modules(args.target_modules),
        )
        model = get_peft_model(model, lora)

        training_args = _training_arguments(
            TrainingArguments,
            output_dir=str(output_dir),
            overwrite_output_dir=True,
            num_train_epochs=args.epochs,
            max_steps=args.max_steps,
            per_device_train_batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            logging_steps=1,
            save_strategy="no",
            report_to=[],
            remove_unused_columns=False,
            dataloader_pin_memory=False,
        )
        collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=TextDataset(),
            data_collator=collator,
        )
        result = trainer.train()
        output_dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        meta = {
            "status": "completed",
            "finished_at": _now(),
            "exit_code": 0,
            "adapter_path": str(output_dir),
            "train_loss": getattr(result, "training_loss", None),
            "offline": True,
        }
        _write_json(output_dir / "cleverly_adapter.json", meta)
        _write_json(job_dir / "result.json", meta)
        _set_status(job_dir, meta)
        return 0
    except Exception as exc:
        error = {
            "status": "failed",
            "finished_at": _now(),
            "exit_code": 1,
            "error": str(exc),
            "traceback_tail": traceback.format_exc()[-6000:],
        }
        _write_json(job_dir / "result.json", error)
        _set_status(job_dir, error)
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
