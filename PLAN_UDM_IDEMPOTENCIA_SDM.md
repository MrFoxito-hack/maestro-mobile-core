# Plan de Implementación: Parche C de Idempotencia y Mitigación de Agotamiento de Suscripciones SDM en Open5GS UDM

Fecha: 30 de septiembre de 2026  
Entorno de ejecución: **Directo en el Core VM (sin intermediarios)**  
Ruta del repositorio Open5GS: `/home/emsadmin/maestro-charging/open5gs`  
Binario destino: `/usr/bin/open5gs-udmd`  
Estándar de referencia: **3GPP TS 29.503 Release 16 (§ 5.2.2.3 — SDM Subscription Management)**

---

## 1. Diagnóstico de Causa Raíz en Código C

Durante pruebas continuas y tormentas de reintentos NAS (por ejemplo, el bucle de `ueransim-ue-04` reintentando cada 1-2 segundos), el servicio `open5gs-udmd` colapsó con:

```text
ERROR: Maximum number of SDM Subscriptions [4096] reached (../src/udm/context.c:374)
ERROR: [imsi-999700000000004] udm_sdm_subscription_add() failed (../src/udm/nudm-handler.c:716)
```

### Inspección del código fuente vivo en `/home/emsadmin/maestro-charging/open5gs`:

1. **`src/udm/context.h`:**
   - La estructura `udm_sdm_subscription_t` está vinculada a `udm_ue_t` a través de la lista enlazada `udm_ue->sdm_subscription_list`.
   - La memoria se reserva mediante un pool estático de tamaño fijo `max_num_of_udm_sdm_subscriptions = 4096`.

2. **`src/udm/context.c` (Líneas 360–390):**
   ```c
   udm_sdm_subscription_t *udm_sdm_subscription_add(udm_ue_t *udm_ue)
   {
       ...
       ogs_pool_alloc(&udm_sdm_subscription_pool, &sdm_subscription);
       if (!sdm_subscription) {
           ogs_error("Maximum number of SDM Subscriptions [%d] reached",
                       max_num_of_udm_sdm_subscriptions);
           return NULL;
       }
       ...
       ogs_list_add(&udm_ue->sdm_subscription_list, sdm_subscription);
       return sdm_subscription;
   }
   ```
   **El fallo de diseño:** `udm_sdm_subscription_add()` **nunca comprueba si el UE ya tiene una suscripción activa previa**. En cada intento de registro o reinicio de UERANSIM, solicita un nuevo slot del pool global de 4096. Si un solo terminal reintenta continuamente sin emitir un `deregister` formal, agota los 4096 slots en ~70 minutos, dejando a toda la red 5G incapacitada de registrar nuevos abonados.

---

## 2. Modificaciones en Código C

Se aplicarán directamente sobre `/home/emsadmin/maestro-charging/open5gs`:

