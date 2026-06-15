# Dependency Audit

Cleverly keeps two dependency files:

- `requirements.txt`: portable Python install input.
- `requirements.lock`: audit snapshot generated from the current development
  virtual environment with `python -m pip freeze --all`.

The lock file is intentionally not the only install path because some packages
resolve to platform-specific wheels. Use it to compare a prepared offline image
against the reviewed environment.

## Generate The Local SBOM

Before shipping a release, generate a local dependency snapshot:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\generate-sbom.ps1
```

This writes `dist\sbom\cleverly-sbom.json` and
`dist\sbom\cleverly-sbom.json.sha256`. The JSON is CycloneDX-compatible so the
GitHub release workflow can attach an SBOM attestation. It does not contact the
internet; it hashes reviewed release files, reads `package-lock.json`, runs
`pip freeze --all`, and inspects already-built Docker images when present.

## Refresh The Lock Snapshot

On a connected prep machine after installing dependencies:

```powershell
.\venv\Scripts\python.exe -m pip freeze --all > requirements.lock
```

Review the diff before committing. Unexpected new packages should have a clear
reason in the PR or commit notes.

## Offline Wheelhouse

For stricter air-gap prep, build a wheelhouse on a connected machine:

```powershell
python -m pip download -r requirements.txt -d dist\wheelhouse
python -m pip install --no-index --find-links dist\wheelhouse -r requirements.txt
```

Copy `dist\wheelhouse` with the Docker image bundle. Do not let the offline
machine run `pip install` against the internet.

## Audit Commands

When a connected audit machine is available:

```powershell
python -m pip install pip-audit
python -m pip_audit -r requirements.txt
npm audit --package-lock-only
```

Do not run those commands on the sensitive offline runtime. They contact public
advisory services unless their databases are already mirrored locally.

## Docker Image Review

Before exporting an offline image, record:

```powershell
docker image inspect cleverly:local
docker run --rm cleverly:local python -m pip freeze --all
```

Compare the container freeze output to `requirements.lock` and document any
intentional platform differences.
