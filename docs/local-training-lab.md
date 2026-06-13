# Cleverly Local Training Lab

The Training Lab is an offline-first starter workflow for experimenting with
language-model training concepts inside Cleverly.

## What It Does

- Saves pasted local text as datasets under `data/training/datasets`.
- Trains a tiny character n-gram model with default order `3`.
- Saves generated artifacts under `data/training/artifacts`.
- Generates sample text from a selected saved artifact.
- Optionally starts LoRA fine-tuning jobs when training dependencies and a
  trainable local base model are already present.

## Offline Boundaries

The starter n-gram trainer does not download datasets, install packages, start
servers, call model endpoints, or run host commands. It uses only local files
plus same-origin `/api/training/*` requests from the UI.

Advanced LoRA fine-tuning runs as a local Python child process and forces
Hugging Face/Transformers offline mode (`HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`). It refuses to start unless
`torch`, `transformers`, `peft`, `accelerate`, and a trainable local model
directory are already available.

Ollama models under `data/ollama` are treated as runtime inference artifacts,
not trainable base weights. To fine-tune, place HF-format trainable weights
under one of these local paths:

- `data/training/finetune/base-models/<model-name>`
- `data/models/<model-name>`
- `data/huggingface/.../snapshots/<snapshot-id>`

Each trainable model directory needs `config.json`, tokenizer files, and model
weights such as `model.safetensors` or `pytorch_model.bin`.

## Optional Fine-Tune Image

Build this only on a connected prep machine:

```bash
docker compose --project-name cleverly build cleverly
docker compose --project-name cleverly -f docker-compose.yml -f docker/finetune.yml build cleverly
docker save cleverly:local cleverly:finetune -o cleverly-finetune-images.tar
```

After transferring the image archive and local model files to the offline
machine, start with the fine-tune overlay and `--pull never`.

This is intentionally a small, safe first integration inspired by practical AI
engineering and from-scratch training repos. No code from those external repos
is vendored into Cleverly.

## External Study Packs

The Training Lab lists these upstream projects as offline study material:

- [FareedKhan-dev/train-llm-from-scratch](https://github.com/FareedKhan-dev/train-llm-from-scratch)
- [Sumanth077/Hands-On-AI-Engineering](https://github.com/Sumanth077/Hands-On-AI-Engineering)
- [elementalsouls/Claude-BugHunter](https://github.com/elementalsouls/Claude-BugHunter)
- [ConardLi/easy-agent](https://github.com/ConardLi/easy-agent)

They are not downloaded, installed, or executed by Cleverly. See
[External Agent Study Packs](external-agent-study-packs.md) for the offline
intake policy, especially the authorized-use boundary for security material.

## Suggested Next Steps

Useful follow-ups are local file import from already-mounted folders, adapter
export helpers, and serving a completed adapter through a compatible local
runtime.
