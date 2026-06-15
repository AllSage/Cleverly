#Requires -Version 5.1
<#
  Create a local self-signed Authenticode code-signing certificate for testing
  the Cleverly Windows installer signing path.

  This is for local signing workflow validation only. It is not a public trust
  certificate and should not be used as a production distribution identity.
#>

param(
    [string]$Subject = "CN=Cleverly Local Test Code Signing",
    [string]$OutputPath = "dist\signing\cleverly-local-test-codesign.pfx",
    [string]$PublicCertificatePath = "",
    [string]$PasswordPath = "dist\signing\cleverly-local-test-codesign.password.txt",
    [string]$ManifestPath = "dist\signing\cleverly-local-test-codesign.manifest.json",
    [int]$Years = 2,
    [securestring]$PfxPassword,
    [switch]$NoPasswordFile,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Resolve-RepoPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $Root $Path
}

function New-LocalPassword {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return ([Convert]::ToBase64String($bytes).TrimEnd("=") + "!Aa1")
}

if (-not (Get-Command New-SelfSignedCertificate -ErrorAction SilentlyContinue)) {
    throw "New-SelfSignedCertificate is not available. Run this script in Windows PowerShell on the signing workstation."
}

$OutputFullPath = Resolve-RepoPath $OutputPath
$PasswordFullPath = Resolve-RepoPath $PasswordPath
$ManifestFullPath = Resolve-RepoPath $ManifestPath
$PublicFullPath = if ($PublicCertificatePath) {
    Resolve-RepoPath $PublicCertificatePath
} else {
    [System.IO.Path]::ChangeExtension($OutputFullPath, ".cer")
}

foreach ($path in @($OutputFullPath, $PasswordFullPath, $ManifestFullPath, $PublicFullPath)) {
    $dir = Split-Path -Parent $path
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}

foreach ($path in @($OutputFullPath, $PublicFullPath, $ManifestFullPath)) {
    if ((Test-Path -LiteralPath $path) -and -not $Force) {
        throw "Output already exists: $path. Pass -Force to replace local test signing material."
    }
}
if ((Test-Path -LiteralPath $PasswordFullPath) -and -not $Force -and -not $NoPasswordFile) {
    throw "Password file already exists: $PasswordFullPath. Pass -Force to replace local test signing material."
}

$generatedPassword = ""
if (-not $PfxPassword) {
    $generatedPassword = New-LocalPassword
    $PfxPassword = ConvertTo-SecureString -String $generatedPassword -AsPlainText -Force
}

$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -KeyAlgorithm RSA `
    -KeyLength 3072 `
    -HashAlgorithm SHA256 `
    -KeyUsage DigitalSignature `
    -KeyExportPolicy Exportable `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears($Years)

try {
    Export-PfxCertificate -Cert $cert -FilePath $OutputFullPath -Password $PfxPassword -Force:$Force | Out-Null
    Export-Certificate -Cert $cert -FilePath $PublicFullPath -Force:$Force | Out-Null

    if ($generatedPassword -and -not $NoPasswordFile) {
        Set-Content -LiteralPath $PasswordFullPath -Value $generatedPassword -Encoding ASCII
    }

    $manifest = [pscustomobject]@{
        subject = $cert.Subject
        thumbprint = $cert.Thumbprint
        not_before = $cert.NotBefore.ToString("o")
        not_after = $cert.NotAfter.ToString("o")
        pfx_path = $OutputFullPath
        public_certificate_path = $PublicFullPath
        password_path = if ($generatedPassword -and -not $NoPasswordFile) { $PasswordFullPath } else { "" }
        intended_use = "Local Cleverly installer signing workflow validation only"
        production_trusted = $false
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ManifestFullPath -Encoding UTF8

    Write-Host ("PFX written: " + $OutputFullPath) -ForegroundColor Green
    Write-Host ("Public certificate written: " + $PublicFullPath) -ForegroundColor Green
    Write-Host ("Manifest written: " + $ManifestFullPath) -ForegroundColor Green
    if ($generatedPassword -and -not $NoPasswordFile) {
        Write-Host ("Password file written: " + $PasswordFullPath) -ForegroundColor Yellow
        Write-Host "This is local ignored signing material. Protect or delete it after testing." -ForegroundColor Yellow
    }
} catch {
    throw
}
