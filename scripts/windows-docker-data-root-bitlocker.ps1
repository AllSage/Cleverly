#Requires -Version 5.1
<#
  Verify or enable BitLocker protection for the Windows drive that stores the
  Docker Desktop data-root VHDX.

  This protects Docker volumes at rest because Docker Desktop stores
  /var/lib/docker inside docker_data.vhdx on the Windows host.

  Examples:
    powershell -ExecutionPolicy Bypass -File .\scripts\windows-docker-data-root-bitlocker.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\windows-docker-data-root-bitlocker.ps1 -RequireEncrypted
    powershell -ExecutionPolicy Bypass -File .\scripts\windows-docker-data-root-bitlocker.ps1 -Enable -RecoveryKeyPath E:\Cleverly-BitLocker-RecoveryKey.txt
#>
param(
    [switch]$Enable,
    [switch]$RequireEncrypted,
    [string]$RecoveryKeyPath
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
}

function Fail([string]$Message, [int]$Code = 1) {
    Write-Host ""
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    exit $Code
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DockerDesktopDataDisk {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Docker\wsl\disk\docker_data.vhdx"),
        (Join-Path $env:LOCALAPPDATA "Docker\wsl\data\ext4.vhdx")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Get-Item -LiteralPath $candidate).FullName
        }
    }

    $found = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA "Docker") -Recurse -Filter "*.vhdx" -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        Select-Object -First 1
    if ($found) { return $found.FullName }

    Fail "Could not find Docker Desktop's WSL data VHDX under $env:LOCALAPPDATA\Docker."
}

function Get-BitLockerState([string]$MountPoint) {
    try {
        return Get-BitLockerVolume -MountPoint $MountPoint
    } catch {
        if (-not (Test-IsAdministrator)) {
            Fail "BitLocker status requires an elevated PowerShell session. Re-run this script as Administrator."
        }
        throw
    }
}

if ($Enable -and -not (Test-IsAdministrator)) {
    Fail "Enabling BitLocker requires an elevated PowerShell session. Re-run this script as Administrator."
}

if ($Enable -and [string]::IsNullOrWhiteSpace($RecoveryKeyPath)) {
    Fail "Pass -RecoveryKeyPath with a path on a removable drive or separate secure location. Do not rely on memory for the recovery key."
}

Write-Step "Finding Docker Desktop data disk"
$dockerDataDisk = Get-DockerDesktopDataDisk
$mountPoint = [System.IO.Path]::GetPathRoot($dockerDataDisk)
Write-Host ("Docker Desktop data disk: " + $dockerDataDisk)
Write-Host ("Windows volume holding Docker data: " + $mountPoint)

Write-Step "Checking BitLocker status"
$volume = Get-BitLockerState -MountPoint $mountPoint
$isProtected = ($volume.ProtectionStatus -eq "On") -or ($volume.EncryptionPercentage -gt 0)

Write-Host ("Volume status: " + $volume.VolumeStatus)
Write-Host ("Protection status: " + $volume.ProtectionStatus)
Write-Host ("Encryption: " + $volume.EncryptionPercentage + "%")
Write-Host ("Encryption method: " + $volume.EncryptionMethod)

if ($isProtected) {
    Write-Host ""
    Write-Host "Docker Desktop data is on a BitLocker-protected volume." -ForegroundColor Green
    exit 0
}

if (-not $Enable) {
    $message = "Docker Desktop data is not on a BitLocker-protected volume."
    if ($RequireEncrypted) { Fail $message 2 }
    Write-Host ""
    Write-Host ("WARNING: " + $message) -ForegroundColor Yellow
    Write-Host "Run this script as Administrator with -Enable and -RecoveryKeyPath to turn on BitLocker for the containing Windows volume."
    exit 0
}

$systemDriveRoot = $env:SystemDrive + "\"
if ($mountPoint -ne $systemDriveRoot) {
    Fail "Automated BitLocker enable is limited to the Windows OS drive. Use the Windows BitLocker UI to protect $mountPoint, then rerun this script to verify it."
}

$normalizedRecoveryPath = if ([System.IO.Path]::IsPathRooted($RecoveryKeyPath)) {
    $RecoveryKeyPath
} else {
    Join-Path (Get-Location) $RecoveryKeyPath
}
$recoveryParent = Split-Path -Parent $normalizedRecoveryPath
if (-not (Test-Path -LiteralPath $recoveryParent)) {
    New-Item -ItemType Directory -Force -Path $recoveryParent | Out-Null
}

Write-Step "Adding BitLocker recovery protector"
$protectorResult = Add-BitLockerKeyProtector -MountPoint $mountPoint -RecoveryPasswordProtector
$recoveryProtector = $protectorResult.KeyProtector |
    Where-Object { $_.KeyProtectorType -eq "RecoveryPassword" -and $_.RecoveryPassword } |
    Select-Object -Last 1
if (-not $recoveryProtector) {
    Fail "BitLocker did not return a recovery password protector."
}

Set-Content -LiteralPath $normalizedRecoveryPath -Encoding ASCII -Value @"
Cleverly Docker data-root BitLocker recovery key

Docker data disk:
$dockerDataDisk

Protected Windows volume:
$mountPoint

Recovery key protector ID:
$($recoveryProtector.KeyProtectorId)

Recovery password:
$($recoveryProtector.RecoveryPassword)

Store this file somewhere separate from the protected computer.
"@
Write-Host ("Recovery key written to: " + $normalizedRecoveryPath) -ForegroundColor Yellow

Write-Step "Starting BitLocker encryption"
$tpm = $null
if (Get-Command Get-Tpm -ErrorAction SilentlyContinue) {
    $tpm = Get-Tpm
}
if ($mountPoint -eq $systemDriveRoot -and (-not $tpm -or -not $tpm.TpmReady)) {
    Fail "The OS drive requires a ready TPM for this automated path. Use the Windows BitLocker UI or prepare TPM first."
}

Enable-BitLocker -MountPoint $mountPoint -EncryptionMethod XtsAes256 -UsedSpaceOnly -TpmProtector -SkipHardwareTest

Write-Host ""
Write-Host "BitLocker encryption has started. It runs in the background." -ForegroundColor Green
Write-Host "Run this script again to check progress."
