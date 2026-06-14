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
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 setup -AllowConnectedPrep
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 start
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 stop
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 status
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 doctor
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 logs
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 seal-data -FineTune
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 prep -AllowConnectedPrep
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 bundle -AllowConnectedPrep -FineTune
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 start -FineTune
    powershell -ExecutionPolicy Bypass -File .\Cleverly.ps1 start -FineTune -HostData
#>
param(
    [ValidateSet("setup", "start", "stop", "restart", "status", "open", "logs", "prep", "doctor", "bundle", "seal-data")]
    [string]$Action = "start",
    [string]$Url = "http://127.0.0.1:7000",
    [string]$Model = "",
    [double]$GpuGB = -1,
    [string]$BundleDir = "dist\cleverly-offline-bundle",
    [switch]$NoOpen,
    [switch]$AllowConnectedPrep,
    [switch]$FineTune,
    [switch]$HostData
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$PrimaryModelFile = "data\cleverly-primary-model.json"
$ModelProfiles = @(
    @{
        Id = "cpu"
        Label = "CPU-only safe starter"
        MinGpuGb = 0.0
        MaxGpuGb = 4.0
        Model = "llama3.2:3b"
        Size = "2.0GB"
        Hardware = "0-3GB GPU VRAM or CPU-only"
        BestFor = "Reliable offline startup on CPU-only or very small GPU machines."
    },
    @{
        Id = "gpu-4"
        Label = "Low VRAM local chat"
        MinGpuGb = 4.0
        MaxGpuGb = 8.0
        Model = "qwen3:4b"
        Size = "2.5GB"
        Hardware = "4-7GB GPU VRAM"
        BestFor = "Better local reasoning than the CPU starter while keeping VRAM headroom."
    },
    @{
        Id = "gpu-8"
        Label = "Balanced 8GB GPU"
        MinGpuGb = 8.0
        MaxGpuGb = 12.0
        Model = "qwen3:8b"
        Size = "5.2GB"
        Hardware = "8-11GB GPU VRAM"
        BestFor = "General chat, summaries, and modest code tasks on consumer GPUs."
    },
    @{
        Id = "gpu-12"
        Label = "Stronger local reasoning"
        MinGpuGb = 12.0
        MaxGpuGb = 16.0
        Model = "qwen3:14b"
        Size = "9.3GB"
        Hardware = "12-15GB GPU VRAM"
        BestFor = "Better reasoning and coding when 8B-class models are not enough."
    },
    @{
        Id = "gpu-16"
        Label = "Local reasoning workstation"
        MinGpuGb = 16.0
        MaxGpuGb = 24.0
        Model = "gpt-oss:20b"
        Size = "14GB"
        Hardware = "16-23GB GPU VRAM"
        BestFor = "Open-weight local reasoning and agent workflows."
    },
    @{
        Id = "gpu-24"
        Label = "24GB coding workstation"
        MinGpuGb = 24.0
        MaxGpuGb = 80.0
        Model = "qwen3-coder:30b"
        Size = "19GB"
        Hardware = "24-79GB GPU VRAM"
        BestFor = "Best default for local repo editing and Code Workspace on a 24GB GPU."
    },
    @{
        Id = "gpu-80"
        Label = "80GB reasoning server"
        MinGpuGb = 80.0
        MaxGpuGb = -1.0
        Model = "gpt-oss:120b"
        Size = "65GB"
        Hardware = "80GB+ GPU VRAM"
        BestFor = "Large local reasoning model on workstation/server-class GPU memory."
    }
)

function Fail([string]$Message) {
    Write-Host ""
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    exit 1
}

function Normalize-PrimaryModel([string]$Value) {
    $candidate = ([string]$Value).Trim()
    if (-not $candidate) { return "" }
    if ($candidate.Length -gt 220 -or $candidate -notmatch '^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$') {
        Fail "Invalid model tag '$candidate'. Use an Ollama model tag such as 'llama3.2:3b', 'qwen3-coder:30b', or 'hf.co/org/model:quant'."
    }
    return $candidate
}

function Read-SavedPrimaryModel {
    if (-not (Test-Path -LiteralPath $PrimaryModelFile)) { return "" }
    try {
        $raw = Get-Content -LiteralPath $PrimaryModelFile -Raw
        $payload = $raw | ConvertFrom-Json
        return Normalize-PrimaryModel ([string]$payload.primary_model)
    } catch {
        try {
            return Normalize-PrimaryModel (Get-Content -LiteralPath $PrimaryModelFile -Raw)
        } catch {
            return ""
        }
    }
}

function Read-SinglePreparedOllamaModel {
    $manifestRoot = "data\ollama\models\manifests"
    if (-not (Test-Path -LiteralPath $manifestRoot)) { return "" }
    $manifests = @(Get-ChildItem -LiteralPath $manifestRoot -Recurse -File -ErrorAction SilentlyContinue)
    if ($manifests.Count -ne 1) { return "" }
    $manifest = $manifests[0]
    $tag = $manifest.Name
    $modelName = Split-Path -Leaf (Split-Path -Parent $manifest.FullName)
    if (-not $tag -or -not $modelName) { return "" }
    return Normalize-PrimaryModel ("${modelName}:${tag}")
}

function Get-DetectedGpuGb {
    if ($GpuGB -ge 0) { return [math]::Round($GpuGB, 1) }
    if ($env:CLEVERLY_GPU_GB) {
        $parsed = 0.0
        if ([double]::TryParse($env:CLEVERLY_GPU_GB, [ref]$parsed) -and $parsed -ge 0) {
            return [math]::Round($parsed, 1)
        }
    }

    $maxGb = 0.0
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $rows = @(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null)
            if ($LASTEXITCODE -eq 0) {
                foreach ($row in $rows) {
                    $mb = 0.0
                    if ([double]::TryParse(([string]$row).Trim(), [ref]$mb)) {
                        $gb = $mb / 1024.0
                        if ($gb -gt $maxGb) { $maxGb = $gb }
                    }
                }
            }
        } finally {
            $ErrorActionPreference = $oldPreference
        }
    }

    if ($maxGb -le 0 -and (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $controllers = @(Get-CimInstance Win32_VideoController | Where-Object {
                $_.AdapterRAM -gt 0 -and $_.Name -notmatch "Basic|Remote|Virtual|DisplayLink"
            })
            foreach ($gpu in $controllers) {
                $gb = [double]$gpu.AdapterRAM / 1GB
                if ($gb -gt $maxGb) { $maxGb = $gb }
            }
        } finally {
            $ErrorActionPreference = $oldPreference
        }
    }

    if ($maxGb -lt 0) { $maxGb = 0 }
    return [math]::Round($maxGb, 1)
}

