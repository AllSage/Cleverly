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
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 doctor
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 logs
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 prep -AllowConnectedPrep
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 bundle -AllowConnectedPrep -FineTune
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 start -FineTune
#>
param(
    [ValidateSet("start", "stop", "restart", "status", "open", "logs", "prep", "doctor", "bundle")]
    [string]$Action = "start",
    [string]$Url = "http://127.0.0.1:7000",
    [string]$Model = "llama3.2:3b",
    [string]$BundleDir = "dist\cleverly-offline-bundle",
    [switch]$NoOpen,
    [switch]$AllowConnectedPrep,
    [switch]$FineTune
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:OLLAMA_IMAGE = if ($env:OLLAMA_IMAGE) { $env:OLLAMA_IMAGE } else { "cleverly-ollama:local" }
$env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { $Model }
$UseFineTune = $FineTune -or ($env:CLEVERLY_ENABLE_FINETUNE -eq "1")
if ($UseFineTune) {
    $env:CLEVERLY_FINETUNE_IMAGE = if ($env:CLEVERLY_FINETUNE_IMAGE) { $env:CLEVERLY_FINETUNE_IMAGE } else { "cleverly:finetune" }
}
$SupportImages = @(
    "docker.io/chromadb/chroma:latest",
    "docker.io/searxng/searxng:latest",
    "docker.io/binwiederhier/ntfy:latest"
)

$ComposeArgs = @(
    "--project-name", "cleverly",
    "--env-file", ".env.example",
    "-f", "docker-compose.yml",
    "-f", "docker/ollama-offline.yml"
)
if ($UseFineTune) {
    $ComposeArgs += @("-f", "docker/finetune.yml")
}

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

function Require-ConnectedPrep {
    $prepAllowed = $AllowConnectedPrep -or ($env:CLEVERLY_ALLOW_CONNECTED_PREP -eq "I_ACCEPT_CONNECTED_PREP")
    if (-not $prepAllowed) {
        Fail "Connected prep is disabled by default. On a non-sensitive connected prep machine, rerun '.\Cleverly.ps1 $Action -AllowConnectedPrep' or set CLEVERLY_ALLOW_CONNECTED_PREP=I_ACCEPT_CONNECTED_PREP. On a sensitive machine, load prepared images/models and run '.\Cleverly.ps1 start'."
    }
}

function Test-Image([string]$Image) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        docker image inspect $Image 1>$null 2>$null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $oldPreference
    }
}

function Test-FileHasText([string]$Path, [string]$Text) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return ((Get-Content -LiteralPath $Path -Raw) -like "*$Text*")
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

function Get-CleverlyContainers {
    @(docker ps --filter "name=cleverly" --format "{{.Names}}")
}

function Write-DoctorOk([string]$Message) {
    Write-Host ("[OK]   " + $Message) -ForegroundColor Green
}

function Write-DoctorWarn([string]$Message) {
    $script:DoctorWarnings += 1
    Write-Host ("[WARN] " + $Message) -ForegroundColor Yellow
}

function Write-DoctorFail([string]$Message) {
    $script:DoctorFailures += 1
    Write-Host ("[FAIL] " + $Message) -ForegroundColor Red
}

