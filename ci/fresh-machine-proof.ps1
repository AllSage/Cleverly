#Requires -Version 5.1
<#
  Fresh-machine proof wrapper for an offline Cleverly target.

  Run this from the copied Cleverly release folder on the target computer. It
  verifies the local bundle shape, confirms Compose is configured not to pull,
  runs the fresh-machine offline smoke, and writes a hashed proof report.
#>

param(
    [string]$BundlePath = ".",
    [switch]$FineTune,
    [switch]$SkipRestart,
    [string]$ReportPath = "dist\fresh-machine-proof.json"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BundleFullPath = if ([System.IO.Path]::IsPathRooted($BundlePath)) { $BundlePath } else { Join-Path $Root $BundlePath }
$ReportFullPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $Root $ReportPath }
$DefaultProofHashName = "fresh-machine-proof.json.sha256"
$Results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Name, [string]$Status, [string]$Detail) {
    $Results.Add([pscustomobject]@{ name = $Name; status = $Status; detail = $Detail }) | Out-Null
    $color = if ($Status -eq "ok") { "Green" } elseif ($Status -eq "warn") { "Yellow" } else { "Red" }
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Test-FilePresent([string]$RelativePath) {
    $path = Join-Path $Root $RelativePath
    if (Test-Path -LiteralPath $path) {
        Add-Result "file:$RelativePath" "ok" "present"
    } else {
        Add-Result "file:$RelativePath" "fail" "missing"
    }
}

function Test-TextContains([string]$RelativePath, [string]$Needle, [string]$Label) {
    $path = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        Add-Result $Label "fail" "$RelativePath missing"
        return
    }
    $text = Get-Content -LiteralPath $path -Raw
    if ($text -like "*$Needle*") {
        Add-Result $Label "ok" "$Needle present"
    } else {
        Add-Result $Label "fail" "$Needle missing"
    }
}

Push-Location $Root
try {
    Test-FilePresent "Cleverly.ps1"
    Test-FilePresent "docker-compose.yml"
    Test-FilePresent "docker\ollama-offline.yml"
    Test-FilePresent "ci\fresh-machine-offline-smoke.ps1"
    Test-TextContains "docker-compose.yml" "pull_policy: never" "compose-pull-policy"
    Test-TextContains "docker\ollama-offline.yml" "pull_policy: never" "ollama-pull-policy"
    Test-TextContains "Cleverly.ps1" "--pull never" "launcher-pull-policy"

    $bundleMarker = Join-Path $BundleFullPath "load-cleverly.cmd"
    if (Test-Path -LiteralPath $bundleMarker) {
        Add-Result "bundle" "ok" "offline bundle helper files found"
    } else {
        Add-Result "bundle" "warn" "load-cleverly.cmd not found at $BundleFullPath; running proof against the repo folder"
    }

    $smokeArgs = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "ci\fresh-machine-offline-smoke.ps1"))
    if ($FineTune) { $smokeArgs += "-FineTune" }
    if ($SkipRestart) { $smokeArgs += "-SkipRestart" }
    & powershell @smokeArgs
    $smokeExit = $LASTEXITCODE
    if ($smokeExit -eq 0) {
        Add-Result "fresh-machine-smoke" "ok" "fresh-machine smoke passed"
    } else {
        Add-Result "fresh-machine-smoke" "fail" "fresh-machine smoke failed with exit code $smokeExit"
    }

    $smokeReport = Join-Path $Root "dist\fresh-machine-offline-smoke.json"
    $smokeHash = ""
    if (Test-Path -LiteralPath $smokeReport) {
        $smokeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $smokeReport).Hash.ToLowerInvariant()
        Add-Result "smoke-report-hash" "ok" $smokeHash
    } else {
        Add-Result "smoke-report-hash" "fail" "fresh-machine smoke report missing"
    }
} finally {
    Pop-Location
}

$proof = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    bundle_path = $BundleFullPath
    fine_tune = [bool]$FineTune
    results = $Results
    ok = @($Results | Where-Object status -eq "ok").Count
    warn = @($Results | Where-Object status -eq "warn").Count
    fail = @($Results | Where-Object status -eq "fail").Count
    smoke_report_sha256 = $smokeHash
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportFullPath) | Out-Null
$proof | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportFullPath -Encoding UTF8
$proofHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReportFullPath).Hash.ToLowerInvariant()
$proofHashPath = if ((Split-Path -Leaf $ReportFullPath) -eq "fresh-machine-proof.json") {
    Join-Path (Split-Path -Parent $ReportFullPath) $DefaultProofHashName
} else {
    "$ReportFullPath.sha256"
}
("$proofHash  $(Split-Path -Leaf $ReportFullPath)") | Set-Content -LiteralPath $proofHashPath -Encoding ASCII

if ($proof.fail -gt 0) {
    Write-Host ("Fresh-machine proof failed. Report: " + $ReportFullPath) -ForegroundColor Red
    exit 1
}

Write-Host ("Fresh-machine proof passed. Report: " + $ReportFullPath) -ForegroundColor Green
