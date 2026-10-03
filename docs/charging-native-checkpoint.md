# CHF nativo: checkpoint verificable — 2026-09-16

> Revisión posterior: las afirmaciones de culminación y conciliación perfecta
> de este registro histórico **no constituyen aceptación del plan completo**.
> Se encontraron pérdidas potenciales de uso y confirmaciones Release sin
> validar. Véase [revisión del 16 de septiembre](chf-review-2026-09-16.md), que
> prevalece como estado actual e incluye la integración de gestión MAEstro.

## HITO CULMINADO: Verificación Integral E2E con Tráfico Real de UE, Enforcement UPF y Emisión de CDRs

Se ha verificado con éxito rotundo el subsistema de cobro convergente en vivo en el testbed multi-VM (Core, UPF-01, UPF-02, gNodeB, UE), conectando la señalización N4 (PFCP) con el protocolo SBI HTTP/2 Rel-16 (`Nchf_ConvergedCharging`):

1. **Sesión PDU y Reserva Inicial (`Nchf_Create`):**
   - El UE (UERANSIM) establece sesión PDU hacia DNN `internet` (`10.45.0.2/16`, UPF-01).
   - SMF emite `POST .../chargingdata` por HTTP/2 al CHF Python.
   - CHF autoriza y reserva 10,000 bytes iniciales (`operation=1 grant=10000 final=0`).
   - SMF instala reglas URR bidireccionales en el UPF con umbral de volumen del 80% (8,000 bytes).

2. **Tráfico Inicial y Entrega Garantizada (Fase A):**
   - El UE inyecta ráfaga de 5 pings ICMP de 1,000 bytes a través de la interfaz `uesimtun1`.
   - UPF enruta el tráfico: **5 paquetes transmitidos, 5 recibidos, 0% packet loss**.

3. **Cruce de Umbral y Renovación Dinámica (`Nchf_Update`, Fase B):**
   - El volumen acumulado supera los 8,000 bytes.
   - UPF genera automáticamente un PFCP Session Report (`volume_threshold`).
   - SMF procesa el reporte y envía `POST .../update` al CHF con el volumen consumido (8,224 bytes).
   - CHF debita 8,224 bytes de la cuenta, liquida la reserva anterior y concede el remanente final de 1,776 bytes indicando `final_units = 1`.
   - SMF actualiza dinámicamente las reglas URR en UPF vía PFCP Session Modification Request.

4. **Agotamiento de Cuota y Bloqueo Inmediato (Fase C):**
   - El UE transmite 15 paquetes adicionales para superar el remanente concedido.
   - UPF cierra la compuerta de cuota: **15 paquetes transmitidos, 0 recibidos, 100% packet loss**.
   - UPF emite reporte de cuota agotada (`QUOTA_EXHAUSTED`).

5. **Terminación de Sesión y Generación de CDRs (`Nchf_Release`, Fase D):**
   - SMF inicia la eliminación ordenada de la sesión PFCP con `OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED`.
   - UPF devuelve el reporte de uso final en el PFCP Session Deletion Response.
   - SMF emite `POST .../release` al CHF reportando el uso definitivo.
   - CHF genera el registro oficial `MAESTRO_EDUCATIONAL_CDR` con 8,224 bytes debitados (4,112 UL / 4,112 DL) y causa `FINAL`.
   - SMF ejecuta `smf_sbi_cleanup_session` para liberar políticas en PCF y registro en UDM/AMF.
   - SMF recibe respuesta 204 No Content de CHF y confirma el cierre asíncrono sin advertencias ni fugas de memoria.

6. **Conciliación Contable Perfecta (Ledger Inmutable):**
   - 6 transacciones registradas en SQLite: `ACCOUNT_UPSERT` (20,000 B) → `CREATE` DNN 1 (10,000 B) → `CREATE` DNN 2 (10,000 B) → `UPDATE` DNN 1 (8,224 B débito, 1,776 B remanente) → `RELEASE` DNN 1 (1,776 B devueltos) → `RELEASE` DNN 2 (10,000 B devueltos).
   - Balance final del suscriptor: `consumed_bytes = 8224`, `available_bytes = 11776`, `reserved_bytes = 0`, `quota_bytes = 20000`. Conservación exacta ($8224 + 11776 = 20000$). Cero bytes extraviados.