function Test-DirectoryHasFiles([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return [bool](Get-ChildItem -LiteralPath $Path -Force -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-TrainableModelFiles {
    $roots = @(
        "data\training\finetune\base-models",
        "data\models",
        "data\huggingface"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $config = Get-ChildItem -LiteralPath $root -Recurse -Filter config.json -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($config) { return $true }
    }
    return $false
}

function Start-Cleverly {
    Require-Docker
    if (-not (Test-Image "cleverly:local")) {
        Fail "Missing image cleverly:local. This launcher will not pull or build during start. Load prepared images first, or run '.\Cleverly.ps1 prep -AllowConnectedPrep' only on a connected prep machine."
    }
    if (-not (Test-Image $env:OLLAMA_IMAGE)) {
        Fail "Missing image $env:OLLAMA_IMAGE. This launcher will not pull models during start. Load prepared images/models first, or run '.\Cleverly.ps1 prep -AllowConnectedPrep' only on a connected prep machine."
    }
    if ($UseFineTune -and -not (Test-Image $env:CLEVERLY_FINETUNE_IMAGE)) {
        Fail "Missing image $env:CLEVERLY_FINETUNE_IMAGE. Build it on a connected prep machine with 'docker compose --project-name cleverly -f docker-compose.yml -f docker/finetune.yml build cleverly', then start with '.\Cleverly.ps1 start -FineTune'."
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
    if ($UseFineTune) {
        Write-Host "Advanced LoRA fine-tuning image enabled." -ForegroundColor Green
    }
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
    Require-ConnectedPrep
    Write-Step "Building Cleverly image"
    docker compose --project-name cleverly --env-file .env.example build cleverly
    if ($LASTEXITCODE -ne 0) { Fail "Failed to build cleverly:local." }

    Write-Step "Pulling support service images"
    docker compose --project-name cleverly --env-file .env.example pull chromadb searxng ntfy
    if ($LASTEXITCODE -ne 0) { Fail "Failed to pull support service images." }

    Write-Step "Building local Ollama image"
    docker build -f docker/ollama-local.Dockerfile -t cleverly-ollama:local .
    if ($LASTEXITCODE -ne 0) { Fail "Failed to build cleverly-ollama:local." }

    Write-Step "Pulling Ollama model into ./data/ollama"
    docker compose --project-name cleverly --env-file .env.example -f docker-compose.yml -f docker/ollama.yml run --rm ollama_pull
    if ($LASTEXITCODE -ne 0) { Fail "Failed to pull Ollama model $env:OLLAMA_MODEL." }

    if ($FineTune) {
        Write-Step "Building optional fine-tune image"
        docker compose --project-name cleverly --env-file .env.example -f docker-compose.yml -f docker/finetune.yml build cleverly
        if ($LASTEXITCODE -ne 0) { Fail "Failed to build cleverly:finetune." }
    }
}

function Invoke-Doctor {
    $script:DoctorFailures = 0
    $script:DoctorWarnings = 0
    Write-Step "Cleverly doctor"

    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-DoctorOk "Docker CLI found"
    } else {
        Write-DoctorFail "Docker CLI not found"
    }

    if ($script:DoctorFailures -eq 0) {
        docker version *> $null
        if ($LASTEXITCODE -eq 0) { Write-DoctorOk "Docker engine is responding" } else { Write-DoctorFail "Docker engine is not responding" }

        docker compose version *> $null
        if ($LASTEXITCODE -eq 0) { Write-DoctorOk "Docker Compose plugin is available" } else { Write-DoctorFail "Docker Compose plugin is not available" }
    }

    foreach ($file in @("docker-compose.yml", "docker\ollama-offline.yml", "Cleverly.ps1", "Cleverly.cmd", ".env.example")) {
        if (Test-Path -LiteralPath $file) { Write-DoctorOk "Found $file" } else { Write-DoctorFail "Missing $file" }
    }
    if ($UseFineTune) {
        if (Test-Path -LiteralPath "docker\finetune.yml") { Write-DoctorOk "Found docker\finetune.yml" } else { Write-DoctorFail "Missing docker\finetune.yml" }
    }

    if (Test-FileHasText "docker-compose.yml" 'CLEVERLY_OFFLINE: "1"') {
        Write-DoctorOk "Compose forces CLEVERLY_OFFLINE=1"
    } else {
        Write-DoctorFail "Compose does not force CLEVERLY_OFFLINE=1"
    }
    if (Test-FileHasText "docker-compose.yml" "internal: true") {
        Write-DoctorOk "Compose has an internal-only private network"
    } else {
        Write-DoctorFail "Compose internal-only private network was not found"
    }
    if (Test-FileHasText "docker\entrypoint.sh" "I_ACCEPT_NETWORK_RISK") {
        Write-DoctorOk "Entrypoint requires explicit network break-glass"
    } else {
        Write-DoctorFail "Entrypoint network break-glass guard was not found"
    }

    if ($script:DoctorFailures -eq 0) {
        if (Test-Image "cleverly:local") { Write-DoctorOk "Image cleverly:local is loaded" } else { Write-DoctorFail "Missing image cleverly:local" }
        if (Test-Image $env:OLLAMA_IMAGE) { Write-DoctorOk "Image $env:OLLAMA_IMAGE is loaded" } else { Write-DoctorFail "Missing image $env:OLLAMA_IMAGE" }
        if ($UseFineTune) {
            if (Test-Image $env:CLEVERLY_FINETUNE_IMAGE) { Write-DoctorOk "Image $env:CLEVERLY_FINETUNE_IMAGE is loaded" } else { Write-DoctorFail "Missing image $env:CLEVERLY_FINETUNE_IMAGE" }
        }
        foreach ($image in $SupportImages) {
            if (Test-Image $image) { Write-DoctorOk "Support image $image is loaded" } else { Write-DoctorWarn "Support image $image is not loaded; full Compose startup may need it" }
        }
    }

    foreach ($dir in @("data", "logs", "data\ollama", "data\training")) {
        if (Test-Path -LiteralPath $dir) { Write-DoctorOk "Found $dir" } else { Write-DoctorWarn "Missing $dir; Docker can create it, but prepared bundles should include needed model data" }
    }
    if (Test-DirectoryHasFiles "data\ollama\models") {
        Write-DoctorOk "Ollama model data is present under data\ollama"
    } else {
        Write-DoctorWarn "No Ollama model files found under data\ollama\models"
    }
    if ($UseFineTune) {
        if (Test-TrainableModelFiles) { Write-DoctorOk "Trainable HF-format model files were found" } else { Write-DoctorWarn "No HF-format trainable model config.json found for fine-tuning" }
    }

    $containers = @()
    if ($script:DoctorFailures -eq 0) {
        $containers = Get-CleverlyContainers
        if ($containers.Count -gt 0) {
            Write-DoctorOk ("Running Cleverly containers: " + ($containers -join ", "))
        } else {
            Write-DoctorWarn "No Cleverly containers are currently running"
        }
    }

    try {
        $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 3
        if ($health.status -eq "healthy") {
            Write-DoctorOk "Health check is healthy at $Url"
        } else {
            Write-DoctorWarn "Health check responded but did not report healthy"
        }
    } catch {
        Write-DoctorWarn "Health check is not reachable at $Url"
    }

    if ($containers -contains "cleverly") {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            docker compose @ComposeArgs exec -T cleverly python -c "import socket; socket.create_connection(('1.1.1.1', 80), 3)" 1>$null 2>$null
            $egressExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $oldPreference
        }
        if ($egressExit -eq 0) {
            Write-DoctorFail "Cleverly container can reach 1.1.1.1:80; offline egress is not blocked"
        } else {
            Write-DoctorOk "Cleverly container internet egress is blocked"
        }

        if ($UseFineTune) {
            $code = "from src.offline_finetune import finetune_status; import json; s=finetune_status(); print(json.dumps({'deps': s['dependencies']['available'], 'models': len(s['trainable_models'])}))"
            $ftRaw = docker compose @ComposeArgs exec -T cleverly python -c $code 2>$null
            if ($LASTEXITCODE -eq 0) {
                try {
                    $ft = $ftRaw | ConvertFrom-Json
                    if ($ft.deps) { Write-DoctorOk "Fine-tune dependencies are available in the running container" } else { Write-DoctorWarn "Fine-tune dependencies are not available in the running container" }
                    if ([int]$ft.models -gt 0) { Write-DoctorOk ("Fine-tune trainable models detected: " + $ft.models) } else { Write-DoctorWarn "No trainable fine-tune models detected in the running container" }
                } catch {
                    Write-DoctorWarn "Fine-tune status returned unparseable output"
                }
            } else {
                Write-DoctorWarn "Could not read fine-tune status from the running container"
            }
        }
    } else {
        Write-DoctorWarn "Skipped container egress check because the cleverly container is not running"
    }

    Write-Host ""
    if ($script:DoctorFailures -gt 0) {
        Write-Host "Doctor finished with $script:DoctorFailures failure(s) and $script:DoctorWarnings warning(s)." -ForegroundColor Red
        exit 1
    }
    if ($script:DoctorWarnings -gt 0) {
        Write-Host "Doctor finished with $script:DoctorWarnings warning(s)." -ForegroundColor Yellow
        return
    }
    Write-Host "Doctor finished cleanly." -ForegroundColor Green
}

function Copy-BundleItem {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source)) { return $false }
    $parent = Split-Path -Parent $Destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $item = Get-Item -LiteralPath $Source -Force
    if ($item.PSIsContainer) {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
        }
    } else {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }
    return $true
}

