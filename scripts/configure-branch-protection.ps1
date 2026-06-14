#Requires -Version 5.1
<#
  Configure baseline GitHub branch protection for the main branch.

  Requires GitHub CLI (`gh`) authenticated with repository administration
  permission. This script intentionally avoids secrets and can be re-run.
#>

param(
    [string]$Repository = "",
    [string]$Branch = "main",
    [string[]]$RequiredChecks = @("Release readiness", "No-network container smoke", "CodeQL"),
    [switch]$RequirePullRequest
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Get-RepositorySlug() {
    if ($Repository) { return $Repository }
    $remote = (& git -C $Root remote get-url origin 2>$null) -join ""
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remote)) {
        throw "Could not determine origin remote. Pass -Repository owner/name."
    }
    if ($remote -match 'github\.com[:/](?<slug>[^/]+/[^/.]+)(\.git)?$') {
        return $Matches.slug
    }
    throw "Could not parse GitHub owner/repo from origin remote: $remote"
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI gh was not found. Install gh and authenticate with repo admin scope."
}

$repo = Get-RepositorySlug
$reviewRules = $null
if ($RequirePullRequest) {
    $reviewRules = [ordered]@{
        dismiss_stale_reviews = $true
        require_code_owner_reviews = $false
        required_approving_review_count = 1
        require_last_push_approval = $false
    }
}

$body = [ordered]@{
    required_status_checks = [ordered]@{
        strict = $true
        contexts = @($RequiredChecks)
    }
    enforce_admins = $false
    required_pull_request_reviews = $reviewRules
    restrictions = $null
    required_linear_history = $false
    allow_force_pushes = $false
    allow_deletions = $false
    block_creations = $false
    required_conversation_resolution = $true
    lock_branch = $false
    allow_fork_syncing = $true
}

$json = $body | ConvertTo-Json -Depth 8
$endpoint = "repos/$repo/branches/$Branch/protection"
$json | gh api --method PUT $endpoint --input -
if ($LASTEXITCODE -ne 0) { throw "gh api branch protection update failed" }
Write-Host ("Branch protection configured for " + $repo + ":" + $Branch) -ForegroundColor Green
Write-Host ("Required checks: " + ($RequiredChecks -join ", "))
