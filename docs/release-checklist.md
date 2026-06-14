# Cleverly Release Checklist

Use this checklist before tagging a build or moving a bundle to a sensitive
machine. Keep the generated reports with the release artifact.

## Source State

- Confirm the branch is correct and the git working tree is clean.
- Confirm the release commit has a passing **Cleverly CI** workflow.
- Confirm **Security Analysis** has run CodeQL on the release commit.
- Confirm branch protection is enabled for `main` with required status checks.
- Confirm license notices and `/licenses` upstream notices are present.
- Confirm README, SECURITY, model onboarding, and offline release docs match
  the release behavior.
- Confirm the root `LICENSE` and bundled notices have not been rewritten
  accidentally.
- Confirm [threat-model.md](threat-model.md) still matches the release.

## Hosted Pipeline

- Keep `.github/workflows/ci.yml`, `.github/workflows/security.yml`, and
  `.github/workflows/release.yml` enabled.
- Use `scripts/configure-branch-protection.ps1` from an authenticated GitHub
  admin shell to require the release-readiness checks:

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\configure-branch-protection.ps1
  ```

- For a stricter team workflow, add `-RequirePullRequest`.
- Do not put signing certificates, model files, or private release data in
  GitHub Actions logs or artifacts.

## Local Verification

Use `scripts/build-offline-release.ps1` for the full release wrapper,
`scripts/generate-sbom.ps1` for the dependency snapshot, and
`scripts/make-release.ps1` for a named release candidate folder.

- Prefer the full release wrapper when building an artifact set:

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\build-offline-release.ps1 -Model qwen3-coder:30b -RequireSignature
  ```

- Run the full Python test suite.
- Run `node --check` for changed frontend modules.
- Run any relevant UI smoke checks after frontend changes.
- Build the Docker image.
- Build the Windows app or installer when shipping Windows artifacts.
- Generate the local CycloneDX SBOM with `scripts/generate-sbom.ps1` and keep
  `dist\sbom\cleverly-sbom.json` plus `cleverly-sbom.json.sha256`.
- Run `scripts/run-static-security.ps1` and keep `static-security.json`.
- Run `scripts/write-model-integrity.ps1` or confirm the release wrapper wrote
  `model-integrity.json`.
- Run `scripts/write-release-dashboard.ps1` or confirm the release wrapper
  wrote `release-dashboard.html` and `release-dashboard.json`.

## No-Network Gates

- Run `ci/no-network-container-smoke.ps1` and keep
  `dist/no-network-container-smoke.json`.
- Run `ci/fresh-machine-offline-smoke.ps1` on the target class of computer and
  keep `dist/fresh-machine-offline-smoke.json`.
- Run `ci/fresh-machine-proof.ps1` on the target machine and keep
  `dist/fresh-machine-proof.json` plus its `.sha256` file.
- In Offline Control, run **Test No Internet** and export a local report.
- Confirm Offline Control shows zero failed checks.
- Confirm the proxy binds only to `127.0.0.1`.
- Confirm the Code Workspace worker uses `network_mode: none`.
- Confirm `CLEVERLY_ALLOW_NETWORK` is not set for normal release startup.

## Model And Data

- Choose the model from the hardware quality profile table in
  `docs/model-onboarding.md`.
- Pull models only on a connected, non-sensitive prep machine.
- Record the selected primary model in the bundle manifest and
  `model-integrity.json`.
- In Offline Control, confirm the intended model is marked **primary** or use
  **Make Primary** before exporting the bundle.
- Confirm sealed Docker data mode is the default startup path.
- Export an encrypted backup and run **Test Restore**. It must decrypt and
  recognize sections without importing data.

## Code Workspace

- Import a small repo archive and confirm path traversal is rejected.
- Confirm Safety Level starts at **Apply With Tests**.
- Confirm **Review Only** blocks Save, Apply, and Commit.
- Confirm **Commit Allowed** is required before a commit can run.
- Confirm Allowed Paths blocks writes and patches outside the configured
  repo-relative prefixes.
- Apply a manual diff with a test command and confirm validation happens before
  the permanent patch.
- Confirm a snapshot is created before a manual diff apply.
- Confirm commits warn when there is no passing local test run.
- Confirm network/package commands remain blocked.

## Windows Release

- Build the installer with `scripts/build-windows-installer.ps1`.
- For release distribution, pass `-RequireSignature`.
- Verify Authenticode signature status is valid.
- Re-check the output with `scripts/verify-windows-installer-signature.ps1
  -RequireTrusted`.
- Save checksums and the installer-generated release checklist.

## Final Packaging

- Create an annotated release tag with `scripts/create-release-tag.ps1`.
- Save Docker images with the launcher bundle or documented manual process.
- Include load, seal, start, and README helper files in the offline bundle.
- Include `release-dashboard.html`, `release-dashboard.json`,
  `model-integrity.json`, SBOM, static-security report, no-network smoke report,
  fresh-machine proof report, and checksums.
- Copy artifacts by trusted removable media only.
- On the offline target, run load, seal, start, Offline Control, and smoke
  checks before importing sensitive data.
