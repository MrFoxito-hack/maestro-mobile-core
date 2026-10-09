param([switch]$VerifyOnly, [string]$Label = 'host-pinning', [ValidateSet('High','Normal')][string]$ExpectedPriority = 'High')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$evidenceRoot = Join-Path $repoRoot '.work\c8-campaign\ieee-2vcpu\setup'
$allocation = @{
    'EMS-Testbed-4G5G' = @(0)
    'EMS-UPF-01' = @(2, 4)
    'EMS-UPF-02' = @(6)
    'EMS-GNB-01' = @(8, 10)
    'EMS-UE-01' = @(12, 14)
}
$rows = @()
$failures = @()
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin -and -not $VerifyOnly) { [System.Diagnostics.Process]::EnterDebugMode() }
$processes = Get-CimInstance Win32_Process -Filter "Name='VBoxHeadless.exe'"
foreach ($vm in $allocation.Keys) {
    # VirtualBox 7.2 Windows runs the VM in the hardened third child.
    # The first process is only a launcher; its High class proves nothing
    # about the CPU-consuming executor. Require exactly one executor per VM.
    $vmProcesses = @($processes | Where-Object { ($_.CommandLine -match ('--comment ' + [regex]::Escape($vm) + ' --startvm') -or $_.CommandLine -match ('--startvm "?' + [regex]::Escape($vm) + '("|\s|$)')) -and $_.CommandLine -match 'suplib-3rdchild' })
    if ($vmProcesses.Count -ne 1) { throw "Expected exactly one hardened VBoxHeadless executor: $vm; found $($vmProcesses.Count)" }
    [long]$mask = 0
    foreach ($cpu in $allocation[$vm]) { $mask = $mask -bor (1L -shl $cpu) }
    foreach ($entry in $vmProcesses) {
        $process = Get-Process -Id $entry.ProcessId
        $before = @{ affinity = $process.ProcessorAffinity.ToInt64(); priority = [string]$process.PriorityClass }
        if (-not $VerifyOnly) {
            try {
                if ($process.ProcessorAffinity.ToInt64() -ne $mask) { $process.ProcessorAffinity = [IntPtr]$mask }
                $process.PriorityClass = $ExpectedPriority
            } catch { $failures += "Mutation failed: $vm PID $($process.Id): $($_.Exception.Message)" }
        }
        $process.Refresh()
        if ($process.ProcessorAffinity.ToInt64() -ne $mask -or [string]$process.PriorityClass -ne $ExpectedPriority) { $failures += "Host affinity or priority mismatch: $vm PID $($process.Id)" }
        $rows += @{ vm = $vm; pid = $process.Id; before = $before; affinity = $process.ProcessorAffinity.ToInt64(); expected_affinity = $mask; logical_cpus = $allocation[$vm]; priority = [string]$process.PriorityClass; role = 'hardened-executor'; threads = $process.Threads.Count; cpu_seconds = $process.CPU }
    }
}
$record = @{ at = [DateTime]::UtcNow.ToString('o'); verified = ($failures.Count -eq 0); processes = $rows; label = $Label; elevated = $isAdmin; failures = $failures; expected_priority = $ExpectedPriority }
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
$record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceRoot "$Label-$stamp.json") -Encoding UTF8
@{ verified = ($failures.Count -eq 0); processes = $rows.Count; physical_p_cores = 8; elevated = $isAdmin; failures = $failures.Count } | ConvertTo-Json -Compress
if ($failures.Count -gt 0) { exit 1 }
