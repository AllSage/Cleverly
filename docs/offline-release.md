# Cleverly Offline Release Runbook

This runbook packages Cleverly for a machine that must run Docker with no
internet access. The offline runtime uses `docker/offline.yml`, which places the
app and bundled services on an internal Docker network and exposes only a local
proxy on `127.0.0.1:${APP_PORT:-7000}`.

## What Offline Mode Changes

- Sets `CLEVERLY_OFFLINE=1`.
- Disables web search, web fetch, and deep research by default.
- Clears internet-facing model/search endpoint defaults.
- Skips startup network warmups.
- Runs the Cleverly app container without internet egress.
- Publishes the UI through the `cleverly_proxy` sidecar.
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
docker compose --env-file .env -f docker-compose.yml -f docker/offline.yml build cleverly
docker compose --env-file .env -f docker-compose.yml -f docker/offline.yml pull chromadb searxng ntfy
```

To bundle an offline chat model with Ollama, pull it into `./data/ollama` on
the connected machine:

```bash
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
  docker.io/ollama/ollama:latest \
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
  -f docker/offline.yml \
  -f docker/ollama-offline.yml \
  up -d --no-build --pull never
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
docker compose --env-file .env -f docker-compose.yml -f docker/offline.yml ps
```

Confirm the app container has no internet egress:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/offline.yml exec cleverly \
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
include `docker/ollama-offline.yml` when starting Compose. The offline overlay
does not run `ollama pull`; it only serves models that are already present in
that directory.

## Stop

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/offline.yml down
```
