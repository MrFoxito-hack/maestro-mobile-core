# Entrega 03 — UE competidor y diagnóstico previo a F2-REAL

Fecha: 2026-09-30 UTC / 2026-09-29 Lima.

## Resultado y alcance

Se completó el paso 1 del briefing: se arrancó exclusivamente
`ueransim-ue-04.service`, que estaba inactivo. No se habilitó su arranque automático
ni se reinició `ueransim-ue.service`. Se verificaron las sesiones Internet:

| Rol | SUPI | IPv4 | Interfaz | Slice |
|---|---|---|---|---|
| Observado | imsi-999700000000001 | 10.45.0.2 | uesimtun0 | SST 1 / SD 000001 |
| Competidor | imsi-999700000000004 | 10.45.0.3 | uesimtun1 | SST 1 / SD 000001 |

El servicio secundario conserva su configuración existente, que también solicita
una sesión corporate. No se editaron configuraciones de Open5GS, del UE o del PCF,
ni código C, cuotas CHF o políticas. El UE secundario queda activo para el laboratorio.

**No se completaron los pasos 2 y 3: RealCoreAdapter actuante, campaña QoE desde
la cola, expediente de campaña y visualización de sus resultados siguen pendientes.
G2 y G4 permanecen abiertos.** Esta entrega produce un expediente de preparación,
no resultados de un experimento ON/OFF.

## Evidencia viva que cambia el siguiente paso

El PCF respondió a la operación de lectura MML `status` con modo `AUTONOMOUS`.
Las únicas claves de esa respuesta fueron `detail`, `mode`, `status`.
PID 749; SHA-256 del ejecutable en ejecución:
`b673ea9dd786e96bab32755897c4d68c77fbae9073c4224b092e044fae96c635`.

Esto verifica el modo, pero no aporta un checkpoint de políticas por sesión,
reglas/QER efectivos, ni transacciones N7 pendientes. El código revisado en
`nwdaf/native/mml-control.inc` ofrece status/mode/qos, y
`nwdaf/native/nwdaf-handler.c::apply_load` recorre todas las sesiones Internet
del slice seleccionado. La revisión del fuente local no equivale a probar su
correspondencia byte a byte con el binario desplegado.

Por tanto, cambiar MANUAL/AUTONOMOUS y devolver el modo inicial no demuestra
restauración del estado inicial de la red. Tampoco acredita exclusión frente
a otro operador MML o al controlador autónomo. El guard existente solo protege
los procesos de la sonda HTTP; la lease de SQLite solo protege el sandbox.

La lectura CHF mostró 17.022.595 bytes libres para el observado y 75.924.573
para el competidor. Son observaciones temporales, no reservas del presupuesto
de una futura campaña. El preflight usa un umbral de diagnóstico de 1 MB por UE;
**no autoriza una campaña con ese umbral**.

## Implementación incorporada

- `backend/app/laboratory/commissioning.py`: herramienta de operador con catálogo
  limitado a secundarios 02–06. Verifica SUPI y slice de configuración, registra
  intención antes de arrancar, no repite un arranque incierto, resuelve PDU/TUN
  vivos y comprueba continuidad del observado mediante sesión, PID/inicio y boot ID.
  Verifica también el S-NSSAI de la sesión PDU real. Un UE activo no se reinicia.
- `backend/app/laboratory/real_readiness.py`: recopila preflight vivo y modo PCF,
  escribe descriptor, observaciones, diario y manifiesto SHA-256. Mantiene
  `execution_ready=false` mientras faltan los requisitos de actuación y recuperación.
- `backend/app/laboratory/worker.py --check-real`: ejecuta solo ese diagnóstico.
  No inicializa ni consume la cola y no permite combinarlo con once/watchdog.
  La cola sigue usando SandboxAdapter; no se ha sustituido por un adaptador ficticio.
- `backend/app/laboratory/live.py`: transporte privilegiado para programas fijos
  de operador, con contraseña únicamente por stdin y plazo remoto conservado.
  No se añade ningún endpoint público de comandos ni permiso de actuación al alumno.
- `backend/app/laboratory/repository.py`: reintento acotado exclusivamente para
  SQLITE_BUSY/SQLITE_LOCKED al activar WAL, tras detectar una carrera de arranque
  API/worker en la prueba de inicialización concurrente.

Los estados se mantienen separados: completar la observación no valida una
hipótesis; el diagnóstico informa `validity_status=inconclusive` y
`hypothesis_outcome=not_evaluated`, sin MOS ni startup inventados.

