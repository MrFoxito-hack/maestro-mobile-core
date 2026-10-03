$ErrorActionPreference = 'Stop'
$taskName = 'MAEstro-NWDAF-Fast-PM'
$codeRoot = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $codeRoot 'backend'
$pythonExecutable = Join-Path $backendDirectory '.venv\Scripts\python.exe'
$bridgeScript = Join-Path $PSScriptRoot 'nwdaf_fast_pm.py'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Task exists: review before replacing' }
$action = New-ScheduledTaskAction -Execute $pythonExecutable -Argument ('"' + $bridgeScript + '" --seconds 50') -WorkingDirectory $backendDirectory
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 55) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Real UPF counter observations for NWDAF. 50s bounded executions, no routes or generated traffic. Requires logged-in user.'
Start-ScheduledTask -TaskName $taskName
