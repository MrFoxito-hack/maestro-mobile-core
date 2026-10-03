# Plan de Implementación: Aceleración del Plano de Usuario 5G SA (UPF) mediante Kernel Bypass con eBPF y XDP

Fecha: 30 de septiembre de 2026  
Entorno de ejecución: **Máquina Virtual UPF (`127.0.0.1:2223`, host `enp0s8` / `enp0s3`)**  
Tecnología clave: **eBPF (Extended Berkeley Packet Filter) + XDP (eXpress Data Path)**  
Estándar de referencia: **3GPP TS 23.501 § 5.8 (UPF Architecture) / TS 29.281 (GTP-U Specification)**  
Objetivo: **Multiplicar el rendimiento de paquetes, reducir la latencia a escala sub-milisegundo y eliminar el cuello de botella de TUN/TAP en espacio de usuario.**

---

## 1. Justificación y Problemática en el Core 5G Actual

En la arquitectura actual de Open5GS (`open5gs-upfd`), la interfaz **N3 (GTP-U sobre UDP 2152)** y la interfaz **N6 (Red de Datos / Internet)** se procesan mediante un dispositivo virtual de Linux TUN/TAP (`ogstun`):

```text
[Arquitectura Tradicional Lenta - Dos Cambios de Contexto por Paquete]

 (1) Tarjeta NIC (enp0s8) 
        │
        ▼ (Kernel Space)
  Pila de Red Linux (sk_buff, interrupciones, colas)
        │
        ▼ [Cambio de Contexto 1: Kernel -> User Space]
  open5gs-upfd (Copia de memoria, lectura de socket UDP, parseo GTP-U)
        │
        ▼ [Cambio de Contexto 2: User Space -> Kernel]
  Interfaz Virtual TUN (ogstun) -> Enrutamiento IP -> Salida N6 (enp0s3)
```

### Limitaciones de esta arquitectura:
1. **Sobrecarga de CPU:** El procesador gasta más ciclos haciendo cambios de contexto (*context switching*) y copias de memoria entre el kernel y el espacio de usuario que procesando los paquetes reales.
2. **Bufferbloat y Jitter:** Cuando varios terminales transmiten simultáneamente o hay congestión (como con el UE 004), las colas de `ogstun` se saturan, elevando el retardo a decenas de milisegundos y provocando *buffering* en transmisiones de video HLS.
3. **Límite de Rendimiento:** La pila tradicional de Linux rara vez supera 1.5 a 2 Gbps por núcleo en hardware virtualizado.

---

## 2. Arquitectura Propuesta: eBPF-XDP Zero-Copy UPF

La aceleración por **XDP (eXpress Data Path)** ejecuta programas en C directamente en la capa más baja del controlador de red (driver space), antes de que el kernel de Linux cree el objeto `sk_buff` y antes de cualquier cambio de contexto:

```text
[Arquitectura Acelerada eBPF-XDP - Kernel Bypass Zero-Copy]

                 (1) Paquete GTP-U llega a NIC (enp0s8)
                                │
                                ▼
         ┌──────────────────────────────────────────────┐
         │       HOOK XDP EN C (Driver Level)           │
         │  1. Parser directo: Eth -> IP -> UDP -> GTP  │
         │  2. Consulta BPF Map: TEID -> Reglas de Sesión│
         │  3. bpf_xdp_adjust_head(): Recorta los 36B   │
         │     de encabezados GTP/UDP/IP en memoria.    │
         │  4. Inyecta MAC de salida N6.                │
         │  5. Retorna: XDP_REDIRECT                    │
         └──────────────────────────────────────────────┘
                                │
                                ▼
         (2) Paquete sale disparado a N6 (enp0s3) hacia Internet
           (Cero copias de memoria, cero cambios de contexto)
```

---

## 3. Componentes del Sistema

