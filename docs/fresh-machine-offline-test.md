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

## What It Checks

- Docker is available.
- Required local Docker images are loaded.
- `Cleverly.ps1 doctor` passes.
- The stack starts without pulls or builds.
- `http://127.0.0.1:7000/api/health` returns 200.
- The local proxy is bound to `127.0.0.1`.
- The Code Workspace worker has Docker `network_mode` set to `none`.
- The app container cannot open outbound TCP to `1.1.1.1:80`.

Any failed check means the machine is not ready for sensitive data.
