# PCF canónico por Nudr — implementación y aceptación

Fecha de validación: 2026-09-24  
Alcance: Open5GS 5G SA del testbed MAEstro, alineado al perfil Rel-16 de la tesis.

## Resultado

El `open5gs-pcfd` desplegado ya no inicializa `libdbi`, no enlaza
`libmongoc`/`libbson`, no contiene una configuración `db_uri` activa y no
abre conexiones directas a MongoDB. El UDR es el único punto de acceso a los
datos persistentes del suscriptor.

La decisión de política de sesión combina dos fuentes SBI distintas:

1. `SmPolicyContextData` de `Npcf_SMPolicyControl`, enviado por el SMF:
   `subsSessAmbr`, `subsDefQos.5qi` y `subsDefQos.arp`.
2. `SmPolicyData` de `Nudr_DataRepository`, enviado por el UDR: selección de
   la entrada `SmPolicyDnnData` correspondiente al S-NSSAI y DNN solicitados.

Esta separación es deliberada: `SmPolicyDnnData` no define 5QI, ARP ni
Session-AMBR en los modelos OpenAPI usados por esta versión de Open5GS.

## Cambios instalados

- PCF sin `ogs-dbi.h`, `ogs_dbi_init`, `ogs_dbi_final`,
  `ogs_dbi_subscription_data` ni `ogs_dbi_session_data`.
- `libdbi_dep` retirado del ejecutable `open5gs-pcfd`.
- Copia profunda y liberación de la política DNN recibida por Nudr en el
  contexto de sesión PCF.
- QoS y Session-AMBR construidos desde el contexto recibido del SMF.
- Corrección del UDR para conservar el slice realmente solicitado y no volver
  de forma accidental a la primera entrada del suscriptor.
- Topología MAEstro: `PCF --Nudr/HTTP2--> UDR`; BSON queda exclusivamente
  entre UDR y MongoDB.
- Lector de terminal: deduplicación de SUPI reportados repetidamente por
  `nr-cli`, evitando el falso estado `Registrado · sin PDU`.
- Saneamiento del runtime UERANSIM: `ueransim-ue` queda como controlador del
  IMSI 001 y el alias duplicado `ueransim-ue-01` queda inactivo/deshabilitado;
  los UEs 02–20 no se modificaron.

## Evidencia E2E

Ejecución: `pcf-canonical-e2e-f60f84852ca1`

- Estado: **PASS**.
- IMSI: `imsi-999700000000001`.
- PDU Session 1: DNN `internet`, S-NSSAI `1-000001`, IP `10.45.1.227`.
- PDU Session 2: DNN `corporate`, S-NSSAI `1-000002`, IP `10.46.1.220`.
- PCAP: 173868 bytes, 503 registros HTTP/2 analizados.
- `GET /nudr-dr/v1/policy-data/ues/{supi}/sm-data`: observado para
  `internet` y `corporate`, tanto en la entrada como en la salida del SCP.
- Respuestas UDR `200 OK`: 8 observadas durante el procedimiento completo.
- Respuestas PCF `201 Created`: 3 observadas (asociación AM y las dos
  asociaciones SM).
- La respuesta de política PCF contiene `5qi: 9`, ARP y Session-AMBR.
- Paquetes entre PCF (`127.0.0.13`) y MongoDB (`27017`): **0**.

Artefactos:

- Evidencia estructurada local:
  `.work/pcf-canonical-e2e-f60f84852ca1.json`
- PCAP remoto:
  `/home/emsadmin/pcf-canonical-e2e-f60f84852ca1-IyBJkW/pcf-nudr.pcapng`
- Copia de seguridad previa al despliegue:
  `/home/emsadmin/pcf-canonical-1ffbd137b1a5-backup`

## Verificaciones de entrega

- `meson compile -C build`: sin trabajo pendiente y sin errores.
- SHA-256 del PCF instalado:
  `4f0ecd06a501cf418fa56c0201f040193420644eabdfb335c5ffa530aed6efdd`.
- `ldd /usr/bin/open5gs-pcfd`: sin `libmongoc` ni `libbson`.
- `grep ogs_dbi src/pcf`: sin resultados.
- `open5gs-pcfd`: activo.
- `open5gs-udrd`: activo.
- Backend MAEstro: 80 pruebas aprobadas.
- Frontend MAEstro: TypeScript y build Vite aprobados.

## Repetición de la aceptación

Desde `Code/backend`:

```powershell
.venv\Scripts\python.exe ..\infra\charging\verify_pcf_canonical.py --execute
```

La prueba detiene temporalmente las dos unidades que comparten el SUPI 001,
abre una conexión HTTP/2 nueva para conservar el contexto HPACK completo,
captura el procedimiento, valida la evidencia y restaura los servicios que
estaban activos.
