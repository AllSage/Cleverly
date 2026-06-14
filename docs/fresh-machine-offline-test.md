# Fresh Machine Offline Test

Run this test on a newly prepared offline computer before putting sensitive data
into Cleverly.

## Preconditions

- Docker Desktop is installed and running.
- The Cleverly offline bundle has already been copied to this computer.
- `load-cleverly.cmd` has been run.
- `seal-data.cmd` has been run if prepared data or model files were included.
- The computer does not need internet access.

## Run

From the Cleverly folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\ci\fresh-machine-offline-smoke.ps1
```

If the optional fine-tune image is part of the bundle:

```powershell
powershell -ExecutionPolicy Bypass -File .\ci\fresh-machine-offline-smoke.ps1 -FineTune
```

The script writes:

```text
dist\fresh-machine-offline-smoke.json
```

For release proof, run the wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File .\ci\fresh-machine-proof.ps1
```

It writes `dist\fresh-machine-proof.json` and
`dist\fresh-machine-proof.json.sha256`.

## What It Checks

- Docker is available.
- Required local Docker images are loaded.
- `Cleverly.ps1 doctor` passes.
- The stack starts without pulls or builds.
- `http://127.0.0.1:7000/api/health` returns 200.
- The local proxy is bound to `127.0.0.1`.
- The Code Workspace worker has Docker `network_mode` set to `none`.
- The app and worker containers report `read_only` root filesystems.
- The app and worker containers report `no-new-privileges:true`.
- The app and worker containers drop Linux capabilities.
- The JSON report includes OS, PowerShell, and Docker runtime metadata.
- The app container cannot open outbound TCP to `1.1.1.1:80`.
- The proof wrapper confirms runtime Compose files use `pull_policy: never` and
  records a SHA-256 hash of the smoke report.

Any failed check means the machine is not ready for sensitive data.