function Get-ModelProfileForGpuGb([double]$DetectedGpuGb) {
    $gpu = [math]::Max(0.0, $DetectedGpuGb)
    foreach ($profile in $ModelProfiles) {
        $min = [double]$profile.MinGpuGb
        $max = [double]$profile.MaxGpuGb
        if ($gpu -ge $min -and ($max -lt 0 -or $gpu -lt $max)) {
            return $profile
        }
    }
    return $ModelProfiles[0]
}

function Resolve-PrimaryModel([switch]$Require) {
    $explicitModel = Normalize-PrimaryModel $Model
    $envModel = Normalize-PrimaryModel $env:OLLAMA_MODEL
    $savedModel = Read-SavedPrimaryModel
    $singlePreparedModel = Read-SinglePreparedOllamaModel
    $script:PrimaryModelDetectedGpuGb = $null
    $script:PrimaryModelProfileId = ""
    $script:PrimaryModelProfileLabel = ""

    if ($explicitModel) {
        $script:PrimaryModelSource = "-Model"
        $script:PrimaryModel = $explicitModel
    } elseif ($envModel) {
        $script:PrimaryModelSource = "OLLAMA_MODEL"
        $script:PrimaryModel = $envModel
    } elseif ($Require -and ($Action -eq "setup" -or $Action -eq "prep" -or $Action -eq "bundle")) {
        $detectedGpuGb = Get-DetectedGpuGb
        $profile = Get-ModelProfileForGpuGb $detectedGpuGb
        $script:PrimaryModelSource = "auto hardware profile"
        $script:PrimaryModel = Normalize-PrimaryModel ([string]$profile.Model)
        $script:PrimaryModelDetectedGpuGb = $detectedGpuGb
        $script:PrimaryModelProfileId = [string]$profile.Id
        $script:PrimaryModelProfileLabel = [string]$profile.Label
    } elseif ($savedModel) {
        $script:PrimaryModelSource = $PrimaryModelFile
        $script:PrimaryModel = $savedModel
    } elseif ($singlePreparedModel) {
        $script:PrimaryModelSource = "single prepared Ollama manifest"
        $script:PrimaryModel = $singlePreparedModel
    } else {
        $script:PrimaryModelSource = ""
        $script:PrimaryModel = ""
    }

    if ($script:PrimaryModel) {
        $env:OLLAMA_MODEL = $script:PrimaryModel
    } elseif ($Require) {
        Fail "No primary model selected. Run '.\Cleverly.ps1 setup -AllowConnectedPrep' on a non-sensitive connected machine, or pass '-Model <ollama-tag>' to prep/bundle."
    }
    return $script:PrimaryModel
}

