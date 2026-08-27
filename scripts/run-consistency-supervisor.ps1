<#
.SYNOPSIS
    Consistency Supervisor Node runner for Graph Engineering.

.DESCRIPTION
    Deterministically audits the operational health of all Graph Engineering
    pipelines (crosstrainingapp, darwin-trader), inspecting:
      1. Local Windows Task Scheduler execution results (CTA-*, DT-*).
      2. Local pipeline log files (logs/local-pipeline/*.log) and stale .git locks.
      3. Remote GitHub Actions workflow runs (gh run list).

    Generates a unified health dashboard (docs/pipeline-health-dashboard.md)
    and dispatches instant mobile alerts to Telegram when critical anomalies
    or state transitions are detected, with built-in anti-spam deduplication.

.PARAMETER Projects
    Array of project configurations to monitor.

.PARAMETER DashboardPath
    Path to output the generated Markdown health dashboard.

.PARAMETER StateFilePath
    Path to the JSON cache maintaining acknowledged errors and state.

.PARAMETER DisableTelegram
    Switch to disable Telegram alert dispatching.

.EXAMPLE
    .\scripts\run-consistency-supervisor.ps1
#>
param(
    [array]$Projects = @(
        @{
            Name        = "crosstrainingapp"
            Repo        = "AntaresAndBharani/crosstrainingapp"
            Path        = "C:\Users\rogal\workspaces\ws-gym\crosstrainingapp"
            TaskPrefix  = "CTA-"
            Description = "Android / Jetpack Compose App"
        },
        @{
            Name        = "darwin-trader"
            Repo        = "AntaresAndBharani/darwin-trader"
            Path        = "C:\Users\rogal\workspaces\ws-trading\darwin-trader"
            TaskPrefix  = "DT-"
            Description = "Algorithmic Trading & MetaTrader Platform"
        }
    ),
    [string]$DashboardPath = (Join-Path $PSScriptRoot "..\docs\pipeline-health-dashboard.md"),
    [string]$StateFilePath = (Join-Path $PSScriptRoot "..\logs\supervisor-state.json"),
    [switch]$DisableTelegram
)

$ErrorActionPreference = "Continue"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$LogFile = Join-Path $LogDir ("supervisor-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-SupervisorLog {
    param(
        [string]$Message,
        [string]$Level = "INFO"
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] [$Level] $Message"
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
}

Write-SupervisorLog "======================================================="
Write-SupervisorLog "  Consistency Supervisor Node - Multi-Project Health Audit"
Write-SupervisorLog "======================================================="

# Ensure GitHub authentication token is loaded
$ghTokenScript = "C:\Users\rogal\workspaces\Set-GhToken-Antares.ps1"
if (Test-Path -LiteralPath $ghTokenScript) {
    try {
        & $ghTokenScript | Out-Null
    } catch {
        Write-SupervisorLog "Warning: Could not invoke Set-GhToken-Antares.ps1: $($_.ToString())" "WARN"
    }
}

function Convert-ToHashtable {
    param($InputObject)
    if ($null -eq $InputObject) { return @{} }
    if ($InputObject -is [System.Management.Automation.PSCustomObject]) {
        $hash = @{}
        foreach ($prop in $InputObject.PSObject.Properties) {
            $hash[$prop.Name] = Convert-ToHashtable $prop.Value
        }
        return $hash
    }
    return $InputObject
}

# Load existing state cache
$state = @{
    LastCheckTime = $null
    KnownErrors   = @{}
    NodeStates    = @{}
}
if (Test-Path -LiteralPath $StateFilePath) {
    try {
        $parsed = Get-Content -LiteralPath $StateFilePath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($parsed) {
            $converted = Convert-ToHashtable $parsed
            if ($converted.LastCheckTime) { $state.LastCheckTime = $converted.LastCheckTime }
            if ($converted.KnownErrors)   { $state.KnownErrors   = $converted.KnownErrors }
            if ($converted.NodeStates)    { $state.NodeStates    = $converted.NodeStates }
        }
    } catch {
        Write-SupervisorLog "Could not parse state file; initializing fresh state: $($_.ToString())" "WARN"
    }
}

$now = Get-Date
$currentRunErrors = @()
$currentRunWarnings = @()
$projectHealthSummaries = @()

foreach ($proj in $Projects) {
    $pName = $proj.Name
    $pRepo = $proj.Repo
    $pPath = $proj.Path
    $pPrefix = $proj.TaskPrefix

    Write-SupervisorLog "Auditing project: $pName ($pRepo)..."

    $projIssues = @()
    $projWarnings = @()
    $tasksSummary = @()
    $logsSummary = @()
    $ghRunsSummary = @()

    # -------------------------------------------------------------
    # 1. Local .git lock file check
    # -------------------------------------------------------------
    $gitLockFile = Join-Path $pPath ".git\index.lock"
    if (Test-Path -LiteralPath $gitLockFile) {
        $lockItem = Get-Item -LiteralPath $gitLockFile
        $lockAgeMinutes = [math]::Round(($now - $lockItem.LastWriteTime).TotalMinutes, 1)
        $issueMsg = "Stale .git/index.lock detected in $pName (Age: $lockAgeMinutes mins, Last Modified: $($lockItem.LastWriteTime))"
        Write-SupervisorLog $issueMsg "ERROR"
        $projIssues += @{
            Category    = "Git Lock Collision"
            Component   = "$pName/.git"
            Message     = $issueMsg
            Remediation = "Remove-Item -LiteralPath '$gitLockFile' -Force"
            Severity    = "CRITICAL"
        }
    }

    # -------------------------------------------------------------
    # 2. Windows Scheduled Tasks Audit
    # -------------------------------------------------------------
    $scheduledTasks = @()
    try {
        $scheduledTasks = Get-ScheduledTask | Where-Object { $_.TaskName -like "$($pPrefix)*" }
    } catch {
        Write-SupervisorLog "Failed to query scheduled tasks for prefix '$pPrefix': $($_.ToString())" "WARN"
    }

    foreach ($task in $scheduledTasks) {
        $taskName = $task.TaskName
        $taskInfo = $null
        try {
            $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        } catch {
            Write-SupervisorLog "Failed to query TaskInfo for $($taskName): $($_.ToString())" "WARN"
        }

        $lastResult = if ($null -ne $taskInfo) { $taskInfo.LastTaskResult } else { -1 }
        $lastRun    = if ($null -ne $taskInfo) { $taskInfo.LastRunTime } else { $null }
        $nextRun    = if ($null -ne $taskInfo) { $taskInfo.NextRunTime } else { $null }
        $statusStr  = if ($lastResult -eq 0) { "HEALTHY (0)" } elseif ($lastResult -eq 267009) { "RUNNING" } else { "FAILED ($lastResult)" }

        if ($lastResult -ne 0 -and $lastResult -ne 267009 -and $lastResult -ne -1) {
            $issueMsg = "Scheduled task $($taskName) exited with error code $($lastResult) on $($lastRun)"
            Write-SupervisorLog $issueMsg "ERROR"
            $projIssues += @{
                Category    = "Scheduled Task Failure"
                Component   = $taskName
                Message     = $issueMsg
                Remediation = "Inspect $($pPath)\logs\local-pipeline\ logs for exact unhandled exception"
                Severity    = "CRITICAL"
            }
        }

        $tasksSummary += [PSCustomObject]@{
            TaskName   = $taskName
            State      = $task.State
            LastRun    = if ($lastRun -and $lastRun.Year -gt 2000) { $lastRun.ToString("yyyy-MM-dd HH:mm:ss") } else { "Never" }
            NextRun    = if ($nextRun -and $nextRun.Year -gt 2000) { $nextRun.ToString("yyyy-MM-dd HH:mm:ss") } else { "N/A" }
            LastResult = $lastResult
            Status     = $statusStr
        }
    }

    # -------------------------------------------------------------
    # 3. Local Pipeline Log Files Audit
    # -------------------------------------------------------------
    $pLogDir = Join-Path $pPath "logs\local-pipeline"
    if (Test-Path -LiteralPath $pLogDir) {
        $logFiles = Get-ChildItem -LiteralPath $pLogDir -Filter "*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 10
        foreach ($lf in $logFiles) {
            # Only scan logs modified in the last 24 hours
            if (($now - $lf.LastWriteTime).TotalHours -le 24) {
                $lines = @()
                try {
                    $lines = Get-Content -LiteralPath $lf.FullName -Encoding utf8
                } catch {
                    Write-SupervisorLog "Could not read log file $($lf.FullName): $($_.ToString())" "WARN"
                }

                $errorLines = @()
                $warnLines  = @()
                foreach ($line in $lines) {
                    if ($line -match "\[ERROR\]" -or $line -match "Unhandled error" -or $line -match "fatal:" -or $line -match "failed with exit code") {
                        $errorLines += $line
                    } elseif ($line -match "\[WARN\]" -or $line -match "\[WARNING\]") {
                        $warnLines += $line
                    }
                }

                $logsSummary += [PSCustomObject]@{
                    FileName     = $lf.Name
                    LastModified = $lf.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
                    SizeKB       = [math]::Round($lf.Length / 1KB, 1)
                    ErrorCount   = $errorLines.Count
                    WarnCount    = $warnLines.Count
                    LatestError  = if ($errorLines.Count -gt 0) { $errorLines[-1] } else { $null }
                }

                if ($errorLines.Count -gt 0) {
                    # Capture unique error lines
                    $recentErrors = $errorLines | Select-Object -Unique -Last 3
                    foreach ($err in $recentErrors) {
                        $issueMsg = "Log error in $($lf.Name): $err"
                        $projIssues += @{
                            Category    = "Pipeline Log Error"
                            Component   = "$pName/$($lf.Name)"
                            Message     = $issueMsg
                            Remediation = "Check full trace in $($lf.FullName)"
                            Severity    = "CRITICAL"
                        }
                    }
                }

                if ($warnLines.Count -gt 0) {
                    $recentWarns = $warnLines | Select-Object -Unique -Last 2
                    foreach ($wrn in $recentWarns) {
                        $projWarnings += @{
                            Category    = "Pipeline Log Warning"
                            Component   = "$pName/$($lf.Name)"
                            Message     = "Warning in $($lf.Name): $wrn"
                            Severity    = "WARNING"
                        }
                    }
                }
            }
        }
    }

    # -------------------------------------------------------------
    # 4. GitHub Actions CI Runs Audit
    # -------------------------------------------------------------
    try {
        $ghRunsRaw = gh run list --repo $pRepo --limit 8 --json databaseId,name,conclusion,status,url,updatedAt,headBranch 2>&1
        if ($LASTEXITCODE -eq 0 -and (-not [string]::IsNullOrWhiteSpace($ghRunsRaw))) {
            $ghRuns = $ghRunsRaw | ConvertFrom-Json
            foreach ($gr in $ghRuns) {
                $c = $gr.conclusion
                $s = $gr.status
                $statusIcon = if ($c -eq "success") {
                    "SUCCESS"
                } elseif ($c -eq "failure") {
                    "FAILURE"
                } elseif ($c -eq "cancelled") {
                    "CANCELLED"
                } elseif ($c -eq "timed_out") {
                    "TIMED_OUT"
                } else {
                    if ($s -eq "in_progress") { "IN_PROGRESS" } else { "$s" }
                }

                $ghRunsSummary += [PSCustomObject]@{
                    Id         = $gr.databaseId
                    Name       = $gr.name
                    Branch     = $gr.headBranch
                    Status     = $statusIcon
                    Conclusion = $c
                    UpdatedAt  = $gr.updatedAt
                    Url        = $gr.url
                }

                if ($c -eq "failure" -or $c -eq "timed_out") {
                    $issueMsg = "GitHub Workflow '$($gr.name)' ($($gr.headBranch)) failed on run #$($gr.databaseId)"
                    $projIssues += @{
                        Category    = "GitHub CI Failure"
                        Component   = "$pName/$($gr.name)"
                        Message     = "$issueMsg ($($gr.url))"
                        Remediation = "Review workflow run logs at $($gr.url)"
                        Severity    = "CRITICAL"
                    }
                }
            }
        } else {
            Write-SupervisorLog "gh run list returned empty or error for $pRepo" "WARN"
        }
    } catch {
        Write-SupervisorLog "Failed to execute gh run list for $($pRepo): $($_.ToString())" "WARN"
    }

    # Overall project health status
    $projHealth = if ($projIssues.Count -gt 0) { "CRITICAL" } elseif ($projWarnings.Count -gt 0) { "WARNING" } else { "HEALTHY" }

    $projectHealthSummaries += [PSCustomObject]@{
        Project      = $pName
        Repo         = $pRepo
        Health       = $projHealth
        Issues       = $projIssues
        Warnings     = $projWarnings
        Tasks        = $tasksSummary
        Logs         = $logsSummary
        GitHubRuns   = $ghRunsSummary
    }

    $currentRunErrors += $projIssues
    $currentRunWarnings += $projWarnings
}

# -----------------------------------------------------------------
# 5. Generate Markdown Health Dashboard
# -----------------------------------------------------------------
Write-SupervisorLog "Generating Health Dashboard at $DashboardPath..."

$overallHealth = if ($currentRunErrors.Count -gt 0) { "CRITICAL / ACTION REQUIRED" } elseif ($currentRunWarnings.Count -gt 0) { "DEGRADED / WARNINGS DETECTED" } else { "ALL SYSTEMS NOMINAL" }
$generatedTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# Graph Engineering - Pipeline Health Dashboard")
[void]$sb.AppendLine()
[void]$sb.AppendLine("**Last Audit Run:** ``$generatedTime``  ")
[void]$sb.AppendLine("**Overall System Health:** **$overallHealth**")
[void]$sb.AppendLine()
[void]$sb.AppendLine("---")
[void]$sb.AppendLine()
[void]$sb.AppendLine("## Executive Overview")
[void]$sb.AppendLine()
[void]$sb.AppendLine("| Project | Target Repository | Health Status | Active Tasks | Log Errors (24h) | Recent CI Failures |")
[void]$sb.AppendLine("| :--- | :--- | :---: | :---: | :---: | :---: |")

foreach ($ph in $projectHealthSummaries) {
    $activeTasksCount = ($ph.Tasks | Where-Object { $_.State -eq "Ready" -or $_.State -eq "Running" }).Count
    $totalLogErrors = ($ph.Logs | Measure-Object -Property ErrorCount -Sum).Sum
    if ($null -eq $totalLogErrors) { $totalLogErrors = 0 }
    $ciFailures = ($ph.GitHubRuns | Where-Object { $_.Conclusion -eq "failure" -or $_.Conclusion -eq "timed_out" }).Count

    [void]$sb.AppendLine("| **$($ph.Project)** | [$($ph.Repo)](https://github.com/$($ph.Repo)) | $($ph.Health) | $activeTasksCount | $totalLogErrors | $ciFailures |")
}

[void]$sb.AppendLine()
[void]$sb.AppendLine("---")
[void]$sb.AppendLine()

# Active Anomalies & Remediation Section
if ($currentRunErrors.Count -gt 0 -or $currentRunWarnings.Count -gt 0) {
    [void]$sb.AppendLine("## Detected Issues & Remediation Action Items")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("The supervisor detected the following issues requiring attention:")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("| Severity | Project / Component | Issue Description | Recommended Action |")
    [void]$sb.AppendLine("| :---: | :--- | :--- | :--- |")

    foreach ($err in $currentRunErrors) {
        $cName = $err.Component
        $cMsg  = ($err.Message -replace "\|", "-")
        $cRem  = ($err.Remediation -replace "\|", "-")
        [void]$sb.AppendLine("| CRITICAL | ``$cName`` | $cMsg | ``$cRem`` |")
    }
    foreach ($wrn in $currentRunWarnings) {
        $cName = $wrn.Component
        $cMsg  = ($wrn.Message -replace "\|", "-")
        [void]$sb.AppendLine("| WARNING | ``$cName`` | $cMsg | Review logs for degradation |")
    }
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("---")
    [void]$sb.AppendLine()
} else {
    [void]$sb.AppendLine("## System Status: All Nodes Healthy")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("No unhandled errors, stale ``.git`` lock files, or broken CI runners detected across all monitored projects.")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("---")
    [void]$sb.AppendLine()
}

# Detailed Per-Project Breakdowns
foreach ($ph in $projectHealthSummaries) {
    [void]$sb.AppendLine("## Project: $($ph.Project) ($($ph.Repo))")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("### Windows Task Scheduler Execution Matrix")
    [void]$sb.AppendLine("| Task Name | State | Last Run Time | Next Run Time | Last Exit Result | Status |")
    [void]$sb.AppendLine("| :--- | :---: | :---: | :---: | :---: | :---: |")

    foreach ($t in $ph.Tasks) {
        [void]$sb.AppendLine("| ``$($t.TaskName)`` | $($t.State) | $($t.LastRun) | $($t.NextRun) | ``$($t.LastResult)`` | $($t.Status) |")
    }

    [void]$sb.AppendLine()
    [void]$sb.AppendLine("### Local Pipeline Daily Logs (Last 24h)")
    [void]$sb.AppendLine("| Log File | Last Modified | Size | Errors | Warnings | Latest Error Snippet |")
    [void]$sb.AppendLine("| :--- | :---: | :---: | :---: | :---: | :--- |")

    foreach ($l in $ph.Logs) {
        $snippet = if ($l.LatestError) {
            $rawSnippet = $l.LatestError -replace "\|", "-"
            "``" + $rawSnippet.Substring(0, [Math]::Min(80, $rawSnippet.Length)) + "``..."
        } else { "None" }
        [void]$sb.AppendLine("| ``$($l.FileName)`` | $($l.LastModified) | $($l.SizeKB) KB | $($l.ErrorCount) | $($l.WarnCount) | $snippet |")
    }

    [void]$sb.AppendLine()
    [void]$sb.AppendLine("### Recent GitHub Actions CI Runs")
    [void]$sb.AppendLine("| Run ID | Workflow Name | Head Branch | Status | Updated At |")
    [void]$sb.AppendLine("| :--- | :--- | :--- | :---: | :---: |")

    foreach ($gr in $ph.GitHubRuns) {
        [void]$sb.AppendLine("| [$($gr.Id)]($($gr.Url)) | $($gr.Name) | ``$($gr.Branch)`` | $($gr.Status) | $($gr.UpdatedAt) |")
    }

    [void]$sb.AppendLine()
    [void]$sb.AppendLine("---")
    [void]$sb.AppendLine()
}

[void]$sb.AppendLine("*Generated deterministically by Consistency Supervisor Node (`scripts/run-consistency-supervisor.ps1`).*")

$md = $sb.ToString()

# Write Dashboard to File
$dashboardDir = Split-Path -Path $DashboardPath -Parent
if (-not (Test-Path -LiteralPath $dashboardDir)) {
    New-Item -ItemType Directory -Path $dashboardDir -Force | Out-Null
}
Set-Content -LiteralPath $DashboardPath -Value $md -Encoding utf8
Write-SupervisorLog "Dashboard written successfully to $DashboardPath"

# -----------------------------------------------------------------
# 6. Telegram Push Alert Dispatcher (with Deduplication)
# -----------------------------------------------------------------
$botToken = $Env:TELEGRAM_BOT_TOKEN
$chatId   = $Env:TELEGRAM_CHAT_ID

# Check for config file fallback if environment variables are not set
$tgConfigFile = Join-Path $env:USERPROFILE ".gemini\antigravity\telegram.json"
if ((-not $botToken -or -not $chatId) -and (Test-Path -LiteralPath $tgConfigFile)) {
    try {
        $tgConfig = Get-Content -LiteralPath $tgConfigFile -Raw | ConvertFrom-Json
        if ($tgConfig.bot_token) { $botToken = $tgConfig.bot_token }
        if ($tgConfig.chat_id)   { $chatId   = $tgConfig.chat_id }
    } catch {
        Write-SupervisorLog "Could not read $($tgConfigFile): $($_.ToString())" "WARN"
    }
}

if ($DisableTelegram) {
    Write-SupervisorLog "Telegram alerting disabled via -DisableTelegram switch."
} elseif (-not $botToken -or -not $chatId) {
    Write-SupervisorLog "Telegram credentials not set (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Skipping Telegram dispatch." "INFO"
} else {
    Write-SupervisorLog "Evaluating Telegram alerts with anti-spam deduplication..."

    $messagesToSend = @()

    # Detect new errors
    foreach ($err in $currentRunErrors) {
        $errorKey = "$($err.Component)::$($err.Message)"
        if (-not $state.KnownErrors.ContainsKey($errorKey)) {
            $state.KnownErrors[$errorKey] = @{
                FirstSeen = $generatedTime
                Alerted   = $true
            }

            $alertMsg = @"
[Graph Engineering Alert]
Component: $($err.Component)
Severity: CRITICAL
Issue: $($err.Message)

Remediation:
$($err.Remediation)
"@
            $messagesToSend += $alertMsg
        }
    }

    # Detect recovered components
    $currentErrorComponents = $currentRunErrors | ForEach-Object { $_.Component }
    $resolvedKeys = @()
    foreach ($knownKey in $state.KnownErrors.Keys) {
        $comp = $knownKey.Split("::")[0]
        if ($comp -notin $currentErrorComponents) {
            $resolvedKeys += $knownKey
            $recoveryMsg = @"
[Graph Engineering Recovery]
Component: $comp
Status: Node has recovered and returned to HEALTHY state.
"@
            $messagesToSend += $recoveryMsg
        }
    }

    # Remove resolved errors from state
    foreach ($rk in $resolvedKeys) {
        $state.KnownErrors.Remove($rk)
    }

    # Dispatch messages via Telegram Bot API
    foreach ($msg in $messagesToSend) {
        try {
            $url = "https://api.telegram.org/bot$botToken/sendMessage"
            $payload = @{
                chat_id                  = $chatId
                text                     = $msg
                disable_web_page_preview = $true
            } | ConvertTo-Json -Compress

            $response = Invoke-RestMethod -Uri $url -Method Post -Body $payload -ContentType "application/json; charset=utf-8"
            if ($response.ok) {
                Write-SupervisorLog "Telegram alert dispatched successfully."
            } else {
                Write-SupervisorLog "Telegram API error: $($response.description)" "WARN"
            }
        } catch {
            Write-SupervisorLog "Failed to dispatch Telegram alert: $($_.ToString())" "WARN"
        }
    }
}

# -----------------------------------------------------------------
# 7. Persist Supervisor State Cache
# -----------------------------------------------------------------
$state.LastCheckTime = $generatedTime
$stateJson = $state | ConvertTo-Json -Depth 5
Set-Content -LiteralPath $StateFilePath -Value $stateJson -Encoding utf8
Write-SupervisorLog "Supervisor state updated at $StateFilePath."
Write-SupervisorLog "===== Audit completed (Health: $overallHealth) ====="
