# Model Onboarding

Use this guide on the connected prep machine. Do not run model pulls on the
sensitive/offline target machine.

The first-run Setup wizard and Offline Control use the same local endpoint:

```text
http://ollama:11434/v1
```

## Recommended Starter Models

These tags were checked against the Ollama library on 2026-06-13.

| Use | Ollama tag | Size shown by Ollama | Good fit |
|---|---:|---:|---|
| Baseline offline chat | `llama3.2:3b` | 2.0GB | Fast first boot, simple private chat, notes, light code review |
| Balanced chat and coding | `qwen2.5:7b` | 4.7GB | Better reasoning, coding, structured answers |
| Local vision option | `gemma3:4b` | 3.3GB | Text plus image workflows |

Sources:

- https://ollama.com/library/llama3.2
- https://ollama.com/library/qwen2.5
- https://ollama.com/library/gemma3

## Connected Prep Commands

Baseline:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model llama3.2:3b
```

Balanced:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model qwen2.5:7b
```

Vision:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model gemma3:4b
```

To build a portable transfer bundle instead:

```powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model llama3.2:3b
```

Swap the `-Model` value for the tag you want.

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
Model: llama3.2:3b
```

Use the tag you actually pulled if you chose another model.

## Code Workspace Model

The Code Workspace model key is blank by default. After the local model is
registered, set the Code model key explicitly in **Code**. Use the same local
model tag unless you have prepared a stronger coding model.

Do not enter a cloud model key on a sensitive machine.
