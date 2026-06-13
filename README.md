# Cleverly

Self-hosted AI workspace for chat, agents, documents, research, model serving,
email, calendar, notes, tasks, memory, and local tools.

![Cleverly](docs/cleverly-icon.svg)

Cleverly is a local-first interface for the kind of work people normally split
across ChatGPT, Claude, model servers, notes, documents, email, and task tools.
It runs on your hardware, with your data.

## Features

- **Chat**: local models and API providers, including Ollama, OpenAI-compatible endpoints, OpenRouter, and OpenAI.
- **Agent tools**: MCP, web, files, shell, skills, memory, and task workflows.
- **Cookbook**: hardware-aware model recommendations, downloads, and serving via vLLM, llama.cpp, and related engines.
- **Deep Research**: multi-step source gathering and synthesis into visual reports.
- **Compare**: blind side-by-side model comparison and synthesis.
- **Documents**: multi-tab editor with markdown, HTML, CSV, syntax highlighting, AI edits, and suggestions.
- **Memory / Skills**: persistent memory and reusable skills with ChromaDB and fastembed.
- **Email**: IMAP/SMTP inbox with AI triage, reminders, tags, summaries, and reply drafts.
- **Notes & Tasks**: notes, reminders, todos, scheduled tasks, ntfy/browser/email notification channels.
- **Calendar**: local-first calendar with CalDAV sync and `.ics` import/export.
- **Mobile / PWA**: responsive interface with installable app behavior.

## Quick Start

Defaults work out of the box: clone, run, then configure models/search/email
inside **Settings**. Only edit `.env` for deployment-level overrides like
`APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, or a pre-seeded admin password.

On first setup, Cleverly creates an admin account (`admin` unless
`CLEVERLY_ADMIN_USER` is set) and prints a temporary password in the terminal.
For Docker installs, the same line is in `docker compose logs cleverly`.
Use that for the first login, then change it in **Settings**.

### Docker

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
cp .env.example .env
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. Docker Compose
publishes only a localhost proxy on `127.0.0.1` by default. The Cleverly app
container itself runs on an internal-only Docker network with no internet
egress. If the port is taken, set `APP_PORT=7001` in `.env` and recreate the
container. Keep `APP_BIND=127.0.0.1` unless you are deliberately placing a
trusted reverse proxy in front of it.

The main Docker container is named `cleverly` by default, so common checks work
with commands like `docker logs cleverly`. The proxy and bundled Ollama
containers default to `cleverly-proxy` and `cleverly-ollama`.

### Offline Docker

Docker is offline-by-default. For a no-internet runtime, build or load the
images first, then start without pulling or building:

```bash
docker compose --env-file .env.example up -d --no-build --pull never
```

The default Compose stack puts the Cleverly app container on an internal-only
Docker network, so the app cannot reach the internet. A tiny no-data proxy sidecar
publishes `127.0.0.1:7000` and forwards only to the app container, so the UI
still works in your browser. Compose also sets `CLEVERLY_OFFLINE=1`, which
disables web search/fetch/deep-research defaults and skips startup network
warmups.

If you need to move this to an air-gapped machine, build and pull images on a
connected machine, then transfer them with `docker save` / `docker load`.
Pre-seed `./data/huggingface` and `./data/cache/fastembed` if you want local
models or embeddings available without downloads.

See [docs/offline-release.md](docs/offline-release.md) for the full offline
release and verification checklist.

### Docker With Bundled Ollama

To have Docker pull a local model on a connected machine, add the Ollama
overlay. The default model is `llama3.2:3b`; override `OLLAMA_MODEL` for a
different Ollama tag.

```bash
docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
OLLAMA_MODEL=llama3.2:3b docker compose -f docker-compose.yml -f docker/ollama.yml up -d --build
```

This stores Ollama models under `./data/ollama`, registers
`http://ollama:11434/v1` as a Cleverly endpoint, and sets the pulled model as
the default if no default model is already configured.

For offline use, pull the model on a connected machine first, transfer
`./data/ollama` with the Docker images, then start with:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker/ollama-offline.yml up -d --no-build --pull never
```

### Native Linux / macOS

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Use `--host 0.0.0.0` only when you intentionally want LAN or reverse-proxy access.

Requirements: Python 3.11+. Cookbook also needs `tmux` for background model
downloads and serves.

### Apple Silicon

Docker on macOS cannot use the Metal GPU. For GPU-accelerated Cookbook on an
M-series Mac, run Cleverly natively:

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
./start-macos.sh
```

It launches at `http://127.0.0.1:7860`. To build a clickable app wrapper:

```bash
./build-macos-app.sh
```

### Native Windows

