#Requires -Version 5.1
<#
  Build the reviewed Cleverly offline release artifact set.

  Run this on a connected, non-sensitive release workstation after selecting
  the model to bundle. The produced bundle is intended to run offline.
#>

param(
    [string]$ReleaseDir = "dist\release",
    [string]$BundleDir = "dist\cleverly-offline-bundle",
    [string]$Image = "cleverly:release-smoke",
    [switch]$SkipTests,
    [switch]$SkipBundle,
    [switch]$SkipInstaller,
    [switch]$FineTune,
    [switch]$RequireSignature,
    [string]$CertificatePath = "",
    [string]$Model = "",
    [double]$GpuGB = -1
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReleaseFullPath = if ([System.IO.Path]::IsPathRooted($ReleaseDir)) { $ReleaseDir } else { Join-Path $Root $ReleaseDir }
$BundleFullPath = if ([System.IO.Path]::IsPathRooted($BundleDir)) { $BundleDir } else { Join-Path $Root $BundleDir }

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Block
    )
    Write-Host ("==> " + $Name) -ForegroundColor Cyan
    & $Block
    Write-Host ("ok: " + $Name) -ForegroundColor Green
}

function Get-GitValue {
    param([string[]]$Args)
    try {
        $out = & git @Args 2>$null
        if ($LASTEXITCODE -eq 0) { return ($out -join "`n").Trim() }
    } catch {
        return ""
    }
    return ""
}

function Get-PythonExe {
    foreach ($candidate in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe", "python")) {
        if ($candidate -eq "python") { return "python" }
        $path = if ([System.IO.Path]::IsPathRooted($candidate)) { $candidate } else { Join-Path $Root $candidate }
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return "python"
}

function Write-Checksums {
    param([string]$Path)
    $checksumPath = Join-Path $ReleaseFullPath "checksums.sha256"
    $checksumFullPath = [System.IO.Path]::GetFullPath($checksumPath)
    $rows = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $Path) {
        Get-ChildItem -LiteralPath $Path -File -Recurse | Where-Object {
            [System.IO.Path]::GetFullPath($_.FullName) -ne $checksumFullPath
        } | Sort-Object FullName | ForEach-Object {
            $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
            $relative = $_.FullName.Substring($ReleaseFullPath.Length).TrimStart('\', '/')
            if (-not $relative) { $relative = $_.Name }
            $rows.Add(("{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $relative.Replace('\', '/'))) | Out-Null
        }
    }
    $rows | Set-Content -LiteralPath $checksumPath -Encoding ASCII
    return $checksumPath
}

Push-Location $Root
try {
    New-Item -ItemType Directory -Force -Path $ReleaseFullPath | Out-Null

    if (-not $SkipTests) {
        Invoke-Step "Frontend syntax checks" {
            foreach ($file in @(
                "static/js/offlineControl.js",
                "static/js/codeWorkspace.js",
                "static/js/setupWizard.js",
                "static/js/tutorials.js"
            )) {
                & node --check $file
                if ($LASTEXITCODE -ne 0) { throw "node --check failed for $file" }
            }
        }

        Invoke-Step "Python regression tests" {
            $python = Get-PythonExe
            & $python -m pytest -q
            if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
        }
    }

    Invoke-Step "Local SBOM" {
        & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\generate-sbom.ps1") -OutputPath (Join-Path $ReleaseFullPath "cleverly-sbom.json")
        if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed" }
    }

    Invoke-Step "Static security checks" {
        & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\run-static-security.ps1") -ReportPath (Join-Path $ReleaseFullPath "static-security.json")
        if ($LASTEXITCODE -ne 0) { throw "static security checks failed" }
    }

    Invoke-Step "No-network container smoke" {
        & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "ci\no-network-container-smoke.ps1") -Image $Image
        if ($LASTEXITCODE -ne 0) { throw "no-network container smoke failed" }
        $smoke = Join-Path $Root "dist\no-network-container-smoke.json"
        if (Test-Path -LiteralPath $smoke) {
            Copy-Item -LiteralPath $smoke -Destination (Join-Path $ReleaseFullPath "no-network-container-smoke.json") -Force
        }
    }

    if (-not $SkipBundle) {
        Invoke-Step "Offline bundle" {
            $args = @("bundle", "-AllowConnectedPrep")
            if ($FineTune) { $args += "-FineTune" }
            if ($Model) { $args += @("-Model", $Model) }
            if ($GpuGB -ge 0) { $args += @("-GpuGB", ([string]$GpuGB)) }
            & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "Cleverly.ps1") @args
            if ($LASTEXITCODE -ne 0) { throw "bundle failed" }
            if (Test-Path -LiteralPath $BundleFullPath) {
                Copy-Item -LiteralPath $BundleFullPath -Destination (Join-Path $ReleaseFullPath "cleverly-offline-bundle") -Recurse -Force
            }
        }
    }

    if (-not $SkipInstaller) {
        Invoke-Step "Windows installer" {
            $args = @()
            if ($RequireSignature) { $args += "-RequireSignature" }
            if ($CertificatePath) { $args += @("-CertificatePath", $CertificatePath) }
            & powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\build-windows-installer.ps1") @args
            if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
            $installerOut = Join-Path $Root "dist\installer"
            if (Test-Path -LiteralPath $installerOut) {
                Copy-Item -LiteralPath $installerOut -Destination (Join-Path $ReleaseFullPath "installer") -Recurse -Force
            }
        }
    }

    $manifest = [pscustomobject]@{
        name = "Cleverly offline release"
        generated_at = (Get-Date).ToString("o")
        git_commit = Get-GitValue @("rev-parse", "HEAD")
        git_branch = Get-GitValue @("rev-parse", "--abbrev-ref", "HEAD")
        image = $Image
        model = $Model
        gpu_gb = if ($GpuGB -ge 0) { $GpuGB } else { $null }
        skipped = [pscustomobject]@{
            tests = [bool]$SkipTests
            bundle = [bool]$SkipBundle
            installer = [bool]$SkipInstaller
        }
        reports = @(
            "cleverly-sbom.json",
            "cleverly-sbom.json.sha256",
            "static-security.json",
            "no-network-container-smoke.json",
            "checksums.sha256"
        )
    }
    $manifestPath = Join-Path $ReleaseFullPath "release-manifest.json"
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $checksumPath = Write-Checksums -Path $ReleaseFullPath

    Write-Host ("Release manifest: " + $manifestPath) -ForegroundColor Green
    Write-Host ("Release checksums: " + $checksumPath) -ForegroundColor Green
} finally {
    Pop-Location
}
