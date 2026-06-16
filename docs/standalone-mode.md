# Standalone Mode

Cleverly standalone mode runs the app natively on Windows without Docker. It is
the easiest no-Docker path, but it is not equivalent to Docker sealed mode.

Use standalone mode for demos, normal local work, and computers where Docker
Desktop is not available. Use Docker sealed mode for sensitive machines where
you need stronger container isolation.

## Safety Boundary

Standalone mode sets these defaults before startup:

- `CLEVERLY_OFFLINE=1`
- `APP_BIND=127.0.0.1`
- `AUTH_ENABLED=true`
- `LOCALHOST_BYPASS=false`
- `CODE_WORKSPACE_RUNNER=in-process`

That gives Cleverly app-level offline behavior: online features are hidden,
cloud/API endpoints are refused by the offline policy, and the web app binds to
loopback only.

Standalone mode does not provide Docker network isolation, a read-only container
filesystem, Linux capability drops, Docker named volumes, or the networkless
Code Workspace worker sidecar. Without Docker or administrator-managed firewall
rules, the host operating system remains the actual security boundary.

## First Setup

Run this only on a connected, non-sensitive prep machine because it may install
Python packages from package indexes:

```powershell
.\Cleverly-Standalone.ps1 setup -AllowConnectedPrep
```

Then start without Docker:

```powershell
.\Cleverly-Standalone.ps1 start
```

Or double-click:

```text
Cleverly-Standalone.cmd
```

## Check The Local Policy

```powershell
.\Cleverly-Standalone.ps1 doctor
```

The doctor reports the standalone environment and runs the app offline-policy
check. Policy failures block startup in strict offline mode.

## Network Break Glass

Standalone mode does not enable network access by default. To intentionally
disable the app-level offline policy for a non-sensitive environment:

```powershell
.\Cleverly-Standalone.ps1 start -AllowNetwork
```

This sets `CLEVERLY_ALLOW_NETWORK=I_ACCEPT_NETWORK_RISK` for that process. Do
not use it on machines that must not leak data.
