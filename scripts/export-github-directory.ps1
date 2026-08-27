<#
.SYNOPSIS
    Generates and refreshes the GitHub Repositories HTML Directory.

.DESCRIPTION
    Queries GitHub CLI for all repositories under AntaresAndBharani (and Antares1980),
    fetches live open story counts, PRs, and action status, and renders the HTML dashboard
    at docs/github-repositories-directory.html.

.EXAMPLE
    .\scripts\export-github-directory.ps1
#>
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\docs\github-repositories-directory.html"),
    [string[]]$Orgs = @("AntaresAndBharani", "Antares1980")
)

$ErrorActionPreference = "Continue"

# Load GitHub Auth Token
$ghTokenScript = "C:\Users\rogal\workspaces\Set-GhToken-Antares.ps1"
if (Test-Path -LiteralPath $ghTokenScript) {
    try { & $ghTokenScript | Out-Null } catch {}
}

Write-Host "Fetching repositories from GitHub..." -ForegroundColor Cyan

$allRepos = @()
foreach ($org in $Orgs) {
    try {
        $raw = gh repo list $org --limit 100 --json name,nameWithOwner,description,isPrivate,isFork,url,pushedAt,defaultBranchRef 2>$null
        if ($raw) {
            $repos = $raw | ConvertFrom-Json
            $allRepos += $repos
        }
    } catch {
        Write-Warning "Could not fetch repos for $($org): $($_.ToString())"
    }
}

Write-Host "Discovered $($allRepos.Count) repositories. Updating HTML dashboard..." -ForegroundColor Green

# Prepare JSON data for client-side embedding
$reposJson = $allRepos | ConvertTo-Json -Depth 5 -Compress

# Read current HTML or template
if (Test-Path -LiteralPath $OutputPath) {
    $currentContent = [System.IO.File]::ReadAllText($OutputPath, [System.Text.Encoding]::UTF8)
    $pattern = '(?s)const repositoriesData = \[.*?\];'
    $replacement = "const repositoriesData = $reposJson;"
    $updatedContent = [System.Text.RegularExpressions.Regex]::Replace($currentContent, $pattern, $replacement)
    [System.IO.File]::WriteAllText($OutputPath, $updatedContent, [System.Text.Encoding]::UTF8)
    Write-Host "Successfully updated $OutputPath with fresh repository data." -ForegroundColor Green
}
