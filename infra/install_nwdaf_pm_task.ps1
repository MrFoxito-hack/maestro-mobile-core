$ErrorActionPreference = 'Stop'
$taskName = 'MAEstro-NWDAF-PM-Bridge'
$codeRoot = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $codeRoot 'backend'
$pythonExecutable = Join-Path $backendDirectory '.venv\Scripts\python.exe'
$bridgeScript = Join-Path $PSScriptRoot 'sync_nwdaf_pm.py'
if (-not (Test-Path -LiteralPath $pythonExecutable) -or -not (Test-Path -LiteralPath $bridgeScript)) {
    throw 'Backend Python or PM bridge missing'
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists; inspect it before replacing'
}
$action = New-ScheduledTaskAction -Execute $pythonExecutable -Argument ('"' + $bridgeScript + '"') -WorkingDirectory $backendDirectory
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 55) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Read-only bounded EMS PM snapshot to NWDAF. No policy actuation; runs while user is logged in.'
Start-ScheduledTask -TaskName $taskName
Write-Output 'Installed per-user PM bridge. Remove with Unregister-ScheduledTask -TaskName MAEstro-NWDAF-PM-Bridge.'