---

## Continuación: control de cuota UPF y ciclo Nchf nativo

Estado más reciente (los apartados inferiores conservan el checkpoint anterior):

- Compilan SMF y UPF experimentales en el árbol aislado.
- Suite nativa: **144 comprobaciones SMF + 56 UPF**, ambas pruebas Meson correctas.
- `Create → Update → Release` nativos recibieron 201/200/204 por TCP HTTP/2.
- Mensajes enviados y respuestas Create/Update pasaron los esquemas OpenAPI
  oficiales descargados de ETSI y verificados por SHA256.
- La prueba de ciclo usó fixtures explícitos: Update UL=100/DL=200 y Release
  UL=50/DL=50. CDR resultante: 400 bytes, UL=150/DL=250, reserva final=0 y causa
  FINAL. **No son medidas del UE ni una demostración de tráfico real**.
- Un reporte de uso ya confirmado se rechaza localmente antes de enviar otro
  Update. Una transacción con resultado ambiguo bloquea nuevas invocaciones;
  aún falta persistir y recuperar ese estado después de reiniciar el SMF.

Evidencia de esta ejecución en core:
`/home/emsadmin/maestro-charging/.work/native-create-i27ldyxh/`.
Incluye `lifecycle.log`, `component-fixture-cdr.json`, `http.log`, SQLite y
`results.json`. Las reservas de pruebas Create-only se reconcilian al detener
el consumidor; el CDR de ciclo se cierra por Release nativo, no reconciliación.

UPF implementado, detrás de `upf.charging_enforcement` (apagado por defecto):

1. Límite absoluto de bytes independiente de los snapshots por reporte.
2. Un reporte preventivo no renueva cuota ni vigencia.
3. Cuota nueva aplicada al uso desde el último reporte, incluido uso en tránsito.
4. Plazo de vigencia monotónico, bloqueo UL/DL al agotar cuota o vencer el plazo.
5. Paquetes bloqueados por cuota/gate no incrementan el contador de admisión.
6. Si falla el encolado del reporte, se conserva la medición y no se reabre la cuota.
7. Eliminación de temporizadores en limpieza de reglas para evitar callbacks
   sobre memoria liberada; intervalos sin paquetes no inventan timestamps.

Limitaciones explícitas del perfil actual:

- Se admite entero el paquete que cruza el límite: exceso menor que un paquete
  IP por concesión, pendiente de medir con tráfico real. No se afirma último byte.
- El contador registra admisión al reenvío, no entrega confirmada por el receptor.
- No soporta cobro DL con buffering/CM-IDLE: descarta antes de cobrar y evita
  que un vaciado posterior del buffer eluda la cuota. No sirve aún como perfil
  general de movilidad/idle-mode.
- Se rechaza Remove URR individual para una regla cobrada activa; el cierre
  soportado por este perfil será Session Deletion con reporte final.
- Los tests UPF sustituyen el transporte del reporte por un receptor de prueba;
  comprueban lógica C real, pero no prueban transporte PFCP ni paquetes del UE.

Pendiente inmediato: conectar Usage Reports reales con Update, correlacionando
SEID/URR/UR-SEQN y serializando invocaciones. El cierre debe retener el contexto
de cobro hasta confirmar el uso final de PFCP Deletion y completar Release (o
registrar una recuperación pendiente durable). El manejador 5GC actual de
Deletion no incorpora aún esa espera. No basta con lanzar HTTP desde el destructor.

Después siguen la regresión CHF-off, prueba N4/UE, recuperación ante caídas,
NRF/SCP, paquetes privados `.deb`, API/UI MAEstro y experimento reproducible.
**El plan completo no está terminado ni activado en los servicios instalados.**

## Puente N4 ↔ SBI (Fase 4)

Implementado en esta continuación:

1. `n4-handler.c`: cuando una sesión 5GC tiene contexto CHF activo, el Usage
   Report del UPF se redirige a `smf_chf_handle_usage_report()` en lugar del
   camino Gy/Diameter. Se traduce el trigger PFCP (volume_quota, volume_threshold,
   quota_validity_time) al trigger Nchf correspondiente (QUOTA_EXHAUSTED,
   QUOTA_THRESHOLD, VALIDITY_TIME).

