# MAEstro CHF — perfil experimental de cobro por volumen

Servicio independiente del proyecto de tesis; **no es soporte upstream de
Open5GS ni una implementación completa de 3GPP**.

## Contrato y alcance

- TS 32.291 V16.17.0, anexo electrónico oficial ETSI, API v3.0.7. El YAML
  distribuido en ese anexo conserva una referencia interna a V16.15.0.
- Tipos comunes: anexo oficial TS 29.571 V16.13.0.
- Los originales y sus SHA256 se verifican con `tools/fetch_contract.py`.
- Create, Update y Release; un rating group y un UPF por contexto de cobro.
- Contabilidad online por volumen (bytes IP), reserva compartida por SUPI,
  cuotas de reemplazo, registro separado de exceso y CDR educativo JSON.
- Un grant positivo con `TERMINATE` es una cuota final por consumir, **no**
  una instrucción para cortar inmediatamente.

Antecedente: [propuesta comunitaria Open5GS #4421](https://github.com/open5gs/open5gs/discussions/4421).
El código de contabilidad, persistencia y pruebas de MAEstro es propio.
Los parches al código AGPL de Open5GS conservan esa licencia.

## Componentes verificables

**Actualización 2026-09-16:** el circuito PFCP/Nchf y la liberación NAS ya
pasaron pruebas con UE y tráfico real. Consultar el
[informe de aceptación y límites](../docs/charging-acceptance-2026-09-16.md).
Las cifras y pendientes de los párrafos históricos siguientes describen el
checkpoint anterior; el informe actualizado prevalece. No es grado de producción.

El CHF dispone de SQLite WAL con transacciones, ledger/CDR inmutables,
replay idempotente de las tres operaciones, validación de propietario y
secuencias de uso, reconciliación explícita y separación de SBI/administración.
No libera reservas automáticamente al vencer un temporizador si el consumidor
podría seguir usando la cuota.

La suite incluye concurrencia, reinicio, fallos de base de datos, autenticación,
contrato OpenAPI oficial y tráfico TCP HTTP/2 contra Hypercorn.
Las pruebas de componentes **no equivalen** a una demostración con el UE.

El SMF experimental incluye estructuras y parser `smf.chf`, cliente C
asíncrono Create/Update/Release y espera de Create previa al establecimiento PFCP.
Update/Release todavía no están conectados al ciclo PFCP de servicio.
Compila sobre Open5GS v2.8.0 y tiene 144 comprobaciones nativas de configuración,
respuestas y reglas URR. El cliente C se probó contra el CHF real por TCP HTTP/2:
reserva inicial, credencial incorrecta, cuota final positiva, crédito disponible
cero por reservas concurrentes, cuenta deshabilitada y conexión rechazada con
reintentos limitados. También se comprobó el modo desactivado del componente.
La construcción de URR asocia UL/DL al mismo contador e incluye cuota, umbral
del 80 % y vigencia. El UPF experimental añade un control de cuota, desactivado
por defecto, con 56 comprobaciones unitarias. Su aplicación con tráfico real
del UE y el circuito cerrado completo siguen pendientes.
El ciclo HTTP/2 Create/Update/Release emitió un CDR de prueba (400 bytes de
fixture, no del UE); sus mensajes pasaron el esquema OpenAPI oficial Rel16.
El artefacto reproducible está en
[`infra/charging/patches`](../infra/charging/patches/0001-native-nchf.patch).
Los binarios instalados en las VMs no se han sustituido.
Resultados y límites: [checkpoint nativo](../docs/charging-native-checkpoint.md).

## Ejecución y pruebas

Crear un entorno virtual e instalar `requirements-dev.txt`. Configurar
`CHF_ADMIN_TOKEN` y `CHF_SBI_TOKENS` con secretos distintos de al menos 32
caracteres. Cada token SBI se vincula al UUID real de su SMF.
Es autenticación estática de laboratorio; **no es OAuth2 del NRF**.

```text
python -m hypercorn app.main:app --bind 127.0.0.1:8081
python -m hypercorn app.main:admin_app --bind 127.0.0.1:8082
```

Ambos procesos deben usar el mismo `CHF_DATABASE_PATH`. La aplicación SBI
no monta rutas administrativas. HTTP/2 claro queda limitado al laboratorio;
no exponer credenciales sobre una red no confiable.

```text
python tools/fetch_contract.py
python -m pytest -q
```

En la VM, ejecutar `tools/fetch_contract.py` y compilar `src/smf/chf-probe`.
Después, `tools/native_acceptance.py`
prueba el cliente C contra un CHF temporal en loopback, con cuenta/base
separadas. Conserva evidencias y termina los procesos creados. No activa N4.

## Puertas pendientes

No se ha demostrado todavía el circuito UE → UPF → Usage Report → Nchf Update
→ corte por cuota → Release/CDR. No habilitar cobro sobre sesiones del testbed
hasta conectar los reportes PFCP, comprobar enforcement con tráfico real y
completar la recuperación durable del cliente nativo.

También quedan NRF/SCP para CHF, empaquetado Debian, integración visual
MAEstro y experimentos E2E reproducibles. No se afirma HA con SQLite ni
corte al último byte: el margen real deberá medirse.
