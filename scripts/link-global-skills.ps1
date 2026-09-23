<#
.SYNOPSIS
    Exposes graph-engineering's SDLC skills globally without copying them.

.DESCRIPTION
    graph-engineering/.agents/skills/ is the single source of truth for the shared SDLC skills.
    This script replaces each skill folder under the Antigravity plugin
    (~/.gemini/config/plugins/swarm-dev-core/skills/<name>) with a directory junction pointing
    back at the repo, so every project sees the same, versioned files. Junctions need no admin rights.

    An existing real folder is moved to a timestamped backup next to the plugin, never deleted.
    Re-running the script is safe: correct junctions are left untouched.

    Never copy these skills into project repos. Their scripts are invoked through the global path
    (e.g. $HOME\.gemini\config\plugins\swarm-dev-core\skills\agy-architect-review\scripts\agy_cross_review.py).
#>
[CmdletBinding()]
param(
    [string]$PluginSkillsDir = (Join-Path $HOME ".gemini\config\plugins\swarm-dev-core\skills")
)

$ErrorActionPreference = "Stop"

$SharedSkills = @(
    "agy-architect-review",
    "claude-architect-review",
    "provision-story",
    "quick-fix",
    "user-story-refining"
)

$RepoSkillsDir = (Resolve-Path (Join-Path $PSScriptRoot "..\.agents\skills")).Path
if (-not (Test-Path $PluginSkillsDir)) {
    New-Item -ItemType Directory -Path $PluginSkillsDir | Out-Null
}
$BackupDir = Join-Path (Split-Path $PluginSkillsDir -Parent) ("skills-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

foreach ($name in $SharedSkills) {
    $source = Join-Path $RepoSkillsDir $name
    $target = Join-Path $PluginSkillsDir $name
    if (-not (Test-Path $source)) {
        throw "Source skill not found: $source"
    }

    $existing = Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    if ($existing) {
        $isLink = [bool]($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)
        if ($isLink -and (@($existing.Target) -contains $source)) {
            Write-Host "ok       $name -> $source"
            continue
        }
        if ($isLink) {
            # A junction pointing elsewhere: removing it does not touch the files it points to.
            [IO.Directory]::Delete($target)
        } else {
            if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }
            Move-Item -LiteralPath $target -Destination (Join-Path $BackupDir $name)
            Write-Host "backup   $name -> $BackupDir"
        }
    }

    New-Item -ItemType Junction -Path $target -Target $source | Out-Null
    Write-Host "linked   $name -> $source"
}
