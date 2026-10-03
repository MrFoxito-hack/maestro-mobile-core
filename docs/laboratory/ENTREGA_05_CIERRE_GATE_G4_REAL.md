# Entrega 05 — Piloto vivo y evaluación del cierre G2/G4

Fecha: 30 de septiembre de 2026. Estado: piloto operativo completado; G2/G4 abiertos.

El nombre del archivo conserva el entregable solicitado. No implica que G4 esté
cerrado. Una ejecución operativa y una comparación científicamente válida tienen
criterios diferentes; este informe conserva también los intentos fallidos.

## Intervenciones y condiciones comprobadas

- Recarga única de **50 000 000 bytes** al UE 001 mediante la API de gestión CHF,
  con identificador idempotente e intención guardada antes de la solicitud. Cuota:
  500 000 000 → 550 000 000; consumo al recargar: 406 146 339 sin modificación;
  reserva: 12 000 000 sin modificación. Evidencia privada:
  `data/laboratory/commissioning/entrega05-topup-d3bdce13baef462f82eae2d0fbcf249e/transaction.json`.
- La sesión anunciada por nr-cli para UE 004 no existía en el SMF. Se reinició
  exclusivamente `ueransim-ue-04.service`, con temporizador independiente de
  arranque armado antes; se verificó que el proceso del UE 001 no se reiniciara.
  Ambos SMF se consultaron posteriormente. IP vigentes: 001=`10.45.0.45`,
  004=`10.45.0.46`. Evidencia:
  `data/laboratory/commissioning/entrega05-ue04-af07ba66212541b5819cf08893cfee63/report.json`.
- No se borraron consumos ni reservas CHF. No se instaló una nueva versión PCF.
  La comprobación nativa de la entrega 04 fue `-fsyntax-only`, no despliegue.
- Tras los dos primeros intentos, se recargó también UE 004 en **50 000 000 bytes**
  para conservar el margen del nuevo bloque. Cuota: 102 428 800 → 152 428 800;
  consumo: 44 373 994 y reservas: 6 500 000, ambos conservados en esa transacción.
  Saldo libre posterior: 101 554 806. Evidencia:
  `data/laboratory/commissioning/entrega05-topup-004-3d89df34243542ff94611d0e74301e37/transaction.json`.
- Plan inmutable: un bloque, semilla 0, OFF seguido de ON, carga solicitada de
  19 Mbps durante 10 segundos por tratamiento, vídeo HLS de 30 segundos,
  presupuesto declarado de 70 MB y captura de 4 MB. La carga solicitada no es
  una medida de recepción ni demuestra un cuello de botella agregado por slice.

## Implementación

`acceptance.py` admite el protocolo `qoe_operational_pilot_v1` exclusivamente
desde el worker con asignación real y preflight vivo. El registro de admisión
no es un campo aceptado por la API pública. `dry_run` conserva su aislamiento.
La disponibilidad del piloto no cambia `full_core_acceptance=false`.

El transporte SSH verifica en Linux la identidad, generación y caducidad del
propietario, bajo un lock remoto y un diario SQLite de intención. Una operación
de resultado desconocido no se repite. Se rechazan tokens antiguos y una
recuperación de generación anterior. Se conserva una marca de ejecuciones
retiradas. Este control abarca las órdenes del transporte del piloto; no abarca
otros escritores EMS ni las decisiones internas del PCF.

Se comprobó el protocolo en el Core con órdenes inocuas de `printf`, incluyendo
rechazo de propietario obsoleto y liberación:
`data/laboratory/validation/remote-guard-57108e37c31a483c87e6329e45f3179f/`.
Los tokens privados de esa prueba no son parte del expediente del alumno.

Antes de instalar rutas se arman y verifican los temporizadores Linux de
compensación de 300 segundos y sus scripts de limpieza. El apagado temporal de
NWDAF dispone además de un temporizador de arranque. Se registran 35 segundos
de espera del baseline; esa espera no prueba el estado de las políticas.
La carga corre en una unidad limitada a 16 segundos, con timeout de 14 segundos.

El vídeo atraviesa DN → UPF → UE, y después un relay SSH entrega los segmentos
al navegador del host. Las medidas del reproductor incluyen el coste del relay.
Se verifican hashes de segmentos y contadores de recepción con boot e ifindex.
Las descargas fallidas se guardan con código de salida; una excepción de red en
Playwright ya no termina Node antes de exportar su diagnóstico.

La recepción del competidor usa intervalos del cliente iperf3 UDP reverse. En la
versión 3.9 observada, `end.sum` no permite inferir correctamente bytes recibidos;
el parser rechaza huecos, duplicados, contadores de emisor y duración incompleta.

Se genera un solo conjunto de medios por campaña, conservado en `media-assets/`
con hashes. ON reutiliza esos bytes mediante escritura remota en bloques con
offset esperado y creación exclusiva; así se respetan los límites de argumentos
de Linux y no se repite una escritura de resultado desconocido.

P.1203 conserva la entrada derivada de ffprobe y de los eventos del reproductor.
Puede reutilizar como calculadora offline el modelo ya instalado en la VM Core,
sin publicar el resultado a NWDAF. No se inventa un MOS cuando no hay reproducción
completa o un perfil admitido. La gráfica de espera en `/laboratory` es descriptiva.

## Primer intento: fallo y recuperación comprobada

