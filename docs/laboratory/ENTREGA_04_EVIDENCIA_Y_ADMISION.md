# Entrega 04 — Evidencia reproducible y admisión del Core

Fecha: 30 septiembre 2026. Continuación del código existente, incluidas las piezas
de expediente y piloto real que estaban escritas pero pendientes de aceptación.
**El laboratorio completo todavía no está al 100%. G2 y G4 siguen abiertos.**
La RTX 5070 no se ha utilizado y no se ha instalado un modelo de IA.

## Cambios terminados en esta entrega

- Validación de contadores por sesión, generación, NF, boot, host, reloj e interfaz.
  Un dato ausente, ambiguo o reiniciado produce dictamen explícito y no un cero.
- Dictamen de efecto con referencias: ACK N7/PFCP sin recepción nunca demuestra
  enforcement; una violación medida no se confunde con una ejecución inválida.
- Separación comprobada de procedencia sintética, histórica y real. Los fixtures
  del adaptador de pruebas ya no se etiquetan como `live_qoe` ni mediciones de red.
- Reanálisis de datasets y efectos tanto en API como en ZIP offline. Referencias
  ajenas, hashes alterados, archivos ambiguos y manifiestos duplicados se rechazan.
- Reconstrucción offline de espera inicial a partir del reloj y evento `playing`
  conservados en la importación histórica v2. No se repite la campaña importada.
- Evidencias navegables en `/laboratory`: documento normalizado y procedencia,
  enlaces entre artefactos, tasa ausente distinguida de cero, dictamen y causas.
  Se conserva el cuaderno, exportación y comparación implementados anteriormente.
- Estimación P.1203 opcional preparada para cálculo local con el módulo existente;
  se elimina la publicación a NWDAF durante la verificación de resultados. La
  dependencia no está instalada en el backend actual: se informa `mos=null`.
- Corrección de las dos discrepancias conocidas de trazas, tras confirmar el
  inventario vivo: 127.0.0.15 corresponde a SMF-02 y 127.0.0.16 a BSF. El catálogo
  CSP admite también `smf2`; conserva el filtrado de BPF privado.

Véase [diccionario de mediciones](DICCIONARIO_MEDICIONES.md) para fórmulas,
fuentes, relojes, calidad y límites.

## Corrección del piloto que estaba en curso

Se encontró un adaptador extraído de la campaña infra, migración v4, selección de
modo real, heartbeat durante pasos largos y controles de UI ya escritos. Sus
pruebas de software podían completar dos tratamientos con un adaptador sintético.
Eso no acreditaba recuperación real ni autorización para ejecutar la extracción.

Los puntos que impedían admitirlo son concretos:

1. `systemctl start maestro-nwdaf` y la eliminación de listeners/rutas no verifican
   el estado previo de políticas efectivas del Core.
2. Comprobar la lease SQLite antes de SSH deja una ventana entre comprobación y
   efecto remoto; no es fencing del actuador ni coordina otros escritores.
3. El tratamiento OFF no verifica por sí solo ausencia de decisiones pendientes
   ni baseline efectivo comparable al tratamiento ON.
4. Los presupuestos necesitan enforcement total por cuenta y cierre, y la carga
   necesita calibración del recurso compartido.

`acceptance.py` ahora bloquea actuación sin opción de entorno ni bypass de rol.
El bloqueo se comprueba al final del preflight y antes de prepare/apply y del
generador de sesión. Se conserva la extracción para continuar la implementación.
`mode=real` sigue admitiendo un diagnóstico persistente con asignación explícita;
la UI lo identifica como tal y la plantilla declara `real_pilot_available=false`.
No transforma una asignación dry_run en permiso de Core.

Si no hubo intervención, la recuperación puede finalizar con alcance
`no_mutation_attempted`, visible en UI. Si existe un ensayo intervenido, limpiar
auxiliares sin prueba de política efectiva deja recuperación no verificada y
reserva bloqueada. No se borra consumo CHF ni se recargan cuentas en esta entrega.

## Observación nativa preparada

Se añadió `laboratory_snapshot` al socket local existente de PCF. Es de lectura,
en el hilo de eventos, con máximo 64 sesiones y respuesta acotada; indica
truncamiento. Devuelve época del proceso, flags del controlador y sus pendientes.
Declara explícitamente que no observa políticas efectivas ni todos los escritores.

`infra/check_laboratory_native.py` verifica la unidad C en un directorio temporal
remoto con las cabeceras y flags actuales, incluidos `-Wall -Werror`, mediante
`-fsyntax-only`. **No enlaza, instala ni reinicia el PCF.** Esta comprobación pasó:
`.work/lab-native-check-9054cc44ee3b44f1872052af2259778a/result.json`.

`policy_observer.py` rechaza respuestas incompletas, pendientes inconsistentes,
sesiones ambiguas y campos inválidos. Proyecta aliases, sin SUPI ni campos libres
del servicio. Un snapshot sano tampoco habilita la campaña.

