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
    [string]$Version = "1.0.0",
    [string]$OutputDir = "dist\installer",
    [string]$CertificatePath = "",
    [securestring]$CertificatePassword,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$InstallerScript = Join-Path $Root "installer\Cleverly.iss"
$ResolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $Root $OutputDir }
$OutputExe = Join-Path $ResolvedOutput ("CleverlySetup-{0}.exe" -f $Version)

function Fail([string]$Message) {
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    exit 1
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
    Write-Host ("Installer written to: " + $OutputExe) -ForegroundColor Green
    exit 0
}

$certFullPath = if ([System.IO.Path]::IsPathRooted($CertificatePath)) { $CertificatePath } else { Join-Path $Root $CertificatePath }
if (-not (Test-Path -LiteralPath $certFullPath)) {
    Fail "Certificate was not found: $certFullPath"
}

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    Fail "signtool.exe was not found. Install the Windows SDK on the signing workstation."
}

$plainPassword = ""
if ($CertificatePassword) {
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

Write-Host ("Signed installer written to: " + $OutputExe) -ForegroundColor Green
