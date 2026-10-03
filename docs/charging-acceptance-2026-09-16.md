# CHF: aceptación del perfil experimental — 2026-09-16

## Dictamen actual

El circuito nativo SMF–CHF–UPF pasó las pruebas E2E descritas abajo con UE
UERANSIM y tráfico IP real. Esto NO certifica un CHF completo Release 16 ni
autoriza presentarlo como listo para producción. No es soporte upstream Open5GS.
Este documento actualiza los hallazgos históricos de `chf-review-2026-09-16.md`.

## Evidencia reproducible

Ejecución local: `.work/chf-e2e-4b988e531c1c/results.json`.
Comando desde backend: `python ../infra/charging/e2e_native.py --execute --case all`.
Este comando interrumpe temporalmente el testbed; usar solo en ventana de ensayo.
Utiliza bases de datos y credenciales privadas de prueba, rollback independiente
y restaura los servicios habituales; no reemplaza binarios/configuraciones instalados.

| Caso | Resultado observado | Contabilidad |
| --- | --- | --- |
| CHF desactivado | 20 respuestas ping; 0 solicitudes Nchf | No cobro |
| Cuota final de 10000 bytes | 5 respuestas; UE recibe NAS Release | 10280 observados, 10000 debitados, 280 de exceso, reserva 0 |
| Renovación hasta 20000 bytes | 10 respuestas; UE recibe NAS Release | 20560 observados, 20000 debitados, 560 de exceso, reserva 0 |

Los intentos automáticos posteriores del UE sin crédito también cerraron sus
contextos de cobro: 6 y 4, respectivamente. No quedaron reservas huérfanas en
estas ejecuciones. El exceso se conserva como evidencia: el control admite
el paquete IP que cruza la cuota, no promete corte exacto al último byte.

SMF probado: SHA256 `28c93ff10612a58c307af96dc216cad571db53e3b2fe8cab62cb133fc3496794`.
Los hashes de UPF y bibliotecas están en el JSON; el ejecutable solo no identifica
el comportamiento, porque PFCP está en una biblioteca dinámica.
Servicios core, UPF y UE restaurados: `active`.

## Correcciones incluidas

- Conservación de todos los reportes PFCP, incluido el consumo de cuota final.
- Release asíncrono conserva propietario y reintentos tras desmontar la sesión.
  Solo HTTP 204 sin cuerpo confirma el cierre.
- Corrección del orden de bytes de Reporting Triggers en Update URR; la
  regresión falla antes del parche y pasa después.
- Liberación NAS/N2 hacia el UE antes de terminar la limpieza SBI.
- Cancelación contable explícita si se rechaza crédito antes de crear N4,
  sin inventar un reporte PFCP de cero bytes.
- Journal privado, fsync previo al envío y bloqueo exclusivo POSIX.
- Charging ID persistente por NF, append-only y bloqueado. Archivos truncados
  provocan fallo seguro, no reinicio silencioso del contador.
- Recuperación explícita de un Release duradero mediante
  `chf/tools/recover_release.py`; nunca inventa consumo perdido.

Pruebas nativas: `meson test -C build --suite charging --print-errorlogs`,
2/2 suites aprobadas. Prueba HTTP/2 de cierre/reintento:
`/home/emsadmin/maestro-charging/.work/native-terminal-p004jz5y`.

## Límites y operación segura

Perfil conectado, no roaming, un rating group/flujo y un UPF por contexto.
No se ha demostrado continuidad general ante movilidad, CM-IDLE ni múltiples
UPF/QoS. Reportes inconsistentes o huecos de secuencia detienen el procesamiento
y exigen reconciliación; no se interpretan como consumo cero.

Conservar juntos identidad NF, contador y journal. Restaurar una copia antigua
del contador mientras existen sesiones posteriores puede reutilizar identidades:
se requiere procedimiento de migración/reconciliación, no borrarlo para arrancar.
El replay soportado es Release exacto; la recuperación automática de Create,
Update y estado N4 después de caída del SMF sigue pendiente.

## Release 16 y publicación

### Dependencias de seguridad

La auditoría inicial detectó avisos en h2 4.3.0 y Starlette 0.47.3.
Se actualizaron los requisitos a FastAPI 0.141.1 y h2 4.4.1; el resolver
seleccionó Starlette 1.6.0. La auditoría de requisitos posterior no reportó
vulnerabilidades conocidas (`.work/chf-dependency-audit-updated.json`). Esto no
es garantía de ausencia de vulnerabilidades ni auditoría de las VMs completas.
La suite Windows del CHF pasó 53 pruebas, con una omitida por bloqueo POSIX
y dos advertencias de deprecación. Las dependencias nuevas se prueban en
entornos aislados; no se reemplazó el entorno del servicio activo.

Confirmación Linux Python 3.10 con las dependencias actualizadas: 54 pruebas
aprobadas, dos advertencias de deprecación. También pasaron ambos casos
nativos HTTP/2 de cierre/reintento con ese entorno:
`/home/emsadmin/maestro-charging/.work/native-terminal-b2umopcf`.
La prueba UE E2E anterior utilizó el entorno previo; no se atribuye a la
actualización de dependencias una prueba UE que todavía no se ha repetido.

Fuentes de mantenimiento: [aviso oficial h2](https://github.com/python-hyper/h2/security/advisories/GHSA-6hr6-w5qg-qmwg),
[versiones de FastAPI](https://fastapi.tiangolo.com/release-notes/).

### Alcance normativo

Contrato acotado: TS 32.291 V16.17.0, anexo oficial API v3.0.7, tipos comunes
TS 29.571 V16.13.0. `fetch_contract.py` verifica los originales y hashes.
La liberación de sesión se contrasta con TS 23.502 §4.3.4.2 del material local.
Validar esquemas no demuestra todos los requisitos normativos. El CDR JSON
educativo no debe etiquetarse como CDR ASN.1 normalizado completo.

Antes de grado de producción faltan, como mínimo:

- NRF/SCP modelo D real y sus pruebas de descubrimiento, fallo y recuperación.
- TLS/mTLS y autorización SBI apropiada; el cliente h2c con token es de laboratorio.
- Recuperación integral de procesos, pruebas de caída y reconciliación operacional.
- Pruebas prolongadas, concurrencia/carga, HA y recuperación de almacenamiento.
- Empaquetado instalable, upgrade/rollback y lock/SBOM de dependencias.
- Cobertura NAS de rechazo, retransmisiones, temporizadores y UE no alcanzable.
- CI ejecutada en el repositorio destino y revisión de licencias/publicación.

La CI añadida aún no se ha ejecutado en GitHub. No se ha publicado el repositorio
ni desplegado permanentemente el nuevo binario SMF/UPF.
