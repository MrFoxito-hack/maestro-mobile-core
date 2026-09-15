# MAEstro CHF — primer incremento verificable

Implementación experimental y autónoma de un subconjunto de
`Nchf_ConvergedCharging` para el testbed de tesis. No forma parte del soporte
upstream de Open5GS y todavía no debe describirse como una implementación 3GPP
completa.

## Contrato fijado

- 3GPP TS 32.291 V16.15.0, API `Nchf_ConvergedCharging` v3.0.7.
- OpenAPI Release 16 fijada en el commit
  `3341e31b43378792ba465c1c702b657208b043c0` de `jdegre/5GC_APIs`.
- Recurso implementado: `POST /nchf-convergedcharging/v3/chargingdata`.
- Operaciones implementadas: Create, Update y Release.
- Perfil inicial: charging online por volumen total, un `ratingGroup` y
  `FinalUnitAction=TERMINATE`.

La propuesta comunitaria Open5GS #4421 se utiliza como antecedente de diseño.
El motor de consistencia, el esquema de datos, las decisiones de fallo y las
pruebas de este directorio son implementación del proyecto MAEstro.

## Propiedades ya comprobables

- reserva agregada por SUPI para impedir sobreasignación entre dos sesiones;
- ledger de eventos append-only;
- Create y Update idempotentes; Create se identifica por SMF, SUPI y
  `chargingId`, y Update por recurso, operación y secuencia;
- rechazo de una secuencia repetida con contenido diferente;
- `totalVolume` no se suma por segunda vez a UL/DL;
- liberación de reserva al cerrar una sesión;
- fallo conservador cuando la cuenta no está aprovisionada;
- API administrativa separada del namespace 3GPP.

SQLite permite ejecutar y probar este incremento localmente. Antes de las
pruebas concurrentes multi-SMF del VNRT se migrará el repositorio a PostgreSQL,
manteniendo las mismas invariantes transaccionales.

## Ejecución local

```powershell
cd C:\Users\Foxi\Desktop\Tesis\Code\chf
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8081
```

Pruebas:

```powershell
python -m pytest -q
```

## Límites actuales y siguiente puerta

Este incremento aún no incluye registro NRF, HTTP/2/h2c, routing mediante SCP,
cliente Nchf dentro del SMF ni aplicación PFCP URR en el UPF. La siguiente
puerta técnica es una prueba vertical aislada:

`SMF modificado -> Nchf Create -> cuota -> PFCP URR -> UPF Usage Report -> Update`.

Hasta superar esa prueba, la interfaz administrativa no se expondrá fuera de
localhost y MAEstro no mostrará el CHF como NF operativa.
