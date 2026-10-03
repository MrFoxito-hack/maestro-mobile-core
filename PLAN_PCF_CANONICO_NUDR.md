# Plan de Implementación: Alineamiento Canónico 3GPP Rel-16 de PCF vía Nudr (Eliminación del Atajo MongoDB en C)

El objetivo es eliminar el atajo de implementación en C de Open5GS donde el PCF (`open5gs-pcfd`) consulta directamente a MongoDB mediante `libdbi`/BSON, y reemplazarlo por la arquitectura canónica **3GPP TS 23.501 / TS 29.519**, en la cual el PCF obtiene las políticas exclusivamente del **UDR** a través de la interfaz SBI **`Nudr` (HTTP/2 + JSON)**.

> [!NOTE]
> **Aclaración canónica aplicada durante la implementación.**
> `SmPolicyDnnData`, recibido desde el UDR por `Nudr`, identifica y valida la
> política aplicable al DNN/S-NSSAI. Los valores de QoS suscrita (5QI, ARP y
> Session-AMBR) no forman parte de ese modelo: el SMF los entrega al PCF en
> `SmPolicyContextData` al crear la asociación `Npcf_SMPolicyControl`. El PCF
> combina ambas fuentes SBI y nunca consulta MongoDB de forma directa.

---

## Análisis Técnico y Diagnóstico del Código en C

Tras inspeccionar el código fuente de Open5GS (`/home/emsadmin/maestro-charging/open5gs`):

1. **UDR ya tiene soporte de la API Nudr Policy Data:**
   En `src/udr/nudr-handler.c` (líneas 1350–1430), el UDR ya implementa los recursos REST:
   - `GET /nudr-dr/v1/policy-data/ues/{ueId}/am-data`
   - `GET /nudr-dr/v1/policy-data/ues/{ueId}/sm-data`
   El UDR lee la información de MongoDB y responde con los modelos OpenAPI 3GPP `OpenAPI_am_policy_data_t` y `OpenAPI_sm_policy_data_t`.

2. **PCF ya emite la petición SBI a UDR:**
   En `src/pcf/npcf-handler.c` (líneas 247 y 570):
   - Al recibir la creación de política de sesión o movilidad, el PCF ejecuta `pcf_sess_sbi_discover_and_send(OpenAPI_service_name_nudr_dr, ..., pcf_nudr_dr_build_query_sm_data, ...)`.

3. **Dónde está el Atajo en C:**
   - **`src/pcf/nudr-handler.c`:**
     - En `pcf_nudr_dr_handle_query_am_data` (línea 67): Al recibir la respuesta de UDR, el PCF ignora el contenido de `recvmsg->AmPolicyData` y hace una llamada directa a MongoDB: `ogs_dbi_subscription_data(pcf_ue_am->supi, &subscription_data);`.
     - En `pcf_nudr_dr_handle_query_sm_data` (líneas 208–224): El PCF valida que `recvmsg->SmPolicyData` no sea nulo, pero **no guarda** las reglas de QoS ni los parámetros de sesión en el contexto de la sesión `sess`.
   - **`src/pcf/context.c`:**
     - En `pcf_get_session_data()` (línea 1029): Cuando el PCF necesita armar la respuesta para el SMF, ejecuta `ogs_dbi_session_data(supi, s_nssai, dnn, session_data);` haciendo una consulta BSON directa a MongoDB.
   - **`src/pcf/init.c` y `meson.build`:**
     - `init.c` inicializa el driver de base de datos con `ogs_dbi_init(ogs_app()->db_uri);`.
     - `meson.build` enlaza `libdbi_dep` (MongoDB C Driver) al ejecutable del PCF.

---

## Decisiones de Diseño y Alcance

> [!IMPORTANT]
> Al desacoplar `open5gs-pcfd` de MongoDB a nivel de compilación (`libdbi_dep`), el binario del PCF se vuelve 100% puro en microservicios 5G SBA: no necesitará `db_uri` en `pcf.yaml` y dependerá exclusivamente del UDR a través del SCP/NRF.

---

## Cambios Propuestos

### 1. Núcleo Open5GS en C (`/home/emsadmin/maestro-charging/open5gs`)

#### [MODIFY] [context.h](file:///home/emsadmin/maestro-charging/open5gs/src/pcf/context.h)
- Agregar un puntero en `pcf_sess_t` para almacenar la estructura de políticas de sesión recibida de UDR:
  ```c
  OpenAPI_sm_policy_dnn_data_t *sm_policy_dnn_data;
  ```
- Retirar el include `#include "ogs-dbi.h"` de `context.h`.

