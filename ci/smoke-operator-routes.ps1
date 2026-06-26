#Requires -Version 5.1
<#
  Smoke test Cleverly's read-only operator route surfaces in the running Docker
  app container. The Python runner is injected from the current worktree, while
  imports resolve against the container's app runtime.
#>

param(
    [string]$ContainerName = "cleverly",
    [string]$Owner = "smoke",
    [int]$Limit = 20,
    [string]$ReportPath = "dist\operator-route-smoke.json"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = Join-Path $Root "ci\operator_route_smoke.py"
$ReportFullPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $Root $ReportPath }

function Fail([string]$Message) {
    Write-Host ("[fail] " + $Message) -ForegroundColor Red
    exit 1
}

Push-Location $Root
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "docker was not found on PATH"
    }
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        Fail "operator route smoke runner not found: $ScriptPath"
    }

    $running = & docker inspect $ContainerName --format "{{.State.Running}}" 2>$null
    if ($LASTEXITCODE -ne 0 -or (($running | Select-Object -First 1) -ne "true")) {
        Fail "container $ContainerName is not running"
    }

    $code = Get-Content -LiteralPath $ScriptPath -Raw
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($code))
    $runner = @"
import base64
import sys
code = base64.b64decode('$encoded').decode('utf-8')
sys.argv = ['operator_route_smoke.py', '--owner', '$Owner', '--limit', '$Limit']
exec(compile(code, 'operator_route_smoke.py', 'exec'), {'__name__': '__main__', '__file__': 'operator_route_smoke.py'})
"@

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $smokeOutput = & docker exec $ContainerName python -c $runner 2>&1
        $smokeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    $smokeText = ($smokeOutput | Out-String).Trim()
    if ($smokeText) {
        Write-Output $smokeText
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportFullPath) | Out-Null
    if ($smokeText.StartsWith("{")) {
        Set-Content -LiteralPath $ReportFullPath -Value ($smokeText + [Environment]::NewLine) -Encoding UTF8
        Write-Host ("Report: " + $ReportFullPath) -ForegroundColor Gray
    } else {
        Write-Host "[warn] Could not parse smoke JSON from container output." -ForegroundColor Yellow
    }

    if ($smokeExit -ne 0) {
        Fail "operator route smoke failed with exit code $smokeExit"
    }
    Write-Host "Operator route smoke passed." -ForegroundColor Green
} finally {
    Pop-Location
}