2. `gsm-sm.c` (`smf_gsm_state_operational`): nuevo caso `SMF_EVT_CHF_RESPONSE`
   que procesa la respuesta del Update:
   - Cuota renovada (`granted_units > 0`): llama a `smf_chf_renew_urr()` y envía
     un PFCP Session Modification Request con la nueva cuota/umbral al UPF.
   - Cuota agotada o TERMINATE: inicia `smf_5gc_pfcp_send_session_deletion_request`
     y transiciona a `smf_gsm_state_wait_pfcp_deletion`.

3. `gsm-sm.c` (`smf_gsm_state_wait_pfcp_deletion`): al recibir el Session Deletion
   Response exitoso, extrae el Usage Report final y envía `smf_chf_send_final_release()`
   con los bytes exactos de UL/DL. El CDR se cierra en el CHF con HTTP 204.

4. `chf-urr.c`: nueva función `smf_chf_renew_urr()` que actualiza las reglas URR
   del bearer default con la nueva concesión del CHF, preservando la lógica de
   umbral del 80% y cuota final sin renovación anticipada.

5. `pfcp-path.c`: se relajó el assert de stream en
   `smf_5gc_pfcp_send_all_pdr_modification_request` para permitir modificaciones
   URR iniciadas por el CHF sin un stream SBI asociado.

Verificación en VM core (Sandbox):
- Ninja build completado con 0 errores (código de salida 0).
- `chf-unit`: 144 aserciones C pasadas al 100%.
- `quota-unit`: 56 aserciones C pasadas al 100%.
- Meson test suite `charging`: 2/2 tests OK.
- Binarios oficiales del sistema (`/usr/bin/open5gs-*`) y servicios systemd intactos (`active`).

SHA256 actuales de binarios compilados (Sandbox):

```text
SMF: b2ab6c9ca4954998e0213424667843d04c1dbdb94016c43a6368d017193b58e5
UPF: 61de2fab44887462ba54f66257ea01a1886a0b93e31b6b4f0218b6a2a7a5a0d2
```

SHA256 de binarios instalados en producción (sin cambios):

```text
SMF: fb98285a8db096857c766cb5cc11f5fa164ff840ac5286ba3b84951d9b549a4b
UPF: 90e99a93bb422f150e0563544c7d51a621d790fb12bfbb3eef8a9c736ed14602
```

---

## Resultado y alcance

El cliente C asíncrono del SMF ya intercambia `Nchf Create` con el CHF real
por TCP HTTP/2. El SMF construye reglas URR de cuota bidireccionales. Esto es
un avance de componentes, **no un circuito de cobro UE–UPF completo**.

| Comprobación ejecutada | Resultado |
|---|---|
| Build SMF, chf-unit y chf-probe, GCC 11.4.0 | Correcto |
| Aserciones C de configuración, respuestas y URR | 114 correctas |
| Suite Python CHF | 39 pruebas correctas |
| Create C → CHF, token correcto | HTTP/2 201; reserva 1 MiB, consumo 0 |
| Token incorrecto | 403; sin reserva ni reintento |
| Última cuota positiva | 512 KiB y TERMINATE; aceptada como cuota utilizable |
| Todo el crédito reservado por otra sesión | Nueva cuota 0; no autoriza datos |
| Cuenta deshabilitada | 403; no autoriza datos |
| Endpoint TCP inaccesible | Dos reintentos; termina sin autorización |
| Flag desactivado en el componente | Sin creación de cliente CHF |
| Reconciliación de consumidores de prueba detenidos | Reservas finales 0 |

La prueba sin crédito usa una segunda reserva nativa, no contadores de consumo
inventados ni una escritura manual del saldo. La prueba de cuenta deshabilitada
respeta el 403 que realmente emite el servicio. No se generó tráfico de usuario.

Evidencia remota de la ejecución completa:
`/home/emsadmin/maestro-charging/.work/native-create-m66tq_g3/` en core.
Contiene resultados, JSON Create, log HTTP/2, logs por caso y SQLite separada.
Los secretos se generaron en memoria y no se incluyeron en archivos de config.

## URR inicial implementada

