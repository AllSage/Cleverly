#Requires -Version 5.1
<#
  Generate a local CycloneDX SBOM dependency snapshot for Cleverly.

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

function Get-PythonExe {
    foreach ($python in @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe"),
        "python"
    )) {
        try {
            & $python --version 1>$null 2>$null
            if ($LASTEXITCODE -eq 0) { return $python }
        } catch {
            continue
        }
    }
    return ""
}

function Get-NpmLockPackages {
    $lockPath = Join-Path $Root "package-lock.json"
    if (-not (Test-Path -LiteralPath $lockPath)) { return @() }
    $python = Get-PythonExe
    if (-not $python) { return @() }
    $script = @"
import json
from pathlib import Path
lock = json.loads(Path(r'''$lockPath''').read_text(encoding='utf-8'))
packages = []
for key, value in (lock.get('packages') or {}).items():
    version = value.get('version')
    if not version:
        continue
    name = value.get('name') or key.removeprefix('node_modules/')
    if not name:
        continue
    packages.append({'name': name, 'version': version, 'path': key})
print(json.dumps(packages))
"@
    try {
        $raw = $script | & $python -
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return @() }
        return @($raw | ConvertFrom-Json)
    } catch {
        return @()
    }
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

function ConvertTo-BomRef {
    param(
        [string]$Type,
        [string]$Name,
        [string]$Version = ""
    )
    $safeName = (($Name -replace '[^A-Za-z0-9._~:/@+-]', '-') -replace '-+', '-').Trim('-')
    $safeVersion = (($Version -replace '[^A-Za-z0-9._~:/@+-]', '-') -replace '-+', '-').Trim('-')
    if ($safeVersion) { return ("{0}:{1}@{2}" -f $Type, $safeName, $safeVersion) }
    return ("{0}:{1}" -f $Type, $safeName)
}

function Convert-PythonFreezeToComponents {
    param([string[]]$Packages)
    $components = New-Object System.Collections.Generic.List[object]
    foreach ($line in @($Packages)) {
        $text = ([string]$line).Trim()
        if (-not $text -or $text.StartsWith("#") -or $text.StartsWith("-")) { continue }

        $name = ""
        $version = ""
        if ($text -match '^([^=<>!~\s]+)===(.+)$') {
            $name = $Matches[1]
            $version = $Matches[2]
        } elseif ($text -match '^([^=<>!~\s]+)==(.+)$') {
            $name = $Matches[1]
            $version = $Matches[2]
        } elseif ($text -match '^([A-Za-z0-9_.-]+)\s+@\s+(.+)$') {
            $name = $Matches[1]
            $version = $Matches[2]
        } else {
            $name = $text
        }

        $normalized = $name.ToLowerInvariant()
        $component = [ordered]@{
            type = "library"
            "bom-ref" = (ConvertTo-BomRef -Type "pkg:pypi" -Name $normalized -Version $version)
            name = $name
            version = $version
            scope = "required"
            purl = if ($version -and -not ($version -match '[:\\/]')) { "pkg:pypi/$normalized@$version" } else { "" }
            properties = @(
                [ordered]@{ name = "cleverly:source"; value = "pip freeze --all" }
            )
        }
        if (-not $component.purl) { $component.Remove("purl") }
        $components.Add([pscustomobject]$component) | Out-Null
    }
    return $components.ToArray()
}

function Convert-NpmLockToComponents {
    param([object[]]$Packages)
    $components = New-Object System.Collections.Generic.List[object]
    foreach ($package in @($Packages)) {
        $name = [string]$package.name
        $version = [string]$package.version
        if (-not $name -or -not $version) { continue }
        $encodedName = $name -replace '^@', '%40'
        $components.Add([pscustomobject]@{
            type = "library"
            "bom-ref" = (ConvertTo-BomRef -Type "pkg:npm" -Name $name -Version $version)
            name = $name
            version = $version
            scope = "required"
            purl = "pkg:npm/$encodedName@$version"
            properties = @(
                [ordered]@{ name = "cleverly:source"; value = "package-lock.json" },
                [ordered]@{ name = "cleverly:path"; value = [string]$package.path }
            )
        }) | Out-Null
    }
    return $components.ToArray()
}