function Save-PrimaryModelManifest([string]$Reason) {
    if (-not $script:PrimaryModel) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PrimaryModelFile) | Out-Null
    [pscustomobject]@{
        primary_model = $script:PrimaryModel
        source = $script:PrimaryModelSource
        profile_id = $script:PrimaryModelProfileId
        profile_label = $script:PrimaryModelProfileLabel
        detected_gpu_gb = $script:PrimaryModelDetectedGpuGb
        reason = $Reason
        written_at = (Get-Date).ToString("o")
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $PrimaryModelFile -Encoding UTF8
}

function Set-BundlePrimaryModelEnv([string]$BundlePath) {
    $envPath = Join-Path $BundlePath ".env.example"
    if (-not (Test-Path -LiteralPath $envPath) -or -not $script:PrimaryModel) { return }
    $lines = @(Get-Content -LiteralPath $envPath)
    $updated = $false
    $out = foreach ($line in $lines) {
        if ($line -match '^\s*#?\s*OLLAMA_MODEL=') {
            $updated = $true
            "OLLAMA_MODEL=$script:PrimaryModel"
        } else {
            $line
        }
    }
    if (-not $updated) {
        $out += "OLLAMA_MODEL=$script:PrimaryModel"
    }
    Set-Content -LiteralPath $envPath -Encoding UTF8 -Value $out
}

$env:OLLAMA_IMAGE = if ($env:OLLAMA_IMAGE) { $env:OLLAMA_IMAGE } else { "cleverly-ollama:local" }
if ($Action -ne "setup" -and $Action -ne "prep" -and $Action -ne "bundle") {
    Resolve-PrimaryModel | Out-Null
}
$UseFineTune = $FineTune -or ($env:CLEVERLY_ENABLE_FINETUNE -eq "1")
if ($UseFineTune) {
    $env:CLEVERLY_FINETUNE_IMAGE = if ($env:CLEVERLY_FINETUNE_IMAGE) { $env:CLEVERLY_FINETUNE_IMAGE } else { "cleverly:finetune" }
}
$UseSealedData = -not $HostData -and ($env:CLEVERLY_HOST_DATA -ne "1") -and ($env:CLEVERLY_USE_HOST_DATA -ne "1")
$SealedDataOverlay = "docker/sealed-data.yml"
$HostDataOverlay = "docker/host-data.yml"
$SupportImages = @(
    "docker.io/chromadb/chroma:latest",
    "docker.io/searxng/searxng:latest",
    "docker.io/binwiederhier/ntfy:latest"
)
$SealedCopyPlan = @(
    @{
        Source = "data"
        Volume = "cleverly-data"
        Label = "app data"
        Exclude = @("ollama", "ssh", "cache", "huggingface", "local", "npm-cache")
    },
    @{ Source = "logs"; Volume = "cleverly-logs"; Label = "logs" },
    @{ Source = "data\ssh"; Volume = "cleverly-ssh"; Label = "SSH keys" },
    @{ Source = "data\cache"; Volume = "cleverly-cache"; Label = "runtime cache" },
    @{ Source = "data\huggingface"; Volume = "cleverly-huggingface"; Label = "Hugging Face cache" },
    @{ Source = "data\local"; Volume = "cleverly-local"; Label = "local packages" },
    @{ Source = "data\npm-cache"; Volume = "cleverly-npm-cache"; Label = "npm cache" },
    @{ Source = "data\ollama"; Volume = "cleverly-ollama"; Label = "Ollama models"; Owner = "0:0" }
)

