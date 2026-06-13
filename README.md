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
- **Training Lab**: offline-only starter text training with local datasets and saved model artifacts.
- **Deep Research**: multi-step source gathering and synthesis into visual reports.
- **Compare**: blind side-by-side model comparison and synthesis.
- **Documents**: multi-tab editor with markdown, HTML, CSV, syntax highlighting, AI edits, and suggestions.
- **Memory / Skills**: persistent memory and reusable skills with ChromaDB and fastembed.
- **Email**: IMAP/SMTP inbox with AI triage, reminders, tags, summaries, and reply drafts.
- **Notes & Tasks**: notes, reminders, todos, scheduled tasks, ntfy/browser/email notification channels.
- **Calendar**: local-first calendar with CalDAV sync and `.ics` import/export.
- **Mobile / PWA**: responsive interface with installable app behavior.

## Start Here

Pick the path that matches your machine.

### Windows Offline App

Use this after the Docker images and local model have already been prepared:

```powershell
.\Cleverly.ps1 start -FineTune
```

If you did not build the optional fine-tune image, use:

```powershell
.\Cleverly.ps1 start
```

The launcher uses sealed Docker named volumes by default. If you are migrating
prepared files from `data/` and `logs/`, stop Cleverly and copy them into the
sealed volumes once:

```powershell
.\Cleverly.ps1 seal-data -FineTune
```

Open:

```text
http://127.0.0.1:7000
```

Common commands:

```powershell
.\Cleverly.ps1 start
.\Cleverly.ps1 start -FineTune
.\Cleverly.ps1 seal-data -FineTune
.\Cleverly.ps1 stop
.\Cleverly.ps1 status
.\Cleverly.ps1 doctor -FineTune
.\Cleverly.ps1 logs
```

Double-clicking `Cleverly.cmd` also starts the offline app and opens the
browser. The Windows launcher does not pull, build, or download during normal
start.

If images or models are missing, run prep on a connected, non-sensitive machine:

```powershell
.\Cleverly.ps1 prep -AllowConnectedPrep -FineTune
```

Then move the prepared images/data to the offline machine and start again.

To make that transfer easier, build a portable offline bundle:

```powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep -FineTune
```

It writes `dist\cleverly-offline-bundle`. Copy that folder to the offline
machine, then run `load-cleverly.cmd`, `seal-data.cmd`, and
`start-cleverly.cmd` to launch.

Use `-HostData` only when you intentionally want Docker to write runtime state
to visible `./data` and `./logs` folders:

```powershell
.\Cleverly.ps1 start -FineTune -HostData
```

### First Login

On first boot, Cleverly creates an admin account named `admin` unless
`CLEVERLY_ADMIN_USER` is set. The temporary password is printed in the terminal.
For Docker, get it with:

```bash
docker compose logs cleverly
```

Log in, then change the password in **Settings**.

### Docker Quick Start

Use this for a normal connected development machine:

```bash
git clone https://github.com/AllSage/Cleverly.git
cd Cleverly
cp .env.example .env
docker compose up -d --build
```

Open `http://127.0.0.1:7000`.

Docker uses the Compose stack name `cleverly` and Docker named volumes for app
runtime state by default. The main containers default to:

```text
cleverly
cleverly-proxy
cleverly-ollama
```

### Offline Docker Start

Use this after images and model data have already been built or loaded:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  -f docker/sealed-data.yml \
  up -d --no-build --pull never
```

With the optional fine-tune image:

```bash
docker compose --env-file .env \
  -f docker-compose.yml \
  -f docker/ollama-offline.yml \
  -f docker/sealed-data.yml \
  -f docker/finetune.yml \
  up -d --no-build --pull never
```

The app container runs on an internal-only Docker network. Only the local proxy
binds to `127.0.0.1:7000`, so your browser can use the app while the app
container has no internet egress.

For the full air-gap checklist, use
[docs/offline-release.md](docs/offline-release.md).

To check a local install without downloading anything:

```powershell
.\Cleverly.ps1 doctor -FineTune
```

### Pull A Local Model

Run this only on a connected prep machine. The default model is
`llama3.2:3b`; set `OLLAMA_MODEL` to another Ollama tag if needed.

```bash
docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
OLLAMA_MODEL=llama3.2:3b docker compose -f docker-compose.yml -f docker/ollama.yml up -d --build
```

This stores Ollama models under `./data/ollama` for transfer. Run
`.\Cleverly.ps1 seal-data` on the offline machine after loading images to copy
that model store into the sealed Docker volume.

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

### Training Lab

The built-in [Training Lab](docs/local-training-lab.md) runs offline. It uses
pasted local text and writes datasets/artifacts under `./data/training`; it does
not download datasets or call model endpoints. Advanced LoRA fine-tuning works
only when its optional dependencies and a trainable local model directory are
already baked into the image.

External AI/security references are tracked as
[study packs](docs/external-agent-study-packs.md) only. Cleverly does not pull
or execute those repositories during offline runtime.

## Docker Notes

Compose starts Cleverly, ChromaDB, SearXNG, ntfy, and the local proxy.
Cleverly and bundled services run on an internal-only Docker network by
default. Only the proxy publishes a host port, and it binds to `127.0.0.1`.

The Cleverly service runs as a non-root UID/GID, drops Linux capabilities, uses
`no-new-privileges`, mounts the application filesystem read-only, and uses tmpfs
for `/tmp`, `/run`, and `/var/tmp`. Runtime state is written to Docker named
volumes by default. The Docker entrypoint also refuses to start with
`CLEVERLY_OFFLINE` disabled unless `CLEVERLY_ALLOW_NETWORK=I_ACCEPT_NETWORK_RISK`
is explicitly set.

Sealed Docker volumes are not encryption. A host administrator, anyone with
Docker access, or anyone with access to Docker's data root can inspect them. For
stronger at-rest protection, use full-disk encryption or an encrypted Docker
data root. On Windows, use
[docs/encrypted-docker-data-root.md](docs/encrypted-docker-data-root.md) to
check or enable BitLocker protection for the drive holding Docker Desktop's data
disk.

To use the old visible host-folder layout, add `-f docker/host-data.yml` to
manual Compose commands or pass `-HostData` to `Cleverly.ps1`. On Linux, make
sure those bind-mounted directories are writable by the configured `PUID`/`PGID`
before first boot:

```bash
mkdir -p data logs data/ssh data/cache data/huggingface data/local data/npm-cache
chown -R "$(id -u):$(id -g)" data logs
```

In sealed mode, Cookbook downloads, local package installs, npm cache, logs,
and app data live in Docker named volumes so they survive container recreation.

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
| `CLEVERLY_HOST_DATA` | unset | Set to `1` only to make `Cleverly.ps1` use visible `./data` and `./logs` bind mounts |
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

With the Docker launcher, user data lives in Docker named volumes by default.
With `-HostData` or native runs, user data lives in `data/` and is gitignored:
`app.db`, `memory.json`, `presets.json`, uploads, personal docs, ChromaDB data,
and settings.

## License

Cleverly is source-available under the [Cleverly Product License](LICENSE).
Original upstream and third-party notices are preserved in
[licenses/](licenses/) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
