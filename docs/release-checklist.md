# Cleverly Release Checklist

Use this checklist before tagging a build or moving a bundle to a sensitive
machine. Keep the generated reports with the release artifact.

## Source State

- Confirm the branch is correct and the git working tree is clean.
- Confirm license notices and `/licenses` upstream notices are present.
- Confirm README, SECURITY, model onboarding, and offline release docs match
  the release behavior.
- Confirm the root `LICENSE` and bundled notices have not been rewritten
  accidentally.

## Local Verification

- Run the full Python test suite.
- Run `node --check` for changed frontend modules.
- Run any relevant UI smoke checks after frontend changes.
- Build the Docker image.
- Build the Windows app or installer when shipping Windows artifacts.

## No-Network Gates

- Run `ci/no-network-container-smoke.ps1` and keep
  `dist/no-network-container-smoke.json`.
- Run `ci/fresh-machine-offline-smoke.ps1` on the target class of computer and
  keep `dist/fresh-machine-offline-smoke.json`.
- In Offline Control, run **Test No Internet** and export a local report.
- Confirm Offline Control shows zero failed checks.
- Confirm the proxy binds only to `127.0.0.1`.
- Confirm the Code Workspace worker uses `network_mode: none`.
- Confirm `CLEVERLY_ALLOW_NETWORK` is not set for normal release startup.

## Model And Data

- Choose the model from the hardware quality profile table in
  `docs/model-onboarding.md`.
- Pull models only on a connected, non-sensitive prep machine.
- Record the selected primary model in the bundle manifest.
- Confirm sealed Docker data mode is the default startup path.
- Export an encrypted backup and test an import on a throwaway Cleverly
  instance before trusting the release backup workflow.

## Code Workspace

- Import a small repo archive and confirm path traversal is rejected.
- Apply a manual diff with a test command and confirm validation happens before
  the permanent patch.
- Confirm a snapshot is created before a manual diff apply.
- Confirm commits warn when there is no passing local test run.
- Confirm network/package commands remain blocked.

## Windows Release

- Build the installer with `scripts/build-windows-installer.ps1`.
- For release distribution, pass `-RequireSignature`.
- Verify Authenticode signature status is valid.
- Save checksums and the installer-generated release checklist.

## Final Packaging

- Save Docker images with the launcher bundle or documented manual process.
- Include load, seal, start, and README helper files in the offline bundle.
- Copy artifacts by trusted removable media only.
- On the offline target, run load, seal, start, Offline Control, and smoke
  checks before importing sensitive data.
