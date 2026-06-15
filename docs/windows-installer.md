# Windows Installer And Signing

Cleverly can be packaged as a per-user Windows installer with Inno Setup. A
release installer should be Authenticode-signed on a dedicated connected signing
workstation.

## Build Unsigned For Local Testing

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1
```

This writes:

```text
dist\installer\CleverlySetup-1.0.0.exe
dist\installer\CleverlySetup-1.0.0.release-checklist.md
```

## Build A Signed Release Installer

Install Inno Setup 6 and the Windows SDK so `iscc.exe` and `signtool.exe` are
available on PATH, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 `
  -Version 1.0.0 `
  -CertificatePath .\certs\cleverly-release.pfx `
  -ReleaseChecklistPath .\dist\installer\CleverlySetup-1.0.0.release-checklist.md `
  -RequireSignature
```

`-RequireSignature` refuses to produce a release artifact unless signing
succeeds and `Get-AuthenticodeSignature` reports `Valid`.

## Create A Local Test Signing Key

For local signing workflow validation, create a self-signed code-signing
certificate and PFX:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\new-self-signed-code-signing-cert.ps1
```

This writes ignored local signing material under `dist\signing`. Use it with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 `
  -Version 1.0.0 `
  -CertificatePath .\dist\signing\cleverly-local-test-codesign.pfx `
  -CertificatePasswordPath .\dist\signing\cleverly-local-test-codesign.password.txt `
  -RequireSignature
```

Self-signed certificates are not public trust certificates. They are acceptable
for proving the signing flow on your own machine, not for public distribution.

After the build, verify the installer again:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-windows-installer-signature.ps1 `
  -Path .\dist\installer\CleverlySetup-1.0.0.exe `
  -RequireTrusted
```

The build script writes a release checklist next to the installer by default.
Keep that checklist with the release artifact after completing the offline smoke
test, Offline Control no-internet proof, and report export.

Do not store the `.pfx` certificate or password in this repository. The default
local test outputs are under ignored `dist\signing`; keep real release
certificates on the signing workstation or in a secure signing service.

## Install Behavior

- Installs under `%LOCALAPPDATA%\Programs\Cleverly`.
- Does not require Administrator rights.
- Adds Start Menu and optional Desktop shortcuts.
- Launches `Cleverly-App.cmd`, which opens the local Windows app shell.
- The app shell includes Start, Stop, Restart, Status, Doctor, Logs, Setup,
  Open Bundle, Open Logs, Checklist, Offline Smoke, README, Make Release, Fresh
  Proof, Security Scan, Release Folder, SBOM, and Proofs actions.
- The installed app still requires Docker Desktop access to run containers.
