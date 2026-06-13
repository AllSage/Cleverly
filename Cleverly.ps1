#Requires -Version 5.1
<#
  Cleverly Docker app launcher.

  Defaults to the offline Docker runtime:
    - Compose project/stack named "cleverly"
    - local-only proxy at http://127.0.0.1:7000
    - Cleverly container named "cleverly"
    - bundled Ollama container named "cleverly-ollama"
    - no image pulls during Start

  Usage:
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 start
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 stop
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 status
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 logs
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 prep -AllowConnectedPrep
#>
param(
    [ValidateSet("start", "stop", "restart", "status", "open", "logs", "prep")]
    [string]$Action = "start",
    [string]$Url = "http://127.0.0.1:7000",
    [string]$Model = "llama3.2:3b",
    [switch]$NoOpen,
    [switch]$AllowConnectedPrep
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:OLLAMA_IMAGE = if ($env:OLLAMA_IMAGE) { $env:OLLAMA_IMAGE } else { "cleverly-ollama:local" }
$env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { $Model }

$ComposeArgs = @(
    "--project-name", "cleverly",
    "--env-file", ".env.example",
    "-f", "docker-compose.yml",
    "-f", "docker/ollama-offline.yml"
)

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    exit 1
}

function Require-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "Docker was not found. Install Docker Desktop, start it, then run this again."
    }
    docker version *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker is not responding. Start Docker Desktop, then run this again."
    }
}

function Test-Image([string]$Image) {
    docker image inspect $Image *> $null
    return ($LASTEXITCODE -eq 0)
}

function Wait-Health {
    param([int]$Seconds = 90)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 3
            if ($health.status -eq "healthy") { return $true }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Show-Status {
    docker ps --filter "name=cleverly" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
}

function Start-Cleverly {
    Require-Docker
    if (-not (Test-Image "cleverly:local")) {
        Fail "Missing image cleverly:local. This launcher will not pull or build during start. Load prepared images first, or run '.\Cleverly.ps1 prep -AllowConnectedPrep' only on a connected prep machine."
    }
    if (-not (Test-Image $env:OLLAMA_IMAGE)) {
        Fail "Missing image $env:OLLAMA_IMAGE. This launcher will not pull models during start. Load prepared images/models first, or run '.\Cleverly.ps1 prep -AllowConnectedPrep' only on a connected prep machine."
    }

    Write-Step "Starting Cleverly offline runtime"
    docker compose @ComposeArgs up -d --no-deps --no-build --pull never ollama cleverly cleverly_proxy
    if ($LASTEXITCODE -ne 0) { Fail "Docker Compose failed to start Cleverly." }

    Write-Step "Waiting for Cleverly health check"
    if (-not (Wait-Health)) {
        Show-Status
        Fail "Cleverly did not become healthy at $Url within the timeout."
    }

    Write-Host ""
    Write-Host "Cleverly is running: $Url" -ForegroundColor Green
    Show-Status
    if (-not $NoOpen) {
        Start-Process $Url
    }
}

function Stop-Cleverly {
    Require-Docker
    Write-Step "Stopping Cleverly"
    docker compose @ComposeArgs down
}

function Prep-Cleverly {
    Require-Docker
    $prepAllowed = $AllowConnectedPrep -or ($env:CLEVERLY_ALLOW_CONNECTED_PREP -eq "I_ACCEPT_CONNECTED_PREP")
    if (-not $prepAllowed) {
        Fail "Connected prep is disabled by default. On a non-sensitive connected prep machine, rerun '.\Cleverly.ps1 prep -AllowConnectedPrep' or set CLEVERLY_ALLOW_CONNECTED_PREP=I_ACCEPT_CONNECTED_PREP. On a sensitive machine, load prepared images/models and run '.\Cleverly.ps1 start'."
    }
    Write-Step "Building Cleverly image"
    docker compose --project-name cleverly --env-file .env.example build cleverly
    if ($LASTEXITCODE -ne 0) { Fail "Failed to build cleverly:local." }

    Write-Step "Building local Ollama image"
    docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
    if ($LASTEXITCODE -ne 0) { Fail "Failed to build cleverly-ollama:local." }

    Write-Step "Pulling Ollama model into ./data/ollama"
    docker compose --project-name cleverly --env-file .env.example -f docker-compose.yml -f docker/ollama.yml run --rm ollama_pull
    if ($LASTEXITCODE -ne 0) { Fail "Failed to pull Ollama model $env:OLLAMA_MODEL." }
}

switch ($Action) {
    "start" { Start-Cleverly }
    "stop" { Stop-Cleverly }
    "restart" { Stop-Cleverly; Start-Cleverly }
    "status" { Require-Docker; Show-Status }
    "open" { Start-Process $Url }
    "logs" { Require-Docker; docker logs -f --tail 200 cleverly }
    "prep" { Prep-Cleverly }
}
