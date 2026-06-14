#Requires -Version 5.1
<#
  CI smoke test for Cleverly's no-network Docker runtime.

  The image build may need a connected CI runner when base layers are not
  cached. The runtime checks below start the app container with --network none
  and fail if offline-only routes, command guards, or egress blocking regress.
#>

param(
    [string]$Image = "cleverly:no-network-smoke",
    [string]$ContainerName = "cleverly-no-network-smoke",
    [string]$ReportPath = "dist/no-network-container-smoke.json",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReportFullPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $Root $ReportPath }
$Results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Name, [string]$Status, [string]$Detail) {
    $Results.Add([pscustomobject]@{
        name = $Name
        status = $Status
        detail = $Detail
    }) | Out-Null
    $color = if ($Status -eq "ok") { "Green" } elseif ($Status -eq "warn") { "Yellow" } else { "Red" }
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Save-Report {
    $summary = [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        image = $Image
        container = $ContainerName
        results = $Results
        ok = @($Results | Where-Object { $_.status -eq "ok" }).Count
        warn = @($Results | Where-Object { $_.status -eq "warn" }).Count
        fail = @($Results | Where-Object { $_.status -eq "fail" }).Count
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportFullPath) | Out-Null
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportFullPath -Encoding UTF8
    Write-Host ("Report: " + $ReportFullPath)
    return $summary
}

function Invoke-ContainerPython([string]$Code) {
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Code))
    $runner = "import base64; exec(base64.b64decode('$encoded').decode('utf-8'))"
    & docker exec $ContainerName python -c $runner
    return $LASTEXITCODE
}

function Test-ContainerRunning {
    $running = & docker inspect $ContainerName --format "{{.State.Running}}" 2>$null
    $exitCode = $LASTEXITCODE
    $state = ($running | Select-Object -First 1)
    return ($exitCode -eq 0 -and $state -eq "true")
}

Push-Location $Root
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Add-Result "docker" "fail" "docker was not found on PATH"
        $summary = Save-Report
        exit 1
    }
    Add-Result "docker" "ok" "docker is available"

    if (-not $SkipBuild) {
        & docker build --pull=false -t $Image .
        if ($LASTEXITCODE -eq 0) {
            Add-Result "image-build" "ok" "built $Image with --pull=false"
        } else {
            Add-Result "image-build" "fail" "docker build failed with exit code $LASTEXITCODE"
            $summary = Save-Report
            exit 1
        }
    } else {
        Add-Result "image-build" "warn" "skipped by -SkipBuild"
    }

    & docker rm -f $ContainerName *> $null

    $envArgs = @(
        "-e", "AUTH_ENABLED=false",
        "-e", "CLEVERLY_OFFLINE=1",
        "-e", "CLEVERLY_OFFLINE_EMBEDDINGS=0",
        "-e", "CLEVERLY_AUTO_ADD_OLLAMA=0",
        "-e", "CODE_WORKSPACE_RUNNER=worker",
        "-e", "CODE_WORKSPACE_WORKER_DIR=/app/data/code-workspaces/.worker",
        "-e", "DATA_DIR=/app/data",
        "-e", "OLLAMA_BASE_URL=",
        "-e", "OLLAMA_MODEL=",
        "-e", "LLM_HOSTS=",
        "-e", "OPENAI_API_KEY=",
        "-e", "RESEARCH_LLM_ENDPOINT=",
        "-e", "SEARXNG_INSTANCE="
    )
    $startCommand = @("sh", "-c", "mkdir -p /app/data/code-workspaces/.worker && exec uvicorn app:app --host 0.0.0.0 --port 7000")
    $containerId = (& docker run -d --name $ContainerName --network none @envArgs $Image @startCommand).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $containerId) {
        Add-Result "container-start" "fail" "docker run --network none failed"
        $summary = Save-Report
        exit 1
    }
    Add-Result "container-start" "ok" "started $ContainerName with --network none"

    $healthy = $false
    $healthCode = @'
