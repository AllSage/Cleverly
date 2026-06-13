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
```

## Build A Signed Release Installer

Install Inno Setup 6 and the Windows SDK so `iscc.exe` and `signtool.exe` are
available on PATH, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 `
  -Version 1.0.0 `
  -CertificatePath .\certs\cleverly-release.pfx `
  -RequireSignature
```

`-RequireSignature` refuses to produce a release artifact unless signing
succeeds and `Get-AuthenticodeSignature` reports `Valid`.

Do not store the `.pfx` certificate or password in this repository. Keep the
certificate on the signing workstation or in a secure signing service.

## Install Behavior

- Installs under `%LOCALAPPDATA%\Programs\Cleverly`.
- Does not require Administrator rights.
- Adds Start Menu and optional Desktop shortcuts.
- Launches `Cleverly-App.cmd`, which opens the local Windows app shell.
- The installed app still requires Docker Desktop access to run containers.
