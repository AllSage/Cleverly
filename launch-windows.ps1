#Requires -Version 5.1
<#
  Backward-compatible native Windows entrypoint.

  This delegates to Cleverly-Standalone.ps1 so native Windows starts use the
  same local-only, app-enforced offline defaults as the explicit standalone
  launcher. Dependency installation still requires connected prep.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Standalone = Join-Path $Root "Cleverly-Standalone.ps1"

if (-not (Test-Path -LiteralPath $Standalone)) {
    Write-Host ""
    Write-Host "ERROR: Cleverly-Standalone.ps1 was not found next to launch-windows.ps1." -ForegroundColor Red
    exit 1
}

Write-Host "launch-windows.ps1 now uses Cleverly standalone mode." -ForegroundColor Cyan
Write-Host "Standalone mode is easier than Docker, but Docker sealed mode remains stronger for sensitive machines." -ForegroundColor Yellow

& $Standalone setup -AllowConnectedPrep -Port $Port -BindHost $BindHost
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$startArgs = @("start", "-Port", [string]$Port, "-BindHost", $BindHost)
if ($NoOpen) { $startArgs += "-NoOpen" }
& $Standalone @startArgs
exit $LASTEXITCODE
