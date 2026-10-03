# ==============================================================================
# SCRIPT MAESTRO DE ARRANQUE: EMS EDUCATIVO 4G/5G
# Trabajo de Egreso en Ingenieria de las Telecomunicaciones - PUCP
# Autor: Miguel Angel Alvizuri Yucra
# ==============================================================================

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " INICIANDO EMS EDUCATIVO 4G/5G (MODO DUAL BACKEND+FRONT) " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

$CodeDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# 1. Comprobacion de Testbed VM
Write-Host "`n[1/3] Verificando conectividad con el Testbed VM (Puerto 2222)..." -ForegroundColor Yellow
$tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port 2222 -WarningAction SilentlyContinue
if ($tcp.TcpTestSucceeded) {
    Write-Host "  -> Conectado exitosamente con la VM Ubuntu por SSH (127.0.0.1:2222)" -ForegroundColor Green
} else {
    Write-Host "  -> AVISO: El puerto 2222 no responde. Asegurate de que la VM VirtualBox este corriendo." -ForegroundColor Red
}

# Processes are hidden and logs belong to this launcher instance.
$ErrorActionPreference = 'Stop'
$runId = [guid]::NewGuid().ToString('N')
$logDir = Join-Path $CodeDir ".work\ems-$runId"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pythonPath = Join-Path $CodeDir 'backend\.venv\Scripts\python.exe'
$workerStop = Join-Path $logDir 'worker.stop'
$watchdogStop = Join-Path $logDir 'watchdog.stop'
$backendProcess = $frontendProcess = $workerProcess = $watchdogProcess = $null

function Stop-LabProcess($process, $stopFile) {
    if ($null -ne $process -and -not $process.HasExited) {
        New-Item -ItemType File -Path $stopFile -Force | Out-Null
        if (-not $process.WaitForExit(10000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    Write-Host "`nIniciando API, worker y watchdog de laboratorio dry_run..." -ForegroundColor Yellow
    $backendProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList '-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload' `
        -WorkingDirectory "$CodeDir\backend" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$logDir\backend.log" -RedirectStandardError "$logDir\backend-error.log"
    $workerProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList "-m app.laboratory.worker --stop-file `"$workerStop`"" `
        -WorkingDirectory "$CodeDir\backend" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$logDir\worker.log" -RedirectStandardError "$logDir\worker-error.log"
    $watchdogProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList "-m app.laboratory.worker --watchdog-only --stop-file `"$watchdogStop`"" `
        -WorkingDirectory "$CodeDir\backend" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$logDir\watchdog.log" -RedirectStandardError "$logDir\watchdog-error.log"
    $frontendProcess = Start-Process -FilePath 'pnpm.cmd' `
        -ArgumentList 'run dev --host' -WorkingDirectory "$CodeDir\frontend" `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$logDir\frontend.log" -RedirectStandardError "$logDir\frontend-error.log"

    Start-Sleep -Seconds 2
    foreach ($process in @($backendProcess, $workerProcess, $watchdogProcess, $frontendProcess)) {
        if ($process.HasExited) { throw "Un proceso termino al iniciar. Revisa $logDir" }
    }
    Write-Host "`nInterfaz: http://localhost:5173 | API: http://localhost:8000/docs" -ForegroundColor Green
    Write-Host "Laboratorio: solo dry_run. Logs: $logDir"
    Write-Host 'Presiona [Enter] para detener los servicios iniciados por esta consola.'
    Read-Host | Out-Null
}
finally {
    # Stop campaign work first; leave the independent recoverer alive until drained.
    Stop-LabProcess $workerProcess $workerStop
    if ($null -ne $workerProcess) {
        $drainProcess = Start-Process -FilePath $pythonPath `
            -ArgumentList '-m app.laboratory.worker --watchdog-only --once' `
            -WorkingDirectory "$CodeDir\backend" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "$logDir\recovery.log" -RedirectStandardError "$logDir\recovery-error.log"
        if (-not $drainProcess.WaitForExit(15000)) {
            Stop-Process -Id $drainProcess.Id -Force -ErrorAction SilentlyContinue
            Write-Warning 'Recuperacion pendiente. El siguiente arranque reconciliara el sandbox.'
        }
    }
    Stop-LabProcess $watchdogProcess $watchdogStop
    foreach ($process in @($frontendProcess, $backendProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            # Terminate only trees started by this invocation (Vite/uvicorn children).
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
    Write-Host "Servicios detenidos. Logs conservados en $logDir" -ForegroundColor Green
}
