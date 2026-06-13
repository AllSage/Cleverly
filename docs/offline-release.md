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
- Leaves model, embedding, Chroma, and npm caches under mounted `./data`
  directories so they can be pre-seeded.

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

To bundle an offline chat model with Ollama, pull it into `./data/ollama` on
the connected machine:

```bash
docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
OLLAMA_MODEL=llama3.2:3b docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama.yml \
  up -d --build
```

Set `OLLAMA_MODEL` to a different Ollama tag if needed. Cleverly will register
`http://ollama:11434/v1` and make the pulled model the default when no default
model is already configured.

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

Load the images:

```bash
docker load -i cleverly-offline-images.tar
```

Create writable runtime directories:

```bash
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
```

On Linux, make the bind mounts writable by the container UID/GID:

```bash
chown -R "$(id -u):$(id -g)" data logs
```

Start without pulling or building:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  up -d --no-build --pull never
```

If you transferred `cleverly:finetune`, include the fine-tune overlay:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
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

Do not run connected prep on the offline/sensitive machine. If you need the
Windows prep helper, run it only on a connected prep machine and pass the
explicit opt-in:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep
```

To build the optional fine-tune image during connected prep:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -FineTune
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
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml ps
```

Confirm the app container has no internet egress:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml exec cleverly \
  python -c "import socket; socket.create_connection(('1.1.1.1', 80), 3)"
```

That command should fail in offline mode.

## Chroma, RAG, and Embeddings

Chroma works offline only if the `docker.io/chromadb/chroma:latest` image was
loaded before startup. If the image is missing and you use `--pull never`,
Compose will refuse to start that service.

FastEmbed is disabled by default in offline mode to avoid first-boot downloads.
To enable local embeddings, pre-seed `data/cache/fastembed` and set:

```env
CLEVERLY_OFFLINE_EMBEDDINGS=1
```

Local model files should be copied into `data/huggingface` or another mounted
path before startup. Offline mode does not download models.

For the bundled Ollama path, copy `data/ollama` from the connected machine and
include `docker/ollama-offline.yml` when starting Compose. The offline Ollama
overlay does not run `ollama pull`; it only serves models that are already
present in that directory.

## Stop

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml down
```
