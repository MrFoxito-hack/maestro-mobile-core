# Revisión CHF e integración MAEstro — 2026-09-16

## Dictamen

> Revisión histórica previa a las correcciones. Consultar el
> [checkpoint posterior con pruebas E2E](charging-acceptance-2026-09-16.md)
> para el estado actual. Se conserva este diagnóstico para trazabilidad.

**El plan completo no está terminado.** Existe una prueba del circuito de cobro
y una implementación experimental, pero el cierre contable y la recuperación
todavía tienen defectos. La conexión visual no certifica el plano de cobro.

Esta revisión inspeccionó el código local y el estado real de la VM. No volvió
a generar tráfico UE ni modificó saldos, reservas, CDR o servicios SMF/UPF.

## Hallazgos que bloquean declarar aceptación E2E

1. **Consumo omitido al recibir el reporte con cuota final.**
   `src/smf/chf-path.c:smf_chf_handle_usage_report` retorna inmediatamente cuando
   `final_units` es verdadero, sin guardar UL/DL de ese reporte. Después se pide
   Session Deletion y Release solo incorpora el delta del reporte de eliminación.
   El UPF ya hizo el snapshot del reporte anterior: ese uso no necesariamente se
   repite en el reporte de eliminación. Debe conservarse y contabilizarse una
   sola vez cada reporte, también cuando no se solicita nueva cuota.
2. **Release tardío no equivale a Release confirmado.**
   `src/smf/smf-sm.c:SMF_EVT_CHF_RESPONSE` escribe “acknowledged after session
   deregistration” para cualquier respuesta Release sin propietario vivo: no
   verifica transporte, HTTP 204 ni cuerpo. `client_callback` deja de reintentar
   cuando desaparece el propietario; `release_pending` no conserva el contexto.
   Falta retención del cierre o una transacción durable recuperable.
3. **Reportes concurrentes y secuencias.**
   `smf_chf_send_change` rechaza un reporte si hay una solicitud pendiente; no
   existe cola durable para conservar su uso. El puente acepta ausencia de
   UR-SEQN como cero y el Release genera `ur_seqn + 1` en lugar de conservar
   explícitamente la secuencia de la evidencia final. Faltan pruebas de
   duplicación, reordenamiento, timeout y reporte durante Update.
4. **Recuperación y distribución aún pendientes.**
   No se verificó journal SMF durable, recuperación después de reinicio,
   empaquetado privado ABI-compatible, receptor de notificaciones ni Nchf
   mediante NRF/SCP. El cliente inspeccionado es directo y estático.

Las correcciones de trigger y limpieza SBI evitan errores de control de flujo,
pero no prueban conservación de todo el consumo ni entrega NAS al UE.

## Estado observado en la VM

- `open5gs-chfd.service` activo; SBI `127.0.0.1:8081`.
- SBI identifica `experimental-rel16-volume-v2`; entorno de laboratorio sin
  autenticación SBI (`CHF_SBI_LAB_NO_AUTH=true`), limitado a loopback.
- Base activa `/home/emsadmin/maestro-charging/charging.sqlite3`: vacía en la
  revisión. No se rellenó con resultados de pruebas.
- Base separada `test-charging.sqlite3`: cuenta con cuota 20 000 B y débito
  8 224 B; dos sesiones RELEASED, una con UL=4 112/DL=4 112 y otra con cero.
  Ledger: ACCOUNT_UPSERT, CREATE, CREATE, UPDATE, RELEASE, RELEASE.
- Esos valores confirman lo almacenado, **no** que se hayan contabilizado todos
  los paquetes. La identidad saldo + débito = cuota no detecta uso omitido.
- SMF en servicio: `/usr/bin/open5gs-smfd`, hash
  `fb98285a8db096857c766cb5cc11f5fa164ff840ac5286ba3b84951d9b549a4b`.
- SMF compilado experimental, separado: hash
  `7f1ccb932c5f17d5ffdce70105016bab1b420a7db093e3078efa8f5bf206b186`.
- El parche exportado pasó `git apply --check --reverse` contra el árbol local.

## Integración realizada

- Ruta `/charging`: cuentas, sesiones, CDR y movimientos; filtro SUPI exacto,
  paginación, detalle bajo demanda y valores exactos en bytes.
- Navegación Servicios → Tarificación 5G y acceso desde Suscriptores.
- MML `LST CHF-ACCOUNT;`, `LST CHF-SESSION;`, `LST CHF-CDR;`: consulta real
  (primera página de 25), registro en historial/auditoría y restricción de rol.
  No se ofrece `DSP SW-VERSION` de Open5GS para este servicio Python externo.
- API EMS `/api/v1/charging/{status,accounts,sessions,cdrs,ledger}` solo GET,
  restringida a docente/administrador. Identificadores enmascarados también en
  registros anidados y respuestas `Cache-Control: no-store`.
- Credencial CHF de lectura distinta de la administrativa, sin permisos de
  cambio de cuota o reconciliación. Ningún secreto se envía al navegador.
- Management `127.0.0.1:8082`, unidad `maestro-chf-management.service`, consulta
  la **misma base activa** que SBI, a través de canal SSH desde el backend.
  No se expuso un puerto de administración a la red del testbed.
- Unidad iniciada, no habilitada al arranque automáticamente. Reprovisionamiento:
  `infra/charging/connect_management.py`, después de `stage_chf.py`, desde backend.
- Topología: CHF directo experimental, sin falsa dependencia MongoDB/NRF.
  El perfil ya no anuncia CDR educativo JSON como formato de facturación Rel16
  ni intenta tratar `config.py` como configuración YAML de una NF.
- Las métricas Nchf, nuevas alarmas y clasificación de trazas no se inventaron:
  su integración especializada sigue pendiente. Las consultas genéricas de
  servicio del catálogo no demuestran tráfico Nchf ni completitud contable.

## Pruebas de esta revisión

- CHF Python: 41 pruebas correctas (incluye permisos de credencial lectora).
- API EMS charging: 5 pruebas correctas, incluyendo MML, RBAC, enmascaramiento,
  filtros, límites, ausencia de escritura y error sin datos ficticios.
- Regresión backend (charging, API general, alarmas y métricas NF): 46 pruebas
  correctas en conjunto, incluidas las cinco anteriores.
- Suite nativa compilada existente: 2/2 pruebas Meson correctas. No se recompiló
  ni se desplegó una corrección C en esta revisión.
- Consulta real por el adaptador EMS: health/ready y las cuatro colecciones
  respondieron correctamente con cero registros, conforme a la base activa.
- TypeScript y Vite build comprobados para la nueva ruta.
- Navegador Edge headless: lectura real de la colección CDR vacía; tabla,
  detalle y paginación con fixture aislado; error 503 sin datos obsoletos;
  separación del escenario 4G. Sin errores JavaScript observados.
  Captura local `.work/charging-live-review.png` y comprobador reutilizable
  `frontend/tools/check-charging.mjs`.

## Siguiente criterio de aceptación

Antes de habilitar cobro de forma permanente: corregir la preservación de cada
delta PFCP y el cierre durable, ejecutar pruebas de concurrencia/reintentos y
repetir un experimento UE conservando PCAP, logs, reglas PFCP, ledger y CDR.
Comparar explícitamente uso observado, uso debitado, exceso y tráfico bloqueado.
La presencia de HTTP 204 o un saldo aritméticamente consistente no sustituye
esa reconciliación extremo a extremo.
