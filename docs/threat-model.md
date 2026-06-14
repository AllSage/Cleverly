# Cleverly Threat Model

This document defines the security boundary Cleverly is designed for. It is a
release artifact, not a claim of formal certification.

## Protected Assets

- Chat history, memories, notes, tasks, calendars, documents, uploads, and
  imported repos.
- Local model endpoint configuration and API keys.
- Email/calendar credentials when those features are intentionally enabled.
- Local model files, model manifests, training datasets, and adapters.
- Backups, exported workspace archives, release manifests, SBOMs, and proof
  reports.

## Intended Deployment

The hardened deployment is a single-user or trusted-small-team local machine:

- Docker offline mode is enabled.
- The UI is bound to `127.0.0.1`.
- App services run on Docker internal networks.
- Code Workspace commands run in the `cleverly-code-worker` sidecar with
  `network_mode: none`.
- Runtime data is stored in sealed Docker named volumes by default.
- Model pulls happen only on a connected, non-sensitive prep machine.

## In-Scope Threats

- Accidental cloud/API model use on a sensitive machine.
- Accidental Docker/image/model pulls during offline startup.
- Web UI exposure beyond loopback caused by deployment drift.
- Prompt/tool injection attempts from pasted, uploaded, or imported content.
- Model-generated diffs trying to write outside an allowed repo path.
- Workspace commands trying obvious network/package/host operations.
- Missing release evidence, missing SBOM, missing smoke report, or stale docs.
- Unsigned or unverifiable release artifacts.

## Out-Of-Scope Threats

- A host administrator, Docker administrator, or attacker with Docker Desktop
  control.
- Physical compromise of the computer or removable media.
- Compromised operating system, GPU driver, Docker daemon, base image, model
  runtime, or model binary.
- Side channels from CPU/GPU/RAM/storage hardware.
- Formal cryptographic certification, classified-system accreditation, or
  independent penetration-test coverage.

## Security Controls

- Offline startup fails closed unless `CLEVERLY_OFFLINE=1` or the explicit
  network break-glass token is set.
- Compose defaults publish only the local proxy on `127.0.0.1`.
- Support services and Ollama overlays use `pull_policy: never`.
- The Code Workspace worker uses `network_mode: none`.
- Code Workspace supports Review Only, Apply With Tests, Commit Allowed, and
  Allowed Paths.
- Offline Control exposes local readiness, storage, audit, model, lifecycle,
  report, and egress checks.
- Release wrappers generate SBOM, static-security, model-integrity,
  no-network-smoke, release-dashboard, manifest, and checksum evidence.
- GitHub Actions run test, syntax, Compose, static-security, no-network smoke,
  CodeQL, dependency review, release artifact, and attestation workflows.

## Required Operator Proof

Before loading sensitive data on a target computer:

1. Build the release on a connected, non-sensitive machine.
2. Keep `release-manifest.json`, `checksums.sha256`, `release-dashboard.html`,
   `cleverly-sbom.json`, `static-security.json`, and
   `model-integrity.json`.
3. Move the bundle by trusted removable media.
4. Run `load-cleverly.cmd`, `seal-data.cmd`, and `start-cleverly.cmd`.
5. Run Offline Control **Test No Internet**.
6. Run `ci\fresh-machine-proof.ps1` and keep the JSON plus `.sha256`.
7. Confirm there are no failed checks before importing private data.

## Residual Risk

Sealed Docker volumes are not encryption. They reduce accidental host-folder
exposure but do not protect against a host/Docker administrator. For stronger
at-rest protection, use full-disk encryption or the optional encrypted Docker
data root when the target computer allows Administrator rights.
