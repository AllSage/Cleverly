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
$Metadata = [ordered]@{
    host = $env:COMPUTERNAME
    os = [System.Environment]::OSVersion.VersionString
    powershell = $PSVersionTable.PSVersion.ToString()
    docker_version = ""
}

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

function Get-DockerValue([string]$Container, [string]$Format) {
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $value = & docker inspect $Container --format $Format 2>$null
        if ($LASTEXITCODE -eq 0) { return ($value | Select-Object -First 1) }
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    return ""
}

function Test-ContainerHardening([string]$Container, [string]$ExpectedNetworkMode) {
    $networkMode = Get-DockerValue $Container "{{.HostConfig.NetworkMode}}"
    if ($networkMode -like $ExpectedNetworkMode) {
        Add-Result "$Container-network" "ok" "$Container network mode is $networkMode"
    } else {
        Add-Result "$Container-network" "fail" "$Container network mode is $networkMode, expected $ExpectedNetworkMode"
    }

    $readOnly = Get-DockerValue $Container "{{.HostConfig.ReadonlyRootfs}}"
    if ($readOnly -eq "true") {
        Add-Result "$Container-readonly-rootfs" "ok" "$Container read_only root filesystem is enabled"
    } else {
        Add-Result "$Container-readonly-rootfs" "warn" "$Container read_only root filesystem is $readOnly"
    }

    $securityOpt = Get-DockerValue $Container "{{json .HostConfig.SecurityOpt}}"
    if ($securityOpt -match "no-new-privileges:true") {
        Add-Result "$Container-no-new-privileges" "ok" "$Container has no-new-privileges:true"
    } else {
        Add-Result "$Container-no-new-privileges" "fail" "$Container missing no-new-privileges:true"
    }

    $capDrop = Get-DockerValue $Container "{{json .HostConfig.CapDrop}}"
    if ($capDrop -match "ALL") {
        Add-Result "$Container-cap-drop" "ok" "$Container drops all Linux capabilities"
    } else {
        Add-Result "$Container-cap-drop" "warn" "$Container cap_drop is $capDrop"
    }
}

Push-Location $Root
try {
    Require-Command "docker"
    $Metadata.docker_version = (& docker version --format "{{json .}}" 2>$null) -join "`n"
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

    Test-ContainerHardening "cleverly" "*offline_private*"
    Test-ContainerHardening "cleverly-code-worker" "none"

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

    $ErrorActionPreference = "Continue"
    try {
        & docker exec cleverly python -c "import socket; socket.getaddrinfo('example.com', 80)" *> $null
        $dnsExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($dnsExitCode -eq 0) {
        Add-Result "dns-leak" "fail" "app container resolved example.com"
    } else {
        Add-Result "dns-leak" "ok" "app container could not resolve example.com"
    }

    $ErrorActionPreference = "Continue"
    try {
        & docker exec cleverly python -c "import socket; socket.create_connection(('example.com', 443), 3)" *> $null
        $httpsExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($httpsExitCode -eq 0) {
        Add-Result "https-egress" "fail" "app container reached example.com:443"
    } else {
        Add-Result "https-egress" "ok" "app container could not reach example.com:443"
    }
} finally {
    Pop-Location
}

$summary = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    url = $Url
    fine_tune = [bool]$FineTune
    metadata = $Metadata
    results = $Results
    ok = @($Results | Where-Object status -eq "ok").Count
    warn = @($Results | Where-Object status -eq "warn").Count
    fail = @($Results | Where-Object status -eq "fail").Count
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportFullPath) | Out-Null
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportFullPath -Encoding UTF8

if ($summary.fail -gt 0) {
    Write-Host ("Offline smoke failed. Report: " + $ReportFullPath) -ForegroundColor Red
    exit 1
}

Write-Host ("Offline smoke passed. Report: " + $ReportFullPath) -ForegroundColor Green