### A. Programa eBPF en el Kernel (`upf_xdp_kern.c`)
Estará compilado con `clang -target bpf` y se cargará en la interfaz de red del UPF:
1. **Filtro N3 (Uplink):**
   * Detecta paquetes `UDP` con puerto destino `2152`.
   * Extrae el **TEID** (Tunnel Endpoint Identifier) de 32 bits de la cabecera GTP-U.
   * Busca en la tabla BPF (`bpf_map_lookup_elem`) la sesión correspondiente.
   * Realiza la desencapsulación in-place mediante `bpf_xdp_adjust_head(ctx, offset)`.
   * Reenvía el paquete IP puro directamente hacia la interfaz N6 (`XDP_REDIRECT`).
2. **Filtro N6 (Downlink):**
   * Intercepta paquetes que regresan de Internet hacia la IP del UE (ej. `10.45.0.45`).
   * Busca en el mapa inverso de BPF la tupla `IP_UE -> {TEID_DL, gNB_IP, gNB_MAC}`.
   * Expande la cabecera con `bpf_xdp_adjust_head(ctx, -offset)` e inyecta la cabecera GTP-U + UDP 2152 + IP externa hacia la gNodeB.
   * Retorna `XDP_TX` para enviarlo a la antena.

### B. Tablas Compartidas en Kernel (BPF Maps)
Estructuras de datos de alto rendimiento compartidas entre el kernel y el espacio de usuario:
* `sessions_uplink_map`: `hash_map<teid_t, session_info_t>`
  * Almacena: IP origen asignada, MAC destino en N6, contadores de bytes y reglas de cuota/rate-limit.
* `sessions_downlink_map`: `hash_map<ipv4_addr_t, downlink_tunnel_t>`
  * Almacena: TEID del gNodeB, IP del gNodeB, puerto UDP y parámetros QoS.

### C. Agente de Control en Espacio de Usuario (`upf_xdp_agent.py`)
* Monitorea la actividad del SMF (por ejemplo, leyendo las asociaciones PFCP en el UPF o consultando las sesiones del Core).
* Inserta, actualiza o retira dinámicamente las entradas en los BPF Maps cada vez que un terminal se conecta o desconecta.
* Provee métricas en tiempo real a la interfaz de MAEstro.

---

## 4. Fases de Implementación

### Fase 1: Entorno de Desarrollo BPF en la VM UPF (Puerto 2223)
- Instalar la cadena de herramientas:
  ```bash
  sudo apt-get update
  sudo apt-get install -y clang llvm libbpf-dev bpftool linux-headers-$(uname -r) gcc-multilib
  ```
- Verificar compatibilidad de la tarjeta de red con XDP (soporta modo `native` o `generic` en `enp0s8` y `enp0s3`).

### Fase 2: Implementación del Parser y Desencapsulador GTP-U
- Escribir `upf_xdp_kern.c`:
  - Definición de cabeceras estructuradas: `struct gtpv1_hdr`, `struct udphdr`, `struct iphdr`.
  - Verificación de límites de memoria (exigido por el *BPF Verifier* de Linux).
  - Lógica de decap/encap con `bpf_xdp_adjust_head()`.
- Compilar el bytecode:
  ```bash
  clang -O2 -g -Wall -target bpf -c upf_xdp_kern.c -o upf_xdp_kern.o
  ```

### Fase 3: Controlador de Espacio de Usuario y Gestión de Mapas
- Desarrollar `upf_xdp_loader.c` (usando `libbpf`) o script en Python con `bcc`/`pyroute2`.
- Cargar el programa XDP en la interfaz `enp0s8`:
  ```bash
  sudo ip link set dev enp0s8 xdpgeneric obj upf_xdp_kern.o sec xdp_upf
  ```
- Llenar los mapas con la sesión activa del UE 001 (`TEID` y `10.45.0.45`).

### Fase 4: Integración en MAEstro (Conmutación A/B y Métricas)
- Añadir en MAEstro un selector en la vista de Topología o Rendimiento:
  * **Modo A (Legacy):** Tráfico pasando por `open5gs-upfd` y `ogstun`.
  * **Modo B (Acelerado eBPF-XDP):** Tráfico pasando por el gancho XDP.
