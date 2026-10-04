<#
.SYNOPSIS
    Exposes graph-engineering's SDLC skills globally without copying them to individual project repos.

.DESCRIPTION
    graph-engineering/skills/ is the single source of truth for the shared SDLC skills.
    This script mirrors each skill folder into the global Antigravity plugin
    (~/.gemini/config/plugins/swarm-dev-core/skills/<name>) so every project and chat sees the same skills.

    IMPORTANT: Real directories are required. NTFS Directory Junctions (reparse points) must NOT
    be used because Antigravity's filesystem crawler ignores reparse points during skill discovery.

    Re-running the script is safe and idempotent.
#>
[CmdletBinding()]
param(
    [string]$PluginSkillsDir = (Join-Path $HOME ".gemini\config\plugins\swarm-dev-core\skills")
)

$ErrorActionPreference = "Stop"

$SharedSkills = @(
    "agy-architect-review",
    "architect-council-review",
    "claude-architect-review",
    "provision-story",
    "quick-fix",
    "user-story-refining"
)

$RepoSkillsDir = (Resolve-Path (Join-Path $PSScriptRoot "..\skills")).Path
if (-not (Test-Path $PluginSkillsDir)) {
    New-Item -ItemType Directory -Path $PluginSkillsDir -Force | Out-Null
}

foreach ($name in $SharedSkills) {
    $source = Join-Path $RepoSkillsDir $name
    $target = Join-Path $PluginSkillsDir $name
    if (-not (Test-Path $source)) {
        throw "Source skill not found: $source"
    }

    $existing = Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    if ($existing) {
        $isLink = [bool]($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)
        if ($isLink) {
            # Legacy junction pointing elsewhere: remove reparse point without touching target
            [IO.Directory]::Delete($target)
        }
    }

    # Mirror directory contents, excluding temporary cache files
    robocopy $source $target /MIR /XD __pycache__ /NJH /NJS /NDL /NC /NS | Out-Null
    Write-Host "synced   $name -> $target"
}