$ComposeArgs = @(
    "--project-name", "cleverly",
    "--env-file", ".env.example",
    "-f", "docker-compose.yml",
    "-f", "docker/ollama-offline.yml"
)
if ($UseSealedData) {
    $ComposeArgs += @("-f", $SealedDataOverlay)
} else {
    $ComposeArgs += @("-f", $HostDataOverlay)
}
if ($UseFineTune) {
    $ComposeArgs += @("-f", "docker/finetune.yml")
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
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

function Test-Volume([string]$Name) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        docker volume inspect $Name 1>$null 2>$null
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

function Test-OllamaModelAvailable {
    param(
        [string]$Model,
        [int]$Seconds = 30
    )
    if (-not $Model) { return $false }
    $containerName = if ($env:CLEVERLY_OLLAMA_CONTAINER_NAME) { $env:CLEVERLY_OLLAMA_CONTAINER_NAME } else { "cleverly-ollama" }
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $lines = @(docker exec $containerName ollama list 2>$null)
            if ($LASTEXITCODE -eq 0) {
                foreach ($line in $lines | Select-Object -Skip 1) {
                    $name = (($line -split "\s+") | Select-Object -First 1)
                    if ($name -eq $Model) { return $true }
                }
            }
        } finally {
            $ErrorActionPreference = $oldPreference
        }
        Start-Sleep -Seconds 2
    }
    return $false
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