Ejecución `365a92b0ba8f4726a1f4f340c9798053`, campaña
`2297b5332e904cc6bd65b832474a4778`, experimento
`4ab461a85ff642bfa40cf5c9a3aa0760` del docente.

- `execution_status=failed`, `validity_status=inconclusive`, hipótesis no evaluada.
- Llegó a OFF y a tráfico UDP real. Hubo un fallo de transporte de HLS y el
  proceso Node salió sin guardar `baseline-player.json`. No hay MOS ni comparación.
- Se conservaron manifiesto, descriptor, eventos, captura, ffprobe, segmentos
  verificados y registro iperf en
  `data/laboratory/runs/365a92b0ba8f4726a1f4f340c9798053/`.
- La compensación comprobó NWDAF activo, unidades de carga/origen/captura
  inactivas, puertos y rutas retirados, y cuotas sin modificación. Los tres
  diarios remotos terminaron en `released`, sin intenciones pendientes.
- `recovery_verified=true` tiene alcance explícito
  `nwdaf_operational_regime_and_owned_auxiliaries`. El informe conserva
  `policy_recovery_verified=false`; no acredita checkpoint efectivo del Core.

## Segundo intento

Ejecución `febec0b95379424592b01677883e2eaa`, campaña
`767fb9e642674db8af64fb138b50789e`, sobre la misma revisión y con otra asignación
limitada a una ejecución. El primer intento conserva todos sus registros.
Plan e inicio: `data/laboratory/commissioning/entrega05-retry-3c7a8e2c6e124e0c82081fe9280a25d4/`.

OFF terminó `PLAYED_TO_END`: espera inicial **7,2846 s**, MOS estimado P.1203
**4,038984**, 2 160 425 bytes de medios recibidos, sin rebuffering. El competidor
recibió 11 667 600 bytes según intervalos del cliente.

La preparación de ON generó contenido distinto (2 160 606 bytes frente a
2 160 425 de OFF). La comprobación de igualdad rechazó la pareja antes de ON.
La ejecución terminó `failed`, conservando la medida OFF y la recuperación
operativa verificada. Esta detección motivó la generación única de medios; no
se eliminó el control ni se mezcló OFF con otra ejecución.

## Tercer intento con medios idénticos

Ejecución `ac1039d04fba4bb3b35323bec7aeabf6`, campaña
`36fae7075b1d4307b15890fe5654c407`, misma revisión y semilla. Evidencia:
`data/laboratory/runs/ac1039d04fba4bb3b35323bec7aeabf6/`.

Terminó `execution_status=completed` a las 14:35:35 UTC, con 13 pasos registrados,
`network_measurements=true`, recuperación operativa verificada y
`validity_status=inconclusive`.

| Medida | OFF | ON |
|---|---:|---:|
| Espera inicial observada (s) | 7,0615 | 3,4524 |
| MOS estimado P.1203 | 4,049165 | 4,281406 |
| Bytes de medios recibidos y verificados | 2 160 183 | 2 160 183 |
| Delta RX de interfaz (bytes) | 2 251 096 | 2 251 184 |
| Bytes recibidos por el competidor | 11 947 200 | 8 402 400 |
| Rebuffering | 0 | 0 |

La diferencia descriptiva ON−OFF es **−3,6091 s**. Ambos reproductores terminaron,
los 17 archivos de medios tienen hashes idénticos entre tratamientos, y el
expediente conserva `descriptor.json`, `manifest.json`, `execution-events.jsonl`,
`measurements.json`, entradas P.1203, capturas y trazas del reproductor. El
analizador excluye la pareja del contraste científico por políticas efectivas
iniciales y recuperadas sin verificar; no cambia ese dictamen por el signo del efecto.

La inspección posterior dejó los propietarios remotos `released`, sin acciones
inciertas, ni unidades, listeners, rutas o reglas temporales activas. AMF, ambos
SMF, PCF, NSSF, NWDAF y UPF activos. La unidad del UE observado se llama
`ueransim-ue.service`; `ueransim-ue-01` no es su nombre. El competidor conserva
`ueransim-ue-04.service`. Consumos finales observados: UE 001=413 123 272 bytes,
UE 004=66 303 381 bytes. No se restablecieron contadores.

Evidencia de salud: `data/laboratory/validation/entrega05-final-health/`.
Comprobación de la interfaz real y captura:
`data/laboratory/validation/entrega05-live-ui/`. El comprobador solo lee la
ejecución existente y comprueba estado, dos barras, MOS y dictamen inconcluso.

## Verificación de software al cerrar esta entrega

- Suite backend completa: **265 aprobadas**, 54,53 s.
- Navegador del módulo: **12 aprobadas**; comprobación adicional contra la API y
  la ejecución viva: aprobada.
- TypeScript, ESLint del módulo/herramientas y build Vite: aprobados.
- Vite conserva el aviso de chunks superiores a 500 kB.
- Casos nuevos: tokens/generaciones remotas, operación desconocida, recepción
  iperf, igualdad/integridad de medios y escritura acotada sin repetición incierta.

## Gates y trabajo restante

G2/G4 permanecen abiertos mientras no exista una campaña completa admitida con
políticas efectivas y recuperación verificadas. Quedan pendientes checkpoint de
SMF/UPF, exclusión de los demás escritores, recuperación independiente de esas
políticas y calibración del recurso compartido. La limpieza de auxiliares no
satisface esos requisitos. No se asigna automáticamente 85% ni 100% a este piloto.

No se ha utilizado la RTX 5070 ni se ha instalado un modelo de IA.