```powershell
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Manual setup:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

For full Cookbook background downloads and the agent shell tool on Windows,
install [Git for Windows](https://git-scm.com/download/win) so `bash.exe` is
available.

## Docker Notes

Compose starts Cleverly, ChromaDB, SearXNG, ntfy, and the local proxy.
Cleverly and bundled services run on an internal-only Docker network by
default. Only the proxy publishes a host port, and it binds to `127.0.0.1`.

The Cleverly service runs as a non-root UID/GID, drops Linux capabilities, uses
`no-new-privileges`, mounts the application filesystem read-only, and uses tmpfs
for `/tmp`, `/run`, and `/var/tmp`. Runtime state is written only to mounted
paths under `./data` and `./logs`. The Docker entrypoint also refuses to start
with `CLEVERLY_OFFLINE` disabled unless `CLEVERLY_ALLOW_NETWORK=I_ACCEPT_NETWORK_RISK`
is explicitly set.

On Linux, make sure the bind-mounted directories are writable by the configured
`PUID`/`PGID` before first boot:

```bash
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
chown -R "$(id -u):$(id -g)" data logs
```

Cookbook downloads live in `./data/huggingface`, and Cookbook-installed Python
CLIs and serve engines live in `./data/local`, so they survive container
recreation.

To use Ollama from Docker, prefer the bundled Ollama overlay documented above.
It keeps inference traffic inside Docker's internal-only network.

Useful checks:

```bash
docker compose ps
docker compose logs --tail=120 cleverly
docker compose logs cleverly | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

## Security Notes

Cleverly is a self-hosted workspace with powerful local tools: shell access,
file uploads, model downloads, web research, email/calendar integrations, API
tokens, and webhooks. Treat it like an admin console.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Do not expose it directly to the public internet without HTTPS and a trusted reverse proxy.
- Keep `data/`, `.env`, logs, databases, and uploaded/generated media out of Git.
- Review `data/auth.json` after first boot: disable open signup unless you intentionally want it.
- Keep shell/Python/file read-write, MCP management, API tokens, webhooks, model serving, backup/vault, and app settings admin-only.
- Rotate any API keys or tokens that were ever pasted into shared chats, screenshots, demos, or logs.
- Prefer binding manual development runs to `127.0.0.1`; bind to `0.0.0.0` only when you intentionally want LAN/reverse-proxy access.

For HTTPS, put a TLS-terminating reverse proxy in front. Minimal Caddy example:

```caddy
cleverly.example.com {
  reverse_proxy localhost:7000
}
```

## Configuration

Most setup is done inside the app with `/setup` or **Settings**. Use `.env`
for deployment-level defaults and secrets you want present before first boot.

| Variable | Default | Description |
|---|---|---|
| `LLM_HOST` | `localhost` | Your LLM server |
| `LLM_HOSTS` | unset | Comma-separated list for model discovery |
| `OPENAI_API_KEY` | unset | Optional OpenAI key |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG URL |
| `SEARXNG_SECRET` | generated on first Docker boot | Optional SearXNG cookie/CSRF secret |
| `APP_BIND` | `127.0.0.1` | Docker Compose local proxy bind address |
| `APP_PORT` | `7000` | Docker Compose host port |
| `CLEVERLY_CONTAINER_NAME` | `cleverly` | Main Cleverly Docker container name |
| `CLEVERLY_PROXY_CONTAINER_NAME` | `cleverly-proxy` | Local proxy Docker container name |
| `CLEVERLY_OLLAMA_CONTAINER_NAME` | `cleverly-ollama` | Bundled Ollama Docker container name |
| `PUID` / `PGID` | `1000` / `1000` | UID/GID used by the hardened Docker container |
| `CLEVERLY_TMPFS_SIZE` | `1g` | Size of the Cleverly `/tmp` tmpfs in Docker |
| `CLEVERLY_PIDS_LIMIT` | `4096` | Process limit for the Cleverly container |
| `CLEVERLY_OFFLINE` | `1` in Docker | Disable internet-facing features and startup network warmups |
| `CLEVERLY_OFFLINE_EMBEDDINGS` | `0` in Docker | Allow local FastEmbed only after its cache is pre-seeded |
| `AUTH_ENABLED` | `true` | Enable/disable login |
| `LOCALHOST_BYPASS` | `false` | Development-only auth bypass for direct loopback requests |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string |
| `CHROMADB_HOST` | `localhost` | ChromaDB host |
| `CHROMADB_PORT` | `8100` | ChromaDB port for manual host runs |
| `EMBEDDING_URL` | unset | OpenAI-compatible embeddings endpoint |

## Built-In MCP Servers

Cleverly auto-registers a few built-in MCP servers at startup. The npx-based
ones only start when their npm package is already in the local npx cache. To
enable the browser MCP server:

```bash
npx -y @playwright/mcp@latest --version
```

Restart Cleverly after the package is installed.

## Architecture

```text
app.py      FastAPI entry point
core/       auth, database, middleware, constants
src/        llm_core, agent loop, tools, chat processor, search
routes/     chat, session, document, memory, model, email, calendar endpoints
services/   docs, memory, search, hwfit
static/     frontend HTML, CSS, and JS modules
docs/       landing page and preview media
```

## Data

All user data lives in `data/` and is gitignored: `app.db`, `memory.json`,
`presets.json`, uploads, personal docs, ChromaDB data, and settings.

## License

Cleverly is source-available under the [Cleverly Product License](LICENSE).
Original upstream and third-party notices are preserved in
[licenses/](licenses/) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
