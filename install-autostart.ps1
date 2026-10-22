# Registers a Task Scheduler task that starts the server when the current user logs on to Windows.
# Remove: Unregister-ScheduledTask -TaskName 'Qalqan MCP' -Confirm:$false
$script = Join-Path $PSScriptRoot 'start.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'Qalqan MCP' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Qalqan Platform MCP server (Oxima)' -Force | Out-Null
Start-ScheduledTask -TaskName 'Qalqan MCP'
Write-Host "Task 'Qalqan MCP' registered and started."
