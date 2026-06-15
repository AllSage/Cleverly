#Requires -Version 5.1
<#
  Cleverly standalone Windows launcher (no Docker).

  This mode is for easy local use when Docker Desktop is not available. It
  enforces Cleverly's app-level offline policy and binds to loopback by default,
  but it does not provide Docker's network namespace, read-only filesystem, or
  sealed-volume isolation. Use Cleverly-App.cmd / Cleverly.ps1 for sensitive
  Docker sealed mode.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\Cleverly-Standalone.ps1 setup -AllowConnectedPrep
    powershell -ExecutionPolicy Bypass -File .\Cleverly-Standalone.ps1 start
    powershell -ExecutionPolicy Bypass -File .\Cleverly-Standalone.ps1 doctor
#>
param(
    [ValidateSet("setup", "start", "doctor", "open")]
    [string]$Action = "start",
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [switch]$AllowConnectedPrep,
    [switch]$AllowNetwork,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$Url = "http://${BindHost}:$Port"
$VenvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$SetupScript = Join-Path $PSScriptRoot "setup.py"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    exit 1
}

function Require-LoopbackBind {
    $hostName = $BindHost.Trim().ToLowerInvariant()
    if ($hostName -notin @("127.0.0.1", "localhost")) {
        Fail "Standalone mode only binds to 127.0.0.1 or localhost. Use Docker sealed mode for stronger isolation, or intentionally run uvicorn yourself for LAN exposure."
    }
}

function Set-StandaloneEnvironment {
    if ($AllowNetwork) {
        $env:CLEVERLY_ALLOW_NETWORK = "I_ACCEPT_NETWORK_RISK"
        $env:CLEVERLY_OFFLINE = "0"
        Write-Host "Network break-glass is enabled for this process." -ForegroundColor Yellow
    } else {
        $env:CLEVERLY_ALLOW_NETWORK = ""
        $env:CLEVERLY_OFFLINE = "1"
    }
    $env:CLEVERLY_STANDALONE = "1"
    $env:APP_BIND = $BindHost
    $env:APP_PORT = [string]$Port
    $env:AUTH_ENABLED = "true"
    $env:LOCALHOST_BYPASS = "false"
    $env:CODE_WORKSPACE_RUNNER = "in-process"
}

function Get-PythonExe {
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    Fail "Python 3.11+ was not found on PATH. Install Python, then rerun standalone setup."
}

function Ensure-Venv {
    param([switch]$Create)
    if (Test-Path -LiteralPath $VenvPython) {
        return
    }
    if (-not $Create) {
        Fail "Standalone dependencies are not prepared. Run '.\Cleverly-Standalone.ps1 setup -AllowConnectedPrep' on a connected, non-sensitive prep machine first."
    }
    $python = Get-PythonExe
    Write-Step "Creating standalone virtual environment"
    & $python -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        Fail "Failed to create the standalone virtual environment."
    }
}

function Install-Dependencies {
    if (-not $AllowConnectedPrep) {
        Fail "Dependency installation may contact package indexes. Rerun setup with -AllowConnectedPrep only on a connected, non-sensitive prep machine."
    }
    Write-Step "Installing standalone dependencies"
    & $VenvPython -m pip install --upgrade pip --quiet
    if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed." }
    & $VenvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed." }
}

function Test-StandaloneDependencies {
    $code = "import fastapi, uvicorn; import src.offline_policy"
    & $VenvPython -c $code 1>$null
    if ($LASTEXITCODE -ne 0) {
        Fail "Standalone Python dependencies are incomplete. Run '.\Cleverly-Standalone.ps1 setup -AllowConnectedPrep' on a connected, non-sensitive prep machine first."
    }
}

function Run-Setup {
    if (-not (Test-Path -LiteralPath $SetupScript)) {
        Fail "setup.py was not found next to this launcher."
    }
    Write-Step "Running first-time app setup"
    & $VenvPython setup.py
    if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }
}

function Invoke-StandaloneDoctor {
    Ensure-Venv
    Test-StandaloneDependencies
    Write-Step "Standalone doctor"
    Write-Host "Standalone mode: app-enforced offline policy, loopback bind, no Docker isolation." -ForegroundColor Yellow
    Write-Host ("URL: " + $Url)
    Write-Host ("CLEVERLY_OFFLINE=" + $env:CLEVERLY_OFFLINE)
    Write-Host ("APP_BIND=" + $env:APP_BIND)
    Write-Host ("AUTH_ENABLED=" + $env:AUTH_ENABLED)
    Write-Host ("LOCALHOST_BYPASS=" + $env:LOCALHOST_BYPASS)
    Write-Host ("CODE_WORKSPACE_RUNNER=" + $env:CODE_WORKSPACE_RUNNER)

    $policyCode = @"
from src.offline_policy import evaluate_offline_policy
report = evaluate_offline_policy(include_db=False)
print("policy_failures=" + str(report["summary"]["fail"]))
print("policy_warnings=" + str(report["summary"]["warn"]))
for item in report["checks"]:
    print(item["status"].upper() + " " + item["id"] + ": " + item["detail"])
raise SystemExit(1 if report["summary"]["fail"] else 0)
"@
    & $VenvPython -c $policyCode
    if ($LASTEXITCODE -ne 0) {
        Fail "Standalone offline policy check failed."
    }
    Write-Host ""
    Write-Host "Standalone doctor finished without policy failures." -ForegroundColor Green
}

function Start-Standalone {
    Ensure-Venv
    Test-StandaloneDependencies
    Write-Step ("Starting Cleverly standalone at " + $Url)
    Write-Host "Docker is not used in this mode. Use Docker sealed mode for sensitive machines." -ForegroundColor Yellow
    Write-Host "Press Ctrl+C to stop."
    Write-Host ""
    if (-not $NoOpen) {
        Start-Process $Url
    }
    & $VenvPython -m uvicorn app:app --host $BindHost --port $Port
}

Require-LoopbackBind
Set-StandaloneEnvironment

switch ($Action) {
    "setup" {
        Ensure-Venv -Create
        Install-Dependencies
        Run-Setup
        Invoke-StandaloneDoctor
    }
    "start" { Start-Standalone }
    "doctor" { Invoke-StandaloneDoctor }
    "open" { Start-Process $Url }
}
