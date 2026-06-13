#Requires -Version 5.1
<#
  Fresh-machine offline smoke test for Cleverly.

  Run this on the target offline computer after loading the prepared bundle.
  It intentionally uses --pull never through Cleverly.ps1 and fails if outbound
  egress from the app container is reachable.
#>

param(
    [string]$Url = "http://127.0.0.1:7000",
    [switch]$FineTune,
    [switch]$SkipRestart,
    [string]$ReportPath = "dist\fresh-machine-offline-smoke.json"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Launcher = Join-Path $Root "Cleverly.ps1"
$ReportFullPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $Root $ReportPath }
$UrlPort = ([Uri]$Url).Port
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

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Add-Result $Name "fail" "$Name was not found on PATH"
        throw "$Name missing"
    }
    Add-Result $Name "ok" "$Name is available"
}

function Test-ImagePresent([string]$Image) {
    docker image inspect $Image 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) {
        Add-Result "image:$Image" "ok" "image is loaded"
    } else {
        Add-Result "image:$Image" "fail" "image is missing; load the offline bundle first"
    }
}

function Invoke-Launcher([string]$Action) {
    $args = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Launcher, $Action)
    if ($FineTune) { $args += "-FineTune" }
    if ($Action -in @("start", "restart")) { $args += "-NoOpen" }
    & powershell @args
    if ($LASTEXITCODE -ne 0) {
        Add-Result "launcher:$Action" "fail" "Cleverly.ps1 $Action failed with exit code $LASTEXITCODE"
        throw "launcher failed"
    }
    Add-Result "launcher:$Action" "ok" "Cleverly.ps1 $Action completed"
}

Push-Location $Root
try {
    Require-Command "docker"
    if (-not (Test-Path -LiteralPath $Launcher)) {
        Add-Result "launcher" "fail" "Cleverly.ps1 not found"
        throw "launcher missing"
    }
    Add-Result "launcher" "ok" "Cleverly.ps1 found"

    Test-ImagePresent "cleverly:local"
    Test-ImagePresent "cleverly-ollama:local"
    if ($FineTune) { Test-ImagePresent "cleverly:finetune" }

    Invoke-Launcher "doctor"
    if (-not $SkipRestart) {
        Invoke-Launcher "restart"
    }

    $health = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 15
    if ($health.StatusCode -eq 200) {
        Add-Result "health" "ok" "$Url/api/health returned 200"
    } else {
        Add-Result "health" "fail" "unexpected status $($health.StatusCode)"
    }

    $ps = docker ps --filter "name=cleverly-proxy" --format "{{.Ports}}"
    if ($ps -match ("127\.0\.0\.1:{0}" -f $UrlPort)) {
        Add-Result "proxy-bind" "ok" "proxy is bound to loopback"
    } else {
        Add-Result "proxy-bind" "fail" "proxy ports did not show 127.0.0.1 binding: $ps"
    }

    $workerNetwork = docker inspect cleverly-code-worker --format "{{.HostConfig.NetworkMode}}" 2>$null
    if (($workerNetwork | Select-Object -First 1) -eq "none") {
        Add-Result "code-worker-network" "ok" "code worker network_mode is none"
    } else {
        Add-Result "code-worker-network" "fail" "code worker network mode is $workerNetwork"
    }

    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & docker exec cleverly python -c "import socket; socket.create_connection(('1.1.1.1', 80), 3)" *> $null
        $egressExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($egressExitCode -eq 0) {
        Add-Result "egress" "fail" "app container reached 1.1.1.1:80"
    } else {
        Add-Result "egress" "ok" "app container could not reach 1.1.1.1:80"
    }
} finally {
    Pop-Location
}

$summary = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    url = $Url
    fine_tune = [bool]$FineTune
    results = $Results
    ok = ($Results | Where-Object status -eq "ok").Count
    warn = ($Results | Where-Object status -eq "warn").Count
    fail = ($Results | Where-Object status -eq "fail").Count
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportFullPath) | Out-Null
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportFullPath -Encoding UTF8

if ($summary.fail -gt 0) {
    Write-Host ("Offline smoke failed. Report: " + $ReportFullPath) -ForegroundColor Red
    exit 1
}

Write-Host ("Offline smoke passed. Report: " + $ReportFullPath) -ForegroundColor Green