#### [MODIFY] [nudr-handler.c](file:///home/emsadmin/maestro-charging/open5gs/src/pcf/nudr-handler.c)
- **Para AM Data (`pcf_nudr_dr_handle_query_am_data`):**
  - Eliminar la llamada `ogs_dbi_subscription_data(...)`.
  - Extraer los parámetros de bitrate (`ue_ambr`) y allowed NSSAI directamente del mensaje de UDR o de `pcf_ue_am->policy_association_request`.
- **Para SM Data (`pcf_nudr_dr_handle_query_sm_data`):**
  - Parsear la lista `recvmsg->SmPolicyData->sm_policy_snssai_data`.
  - Localizar el S-NSSAI y DNN de la sesión solicitada y almacenar `OpenAPI_sm_policy_dnn_data_copy()` en `sess->sm_policy_dnn_data`.
  - Continuar el flujo normal hacia BSF / SMF.

#### [MODIFY] [context.c](file:///home/emsadmin/maestro-charging/open5gs/src/pcf/context.c)
- Modificar `pcf_get_session_data()`:
  - En lugar de invocar `ogs_dbi_session_data(supi, s_nssai, dnn, session_data)`, validar DNN/S-NSSAI con `sess->sm_policy_dnn_data` provisto por el UDR y extraer 5QI, ARP y Session-AMBR de `SmPolicyContextData`, ya copiado en el contexto de sesión por el manejador `Npcf`.
  - Si no existe sesión dinámica, consultar la configuración local YAML (`ogs_app_config_session_data`).
  - Eliminar la dependencia de `libdbi`.
- En `pcf_context_init()`:
  - Retirar el registro de dominio de log de dbi: `ogs_log_install_domain(&__ogs_dbi_domain, "dbi", ...)`.

#### [MODIFY] [init.c](file:///home/emsadmin/maestro-charging/open5gs/src/pcf/init.c)
- Remover `ogs_dbi_init(ogs_app()->db_uri);` en la inicialización.
- Remover `ogs_dbi_final();` en la finalización.

#### [MODIFY] [meson.build](file:///home/emsadmin/maestro-charging/open5gs/src/pcf/meson.build)
- Retirar `libdbi_dep` de las dependencias de `open5gs-pcfd`.
- Verificar que el binario compila de forma autónoma sin vincular `libmongoc` ni `libbson`.

---

### 2. Configuración de Open5GS (`/etc/open5gs/pcf.yaml`)

#### [MODIFY] `/etc/open5gs/pcf.yaml`
- Comentar o eliminar la directiva `db_uri: mongodb://localhost/open5gs` para constatar que el servicio no requiere base de datos.

---

### 3. Diagrama de Topología del EMS (`frontend/src/features/topology/ems-topology.tsx`)

#### [MODIFY] [ems-topology.tsx](file:///c:/Users/Foxi/Desktop/Tesis/Code/frontend/src/features/topology/ems-topology.tsx)
- Reemplazar el enlace `pcf-mongodb` (`"BSON (Atajo Open5GS)"`) por:
  ```tsx
  { from: 'pcf', to: 'udr' }
  ```
- Configurar la etiqueta canónica en `sbaInterfacesEdges`:
  ```tsx
  'pcf-udr': { label: 'Nudr', stroke: 'solid', color: '#475569' }
  ```
- Eliminar la línea del atajo BSON entre PCF y MongoDB.

---

## Plan de Verificación

### 1. Compilación y Validación de Enlaces
- Ejecutar en la VM:
  ```bash
  cd /home/emsadmin/maestro-charging/open5gs
  meson compile -C build
  ldd build/src/pcf/open5gs-pcfd | grep -i mongo
  ```
  *(Debe retornar vacío, confirmando que el binario del PCF no tiene dependencias dinámicas con MongoDB).*

### 2. Pruebas de Señalización y Flujo 5G SA E2E
- Desplegar el nuevo binario `open5gs-pcfd`.
- Iniciar AMF, SMF, UDR, PCF y gNodeB/UE UERANSIM.
- Realizar Attach y PDU Session Establishment con los UEs del laboratorio (`imsi-999700000000001`).
- Capturar tráfico en interfaz loopback `lo` con `tshark`:
  - Constatar petición HTTP/2 del PCF hacia UDR:
    `GET /nudr-dr/v1/policy-data/ues/imsi-999700000000001/sm-data`
  - Constatar respuesta HTTP/2 `200 OK` del UDR con `SmPolicyData`.
  - Constatar respuesta HTTP/2 `201 Created` del PCF al SMF con los parámetros QoS (5QI=9, ARP, Session-AMBR).
  - Verificar que en el puerto 27017 (MongoDB) no hay un solo paquete proveniente del proceso `open5gs-pcfd`.

### 3. Verificación Frontend
- Comprobar que la vista de topología muestra la línea formal `PCF ──Nudr──► UDR` y compilar con `pnpm run build`.