### A. Idempotencia en `src/udm/context.c` (Función `udm_sdm_subscription_add`)
Antes de llamar a `ogs_pool_alloc()`, iterar sobre `udm_ue->sdm_subscription_list`:
```c
udm_sdm_subscription_t *udm_sdm_subscription_add(udm_ue_t *udm_ue)
{
    udm_sdm_subscription_t *sdm_subscription = NULL;
    char id[OGS_UUID_FORMATTED_LENGTH + 1];
    ogs_uuid_t uuid;

    ogs_assert(udm_ue);

    /* --- [IDEMPOTENCIA 3GPP TS 29.503] --- */
    /* Si este UE ya cuenta con una suscripción SDM activa, se reutiliza su slot
       en lugar de consumir uno nuevo del pool de memoria. */
    ogs_list_for_each(&udm_ue->sdm_subscription_list, sdm_subscription) {
        if (sdm_subscription && sdm_subscription->id) {
            ogs_info("[%s] Reutilizando suscripción SDM existente [%s] (idempotencia activa)",
                     udm_ue->supi ? udm_ue->supi : "unknown", sdm_subscription->id);
            return sdm_subscription;
        }
    }
    sdm_subscription = NULL;
    /* ------------------------------------- */

    ogs_uuid_get(&uuid);
    ogs_uuid_format(id, &uuid);

    ogs_pool_alloc(&udm_sdm_subscription_pool, &sdm_subscription);
    if (!sdm_subscription) {
        ogs_error("Maximum number of SDM Subscriptions [%d] reached",
                    max_num_of_udm_sdm_subscriptions);
        return NULL;
    }
    memset(sdm_subscription, 0, sizeof *sdm_subscription);

    sdm_subscription->id = ogs_strdup(id);
    if (!sdm_subscription->id) {
        ogs_error("No memory for sdm_subscription->id [%s]", udm_ue->suci);
        ogs_pool_free(&udm_sdm_subscription_pool, sdm_subscription);
        return NULL;
    }

    sdm_subscription->udm_ue = udm_ue;

    ogs_hash_set(self.sdm_subscription_id_hash, sdm_subscription->id,
            strlen(sdm_subscription->id), sdm_subscription);

    ogs_list_add(&udm_ue->sdm_subscription_list, sdm_subscription);

    return sdm_subscription;
}
```

### B. Actualización en `src/udm/nudm-handler.c`
En la función receptora HTTP/2 (`POST /nudm-sdm/v1/{supi}/sdm-subscriptions`), si la suscripción es reutilizada:
* Liberar el `data_change_callback_uri` antiguo si cambió.
* Asignar el nuevo URI provisto por el AMF.
* Responder `HTTP 200 OK` (actualización) o `HTTP 201 Created` con el recurso persistente.

### C. Margen del Pool Preventivo en `src/udm/context.c`
Elevar el tamaño base del pool a 16384 para brindar tolerancia adicional en escenarios de alta densidad.

---

## 3. Procedimiento de Ejecución Directa en el Core

Todos los pasos se ejecutan de forma directa en el host Core vía SSH (`port 2222`, usuario `emsadmin`):

1. **Edición del código:**
   Modificar directamente `context.c` y `nudm-handler.c` en `/home/emsadmin/maestro-charging/open5gs/src/udm/`.

2. **Compilación incremental:**
   ```bash
   cd /home/emsadmin/maestro-charging/open5gs/build
   ninja src/udm/open5gs-udmd
   ```
   *(Tiempo estimado: < 10 segundos con los 8 vCPUs de la VM).*

3. **Instalación y Sustitución:**
   * Respaldar el binario anterior como `.original` (para permitir la comparativa A/B de la tesis):
     ```bash
     sudo cp /usr/bin/open5gs-udmd /usr/bin/open5gs-udmd.original
     ```
   * Instalar el binario compilado:
     ```bash
     sudo cp /home/emsadmin/maestro-charging/open5gs/build/src/udm/open5gs-udmd /usr/bin/open5gs-udmd
     ```

4. **Reinicio de Servicios:**
   ```bash
   sudo systemctl restart open5gs-udmd open5gs-amfd
   ```

5. **Verificación de Estabilidad:**
   * Comprobar que `open5gs-udmd` levanta en estado `active (running)`.
   * Verificar en logs que el mensaje de inicialización no reporta errores.

---

## 4. Prueba de Validación y Resultados Esperados

1. **Prueba de Registro de Terminal 1:**
   * Cambiar APN o iniciar sesión con UE 001.
   * La traza en MAEstro (`/traces/e2e`) debe mostrar `Nudm_SDM_Subscribe Response (200/201)` inmediato, seguido de `Registration Accept` (sin alarma `UE sin registro 5G confirmado`).

2. **Prueba de Resistencia a Tormenta:**
   * Levantar el UE 004 en bucle de reintento forzado.
   * Monitorear los logs de `open5gs-udmd`: se observará el log `Reutilizando suscripción SDM existente [id] (idempotencia activa)`.
   * El contador del pool se mantendrá en $\le 2$, impidiendo el agotamiento de memoria.
