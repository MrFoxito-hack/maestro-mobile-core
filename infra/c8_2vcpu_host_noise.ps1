param([ValidateSet('Apply','Restore','Status','Watchdog')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $repoRoot '.work\c8-campaign\ieee-2vcpu\setup\host-application-affinity.json'
$eventsPath = Join-Path $repoRoot '.work\c8-campaign\ieee-2vcpu\setup\host-application-affinity-events.jsonl'
[long]$ecoreMask = 268369920 # logical CPUs 16..27, verified host topology
if ($Action -eq 'Watchdog') {
    Start-Sleep -Seconds 3600
    & $PSCommandPath -Action Restore
    exit
}
$record = if (Test-Path -LiteralPath $recordPath) { Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json } else {
    [PSCustomObject]@{ authorized = 'User explicitly selected temporary E-core affinity, 2026-10-08'; status = 'new'; processes = @() }
}
if ($Action -eq 'Restore' -and $record.status -eq 'restored') {
    @{ status = 'already_restored'; tracked = @($record.processes).Count } | ConvertTo-Json -Compress
    exit
}
if ($Action -eq 'Apply') {
    if ($record.status -eq 'restored') { throw 'Already restored; archive record before a new intervention' }
    $apps = @(Get-Process -Name 'wallpaper*','chrome','RobloxPlayerBeta' -ErrorAction SilentlyContinue)
    foreach ($process in $apps) {
        $started = $process.StartTime.ToUniversalTime().ToString('o')
        $existing = @($record.processes | Where-Object { $_.pid -eq $process.Id -and $_.started -eq $started })
        if ($existing.Count -eq 0) {
            $record.processes += [PSCustomObject]@{ pid = $process.Id; name = $process.Name; started = $started; before = $process.ProcessorAffinity.ToInt64(); applied = $ecoreMask; restored = $false }
            $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $recordPath -Encoding UTF8
        }
        $process.ProcessorAffinity = [IntPtr]$ecoreMask
        $process.Refresh()
        if ($process.ProcessorAffinity.ToInt64() -ne $ecoreMask) { throw 'Application affinity verification failed' }
    }
    if ($record.status -eq 'new') {
        Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-File',('"' + $PSCommandPath + '"'),'-Action','Watchdog') -WindowStyle Hidden
    }
    $record.status = 'applied'
} elseif ($Action -eq 'Restore') {
    foreach ($entry in $record.processes) {
        $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        if ($null -eq $process -or $process.StartTime.ToUniversalTime().ToString('o') -ne $entry.started) {
            $entry.restored = 'process_exited'
            continue
        }
        $process.ProcessorAffinity = [IntPtr][long]$entry.before
        $process.Refresh()
        if ($process.ProcessorAffinity.ToInt64() -ne [long]$entry.before) { throw 'Application affinity restoration failed' }
        $entry.restored = $true
    }
    $record.status = 'restored'
}
$record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $recordPath -Encoding UTF8
@{ at = [DateTime]::UtcNow.ToString('o'); action = $Action; status = $record.status; tracked = @($record.processes).Count } | ConvertTo-Json -Compress | Add-Content -LiteralPath $eventsPath -Encoding UTF8
@{ status = $record.status; tracked = @($record.processes).Count } | ConvertTo-Json -Compress
