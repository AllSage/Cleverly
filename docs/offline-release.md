# Cleverly Offline Release Runbook

This runbook packages Cleverly for a machine that must run Docker with no
internet access. Docker Compose is offline-by-default: the app and bundled
services run on an internal Docker network, and only a local proxy is published
on `127.0.0.1:${APP_PORT:-7000}`.

## What Offline Mode Changes

- Sets `CLEVERLY_OFFLINE=1` in base Compose.
- Disables web search, web fetch, and deep research by default.
- Clears internet-facing model/search endpoint defaults.
- Skips startup network warmups.
- Runs the Cleverly app container without internet egress.
- Publishes the UI through the `cleverly_proxy` sidecar.
- Refuses Docker startup with `CLEVERLY_OFFLINE` disabled unless
  `CLEVERLY_ALLOW_NETWORK=I_ACCEPT_NETWORK_RISK` is explicitly set.
- Keeps the Training Lab local-only; datasets and artifacts stay under
  `./data/training`.
- Enables Advanced LoRA fine-tuning only when optional training dependencies
  and trainable local model weights were pre-baked on a connected prep machine.
- Lists external agent/security study packs as reference metadata only; Cleverly
  does not clone, install, or run them during offline runtime.
- Stores runtime app data, logs, caches, and bundled Ollama models in Docker
  named volumes by default.
- Supports explicit host-folder bind mounts only with `-HostData`,
  `CLEVERLY_HOST_DATA=1`, or `docker/host-data.yml`.
- Treats Docker-volume sealing as storage isolation, not encryption. Optional
  [encrypted Docker data root](encrypted-docker-data-root.md) hardening can add
  at-rest protection when the operator has host admin rights.

## Connected Build Machine

Use a machine with internet access to build the Cleverly image and pull the
service images:

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
cp .env.example .env
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
docker compose --env-file .env build cleverly
docker compose --env-file .env pull chromadb searxng ntfy
```

On Windows, the easiest connected-prep path is the bundle command:

```powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model qwen2.5:7b -FineTune
```

It builds the local images, pulls support service images, pulls the configured
Ollama model, copies prepared model caches, saves Docker images to
`cleverly-images.tar`, and writes load, seal, and start helper scripts under
`dist\cleverly-offline-bundle`.

To bundle an offline chat model with Ollama, pull it into `./data/ollama` on
the connected machine:

```bash
docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
OLLAMA_MODEL=qwen2.5:7b docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama.yml \
  up -d --build
```

Set `OLLAMA_MODEL` to the exact tag you want as the primary offline model.
Cleverly will register `http://ollama:11434/v1` and make the selected model the
default when no default model is already configured.

Save the images to a portable archive:

```bash
docker save \
  cleverly:local \
  cleverly-ollama:local \
  docker.io/chromadb/chroma:latest \
  docker.io/searxng/searxng:latest \
  docker.io/binwiederhier/ntfy:latest \
  -o cleverly-offline-images.tar
```

Copy these items to the offline machine:

- `cleverly-offline-images.tar`
- the Cleverly repository checkout
- `.env`
- `data/ollama` if using the bundled Ollama model
- any pre-seeded `data/huggingface`, `data/cache/fastembed`, `data/local`, or
  `data/npm-cache` contents you need

For Advanced LoRA fine-tuning, build the optional dependency image on the
connected prep machine and include it in your transfer:

```bash
docker compose --project-name cleverly -f docker-compose.yml -f docker/finetune.yml build cleverly
docker save cleverly:local cleverly:finetune -o cleverly-finetune-images.tar
```

Also copy HF-format trainable model directories into
`data/training/finetune/base-models`, `data/models`, or `data/huggingface`.
Ollama runtime files in `data/ollama` are usable for inference but are not
trainable base weights.

## Offline Machine

If you used `.\Cleverly.ps1 bundle`, copy `dist\cleverly-offline-bundle` to the
offline machine, then run:

```text
load-cleverly.cmd
seal-data.cmd
start-cleverly.cmd
```

The rest of this section shows the manual equivalent.

Load the images:

```bash
docker load -i cleverly-offline-images.tar
```

If you intentionally want visible host-folder storage instead of sealed Docker
volumes, create writable runtime directories and use the host-data overlay:

```bash
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
```

On Linux, make those bind mounts writable by the container UID/GID:

```bash
chown -R "$(id -u):$(id -g)" data logs
```

Start without pulling or building:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  -f docker/sealed-data.yml \
  up -d --no-build --pull never
```

If you transferred `cleverly:finetune`, include the fine-tune overlay:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  -f docker/sealed-data.yml \
  -f docker/finetune.yml \
  up -d --no-build --pull never
```

On Windows, the launcher uses that same offline start path:

```powershell
.\Cleverly.ps1 start
```

For the optional fine-tune image on Windows:

```powershell
.\Cleverly.ps1 start -FineTune
```

If you copied prepared `data/` or `logs/` folders to the offline machine, run
this once after loading images and before starting:

```powershell
.\Cleverly.ps1 seal-data -FineTune
```

Use `-HostData` only when you intentionally want the old visible-folder bind
mounts:

```powershell
.\Cleverly.ps1 start -FineTune -HostData
```

Do not run connected prep on the offline/sensitive machine. If you need the
Windows prep helper, run it only on a connected prep machine and pass the
explicit opt-in:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model qwen2.5:7b
```

To build the optional fine-tune image during connected prep:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -Model qwen2.5:7b -FineTune
```

Open:

```text
http://127.0.0.1:7000
```

## Verify

Check health through the local proxy:

```bash
curl http://127.0.0.1:7000/api/health
```

Check the Compose state:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml -f docker/sealed-data.yml ps
```

Confirm the app container has no internet egress:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml -f docker/sealed-data.yml exec cleverly \
  python -c "import socket; socket.create_connection(('1.1.1.1', 80), 3)"
```

That command should fail in offline mode.

On Windows, the bundled doctor command runs the same practical checks:

```powershell
.\Cleverly.ps1 doctor -FineTune
```

## Chroma, RAG, and Embeddings

Chroma works offline only if the `docker.io/chromadb/chroma:latest` image was
loaded before startup. If the image is missing and you use `--pull never`,
Compose will refuse to start that service.

FastEmbed is disabled by default in offline mode to avoid first-boot downloads.
To enable local embeddings, pre-seed `data/cache/fastembed`, run
`.\Cleverly.ps1 seal-data`, and set:

```env
CLEVERLY_OFFLINE_EMBEDDINGS=1
```

Local model files should be copied into `data/huggingface` before running
`seal-data`, or loaded into the matching Docker volume another way before
startup. Offline mode does not download models.

For the bundled Ollama path, copy `data/ollama` from the connected machine and
run `seal-data` before starting Compose with `docker/sealed-data.yml`. The
offline Ollama overlay does not run `ollama pull`; it only serves models that
are already present in the sealed Docker volume.

## Stop

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml -f docker/sealed-data.yml down
```