function New-CleverlyBundle {
    Require-Docker
    Require-ConnectedPrep

    Write-Step "Preparing images and model data"
    Prep-Cleverly

    $bundlePath = if ([System.IO.Path]::IsPathRooted($BundleDir)) { $BundleDir } else { Join-Path $PSScriptRoot $BundleDir }
    New-Item -ItemType Directory -Force -Path $bundlePath | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $bundlePath "data") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $bundlePath "docs") | Out-Null

    Write-Step "Copying launcher and Compose files"
    foreach ($file in @("Cleverly.ps1", "Cleverly.cmd", "docker-compose.yml", ".env.example", "README.md", "LICENSE", "ACKNOWLEDGMENTS.md")) {
        Copy-BundleItem $file (Join-Path $bundlePath $file) | Out-Null
    }
    Copy-BundleItem "docker" (Join-Path $bundlePath "docker") | Out-Null
    Copy-BundleItem "config" (Join-Path $bundlePath "config") | Out-Null
    foreach ($doc in @("docs\offline-release.md", "docs\local-training-lab.md", "docs\external-agent-study-packs.md")) {
        Copy-BundleItem $doc (Join-Path $bundlePath $doc) | Out-Null
    }

    Write-Step "Copying prepared local model data"
    foreach ($dir in @(
        "data\ollama",
        "data\training\finetune\base-models",
        "data\huggingface",
        "data\cache\fastembed"
    )) {
        if (Copy-BundleItem $dir (Join-Path $bundlePath $dir)) {
            Write-Host ("Copied " + $dir)
        }
    }

    $images = @("cleverly:local", $env:OLLAMA_IMAGE)
    if ($UseFineTune) { $images += $env:CLEVERLY_FINETUNE_IMAGE }
    foreach ($image in $SupportImages) {
        if (Test-Image $image) { $images += $image }
    }
    $images = @($images | Select-Object -Unique)
    foreach ($image in $images) {
        if (-not (Test-Image $image)) { Fail "Cannot bundle missing image $image." }
    }

    Write-Step "Saving Docker images"
    $archive = Join-Path $bundlePath "cleverly-images.tar"
    docker save -o $archive @images
    if ($LASTEXITCODE -ne 0) { Fail "Failed to save Docker images." }

    $startArgs = if ($UseFineTune) { "start -FineTune" } else { "start" }
    Set-Content -LiteralPath (Join-Path $bundlePath "load-cleverly.cmd") -Encoding ASCII -Value @"
