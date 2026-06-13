# Cleverly Security Review

Review date: 2026-06-13

Scope: Cleverly local/offline Docker runtime, sealed data mode, local model
onboarding, Code Workspace, backup/export, launcher, and offline operator flows.

Reviewer: internal project review. This is a formal internal review artifact,
not an independent third-party penetration test.

## Security Objective

Cleverly is intended to run as a private local AI workspace. The hardened target
profile is:

- UI reachable only through `127.0.0.1`.
- App and support services on Docker internal networks.
- Offline mode enabled by default.
- Runtime data in Docker named volumes by default.
- Code Workspace commands isolated in a networkless sidecar.
- Cloud/API/network features hidden or disabled while offline.
- No internet egress from the app container during sensitive operation.

## Threat Model

Primary assets:

- Chat messages, memories, notes, documents, uploaded files, imported repos.
- Model endpoint configuration and API keys.
- Email/calendar credentials when those features are intentionally enabled.
- Backups and exported workspace archives.
- Local model files and training datasets.

Primary adversaries:

- Remote web attackers reaching an exposed deployment.
- Malicious content pasted/uploaded/imported into Cleverly.
- Compromised model output attempting prompt/tool injection.
- Accidental operator misconfiguration.
- Local users with Docker or filesystem access.

Out of scope:

- A host administrator or anyone with full Docker Desktop admin access.
- Physical compromise of the computer.
- Compromised base operating system, GPU driver, Docker daemon, or model binary.
- Formal cryptographic certification.

## Controls Reviewed

Authentication and admin gating:

- Auth is enabled by default.
- Admin-only routes guard high-risk actions with `require_admin`.
- First-run admin setup is isolated to the login/setup flow.
- API tokens, model endpoint management, backups, shell/tooling, and offline
  control are admin-only.

Offline enforcement:

- Docker startup defaults to `CLEVERLY_OFFLINE=1`.
- The entrypoint refuses startup unless offline mode is enabled or the explicit
  break-glass token is set.
- The app-level offline policy checks offline mode, loopback bind, online
  feature flags, local-only model endpoints, data mode, and Code Workspace
  worker isolation.
- Offline Control exposes an operator-visible status and egress proof check.

Network isolation:

- The UI is published through a local proxy bound to `127.0.0.1`.
- The app container runs on an internal-only Docker network.
- Code Workspace command execution runs in `cleverly-code-worker` with
  `network_mode: none`.
- Startup and smoke scripts use `--pull never` and `--no-build` in offline
  mode.

Data handling:

- Sealed mode uses Docker named volumes by default.
- Optional host-visible data mode is explicit.
- Encrypted app backup export/import uses PBKDF2-SHA256 plus Fernet.
- Secret columns use application-level Fernet encryption at rest.
- License and upstream notices are preserved.

Code Workspace:

- Archive imports reject path traversal, symlinks, `.git` internals, and large
  expansion.
- Workspace commands block obvious network and package-install commands.
- Snapshots and diff review are available before applying model-generated code.

Packaging:

- Windows launcher starts the offline Docker path by default.
- Installer build path supports Authenticode signing and can require a valid
  signature before producing a release artifact.
- The installer runs per-user and does not require Administrator rights.

## Residual Risks

- Docker named volumes are not encryption. A host admin or Docker admin can
  inspect them.
- Browser access to `127.0.0.1` still exposes the UI to any local browser user
  with valid credentials.
- Local models are executable software stacks; model server images and model
  runtimes must be trusted before transfer to a sensitive machine.
- Code Workspace can still execute local commands. The networkless worker
  reduces egress risk but does not make untrusted code safe.
- API/cloud features can be re-enabled only by explicit configuration changes,
  but a break-glass network configuration changes the threat model.
- This review did not fuzz every parser or perform third-party penetration
  testing.

## Required Release Gates

Before declaring a sensitive-machine release ready:

- Full test suite passes.
- `node --check` passes for changed frontend modules.
- Docker image builds successfully.
- Fresh-machine offline smoke test passes on the target class of computer.
- Offline Control reports zero failed checks.
- Egress proof reports outbound TCP blocked.
- Windows installer is Authenticode-signed for release distribution.
- README sensitive-machine checklist is followed.

## Recommended External Review

Before using Cleverly for regulated, classified, attorney-client, medical, or
financial records, commission an independent review covering:

- Docker/host hardening.
- Auth/session security.
- File upload/archive parsing.
- Code Workspace worker isolation.
- Backup cryptography and key handling.
- Windows installer signing and supply-chain controls.
