#Requires -Version 5.1
<#
  Command Center UI smoke.

  Static checks always run. Authenticated live desktop/mobile checks run when
  CLEVERLY_BOMBADIL_USERNAME and CLEVERLY_BOMBADIL_PASSWORD are set.
#>

param(
    [string]$Url = "http://127.0.0.1:7000/",
    [string]$ReportPath = "dist\command-center-ui-smoke.json",
    [switch]$StaticOnly,
    [switch]$RequireLive,
    [string]$Browser = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = Join-Path $Root "ci\command_center_ui_smoke.py"
$ReportFullPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $Root $ReportPath }

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Command Center UI smoke runner not found: $ScriptPath"
}

$args = @($ScriptPath, "--url", $Url, "--report", $ReportFullPath)
if ($StaticOnly) { $args += "--static-only" }
if ($RequireLive) { $args += "--require-live" }
if ($Browser) { $args += @("--browser", $Browser) }

Push-Location $Root
try {
    & python @args
    if ($LASTEXITCODE -ne 0) {
        throw "Command Center UI smoke failed with exit code $LASTEXITCODE"
    }
    Write-Host "Command Center UI smoke passed." -ForegroundColor Green
} finally {
    Pop-Location
}