@echo off
setlocal
docker load -i "%~dp0cleverly-images.tar"
if errorlevel 1 pause
"@
    Set-Content -LiteralPath (Join-Path $bundlePath "start-cleverly.cmd") -Encoding ASCII -Value @"
@echo off
setlocal
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Cleverly.ps1" $startArgs
if errorlevel 1 pause
"@
    Set-Content -LiteralPath (Join-Path $bundlePath "README-OFFLINE.md") -Encoding UTF8 -Value @"
# Cleverly Offline Bundle

This folder was created by:

````powershell
.\Cleverly.ps1 bundle -AllowConnectedPrep$(if ($UseFineTune) { " -FineTune" } else { "" })
````

Use it on the offline machine:

1. Install Docker Desktop.
2. Run `load-cleverly.cmd`.
3. Run `start-cleverly.cmd`.
4. Open $Url.

The start script uses `--no-build` and `--pull never` through `Cleverly.ps1`.
Normal startup does not download images, packages, models, or study packs.

Run a local check with:

````powershell
.\Cleverly.ps1 doctor$(if ($UseFineTune) { " -FineTune" } else { "" })
````
"@

    Write-Host ""
    Write-Host ("Offline bundle written to: " + $bundlePath) -ForegroundColor Green
    Write-Host "Copy that folder to the offline machine, then run load-cleverly.cmd and start-cleverly.cmd."
}

switch ($Action) {
    "start" { Start-Cleverly }
    "stop" { Stop-Cleverly }
    "restart" { Stop-Cleverly; Start-Cleverly }
    "status" { Require-Docker; Show-Status }
    "open" { Start-Process $Url }
    "logs" { Require-Docker; docker logs -f --tail 200 cleverly }
    "prep" { Prep-Cleverly }
    "doctor" { Invoke-Doctor }
    "bundle" { New-CleverlyBundle }
}
