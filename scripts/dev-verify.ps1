param(
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Run-Step {
    param(
        [string]$Name,
        [scriptblock]$Script
    )
    Write-Host "==> $Name"
    $global:LASTEXITCODE = 0
    & $Script
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if ($Install) {
    if (-not (Test-Path ".venv")) {
        Run-Step "Create virtual environment" { python -m venv .venv }
    }
    $python = ".\.venv\Scripts\python.exe"
    Run-Step "Install dependencies" { & $python -m pip install -r requirements.txt -c requirements.lock }
} elseif (Test-Path ".\.venv\Scripts\python.exe") {
    $python = ".\.venv\Scripts\python.exe"
} else {
    $python = "python"
}

Run-Step "Python compile check" {
    & $python -m compileall -q app.py core routes services src
}

Run-Step "Pytest" {
    & $python -m pytest
}

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Run-Step "Node package metadata" {
        npm run --silent test --if-present
    }
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Run-Step "Docker Compose config" {
        docker compose config --quiet
    }
}
