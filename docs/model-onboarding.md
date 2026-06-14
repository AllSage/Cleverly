# Model Onboarding

Use this guide on the connected prep machine. Do not run model pulls on the
sensitive/offline target machine.

The first-run Setup wizard and Offline Control use the same local endpoint:

```text
http://ollama:11434/v1
```

## Hardware-Based Pull Profiles

These tags were checked against the Ollama library on 2026-06-13. Cleverly uses
the same table when connected prep or bundle runs without `-Model`.

| GPU memory | Profile | Ollama tag | Size shown by Ollama | Good fit |
|---:|---|---:|---:|---|
| CPU-only / 0-3GB | CPU Safe | `llama3.2:3b` | 2.0GB | Fast first boot, simple private chat, notes, light code review |
| 4-7GB | Low VRAM | `qwen3:4b` | 2.5GB | Better local reasoning with small VRAM |
| 8-11GB | Balanced | `qwen3:8b` | 5.2GB | General chat, summaries, and modest code work |
| 12-15GB | Stronger | `qwen3:14b` | 9.3GB | Stronger local reasoning and coding |
| 16-23GB | Reasoning | `gpt-oss:20b` | 14GB | Open-weight local reasoning and agent workflows |
| 24-79GB | Code | `qwen3-coder:30b` | 19GB | Best default for local repo editing and Code Workspace on a 24GB GPU |
| 80GB+ | Max | `gpt-oss:120b` | 65GB | Large local reasoning model on workstation/server-class GPU memory |

Sources:

- https://ollama.com/library/llama3.2
- https://ollama.com/library/qwen3
- https://ollama.com/library/qwen3-coder
- https://ollama.com/library/gpt-oss

## Easiest Connected Setup

For a first run on a connected, non-sensitive prep machine, use:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep
```

That command builds/pulls the Docker images, chooses a model from the hardware
table, pulls it into local Ollama storage, seals prepared data into Docker
volumes, and starts Cleverly. Normal `start` after that remains offline-only.

Force a hardware tier or exact model when needed:

```powershell
.\Cleverly.ps1 setup -AllowConnectedPrep -GpuGB 24
.\Cleverly.ps1 setup -AllowConnectedPrep -Model qwen3-coder:30b
```

## Advanced Prep Commands

Run auto prep when you want Cleverly to choose based on detected GPU memory:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep
```

For a 24GB GPU profile without relying on auto-detection:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -GpuGB 24
```

To override the hardware profile with an exact tag:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model gpt-oss:20b
```

The launcher records the selected tag in `data/cleverly-primary-model.json`
and writes it into the offline bundle so startup does not guess.

Before shipping a release, write a model integrity manifest:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\write-model-integrity.ps1 `
  -Model qwen3-coder:30b `
  -SourceUrl https://ollama.com/library/qwen3-coder `
  -ExpectedSize 19GB `
  -ExpectedGpuGB 24
```

If you have a local model directory available, pass `-ModelPath` to hash the
files. Without `-ModelPath`, the manifest is marked `metadata-only`; this is
still useful for release review, but it is not a byte-for-byte model proof.

Offline Control can also mark a discovered or recommended model as primary with
**Make Primary**. That writes the same primary-model manifest and updates the
default local model setting.

Use **Pick Best Model** in Offline Control when you want Cleverly to choose from
the hardware profile table automatically. Use **Verify Primary** after loading
or sealing data to confirm the selected tag appears in the local model scan.

To build a portable transfer bundle instead:

```powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep
```

Force a bundle tier or exact model the same way:

```powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep -GpuGB 24
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model qwen3-coder:30b
```

Manual explicit examples:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model llama3.2:3b
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model llama3.2:3b
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model qwen3:8b
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model qwen3-coder:30b
```

Swap the `-Model` value for the tag you want. If you want to replace the
primary model later, run `prep` or `bundle` again on a connected, non-sensitive
machine with the new `-Model` value, then move the new bundle to the offline
machine.

## Offline Machine

After loading the bundle on the offline machine:

```powershell
.\Cleverly.ps1 seal-data
.\Cleverly.ps1 start
```

Then open Cleverly at:

```text
http://127.0.0.1:7000
```

Open **Setup** or **Offline** and register:

```text
Name: Local Ollama
Base URL: http://ollama:11434/v1
Model: qwen3-coder:30b
```

Use the tag recorded in the bundle if you chose another model.

## Code Workspace Model

The Code Workspace model key is blank by default. After the local model is
registered, set the Code model key explicitly in **Code**. Use the same local
model tag unless you have prepared a stronger coding model.

Do not enter a cloud model key on a sensitive machine.
