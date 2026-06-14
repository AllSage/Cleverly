#Requires -Version 5.1
<#
  Write a small offline-readable release dashboard from local release evidence.

  The dashboard is intentionally static HTML plus a JSON summary so it can be
  carried with an offline release folder and opened without contacting a server.
#>

param(
    [string]$ReleaseDir = "dist\release",
    [string]$OutputHtml = "",
    [string]$OutputJson = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReleaseFullPath = if ([System.IO.Path]::IsPathRooted($ReleaseDir)) { $ReleaseDir } else { Join-Path $Root $ReleaseDir }
$DashboardHtml = if ($OutputHtml) {
    if ([System.IO.Path]::IsPathRooted($OutputHtml)) { $OutputHtml } else { Join-Path $Root $OutputHtml }
} else {
    Join-Path $ReleaseFullPath "release-dashboard.html"
}
$DashboardJson = if ($OutputJson) {
    if ([System.IO.Path]::IsPathRooted($OutputJson)) { $OutputJson } else { Join-Path $Root $OutputJson }
} else {
    Join-Path $ReleaseFullPath "release-dashboard.json"
}

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{ parse_error = $_.Exception.Message }
    }
}

function Read-HashLine([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
    return ((Get-Content -LiteralPath $Path -TotalCount 1) -join "").Trim()
}

function Html-Escape([object]$Value) {
    return [System.Net.WebUtility]::HtmlEncode([string]$Value)
}

function Status-Class([object]$Value) {
    $text = ([string]$Value).ToLowerInvariant()
    if ($text -eq "0" -or $text -eq "ok" -or $text -eq "pass" -or $text -eq "ready") { return "ok" }
    if ($text -eq "" -or $text -eq "unknown") { return "warn" }
    return "bad"
}

if (-not (Test-Path -LiteralPath $ReleaseFullPath -PathType Container)) {
    throw "Release directory not found: $ReleaseFullPath"
}

$manifest = Read-JsonFile (Join-Path $ReleaseFullPath "release-manifest.json")
$sbom = Read-JsonFile (Join-Path $ReleaseFullPath "cleverly-sbom.json")
$static = Read-JsonFile (Join-Path $ReleaseFullPath "static-security.json")
$smoke = Read-JsonFile (Join-Path $ReleaseFullPath "no-network-container-smoke.json")
$model = Read-JsonFile (Join-Path $ReleaseFullPath "model-integrity.json")
$checksums = Read-HashLine (Join-Path $ReleaseFullPath "checksums.sha256")
$sbomHash = Read-HashLine (Join-Path $ReleaseFullPath "cleverly-sbom.json.sha256")

$summary = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    release_dir = $ReleaseFullPath
    git_commit = if ($manifest) { $manifest.git_commit } else { "" }
    git_branch = if ($manifest) { $manifest.git_branch } else { "" }
    image = if ($manifest) { $manifest.image } else { "" }
    model = if ($model -and $model.model) { $model.model } elseif ($manifest) { $manifest.model } else { "" }
    model_verification = if ($model) { $model.verification_state } else { "missing" }
    sbom_hash = $sbomHash
    checksum_first_line = $checksums
    static_security = if ($static -and $static.summary) { $static.summary } else { $null }
    no_network_smoke = if ($smoke) {
        [pscustomobject]@{
            ok = $smoke.ok
            warn = $smoke.warn
            fail = $smoke.fail
        }
    } else { $null }
    reports = if ($manifest -and $manifest.reports) { @($manifest.reports) } else { @() }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DashboardJson) | Out-Null
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $DashboardJson -Encoding UTF8

$staticFail = if ($summary.static_security) { $summary.static_security.fail } else { "unknown" }
$staticWarn = if ($summary.static_security) { $summary.static_security.warn } else { "unknown" }
$smokeFail = if ($summary.no_network_smoke) { $summary.no_network_smoke.fail } else { "unknown" }
$smokeWarn = if ($summary.no_network_smoke) { $summary.no_network_smoke.warn } else { "unknown" }

$reportRows = @()
foreach ($report in @($summary.reports)) {
    $path = Join-Path $ReleaseFullPath ([string]$report)
    $present = Test-Path -LiteralPath $path -PathType Leaf
    $hash = if ($present) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() } else { "" }
    $reportRows += "<tr><td>$(Html-Escape $report)</td><td>$(Html-Escape $present)</td><td><code>$(Html-Escape $hash)</code></td></tr>"
}
if ($reportRows.Count -eq 0) {
    $reportRows += "<tr><td colspan=""3"">No report list found.</td></tr>"
}

$html = @"
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cleverly Release Dashboard</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, Segoe UI, sans-serif; background: #090b12; color: #e8f7ff; }
    body { margin: 0; padding: 32px; }
    main { max-width: 1080px; margin: 0 auto; }
    h1 { margin: 0 0 8px; font-size: 32px; }
    .muted { color: #8ca8b8; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 24px 0; }
    .card { border: 1px solid #244154; border-radius: 8px; padding: 14px; background: #10131c; }
    .label { color: #8ca8b8; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
    .value { font-size: 18px; margin-top: 6px; overflow-wrap: anywhere; }
    .ok { color: #38f5a6; }
    .warn { color: #ffd166; }
    .bad { color: #ff6b6b; }
    code { color: #00e7ff; overflow-wrap: anywhere; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th, td { border-bottom: 1px solid #203547; padding: 10px; text-align: left; vertical-align: top; }
    th { color: #8ca8b8; font-weight: 600; }
  </style>
</head>
<body>
<main>
  <h1>Cleverly Release Dashboard</h1>
  <div class="muted">Generated $(Html-Escape $summary.generated_at)</div>
  <section class="grid">
    <div class="card"><div class="label">Commit</div><div class="value"><code>$(Html-Escape $summary.git_commit)</code></div></div>
    <div class="card"><div class="label">Branch</div><div class="value">$(Html-Escape $summary.git_branch)</div></div>
    <div class="card"><div class="label">Image</div><div class="value">$(Html-Escape $summary.image)</div></div>
    <div class="card"><div class="label">Model</div><div class="value">$(Html-Escape $summary.model)</div></div>
    <div class="card"><div class="label">Model Integrity</div><div class="value $(Status-Class $summary.model_verification)">$(Html-Escape $summary.model_verification)</div></div>
    <div class="card"><div class="label">Static Security</div><div class="value $(Status-Class $staticFail)">fail=$(Html-Escape $staticFail), warn=$(Html-Escape $staticWarn)</div></div>
    <div class="card"><div class="label">No-Network Smoke</div><div class="value $(Status-Class $smokeFail)">fail=$(Html-Escape $smokeFail), warn=$(Html-Escape $smokeWarn)</div></div>
    <div class="card"><div class="label">SBOM Hash</div><div class="value"><code>$(Html-Escape $summary.sbom_hash)</code></div></div>
  </section>
  <h2>Release Evidence</h2>
  <table>
    <thead><tr><th>File</th><th>Present</th><th>SHA-256</th></tr></thead>
    <tbody>
      $($reportRows -join "`n      ")
    </tbody>
  </table>
  <p class="muted">Open this file locally. It does not load external assets.</p>
</main>
</body>
</html>
"@

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DashboardHtml) | Out-Null
$html | Set-Content -LiteralPath $DashboardHtml -Encoding UTF8
Write-Host ("Release dashboard written: " + $DashboardHtml)
Write-Host ("Release dashboard JSON written: " + $DashboardJson)
