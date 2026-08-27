<#
.SYNOPSIS
    Registers the Consistency Supervisor Node in Windows Task Scheduler.

.DESCRIPTION
    Registers 'GE-ConsistencySupervisor' to run scripts\run-consistency-supervisor.ps1
    periodically (default every 15 minutes) under the logged-on user.

.PARAMETER IntervalMinutes
    Interval between supervisor runs in minutes (default: 15).

.EXAMPLE
    .\scripts\register-supervisor-task.ps1 -IntervalMinutes 15
#>
param(
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"

$TaskName = "GE-ConsistencySupervisor"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ScriptPath = Join-Path $RepoRoot "scripts\run-consistency-supervisor.ps1"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Supervisor script not found at $ScriptPath"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Write-Host "Registering scheduled task '$TaskName' to run every $IntervalMinutes minutes..."

try {
    # Unregister existing task if present
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
} catch {
    # ignore if not found
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Graph Engineering Consistency Supervisor watchdog for multi-project pipeline health monitoring."

Write-Host "Successfully registered scheduled task '$TaskName'." -ForegroundColor Green
