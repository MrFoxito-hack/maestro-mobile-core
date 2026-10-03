#!/usr/bin/env bash
# ==============================================================================
# SCRIPT MAESTRO DE ARRANQUE: EMS EDUCATIVO 4G/5G (Linux / macOS / WSL)
# Trabajo de Egreso en Ingenieria de las Telecomunicaciones - PUCP
# Autor: Miguel Angel Alvizuri Yucra
# ==============================================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
BACKEND_PID='' FRONTEND_PID='' WORKER_PID='' WATCHDOG_PID=''
cleanup() {
    trap - EXIT INT TERM
    if [ -n "$WORKER_PID" ]; then
        kill "$WORKER_PID" 2>/dev/null || true
        wait "$WORKER_PID" 2>/dev/null || true
        (cd "$DIR/backend" && python -m app.laboratory.worker --watchdog-only --once) || true
    fi
    for ems_pid in "$WATCHDOG_PID" "$FRONTEND_PID" "$BACKEND_PID"; do
        if [ -n "$ems_pid" ]; then kill "$ems_pid" 2>/dev/null || true; fi
    done
}
trap cleanup EXIT
trap 'exit 0' INT TERM

echo "========================================================"
echo " INICIANDO EMS EDUCATIVO 4G/5G (MODO DUAL BACKEND+FRONT) "
echo "========================================================"

echo ""
echo "[1/3] Verificando Backend..."
cd "$DIR/backend"
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
python -m app.laboratory.worker &
WORKER_PID=$!
python -m app.laboratory.worker --watchdog-only &
WATCHDOG_PID=$!

echo ""
echo "[2/3] Verificando Frontend..."
cd "$DIR/frontend"
pnpm run dev --host &
FRONTEND_PID=$!

echo ""
echo "========================================================"
echo " SISTEMA OPERATIVO Y DISPONIBLE"
echo "  - Interfaz Web: http://localhost:5173"
echo "  - API Docs:     http://localhost:8000/docs"
echo "========================================================"
echo ""
echo "Laboratorio: worker y watchdog dry_run activos."
echo "Presiona Ctrl+C para detener los servicios."
wait
