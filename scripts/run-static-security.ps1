#Requires -Version 5.1
<#
  Local static-security checks for Cleverly.

  This script is intentionally offline-capable. It runs lightweight repository
  scans without downloading advisory databases or contacting external services.
#>

param(
    [string]$ReportPath = "dist\security\static-security.json",
    [switch]$WarnOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReportFullPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $Root $ReportPath }
$Findings = New-Object System.Collections.Generic.List[object]

function Add-Finding([string]$Rule, [string]$Severity, [string]$Path, [int]$Line, [string]$Detail) {
    $Findings.Add([pscustomobject]@{
        rule = $Rule
        severity = $Severity
        path = $Path
        line = $Line
        detail = $Detail
    }) | Out-Null
}

function Scan-File([System.IO.FileInfo]$File) {
    $relative = $File.FullName.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
    if ($relative -match '^(venv|\.venv|node_modules|dist|backups|\.git)/') { return }
    $lineNo = 0
    foreach ($line in Get-Content -LiteralPath $File.FullName -ErrorAction SilentlyContinue) {
        $lineNo += 1
        if ($line -match '(?i)(api[_-]?key|secret|token|password)\s*=\s*["''][^"'']{16,}["'']' -and $line -notmatch 'req\.|\$\{|_\w*squote|\$\$|\$env:') {
            Add-Finding "hardcoded-secret-like-value" "fail" $relative $lineNo "Secret-like assignment found"
        }
        if ($relative -match '^(routes|src|static/js|docker|ci|scripts)/' -and $line -match 'Invoke-Expression|iex\s|\beval\s*\(|exec\s*\(') {
            Add-Finding "dynamic-code-execution" "warn" $relative $lineNo "Dynamic execution primitive found"
        }
        if ($relative -match '^(static/js/offlineControl|src/offline|routes/offline|ci/no-network|ci/fresh-machine)' -and $line -match 'https?://(?!127\.0\.0\.1|localhost|ollama:|chromadb)') {
            Add-Finding "offline-surface-external-url" "warn" $relative $lineNo "External URL appears in an offline surface"
        }
    }
}

Push-Location $Root
try {
    $tracked = @(& git ls-files 2>$null)
    if (-not $tracked) {
        $tracked = @()
        Get-ChildItem -LiteralPath $Root -File -Recurse | ForEach-Object {
            $tracked += $_.FullName.Substring($Root.Length).TrimStart('\', '/')
        }
    }
    foreach ($relative in $tracked) {
        if ($relative -notmatch '\.(py|js|ps1|sh|ya?ml|json|md)$') { continue }
        $file = Get-Item -LiteralPath (Join-Path $Root $relative) -ErrorAction SilentlyContinue
        if ($file) { Scan-File $file }
    }

    $summary = [pscustomobject]@{
        generated_at = (Get-Date).ToString("o")
        fail = @($Findings | Where-Object severity -eq "fail").Count
        warn = @($Findings | Where-Object severity -eq "warn").Count
        findings = $Findings
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportFullPath) | Out-Null
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportFullPath -Encoding UTF8
    Write-Host ("Static security report: " + $ReportFullPath)
    if ($summary.fail -gt 0 -and -not $WarnOnly) {
        Write-Host ("Static security checks failed: " + $summary.fail) -ForegroundColor Red
        exit 1
    }
    Write-Host ("Static security checks complete. fail=$($summary.fail) warn=$($summary.warn)") -ForegroundColor Green
} finally {
    Pop-Location
}
