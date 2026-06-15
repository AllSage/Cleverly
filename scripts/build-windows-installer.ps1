#Requires -Version 5.1
<#
  Build the Cleverly Windows installer.

  Requirements:
    - Inno Setup 6 (`iscc.exe`) on PATH.
    - Optional Windows SDK `signtool.exe` and a code-signing certificate.

  Examples:
    powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 -RequireSignature -CertificatePath .\certs\cleverly.pfx
#>

param(
    [string]$Version = "",
    [string]$OutputDir = "dist\installer",
    [string]$ReleaseChecklistPath = "",
    [string]$CertificatePath = "",
    [string]$CertificatePasswordPath = "",
    [securestring]$CertificatePassword,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$InstallerScript = Join-Path $Root "installer\Cleverly.iss"
$ResolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $Root $OutputDir }

function Fail([string]$Message) {
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    exit 1
}

function Resolve-InstallerVersion() {
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        return $Version
    }
    $packageJson = Join-Path $Root "package.json"
    if (Test-Path -LiteralPath $packageJson) {
        try {
            $package = Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json
            if ($package.version) {
                return [string]$package.version
            }
        } catch {
            Write-Warning ("Could not read package.json version: " + $_.Exception.Message)
        }
    }
    return "1.0.0"
}

function Get-GitCommit() {
    try {
        $commit = & git -C $Root rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($commit)) {
            return $commit.Trim()
        }
    } catch {}
    return "unknown"
}

$Version = Resolve-InstallerVersion
$OutputExe = Join-Path $ResolvedOutput ("CleverlySetup-{0}.exe" -f $Version)
$ResolvedChecklist = if ([System.IO.Path]::IsPathRooted($ReleaseChecklistPath)) {
    $ReleaseChecklistPath
} elseif (-not [string]::IsNullOrWhiteSpace($ReleaseChecklistPath)) {
    Join-Path $Root $ReleaseChecklistPath
} else {
    Join-Path $ResolvedOutput ("CleverlySetup-{0}.release-checklist.md" -f $Version)
}

function Write-ReleaseChecklist([string]$SignatureStatus) {
    $checklistDir = Split-Path -Parent $ResolvedChecklist
    if ($checklistDir) {
        New-Item -ItemType Directory -Force -Path $checklistDir | Out-Null
    }
    $lines = @(
        "# Cleverly Windows Release Checklist",
        "",
        "- Version: $Version",
        "- Installer: $OutputExe",
        "- Git commit: $(Get-GitCommit)",
        "- Signature status: $SignatureStatus",
        "- RequireSignature: $RequireSignature",
        "",
        "## Required gates",
        "- [ ] Run ``npm run build``.",
        "- [ ] Run ``powershell -NoLogo -NoProfile -File .\ci\fresh-machine-offline-smoke.ps1`` on a prepared offline test machine.",
        "- [ ] Run Offline Control: Test No Internet.",
        "- [ ] Export the Offline Control HTML report.",
        "- [ ] Confirm the installer is Authenticode-signed for release builds.",
        "- [ ] Confirm the README sensitive machine checklist matches the release.",
        ""
    )
    Set-Content -LiteralPath $ResolvedChecklist -Value $lines -Encoding UTF8
    Write-Host ("Release checklist written to: " + $ResolvedChecklist) -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath $InstallerScript)) {
    Fail "Missing installer script: $InstallerScript"
}

$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    Fail "Inno Setup compiler (iscc.exe) was not found. Install Inno Setup 6 on a connected build/signing workstation."
}

New-Item -ItemType Directory -Force -Path $ResolvedOutput | Out-Null

Push-Location $Root
try {
    & $iscc.Source "/DMyAppVersion=$Version" "/O$ResolvedOutput" $InstallerScript
    if ($LASTEXITCODE -ne 0) {
        Fail "Inno Setup failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $OutputExe)) {
    Fail "Expected installer was not produced: $OutputExe"
}

if ([string]::IsNullOrWhiteSpace($CertificatePath)) {
    if ($RequireSignature) {
        Fail "A certificate is required because -RequireSignature was set."
    }
    Write-Warning "Built unsigned installer. Re-run with -CertificatePath and -RequireSignature for release builds."
    Write-ReleaseChecklist "Unsigned"
    Write-Host ("Installer written to: " + $OutputExe) -ForegroundColor Green
    exit 0
}

$certFullPath = if ([System.IO.Path]::IsPathRooted($CertificatePath)) { $CertificatePath } else { Join-Path $Root $CertificatePath }
if (-not (Test-Path -LiteralPath $certFullPath)) {
    Fail "Certificate was not found: $certFullPath"
}

$certPasswordFullPath = ""
if (-not [string]::IsNullOrWhiteSpace($CertificatePasswordPath)) {
    $certPasswordFullPath = if ([System.IO.Path]::IsPathRooted($CertificatePasswordPath)) { $CertificatePasswordPath } else { Join-Path $Root $CertificatePasswordPath }
    if (-not (Test-Path -LiteralPath $certPasswordFullPath -PathType Leaf)) {
        Fail "Certificate password file was not found: $certPasswordFullPath"
    }
}

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    Fail "signtool.exe was not found. Install the Windows SDK on the signing workstation."
}

$plainPassword = ""
if ($certPasswordFullPath) {
    $plainPassword = ((Get-Content -LiteralPath $certPasswordFullPath -Raw) -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($plainPassword)) {
        Fail "Certificate password file is empty: $certPasswordFullPath"
    }
} elseif ($CertificatePassword) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($CertificatePassword)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
} else {
    $prompted = Read-Host "PFX password" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($prompted)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

& $signtool.Source sign /fd SHA256 /tr $TimestampUrl /td SHA256 /f $certFullPath /p $plainPassword $OutputExe
if ($LASTEXITCODE -ne 0) {
    Fail "signtool failed with exit code $LASTEXITCODE."
}

$signature = Get-AuthenticodeSignature -LiteralPath $OutputExe
if ($signature.Status -ne "Valid") {
    Fail ("Installer signature validation failed: " + $signature.Status)
}

Write-ReleaseChecklist $signature.Status
Write-Host ("Signed installer written to: " + $OutputExe) -ForegroundColor Green
