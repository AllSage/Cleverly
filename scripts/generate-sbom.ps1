#Requires -Version 5.1
<#
  Generate a local SBOM-style dependency snapshot for Cleverly.

  This script is intentionally offline-friendly. It reads local lockfiles,
  hashes release inputs, and inspects already-built Docker images when present.
#>

param(
    [string]$OutputPath = "dist\sbom\cleverly-sbom.json",
    [string]$DockerImage = "cleverly:local",
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$OutputFullPath = if ([System.IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $Root $OutputPath }
$DefaultChecksumName = "cleverly-sbom.json.sha256"

function Get-RepoCommandOutput {
    param([string[]]$Args)
    try {
        $out = & git @Args 2>$null
        if ($LASTEXITCODE -eq 0) { return ($out -join "`n").Trim() }
    } catch {
        return ""
    }
    return ""
}

function Get-FileHashRecord {
    param([string]$RelativePath)
    $path = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $path
    return [pscustomobject]@{
        path = $RelativePath
        sha256 = $hash.Hash.ToLowerInvariant()
        bytes = (Get-Item -LiteralPath $path).Length
    }
}

function Get-PythonFreeze {
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe"),
        "python"
    )
    foreach ($python in $candidates) {
        try {
            $out = & $python -m pip freeze --all 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{
                    source = $python
                    packages = @($out | Where-Object { $_ -and $_.Trim() })
                }
            }
        } catch {
            continue
        }
    }
    return [pscustomobject]@{ source = ""; packages = @() }
}

function Get-NpmLockPackages {
    $lockPath = Join-Path $Root "package-lock.json"
    if (-not (Test-Path -LiteralPath $lockPath)) { return @() }
    Add-Type -AssemblyName System.Web.Extensions
    $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $serializer.MaxJsonLength = [int]::MaxValue
    $lock = $serializer.DeserializeObject((Get-Content -LiteralPath $lockPath -Raw))
    if (-not $lock.ContainsKey("packages")) { return @() }
    $lockPackages = $lock["packages"]
    $packages = @()
    foreach ($key in $lockPackages.Keys) {
        $value = $lockPackages[$key]
        if (-not $value.ContainsKey("version")) { continue }
        $name = if ($value.ContainsKey("name") -and $value["name"]) { $value["name"] } else { $key -replace '^node_modules/', '' }
        if (-not $name) { continue }
        $packages += [pscustomobject]@{
            name = $name
            version = $value["version"]
            path = $key
        }
    }
    return $packages
}

function Get-DockerSnapshot {
    param([string]$Image)
    if ($SkipDocker) { return $null }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $null }

    & docker image inspect $Image 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        return [pscustomobject]@{
            image = $Image
            present = $false
            inspect = $null
            pip_freeze = @()
        }
    }

    $inspectRaw = & docker image inspect $Image 2>$null
    $freeze = & docker run --rm --network none $Image python -m pip freeze --all 2>$null
    return [pscustomobject]@{
        image = $Image
        present = $true
        inspect = (($inspectRaw -join "`n") | ConvertFrom-Json)
        pip_freeze = @($freeze | Where-Object { $_ -and $_.Trim() })
    }
}

Push-Location $Root
try {
    $hashes = @(
        "requirements.txt",
        "requirements.lock",
        "package.json",
        "package-lock.json",
        "Dockerfile",
        "docker-compose.yml",
        "Cleverly.ps1",
        "LICENSE"
    ) | ForEach-Object { Get-FileHashRecord $_ } | Where-Object { $_ }

    $payload = [pscustomobject]@{
        name = "Cleverly"
        generated_at = (Get-Date).ToString("o")
        git = [pscustomobject]@{
            commit = Get-RepoCommandOutput @("rev-parse", "HEAD")
            branch = Get-RepoCommandOutput @("rev-parse", "--abbrev-ref", "HEAD")
            status = Get-RepoCommandOutput @("status", "--short")
        }
        files = @($hashes)
        python = Get-PythonFreeze
        npm = [pscustomobject]@{
            source = "package-lock.json"
            packages = @(Get-NpmLockPackages)
        }
        docker = Get-DockerSnapshot -Image $DockerImage
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputFullPath) | Out-Null
    $payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputFullPath -Encoding UTF8
    $digest = Get-FileHash -Algorithm SHA256 -LiteralPath $OutputFullPath
    $shaPath = if ((Split-Path -Leaf $OutputFullPath) -eq "cleverly-sbom.json") {
        Join-Path (Split-Path -Parent $OutputFullPath) $DefaultChecksumName
    } else {
        "$OutputFullPath.sha256"
    }
    ("{0}  {1}" -f $digest.Hash.ToLowerInvariant(), (Split-Path -Leaf $OutputFullPath)) | Set-Content -LiteralPath $shaPath -Encoding ASCII
    Write-Host ("SBOM written: " + $OutputFullPath)
    Write-Host ("SBOM hash written: " + $shaPath)
} finally {
    Pop-Location
}
