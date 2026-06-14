#Requires -Version 5.1
<#
  Verify a Cleverly Windows installer Authenticode signature.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [switch]$RequireTrusted
)

$ErrorActionPreference = "Stop"
$FullPath = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path (Get-Location) $Path }
if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
    throw "Installer not found: $FullPath"
}

$signature = Get-AuthenticodeSignature -LiteralPath $FullPath
$result = [pscustomobject]@{
    path = $FullPath
    status = [string]$signature.Status
    status_message = [string]$signature.StatusMessage
    signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { "" }
    thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { "" }
}

$result | Format-List
if ($RequireTrusted -and $signature.Status -ne "Valid") {
    throw "Installer signature is not trusted: $($signature.Status)"
}