function Copy-HostPathToVolume {
    param(
        [string]$Source,
        [string]$Volume,
        [string]$Label,
        [string[]]$Exclude = @(),
        [string]$Owner = ""
    )

    $puid = if ($env:PUID) { $env:PUID } else { "1000" }
    $pgid = if ($env:PGID) { $env:PGID } else { "1000" }
    $ownerSpec = if ([string]::IsNullOrWhiteSpace($Owner)) { "${puid}:${pgid}" } else { $Owner }

    docker volume create `
        --label "com.docker.compose.project=cleverly" `
        --label "com.docker.compose.volume=$Volume" `
        $Volume 1>$null
    if ($LASTEXITCODE -ne 0) { Fail "Could not create Docker volume $Volume." }

    $sourceExists = Test-Path -LiteralPath $Source
    $dockerArgs = @("run", "--rm", "--network", "none", "--entrypoint", "/bin/sh")
    if ($sourceExists) {
        $sourcePath = (Resolve-Path -LiteralPath $Source).ProviderPath
        $dockerArgs += @("-v", "${sourcePath}:/from:ro")
        $excludeArgs = ""
        if ($Exclude.Count -gt 0) {
            $excludeArgs = ($Exclude | ForEach-Object { "--exclude='./$_'" }) -join " "
        }
        $copyCommand = "set -eu; mkdir -p /to; tar cf - $excludeArgs -C /from . | tar xf - -C /to; chown -R $ownerSpec /to"
    } else {
        $copyCommand = "set -eu; mkdir -p /to; chown -R $ownerSpec /to"
    }
    $dockerArgs += @("-v", "${Volume}:/to", "cleverly:local", "-c", $copyCommand)

    docker @dockerArgs
    if ($LASTEXITCODE -ne 0) { Fail "Failed to prepare Docker volume $Volume for $Label." }

    if ($sourceExists) {
        Write-Host ("Copied " + $Label + " into Docker volume " + $Volume)
    } else {
        Write-Host ("Prepared empty Docker volume " + $Volume + " for " + $Label)
    }
}

function Seal-Data {
    Require-Docker
    if (-not (Test-Path -LiteralPath $SealedDataOverlay)) {
        Fail "Missing $SealedDataOverlay. Cannot start sealed data mode without the sealed-volume overlay."
    }
    if (-not (Test-Image "cleverly:local")) {
        Fail "Missing image cleverly:local. Load or build prepared images before sealing data."
    }

    $running = Get-CleverlyContainers
    if ($running.Count -gt 0) {
        Fail ("Stop Cleverly before sealing data to avoid an inconsistent copy. Running containers: " + ($running -join ", "))
    }

    Write-Step "Copying host data into sealed Docker volumes"
    foreach ($item in $SealedCopyPlan) {
        $exclude = @()
        if ($item.ContainsKey("Exclude")) { $exclude = @($item.Exclude) }
        $owner = ""
        if ($item.ContainsKey("Owner")) { $owner = [string]$item.Owner }
        Copy-HostPathToVolume -Source $item.Source -Volume $item.Volume -Label $item.Label -Exclude $exclude -Owner $owner
    }

    $nextStart = if ($UseFineTune) { ".\Cleverly.ps1 start -FineTune" } else { ".\Cleverly.ps1 start" }
    Write-Host ""
    Write-Host "Sealed Docker volumes are ready." -ForegroundColor Green
    Write-Host "Start sealed Cleverly with: $nextStart"
    Write-Host "Host folders were not deleted. Use -HostData only when you intentionally want visible host bind mounts."
}

function Start-Cleverly {
    Require-Docker
    Resolve-PrimaryModel -Require | Out-Null
    if (-not (Test-Path -LiteralPath $PrimaryModelFile)) {
        Save-PrimaryModelManifest "offline-start"
    }
    if (-not (Test-Image "cleverly:local")) {
        Fail "Missing image cleverly:local. This launcher will not pull or build during start. Load prepared images first, or run '.\Cleverly.ps1 prep -AllowConnectedPrep' only on a connected prep machine."
    }
    if (-not (Test-Image $env:OLLAMA_IMAGE)) {
        Fail "Missing image $env:OLLAMA_IMAGE. This launcher will not pull models during start. Load prepared images/models first, or run '.\Cleverly.ps1 prep -AllowConnectedPrep' only on a connected prep machine."
    }
    if ($UseFineTune -and -not (Test-Image $env:CLEVERLY_FINETUNE_IMAGE)) {
        Fail "Missing image $env:CLEVERLY_FINETUNE_IMAGE. Build it on a connected prep machine with 'docker compose --project-name cleverly -f docker-compose.yml -f docker/finetune.yml build cleverly', then start with '.\Cleverly.ps1 start -FineTune'."
    }
    if ($UseSealedData) {
        if (-not (Test-Path -LiteralPath $SealedDataOverlay)) {
            Fail "Missing $SealedDataOverlay. Cannot start sealed data mode without the sealed-volume overlay."
        }
        $unsealedSources = @()
        foreach ($item in $SealedCopyPlan) {
            if ((Test-DirectoryHasFiles $item.Source) -and -not (Test-Volume $item.Volume)) {
                $unsealedSources += $item.Source
            }
        }
        if ($unsealedSources.Count -gt 0) {
            $sealCommand = if ($UseFineTune) { ".\Cleverly.ps1 seal-data -FineTune" } else { ".\Cleverly.ps1 seal-data" }
            Write-Host ""
            Write-Host ("WARNING: Host data exists but sealed volumes are missing for: " + ($unsealedSources -join ", ")) -ForegroundColor Yellow
            Write-Host "Run '$sealCommand' before start if you want that data copied into Docker volumes." -ForegroundColor Yellow
        }
    } else {
        if (-not (Test-Path -LiteralPath $HostDataOverlay)) {
            Fail "Missing $HostDataOverlay. Cannot start host data mode without the host-data overlay."
        }
    }

    Write-Step "Starting Cleverly offline runtime"
    docker compose @ComposeArgs up -d --no-deps --no-build --pull never ollama cleverly_code_worker cleverly cleverly_proxy
    if ($LASTEXITCODE -ne 0) { Fail "Docker Compose failed to start Cleverly." }

    Write-Step "Waiting for Cleverly health check"
    if (-not (Wait-Health)) {
        Show-Status
        Fail "Cleverly did not become healthy at $Url within the timeout."
    }
    if (-not (Test-OllamaModelAvailable -Model $script:PrimaryModel -Seconds 30)) {
        Fail "Primary model '$script:PrimaryModel' is not loaded in bundled Ollama. Re-run '.\Cleverly.ps1 prep -AllowConnectedPrep -Model $script:PrimaryModel' on a connected prep machine, then seal or bundle again."
    }

    Write-Host ""
    Write-Host "Cleverly is running: $Url" -ForegroundColor Green
    if ($UseSealedData) {
        Write-Host "Sealed Docker volume data is enabled." -ForegroundColor Green
    } else {
        Write-Host "Host folder data mode is enabled by explicit opt-out." -ForegroundColor Yellow
    }
    if ($UseFineTune) {
        Write-Host "Advanced LoRA fine-tuning image enabled." -ForegroundColor Green
    }
    Write-Host ("Primary model: " + $script:PrimaryModel) -ForegroundColor Green
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
    Resolve-PrimaryModel -Require | Out-Null
    Write-Host ("Primary model: " + $script:PrimaryModel + " (" + $script:PrimaryModelSource + ")") -ForegroundColor Green
    if ($script:PrimaryModelSource -eq "auto hardware profile") {
        Write-Host ("Hardware profile: " + $script:PrimaryModelProfileLabel + " (" + $script:PrimaryModelDetectedGpuGb + " GB GPU VRAM)") -ForegroundColor Green
    }
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
    Save-PrimaryModelManifest "connected-prep"

    if ($FineTune) {
        Write-Step "Building optional fine-tune image"
        docker compose --project-name cleverly --env-file .env.example -f docker-compose.yml -f docker/finetune.yml build cleverly
        if ($LASTEXITCODE -ne 0) { Fail "Failed to build cleverly:finetune." }
    }
}

function Setup-Cleverly {
    Write-Step "First-run setup"
    Write-Host "Default runtime: sealed Docker volumes, local-only UI, no network during normal start." -ForegroundColor Green
    Write-Host "Connected setup will auto-pick a model from detected GPU memory unless -Model or OLLAMA_MODEL is set." -ForegroundColor Green
    Prep-Cleverly
    Write-Step "Stopping any existing Cleverly runtime"
    docker compose @ComposeArgs down
    if ($UseSealedData) {
        Write-Step "Sealing prepared data into Docker volumes"
        Seal-Data
    } else {
        Write-Host "Host folder data mode is enabled by explicit opt-out; skipping sealed-volume copy." -ForegroundColor Yellow
    }
    Write-Step "Launching Cleverly"
    Start-Cleverly
}

function Invoke-Doctor {
    $script:DoctorFailures = 0
    $script:DoctorWarnings = 0
    Write-Step "Cleverly doctor"
    Resolve-PrimaryModel | Out-Null

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

    foreach ($file in @("docker-compose.yml", "docker\ollama-offline.yml", "docker\sealed-data.yml", "docker\host-data.yml", "Cleverly.ps1", "Cleverly.cmd", ".env.example")) {
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
    if ((Test-FileHasText "docker-compose.yml" "cleverly_code_worker:") -and (Test-FileHasText "docker-compose.yml" 'network_mode: "none"')) {
        Write-DoctorOk "Code Workspace worker is configured with no Docker network"
    } else {
        Write-DoctorFail "Code Workspace worker no-network mode was not found"
    }
    if (Test-FileHasText "docker\entrypoint.sh" "I_ACCEPT_NETWORK_RISK") {
        Write-DoctorOk "Entrypoint requires explicit network break-glass"
    } else {
        Write-DoctorFail "Entrypoint network break-glass guard was not found"
    }
    if ($UseSealedData) {
        if (Test-FileHasText $SealedDataOverlay "cleverly-data:/app/data") {
            Write-DoctorOk "Sealed data mode is enabled by default"
        } else {
            Write-DoctorFail "Sealed data overlay is missing the app data volume"
        }
    } else {
        Write-DoctorWarn "Host folder data mode is enabled by explicit opt-out"
    }

    if ($script:DoctorFailures -eq 0) {
        if ($script:PrimaryModel) {
            Write-DoctorOk ("Primary model is set to " + $script:PrimaryModel + " via " + $script:PrimaryModelSource)
        } else {
            Write-DoctorWarn "No primary model is set; run connected prep or bundle with '-Model <ollama-tag>'"
        }
        if (Test-Image "cleverly:local") { Write-DoctorOk "Image cleverly:local is loaded" } else { Write-DoctorFail "Missing image cleverly:local" }
        if (Test-Image $env:OLLAMA_IMAGE) { Write-DoctorOk "Image $env:OLLAMA_IMAGE is loaded" } else { Write-DoctorFail "Missing image $env:OLLAMA_IMAGE" }
        if ($UseFineTune) {
            if (Test-Image $env:CLEVERLY_FINETUNE_IMAGE) { Write-DoctorOk "Image $env:CLEVERLY_FINETUNE_IMAGE is loaded" } else { Write-DoctorFail "Missing image $env:CLEVERLY_FINETUNE_IMAGE" }
        }
        foreach ($image in $SupportImages) {
            if (Test-Image $image) { Write-DoctorOk "Support image $image is loaded" } else { Write-DoctorWarn "Support image $image is not loaded; full Compose startup may need it" }
        }
        if ($UseSealedData) {
            foreach ($item in $SealedCopyPlan) {
                if (Test-Volume $item.Volume) {
                    Write-DoctorOk ("Sealed Docker volume " + $item.Volume + " exists")
                } else {
                    Write-DoctorWarn ("Sealed Docker volume " + $item.Volume + " is not created yet; run '.\Cleverly.ps1 seal-data' or start once")
                }
            }
        }
    }

    if ($UseSealedData) {
        if ($script:DoctorFailures -eq 0) {
            foreach ($item in $SealedCopyPlan) {
                if ((Test-DirectoryHasFiles $item.Source) -and -not (Test-Volume $item.Volume)) {
                    Write-DoctorWarn ("Host " + $item.Source + " has files but sealed volume " + $item.Volume + " is missing; run '.\Cleverly.ps1 seal-data' before relying on sealed startup")
                }
            }
        }
    } else {
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
    }

    $containers = @()
    if ($script:DoctorFailures -eq 0) {
        $containers = Get-CleverlyContainers
        if ($containers.Count -gt 0) {
            Write-DoctorOk ("Running Cleverly containers: " + ($containers -join ", "))
        } else {
            Write-DoctorWarn "No Cleverly containers are currently running"
        }
        if (($containers -contains "cleverly-ollama") -and $script:PrimaryModel) {
            if (Test-OllamaModelAvailable -Model $script:PrimaryModel -Seconds 5) {
                Write-DoctorOk ("Primary model " + $script:PrimaryModel + " is loaded in bundled Ollama")
            } else {
                Write-DoctorWarn ("Primary model " + $script:PrimaryModel + " was not found in bundled Ollama")
            }
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
    Set-BundlePrimaryModelEnv $bundlePath
    Copy-BundleItem "docker" (Join-Path $bundlePath "docker") | Out-Null
    Copy-BundleItem "config" (Join-Path $bundlePath "config") | Out-Null
    foreach ($doc in @("docs\offline-release.md", "docs\local-training-lab.md", "docs\external-agent-study-packs.md")) {
        Copy-BundleItem $doc (Join-Path $bundlePath $doc) | Out-Null
    }

    Write-Step "Copying prepared local model data"
    foreach ($dir in @(
        "data\ollama",
        "data\cleverly-primary-model.json",
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

    $sealArgs = if ($UseFineTune) { "seal-data -FineTune" } else { "seal-data" }
    $startArgs = if ($UseFineTune) { "start -FineTune" } else { "start" }
    Set-Content -LiteralPath (Join-Path $bundlePath "load-cleverly.cmd") -Encoding ASCII -Value @"
@echo off
setlocal
docker load -i "%~dp0cleverly-images.tar"
if errorlevel 1 pause
"@
    Set-Content -LiteralPath (Join-Path $bundlePath "seal-data.cmd") -Encoding ASCII -Value @"
@echo off
setlocal
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Cleverly.ps1" $sealArgs
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
.\Cleverly.ps1 bundle -AllowConnectedPrep -Model $script:PrimaryModel$(if ($UseFineTune) { " -FineTune" } else { "" })
````

Primary model:

````text
$script:PrimaryModel
````

Use it on the offline machine:

1. Install Docker Desktop.
2. Run `load-cleverly.cmd`.
3. Run `seal-data.cmd` once to copy bundled data into Docker volumes.
4. Run `start-cleverly.cmd`.
5. Open $Url.

The start script uses `--no-build` and `--pull never` through `Cleverly.ps1`.
Normal startup does not download images, packages, models, or study packs.
Runtime data is stored in Docker named volumes by default. Use `-HostData`
only when you intentionally want visible host-folder bind mounts.

Run a local check with:

````powershell
.\Cleverly.ps1 doctor$(if ($UseFineTune) { " -FineTune" } else { "" })
````
"@

    Write-Host ""
    Write-Host ("Offline bundle written to: " + $bundlePath) -ForegroundColor Green
    Write-Host "Copy that folder to the offline machine, then run load-cleverly.cmd, seal-data.cmd, and start-cleverly.cmd."
}

switch ($Action) {
    "setup" { Setup-Cleverly }
    "start" { Start-Cleverly }
    "stop" { Stop-Cleverly }
    "restart" { Stop-Cleverly; Start-Cleverly }
    "status" { Require-Docker; Show-Status }
    "open" { Start-Process $Url }
    "logs" { Require-Docker; docker logs -f --tail 200 cleverly }
    "prep" { Prep-Cleverly }
    "doctor" { Invoke-Doctor }
    "bundle" { New-CleverlyBundle }
    "seal-data" { Seal-Data }
}