function Convert-FilesToComponents {
    param([object[]]$Files)
    $components = New-Object System.Collections.Generic.List[object]
    foreach ($file in @($Files)) {
        $relative = [string]$file.path
        if (-not $relative) { continue }
        $components.Add([pscustomobject]@{
            type = "file"
            "bom-ref" = (ConvertTo-BomRef -Type "file" -Name ($relative.Replace('\', '/')))
            name = $relative.Replace('\', '/')
            hashes = @(
                [ordered]@{
                    alg = "SHA-256"
                    content = [string]$file.sha256
                }
            )
            properties = @(
                [ordered]@{ name = "cleverly:bytes"; value = [string]$file.bytes }
            )
        }) | Out-Null
    }
    return $components.ToArray()
}

function Convert-DockerSnapshotToComponent {
    param([object]$Docker)
    if (-not $Docker) { return $null }

    $properties = @(
        [ordered]@{ name = "cleverly:source"; value = "docker image inspect" },
        [ordered]@{ name = "cleverly:present"; value = [string][bool]$Docker.present }
    )
    $version = ""
    if ($Docker.present -and $Docker.inspect) {
        $imageId = [string]$Docker.inspect.Id
        if ($imageId) {
            $version = $imageId
            $properties += [ordered]@{ name = "cleverly:image-id"; value = $imageId }
        }
    }

    return [pscustomobject]@{
        type = "container"
        "bom-ref" = (ConvertTo-BomRef -Type "container" -Name ([string]$Docker.image) -Version $version)
        name = [string]$Docker.image
        version = $version
        properties = $properties
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

    $gitCommit = Get-RepoCommandOutput @("rev-parse", "HEAD")
    $gitBranch = Get-RepoCommandOutput @("rev-parse", "--abbrev-ref", "HEAD")
    $gitStatus = Get-RepoCommandOutput @("status", "--short")
    $pythonFreeze = Get-PythonFreeze
    $npmPackages = @(Get-NpmLockPackages)
    $dockerSnapshot = Get-DockerSnapshot -Image $DockerImage
    $applicationVersion = if ($gitCommit) { $gitCommit } else { "local" }
    $serialNumber = "urn:uuid:{0}" -f ([guid]::NewGuid().Guid)

    $components = New-Object System.Collections.Generic.List[object]
    foreach ($component in @(Convert-FilesToComponents -Files @($hashes))) { $components.Add($component) | Out-Null }
    foreach ($component in @(Convert-PythonFreezeToComponents -Packages @($pythonFreeze.packages))) { $components.Add($component) | Out-Null }
    foreach ($component in @(Convert-NpmLockToComponents -Packages @($npmPackages))) { $components.Add($component) | Out-Null }
    $dockerComponent = Convert-DockerSnapshotToComponent -Docker $dockerSnapshot
    if ($dockerComponent) { $components.Add($dockerComponent) | Out-Null }

    $payload = [pscustomobject]@{
        bomFormat = "CycloneDX"
        specVersion = "1.5"
        serialNumber = $serialNumber
        version = 1
        metadata = [pscustomobject]@{
            timestamp = (Get-Date).ToString("o")
            tools = @(
                [pscustomobject]@{
                    vendor = "AllSage"
                    name = "Cleverly generate-sbom.ps1"
                    version = "1"
                }
            )
            component = [pscustomobject]@{
                type = "application"
                "bom-ref" = (ConvertTo-BomRef -Type "pkg:generic" -Name "cleverly" -Version $gitCommit)
                name = "Cleverly"
                version = $applicationVersion
            }
            properties = @(
                [ordered]@{ name = "cleverly:generated-by"; value = "scripts/generate-sbom.ps1" },
                [ordered]@{ name = "cleverly:git-commit"; value = $gitCommit },
                [ordered]@{ name = "cleverly:git-branch"; value = $gitBranch },
                [ordered]@{ name = "cleverly:git-status"; value = $gitStatus },
                [ordered]@{ name = "cleverly:python-source"; value = [string]$pythonFreeze.source },
                [ordered]@{ name = "cleverly:npm-source"; value = "package-lock.json" },
                [ordered]@{ name = "cleverly:docker-image"; value = $DockerImage }
            )
        }
        components = $components.ToArray()
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