La consulta al `/pdu-info` actual de SMF mostró QFI/5QI y datos N3, pero no MBR/QER
efectivos ni transacciones en vuelo. No se utiliza esa interfaz como si ofreciera
un checkpoint que todavía no expone.

## Evidencia ejecutada

Rutas relativas a la raíz del proyecto:

| Evidencia | Resultado y alcance |
|---|---|
| `data/laboratory/readiness/8b8b087c709e4e8b9cde9013a020dfa8/` | Sesiones y saldo observados; PCF AUTONOMOUS; extensión nativa aún no instalada. `execution_ready=false`. |
| `data/laboratory/validation/57e5a686c7d149b2a47f8e3a96711033/` | Cola SQLite aislada y SSH real de lectura. Termina con `real_actuator_not_accepted`, sin tráfico QoE; reserva liberada por no haber intervenido. No usa la cola productiva. |
| `data/laboratory/analysis/1f4532e1e763423d8f90630646f3c44b/` | ZIP de la campaña histórica existente y reanálisis offline. Dos versiones de dataset, ambas sin parejas científicamente válidas por baseline/recuperación no verificados. |
| `data/laboratory/inventory/cd8dc2c0fa434cc2ac6d52438295404d/` | YAML proyectado, procesos activos y sockets 7777 que respaldan la corrección SMF-02/BSF. |

El saldo y las IP de estos informes son observaciones temporales. El observado
ya no tenía la IP de la entrega 03; el colector resolvió la PDU vigente. Los
informes de operador y sus bases aisladas son privados, no paquetes preparados
para entregar a alumnos. El ZIP del expediente usa la proyección autorizada.

La reconstrucción adicional en `offline-analysis-with-player.json` reproduce
8,221 s (OFF) y 4,221600000000093 s (ON) desde el archivo histórico v2. Los originales
v1 sin timestamps/eventos quedan inconclusos. La validez de estas dos mediciones
no cambia la exclusión de la pareja por condiciones experimentales sin verificar.

## Verificación

- Suite global backend: **253 aprobadas**, sin los dos fallos anteriores de trazas.
- Tras incorporar la reconstrucción por evento de reproductor: **40 pruebas
  focalizadas aprobadas**, incluida una nueva; el catálogo final contiene 254
  pruebas backend, 134 del laboratorio. No se presenta la colección como una
  segunda ejecución global.
- Navegador Chromium: **12 aprobadas** en los dos archivos del laboratorio.
- TypeScript (`tsc -b`), ESLint del laboratorio y build Vite: aprobados. Vite
  conserva su advertencia de tamaño de un chunk general de la aplicación.
- Sintaxis C con cabeceras y flags reales: aprobada; no equivale a prueba funcional
  del binario instalado ni de la nueva respuesta en un PCF en ejecución.
- Cola aislada con lectura viva, manifestación SHA y rechazo previo a mutación:
  comprobada. ZIP histórico y análisis sin red: comprobados.

`git diff --check` global detecta whitespace en cambios preexistentes ajenos al
laboratorio; no se reescribieron esos archivos. No se hizo commit, reset ni
limpieza de modificaciones del usuario.

Comandos desde `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
.\.venv\Scripts\python.exe -m app.laboratory.worker --check-real
.\.venv\Scripts\python.exe ../infra/check_laboratory_native.py
.\.venv\Scripts\python.exe -m app.laboratory.analyze_export ../data/laboratory/analysis/1f4532e1e763423d8f90630646f3c44b/historical-dossier.zip
```

`--check-real` devuelve deliberadamente código 2 al mantener bloqueada la campaña;
PowerShell puede propagar código externo 1. No es una campaña QoE fallida en red.
Para frontend, desde `frontend`: `pnpm exec vitest run src/features/laboratory
--browser.headless`, `pnpm exec eslint src/features/laboratory`, `pnpm build`.

## Continuación necesaria

El siguiente trabajo crítico es obtener un checkpoint efectivo y observable de
SMF/UPF, integrar exclusión de todos los escritores y un guard con recuperación
independiente. Debe probar pérdida de token, muerte del worker y respuesta SSH
desconocida antes de retirar `require_real_actuator`.

Después: calibración y presupuestos reales, campaña desde UI con recuperación
verificada, evidencia original enlazada, incidentes reales con causa privada y
evaluación. El investigador GPU viene después de ese laboratorio sin IA. La
selección del modelo sigue abierta; disponer de 12 GB VRAM no demuestra por sí
solo calidad, compatibilidad del runtime ni ausencia de interferencia.

No se asigna 100% a archivos escritos ni a tests con mocks. La evaluación con
alumnos y la comparación de IA del plan tampoco se pueden reemplazar con pruebas
sintéticas.