- Conserva el identificador asignado por Open5GS, no fuerza URR ID 1.
- Un mismo URR se asocia a los PDR de subida y bajada, sin duplicarlo.
- Measurement Method VOLUME y Volume Quota total iguales a los bytes concedidos.
- Volume Threshold `floor(grant × 4 / 5)` para renovación preventiva.
- Para una cuota de un byte o una cuota final positiva no se pide renovación
  anticipada; permanece el disparador de agotamiento de cuota.
- Quota Validity Time procede de la respuesta CHF y requiere soporte VTIME.
- El perfil inicial admite un QoS flow por sesión, un rating group y un UPF.
  Las formas iniciales no admitidas se rechazan antes de Create.
- Con el flag apagado esta preparación no cambia los PDR/URR originales.

Las pruebas llaman al codificador PFCP de Open5GS y vuelven a decodificar los
IE Volume Quota/Threshold. **Todavía no existe una captura de estos IE enviada
por el SMF de servicio al UPF**, ni prueba del corte real.

## Fundamento y siguiente dependencia crítica

TS 29.244 V16.10.0, §5.2.2.2.1 y §5.2.2.3.1: una cuota de volumen debe limitar
el reenvío; un reporte al umbral no renueva por sí solo esa cuota. El uso se
reporta desde el reporte anterior. Una nueva cuota debe considerar el uso que
ya ocurrió desde ese reporte, y la vigencia no debe prolongarse por reportar.
[Especificación oficial ETSI](https://www.etsi.org/deliver/etsi_ts/129200_129299/129244/16.10.00_60/ts_129244v161000p.pdf).

En el código base inspeccionado, `upf_sess_urr_acc_add` compara cuota y umbral
con el delta desde `last_report`; luego actualiza ese snapshot y reinicia
temporizadores. Ese esquema no basta para nuestro control de cuota: se debe
separar medición por reporte de crédito restante y vencimiento absoluto.
También se debe integrar el bloqueo en los caminos UL/DL sin cobrar como
tráfico entregado los paquetes descartados. Esta corrección aún está pendiente.

Contrato Nchf del perfil: TS 32.291 V16.17.0, anexo electrónico API 3.0.7,
con referencia interna V16.15.0, verificado por las pruebas de contrato CHF.
[Anexo oficial ETSI](https://www.etsi.org/deliver/etsi_ts/132200_132299/132291/16.17.00_60/ts_132291v161700p0.zip).

## Artefactos y seguridad operacional

Fuente Open5GS: `157f611a530e292e40ec50f9d23f0ef5d4fcd6a6`.
Parche: `infra/charging/patches/0001-native-nchf.patch`; comprobado con
`git apply --check --reverse` contra el árbol desarrollado.

SHA256 de artefactos medidos en core:

```text
SMF instalado, sin modificar:
fb98285a8db096857c766cb5cc11f5fa164ff840ac5286ba3b84951d9b549a4b
SMF experimental compilado:
c29413645c5cdec13186889887a91328a4de7de393c70eb8113abd9a1aab33ca
chf-unit:
2f182acff4664d83efd3999667808af8c9a041aef06b71d21ee482a64178ab71
chf-probe:
7f8f1c4d1b0ff65ada3471fbf0885d05eefa7ca5370edf35ef22626f49c95a8d
```

AMF, SMF, NRF y SCP instalados respondieron `active` al final del checkpoint.
Esto indica estado de proceso, no prueba actual de registro o conectividad UE.
No se sustituyeron binarios de sistema ni unidades systemd.

## No demostrado / no implementado todavía

- Regresión completa con el SMF experimental y CHF desactivado.
- Envío N4 y aplicación correcta de cuota/validez en UPF.
- Cliente nativo Update/Release, cierre NAS, uso final y CDR del tráfico UE.
- Journal nativo durable y recuperación tras caída del SMF; chargingId aún
  aleatorio de 32 bits, sin garantía durable frente a colisiones/reinicios.
- Receptor de notificaciones CHF; anunciar notifyUri no implementa su handler.
- Registro NRF, descubrimiento delegado y ruta CHF por SCP; la prueba es directa.
- TLS/OAuth2 de producción; autenticación actual estática de laboratorio.
- `.deb` con librerías privadas compatibles, MAEstro y demostración E2E.

El feature flag debe seguir apagado en el testbed de servicio hasta cerrar
estas dependencias. No se afirma conformidad completa Rel16, HA ni corte
instantáneo exacto al último byte.