import json
import urllib.request
response = urllib.request.urlopen("http://127.0.0.1:7000/api/health", timeout=2)
payload = json.loads(response.read().decode("utf-8"))
assert payload.get("status") == "healthy", payload
'@
    for ($i = 0; $i -lt 60; $i++) {
        if (-not (Test-ContainerRunning)) {
            break
        }
        Invoke-ContainerPython $healthCode *> $null
        if ($LASTEXITCODE -eq 0) {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if ($healthy) {
        Add-Result "health" "ok" "local health endpoint is reachable inside the no-network container"
    } else {
        $logs = (& docker logs $ContainerName --tail 80) -join "`n"
        Add-Result "health" "fail" "health endpoint did not become ready. Logs: $logs"
    }

    $routeCode = @'
import json
import urllib.request

def get(path):
    response = urllib.request.urlopen("http://127.0.0.1:7000" + path, timeout=5)
    assert response.status == 200, (path, response.status)
    return json.loads(response.read().decode("utf-8"))

status = get("/api/offline-control/status")
assert status["runtime"]["offline"] is True, status["runtime"]
assert status["models"]["enabled_external"] == 0, status["models"]
recommendations = get("/api/offline-control/models/recommendations")
assert recommendations["recommendations"], recommendations
assert recommendations["offline_warning"], recommendations
help_payload = get("/api/offline-control/help")
assert any(section["title"] == "Sensitive machine checklist" for section in help_payload["sections"]), help_payload
'@
    if (-not (Test-ContainerRunning)) {
        Add-Result "offline-routes" "fail" "container is not running"
    } elseif ((Invoke-ContainerPython $routeCode) -eq 0) {
        Add-Result "offline-routes" "ok" "offline status, model recommendations, and help routes work without network"
    } else {
        Add-Result "offline-routes" "fail" "one or more local offline-control routes failed"
    }

    $denyCode = @'
from src import code_workspace as cw

blocked = [
    "curl https://example.com",
    "wget https://example.com",
    "git pull",
    "python -m pip install requests",
    "npm install",
    "docker ps",
]
for command in blocked:
    assert cw.DENIED_COMMAND_RE.search(command), command
'@
    if (-not (Test-ContainerRunning)) {
        Add-Result "code-workspace-denylist" "fail" "container is not running"
    } elseif ((Invoke-ContainerPython $denyCode) -eq 0) {
        Add-Result "code-workspace-denylist" "ok" "network/package/host commands are denied"
    } else {
        Add-Result "code-workspace-denylist" "fail" "Code Workspace denylist check failed"
    }

    $networkMode = (& docker inspect $ContainerName --format "{{.HostConfig.NetworkMode}}" 2>$null | Select-Object -First 1)
    if ($networkMode -eq "none") {
        Add-Result "network-mode" "ok" "container HostConfig.NetworkMode is none"
    } else {
        Add-Result "network-mode" "fail" "container network mode is $networkMode"
    }

    $egressCode = "import socket; socket.create_connection(('1.1.1.1', 80), 3)"
    if (-not (Test-ContainerRunning)) {
        Add-Result "egress" "fail" "container is not running"
    } else {
        Invoke-ContainerPython $egressCode *> $null
        $egressExitCode = $LASTEXITCODE
        if ($egressExitCode -ne 0) {
            Add-Result "egress" "ok" "app container could not reach 1.1.1.1:80"
        } else {
            Add-Result "egress" "fail" "app container reached 1.1.1.1:80"
        }
    }

    & docker run --rm --network none -e CLEVERLY_OFFLINE=0 $Image python -c "print('should not run')" *> $null
    if ($LASTEXITCODE -eq 64) {
        Add-Result "entrypoint-guard" "ok" "entrypoint refused non-offline runtime without break-glass"
    } else {
        Add-Result "entrypoint-guard" "fail" "entrypoint guard exit code was $LASTEXITCODE, expected 64"
    }
} finally {
    & docker rm -f $ContainerName *> $null
    Pop-Location
}

$summary = Save-Report
if ($summary.fail -gt 0) {
    Write-Host "No-network container smoke failed." -ForegroundColor Red
    exit 1
}

Write-Host "No-network container smoke passed." -ForegroundColor Green
