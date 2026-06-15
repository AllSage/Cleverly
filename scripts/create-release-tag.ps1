#Requires -Version 5.1
<#
  Create an annotated Cleverly release or release-candidate tag.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$Message = "",
    [switch]$Push,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Push-Location $Root
try {
    if (-not $AllowDirty) {
        $status = (& git status --porcelain=v1) -join "`n"
        if (-not [string]::IsNullOrWhiteSpace($status)) {
            throw "Working tree is not clean. Commit changes before tagging or pass -AllowDirty for a local test tag."
        }
    }

    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git rev-parse --verify "refs/tags/$Version" 1>$null 2>$null
        $tagExists = $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($tagExists) {
        throw "Tag already exists: $Version"
    }

    $tagMessage = if ($Message) { $Message } else { "Cleverly $Version" }
    & git tag -a $Version -m $tagMessage
    if ($LASTEXITCODE -ne 0) { throw "git tag failed" }
    Write-Host ("Created tag: " + $Version) -ForegroundColor Green

    if ($Push) {
        & git push origin $Version
        if ($LASTEXITCODE -ne 0) { throw "git push tag failed" }
        Write-Host ("Pushed tag: " + $Version) -ForegroundColor Green
    }
} finally {
    Pop-Location
}
