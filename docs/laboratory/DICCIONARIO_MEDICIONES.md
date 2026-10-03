# Contratos de medición del laboratorio

Versión 2, 30 septiembre 2026. Implementación: `measurements.py`,
`player_observation.py`, `qoe_metrics.py`, `analysis.py` y `analyze_export.py`.
Los contratos describen cómo aceptar datos; su existencia no demuestra que el
colector vivo produzca todos esos datos.

| Medida | Unidad y cálculo | Fuente y reloj | Alcance y límites |
|---|---|---|---|
| `player_startup_delay_seconds` | `(firstPlaying-requested)/1000` | `performance.now()` de una misma página; primer evento `playing` conservado | Incluye el reproductor y el relay del host. No representa exclusivamente latencia del Core ni retardo unidireccional. |
| `receiver_throughput_bps` | `8*(bytes_after-bytes_before)/duration_seconds` | Dos contadores, reloj monótono de un host y época | Tasa media del intervalo. Recepción de payload e interfaz se distinguen; una interfaz puede incluir tráfico adicional. |
| `counter_delta_bytes` | Contador final menos inicial | Mismo punto, interfaz, sesión y época | Un reset, cambio de sesión/boot/interfaz o cobertura incompleta produce `null`, nunca cero inventado. Un delta cero observado sí es cero. |
| `p1203_mos` | Estimación P.1203 usando segmentos audiovisuales y pausas | Implementación existente fijada por NWDAF a `itu-p1203 1.8.3`; cálculo offline opcional en backend o entorno existente de la VM Core | Perfil audiovisual validado por ese módulo; no es opinión subjetiva ni una fórmula de RTT/pérdida. No publica la estimación a NWDAF. Si no existe calculadora admitida, `mos=null`. |
| `competing_received_bytes` | Suma de bytes de intervalos contiguos del cliente UDP reverse durante 10 s | JSON iperf3 del UE competidor, `sender=false` en cada intervalo | No usar `end.sum.bytes` como recepción en iperf 3.9. El parser rechaza intervalos duplicados, huecos y duración fuera de 9,5–11 s. No prueba por sí solo la oferta en el recurso compartido. |
| `rebuffer_count`, `rebuffer_seconds` | Conteo y suma de pausas registradas | Eventos del reproductor, tiempo de vídeo para posición y monótono para duración | No rellenar un intervalo sin eventos ni convertir una pausa abierta en una pausa de duración cero. |
| `unreserved_bytes` | `quota-consumed-reserved` | Lectura SQLite CHF de solo lectura | Saldo observado, no reserva de campaña. El débito puede llegar después del tráfico. Nunca se restaura el consumo como rollback. |
| `controller_pending_count` | Sesiones Internet con `nwdaf_pending` | Snapshot del hilo de eventos PCF | Solo transacciones NWDAF/MML de ese módulo. No cubre todos los escritores ni prueba QER efectivo. Extensión C preparada, no instalada. |
| Diferencia emparejada | Espera ON menos espera OFF | Dos ensayos válidos del mismo bloque | Negativa significa menor espera observada. Una pareja no demuestra significación, causalidad ni apoyo a la hipótesis. |

## Identidad y continuidad

La identidad normalizada incluye alias de sujeto, generación de sesión, PDU ID,
instancia NF y boot ID. Cada contador agrega host, época del reloj, ifindex,
punto de medida y referencia de evidencia. Una IP o un QER ID aislados no son una
identidad global. La generación debe provenir del ciclo de vida observado; no
inventar un identificador para esconder que se desconoce si hubo reconexión.

La comprobación viva de esta entrega encontró otra IP para el observado respecto
a la entrega 03. Ese hecho justifica resolver PDU/interfaz nuevamente en cada
preflight. Los campos de `ps-list` por sí solos no proporcionan toda la identidad
normalizada ni un historial de cambios de política.

UTC se conserva como referencia; no interviene en el delta de contadores de un
mismo reloj monótono. No se restan relojes de hosts diferentes. Una corrección del
reloj de pared no invalida un intervalo monótono continuo; un cambio de época sí.

## Dictamen de efecto

`effect_evidence` relaciona observaciones mediante IDs privados del expediente.
El análisis exige recepción de payload, identidad exacta, política observada,
ruta, baseline, recuperación y oferta superior al límite declarado. Comprueba
referencias, procedencia, continuidad, duración mínima y tolerancia explícitas.
Los productores siguen siendo internos: no existe POST público de medidas.

- `inconclusive`: falta un requisito o la correlación/calidad es insuficiente.
- `limit_not_observed`: la tasa medida supera el límite y tolerancia del contrato.
- `consistent_with_limit`: la tasa cumple ese criterio; no demuestra que la
  política sea la única causa posible.

La validez de la observación es independiente del resultado: una violación medida
puede ser una observación válida. `hypothesis_outcome=not_evaluated` en este
dictamen evita convertir un umbral técnico en una conclusión científica.

Los valores por defecto del contrato (ventana 5 s y tolerancia 0) son parámetros
del validador, no una calibración del testbed. El protocolo de campaña deberá
fijarlos antes de comparar resultados. No se declara enforcement por slice,
URLLC, latencia de reacción en milisegundos ni aislamiento físico.

## Reproducción y datos ausentes

`analyze_export` verifica entradas únicas y seguras del ZIP, inventario completo,
longitudes, SHA-256, huellas internas y referencias. Regenera comparaciones y
dictámenes sin red. En archivos históricos v2 reconstruye además la espera desde
timestamps y verifica el evento `playing`; un archivo v1 sin esos campos conserva
su insuficiencia. Las parejas con baseline/recuperación sin verificar se excluyen
aunque la espera del reproductor sí sea reproducible.

Las pruebas sintéticas llevan `source=synthetic_test` y no se aceptan como fuente
de un análisis `live_campaign` o `historical_import`. Los hashes detectan cambios;
no certifican autoría frente a alguien que reescribe contenido y manifiesto.
El analizador no vuelve a transmitir vídeo, no publica observaciones NWDAF y no
usa SSH, credenciales CHF ni GPU.
