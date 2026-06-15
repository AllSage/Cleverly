#Requires -Version 5.1
<#
  Create a Cleverly release-candidate folder.

  This is the operator entrypoint for a real release. It wraps the lower-level
  build-offline-release.ps1 script, requires a clean tree by default, writes a
  release candidate note, and can zip the finished artifact folder.
#>

param(
    [string]$Version = "",
    [string]$Model = "",
    [double]$GpuGB = -1,
    [switch]$FineTune,
    [switch]$RequireSignature,
    [string]$CertificatePath = "",
    [string]$CertificatePasswordPath = "",
    [switch]$SkipTests,
    [switch]$SkipBundle,
    [switch]$SkipInstaller,
    [switch]$AllowDirty,
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Get-GitOutput {
    param([string[]]$Args)
    try {
        $out = & git @Args 2>$null
        if ($LASTEXITCODE -eq 0) { return ($out -join "`n").Trim() }
    } catch {
        return ""
    }
    return ""
}

function Get-PowerShellExe {
    foreach ($candidate in @("powershell", "pwsh")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            return $candidate
        }
    }
    throw "PowerShell executable not found"
}

Push-Location $Root
try {
    $PowerShellExe = Get-PowerShellExe
    $commit = Get-GitOutput @("rev-parse", "--short=12", "HEAD")
    if (-not $Version) {
        $Version = if ($commit) { "rc-$commit" } else { "rc-local" }
    }
    $status = Get-GitOutput @("status", "--short")
    if ($status -and -not $AllowDirty) {
        throw "Working tree is not clean. Commit changes first or pass -AllowDirty for a local test release."
    }

    $releaseRoot = Join-Path $Root "dist\release-candidates"
    $releaseDir = Join-Path $releaseRoot $Version
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    $args = @(
        "-ReleaseDir", $releaseDir,
        "-Image", "cleverly:release-$Version"
    )
    if ($Model) { $args += @("-Model", $Model) }
    if ($GpuGB -ge 0) { $args += @("-GpuGB", ([string]$GpuGB)) }
    if ($FineTune) { $args += "-FineTune" }
    if ($RequireSignature) { $args += "-RequireSignature" }
    if ($CertificatePath) { $args += @("-CertificatePath", $CertificatePath) }
    if ($CertificatePasswordPath) { $args += @("-CertificatePasswordPath", $CertificatePasswordPath) }
    if ($SkipTests) { $args += "-SkipTests" }
    if ($SkipBundle) { $args += "-SkipBundle" }
    if ($SkipInstaller) { $args += "-SkipInstaller" }

    & $PowerShellExe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\build-offline-release.ps1") @args
    if ($LASTEXITCODE -ne 0) { throw "build-offline-release.ps1 failed" }

    $notePath = Join-Path $releaseDir "RELEASE-CANDIDATE.txt"
    @(
        "Cleverly Release Candidate",
        "Version: $Version",
        "Git commit: $(Get-GitOutput @('rev-parse', 'HEAD'))",
        "Generated: $((Get-Date).ToString('o'))",
        "Model: $Model",
        "GPU GB override: $(if ($GpuGB -ge 0) { $GpuGB } else { 'auto' })",
        "",
        "Required target-machine proof:",
        "1. Copy this release folder to the offline machine.",
        "2. Run load-cleverly.cmd from the bundle when present.",
        "3. Run seal-data.cmd when prepared data/model files are included.",
        "4. Run powershell -NoLogo -NoProfile -File .\ci\fresh-machine-proof.ps1.",
        "5. Open release-dashboard.html and review all evidence.",
        "6. Keep fresh-machine-proof.json, model-integrity.json, SBOM, static-security report, and checksums with this release.",
        "7. Create or verify the annotated release tag with scripts\create-release-tag.ps1."
    ) | Set-Content -LiteralPath $notePath -Encoding UTF8

    if ($Zip) {
        $zipPath = Join-Path $releaseRoot "$Version.zip"
        if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
        Compress-Archive -Path (Join-Path $releaseDir "*") -DestinationPath $zipPath -Force
        Write-Host ("Release candidate zip: " + $zipPath) -ForegroundColor Green
    }

    Write-Host ("Release candidate folder: " + $releaseDir) -ForegroundColor Green
} finally {
    Pop-Location
}
