# Air-Gap Operator Checklist

Use this checklist on the machine that will run Cleverly with sensitive data.

## Green Checks

Run the app checklist from **Code -> Checks**, open
`http://127.0.0.1:7000/operator`, or call:

```powershell
Invoke-RestMethod http://127.0.0.1:7000/api/operator/checks
```

Expected results:

- `Offline mode`: `ok`
- `Online feature flags`: `ok`
- `Configured model endpoints`: `ok`
- `Code Workspace worker isolation`: `ok` with `runner=worker`
- `Code Workspace model key`: `ok` after you set the local model key
- `Proxy bind`: `ok` with `APP_BIND=127.0.0.1`

## Docker Runtime

Start with the sealed offline stack:

```powershell
.\Cleverly.ps1 start -FineTune
```

Then verify:

```powershell
docker ps --filter "name=cleverly"
docker inspect cleverly-code-worker --format "{{.HostConfig.NetworkMode}}"
```

The worker should report `none`. That worker is where Code Workspace test/build
commands run. It communicates through the sealed Docker data volume, not a
network socket.

## Leak Smoke Test

With the stack running:

```powershell
powershell -NoLogo -NoProfile -File .\ci\smoke-offline-leaks.ps1
```

The script checks that dangerous fetch/install commands are denied and that the
worker cannot connect to `example.com:443`.

## Red Conditions

Do not load sensitive data if any of these are true:

- `CLEVERLY_OFFLINE` is disabled.
- `CODE_WORKSPACE_RUNNER` is not `worker` in Docker.
- `cleverly-code-worker` has any network mode other than `none`.
- The app proxy is bound to a LAN/public address.
- Any enabled model endpoint points to a cloud/API/LAN URL instead of loopback or a Docker service name.
- You intentionally enabled `-HostData` and the host folder is readable by other users.

## Data Boundary

Sealed Docker volumes keep app data out of the project folder by default, but
they are not encryption. A host administrator or anyone with Docker access can
inspect Docker volumes. Use full-disk encryption or the optional encrypted
Docker data root when the host policy allows it.