- Crear telemetría de contadores de paquetes y ciclos de CPU mediante `bpftool map dump`.

---

## 5. Diseño Experimental y Validación de Tesis (El Gran Benchmark)

Para demostrar científicamente la superioridad de la solución en la memoria de titulación:

### Prueba 1: Saturación de Rendimiento (Throughput Máximo)
* Inyectar tráfico UDP/TCP masivo con `iperf3 -P 8 -b 0`.
* **Métrica evaluada:** Mbps logrados antes de caída de paquetes y uso de CPU en el UPF.
* **Hipótesis:** eBPF multiplica el throughput alcanzable en al menos un **300% a 500%** frente al modo TUN/TAP en el mismo hardware.

### Prueba 2: Prueba de Latencia y Jitter en Tiempo Real
* Enviar paquetes de prueba con alta frecuencia (ping de 1 ms / trafico VoIP/Gaming).
* **Métrica evaluada:** RTT promedio, percentil 99 (p99 latency) y desviación estándar del retardo.

### Prueba 3: QoE de Video Streaming (HLS) bajo Congestión
* Reproducir video HLS en el UE 001 mientras el UE 004 inyecta tráfico competidor.
* **Métrica evaluada:** Tiempo de inicio del video (*Startup Delay*) y pausas de carga (*Rebuffering Ratio*).
* **Demostración:** En el modo tradicional el video sufre caídas de MOS ITU-T P.1203; en el modo eBPF el video mantiene MOS óptimo por la ausencia de colas en el espacio de usuario.

---

## 6. Entregables para la Tesis de Titulación

1. **Código Fuente:** Repositorio con el programa eBPF en C (`upf_xdp_kern.c`), el loader y el módulo de sincronización.
2. **Capítulo de Tesis Dedicado:**
   * *Capítulo:* "Optimización del Plano de Datos 5G SA mediante Programación en el Kernel con eBPF y XDP".
   * Diagramas de flujo de paquetes, explicación del BPF Verifier y análisis matemático de latencia de colas.
3. **Paper Científico (Formato IEEE):**
   * Título preliminar: *"High-Throughput, Low-Latency 5G User Plane Acceleration Using In-Kernel eBPF/XDP Processing in Open-Source Core Networks"*.
   * Gráficas de rendimiento comparativo y tablas de consumo de recursos.


## 7. Cierre experimental de la Fase 4 — 2 de octubre de 2026

Se completó una campaña de 24 pruebas A/B (TCP/UDP, UL/DL, tres repeticiones),
más cuatro comprobaciones controladas de bypass. El informe auditable, sus
condiciones, tablas y limitaciones están en
[RESULTADO.md](reportes/evidencias/upf-xdp-20261002/RESULTADO.md).

- Corrección de `curl`: `check=False`; errores de conectividad auxiliares no abortan el benchmark.
- TCP recibido: UL 5,002 → 6,179 Mbps (+23,54%); DL 1,069 → 3,497 Mbps (+227,30%).
- UDP a 10 Mbps ofrecidos: UL +3,24%; DL +14,93%. La carga ilimitada produjo intentos fallidos conservados.
- Bypass absoluto: verificado con cero RX/TX y PCAP vacío de `ogstun` en cuatro ventanas controladas a objetivo 2 Mbps.
- En la campaña principal, 10/12 ventanas XDP fueron vacías; otras dos registraron cinco errores ICMP de reensamblado, documentados sin excluirlos.
- CPU, RTT, p99, variación del RTT, jitter UDP y contadores BPF extraídos de evidencias reales. Seis pruebas de API aprobadas.
- Estado final: Legacy, servicio UPF activo, sesión UE `10.45.1.183` operativa.

El cierre experimental **no confirma** una mejora general de +300–500% ni RTT
submilisegundo. Se usó XDP genérico sobre SKB: las descripciones anteriores de
ejecución nativa/zero-copy son objetivos de arquitectura, no propiedades
demostradas en esta VM. El prototipo no sincroniza integralmente políticas
PFCP/QER/URR. HLS multi-UE y MOS no se midieron en esta campaña.
