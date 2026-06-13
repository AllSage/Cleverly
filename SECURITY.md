# Security Policy

Cleverly is a self-hosted AI workspace with privileged local capabilities. Please do not run it as a public, unauthenticated service.

## Supported Versions

Security fixes are handled on the default branch until formal releases are cut.

## Deployment Guidance

- Keep `AUTH_ENABLED=true`.
- For sensitive/offline machines, follow the
  [README sensitive-machine checklist](README.md#sensitive-machine-checklist)
  before loading private data.
- Use HTTPS when exposing the app beyond localhost.
- Put the app behind a trusted reverse proxy or private network.
- Protect `.env`, `data/`, logs, uploaded files, generated media, and database files.
- Use sealed Docker volumes by default. When you have host admin rights,
  optionally add full-disk encryption or an encrypted Docker data root; see
  [docs/encrypted-docker-data-root.md](docs/encrypted-docker-data-root.md).
- Disable open signup unless you intentionally want new accounts.
- Keep demo/test users non-admin, and remove them entirely on serious deployments.
- Give admin accounts strong passwords and enable 2FA where possible.
- Leave high-risk agent tools restricted to admins: shell, Python, file read/write, email send/read, MCP, app API, task/skill/memory management, settings, tokens, and model serving.
- Rotate API keys, webhook secrets, and Cleverly API tokens if they appear in logs, screenshots, demos, or shared chats.
- Treat shell, model-serving, MCP, email, calendar, and vault features as privileged admin functionality.
- Use [docs/model-onboarding.md](docs/model-onboarding.md) to prepare local
  models on a connected, non-sensitive machine.
- Use [docs/fresh-machine-offline-test.md](docs/fresh-machine-offline-test.md)
  and [docs/security-review.md](docs/security-review.md) as release gates for
  sensitive-machine installs.
- Release Windows installers should be Authenticode-signed; see
  [docs/windows-installer.md](docs/windows-installer.md).

## Publishing A Fork

Before pushing a public fork, run:

```bash
git status --short
git check-ignore -v .env data/auth.json data/app.db logs/compound.log cleverly.db
git grep -n -I -E "(sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|AIza[0-9A-Za-z_-]{20,}|Bearer [A-Za-z0-9._~+/-]{20,})" -- . ':!static/lib/**' ':!package-lock.json'
```

Only `.env.example`, docs, source, tests, and static assets should be committed. Never commit live `data/` contents, local databases, uploaded files, generated media, logs, backups, API keys, password hashes, or personal documents.

## Reporting

Please report vulnerabilities privately via GitHub security advisories if available, or by opening a minimal issue that does not disclose exploit details.
