#Requires -Version 5.1
<#
  Write a local model integrity manifest.

  When -ModelPath is supplied, the script hashes files under that path. When a
  model path is not available, it still records model/source/license/VRAM
  metadata so the release has an explicit primary-model record.
#>

param(
    [string]$Model = "",
    [string]$SourceUrl = "",
    [string]$License = "",
    [string]$ExpectedSize = "",
    [double]$ExpectedGpuGB = -1,
    [string]$ModelPath = "",
    [string]$OutputPath = "dist\release\model-integrity.json"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$OutputFullPath = if ([System.IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $Root $OutputPath }

function Read-PrimaryModel() {
    $path = Join-Path $Root "data\cleverly-primary-model.json"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-ModelFiles([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return @() }
    $full = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $Root $Path }
    if (-not (Test-Path -LiteralPath $full)) { return @() }
    $base = [System.IO.Path]::GetFullPath($full)
    return @(
        Get-ChildItem -LiteralPath $base -File -Recurse | Sort-Object FullName | ForEach-Object {
            $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
            [pscustomobject]@{
                path = $_.FullName.Substring($base.Length).TrimStart('\', '/').Replace('\', '/')
                bytes = $_.Length
                sha256 = $hash.Hash.ToLowerInvariant()
            }
        }
    )
}

$primary = Read-PrimaryModel
if (-not $Model -and $primary -and $primary.primary_model) {
    $Model = [string]$primary.primary_model
}
if (-not $SourceUrl -and $primary -and $primary.source) {
    $SourceUrl = [string]$primary.source
}

$files = Get-ModelFiles $ModelPath
$totalBytes = 0
foreach ($file in $files) { $totalBytes += [int64]$file.bytes }
$verificationState = if ($files.Count -gt 0) { "hashed" } elseif ($Model) { "metadata-only" } else { "missing" }

$payload = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    model = $Model
    source_url = $SourceUrl
    license = $License
    expected_size = $ExpectedSize
    expected_gpu_gb = if ($ExpectedGpuGB -ge 0) { $ExpectedGpuGB } else { $null }
    model_path = $ModelPath
    verification_state = $verificationState
    file_count = $files.Count
    total_bytes = $totalBytes
    files = $files
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputFullPath) | Out-Null
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputFullPath -Encoding UTF8
$digest = Get-FileHash -Algorithm SHA256 -LiteralPath $OutputFullPath
("{0}  {1}" -f $digest.Hash.ToLowerInvariant(), (Split-Path -Leaf $OutputFullPath)) |
    Set-Content -LiteralPath "$OutputFullPath.sha256" -Encoding ASCII
Write-Host ("Model integrity manifest written: " + $OutputFullPath)