## Rutas de evidencia

Raíz: `C:\Users\Foxi\Desktop\Tesis\Code`.

- `data/laboratory/commissioning/ue-04-start-observation.json`: lectura inmediatamente
  después del arranque, todavía PS-ACTIVE-PENDING. No usarla como prueba de sesión activa.
- `data/laboratory/commissioning/ue-04-active-observation.json`: PDU Internet activas.
- `data/laboratory/commissioning/ue04-slice-verified-20260930/report.json`:
  verificación automatizada final de identidad, slice y continuidad del observado.
- `data/laboratory/readiness/77101aff608143b1a2ca8f7bbb7688b1/`:
  `descriptor.json`, `report.json`, `raw-observations.json`,
  `execution-events.jsonl`, `manifest.json`.

Son archivos locales de operación con identidades reales. No son una exportación
seudonimizada para distribuir. Se guardan bajo `readiness`, no `runs`, para no
presentarlos como el primer expediente de campaña solicitado.

## Comandos reproducibles

Desde `C:\Users\Foxi\Desktop\Tesis\Code\backend`, PowerShell:

```powershell
# Solo inspección. Usar una ruta nueva; no sobreescribe evidencia anterior.
.\.venv\Scripts\python.exe -m app.laboratory.commissioning --index 4 --output ..\data\laboratory\commissioning\nueva-verificacion

# Si el secundario está inactivo, permite arrancarlo; nunca reinicia el observado.
.\.venv\Scripts\python.exe -m app.laboratory.commissioning --index 4 --execute --output ..\data\laboratory\commissioning\nuevo-arranque

# Diagnóstico vivo: escribe una ruta única y no toca la cola.
.\.venv\Scripts\python.exe -m app.laboratory.worker --check-real
```

`--check-real` finaliza deliberadamente con código 2 porque el Core aún no está
habilitado para campañas; `execution_status=completed` se refiere a la observación.
El lanzador PowerShell puede presentar un código externo 1 al propagar esa salida.

## Continuación necesaria para RealCoreAdapter

1. Identificar una interfaz fiable de lectura de políticas/sesiones efectivas e
   inflight del Core actual. `status` no basta. Si ninguna interfaz existente la
   expone, resolver expresamente la restricción de no modificar el PCF C antes de
   instrumentarlo; no sustituir el checkpoint por valores nominales supuestos.
2. Definir el dominio de exclusión real: el modo actual es global y el controlador
   recorre un slice, mientras la asignación docente vincula dos UEs. Incluir los
   otros escritores de políticas en la estrategia de exclusión/detección.
3. Implementar guard remoto persistente, ownership y recuperación independiente
   de PCF/tráfico/rutas; armarlo antes de cualquier intervención. Un timer que
   solo arranca NWDAF no equivale a restaurar el checkpoint de políticas.
4. Extraer componentes de las campañas infra: recepción HLS real, reproductor,
   muestreo PM, carga y captura acotadas. Calibrar competencia y presupuestar
   preparación, dos tratamientos, margen y cierre por cada SUPI.
5. Migrar explícitamente permisos, tablas y API de dry_run a real; no reutilizar
   silenciosamente una asignación dry_run. Integrar heartbeat durante las fases
   largas y recuperación ante resultados desconocidos sin repetir mutaciones.
6. Solo entonces ejecutar desde la cola, importar evidencias medidas al cuaderno,
   verificar `/laboratory` y evaluar G2/G4 con la campaña y fallos reales exigidos.

La RTX continúa sin utilizarse. No hace falta IA para resolver estos requisitos.

## Validación ejecutada

- Suite de laboratorio antes de las últimas tres comprobaciones adicionales:
  80 aprobadas. Las 12 pruebas del archivo nuevo de comisionamiento pasaron
  después de completar sus cambios.
- Suite global: 200 aprobadas y 2 fallos conocidos en trazas (catálogo CSP con
  SMF-02 y atribución de 127.0.0.15). La última prueba añadida, de separación
  entre diagnóstico y cola, pasó posteriormente en la ejecución focalizada.
- 15 rondas adicionales con tres inicializadores SQLite simultáneos: aprobadas.
- Verificación viva final de PDU/slice y continuidad del observado: completed/valid.
- Diagnóstico vivo de worker: observación completed; campaña execution_ready=false.

No se ejecutaron pruebas de frontend ni ensayos QoE nuevos: esa integración no
fue modificada ni se declara completada en esta entrega.
