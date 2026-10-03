# MAEstro: Plataforma Integral 5G SA (EMS, CHF, NWDAF & Aceleración eBPF/XDP)

> ⚠️ **AVISO LEGAL DE PROPIEDAD INTELECTUAL Y DERECHOS DE AUTOR**  
> **Autor y Titular Exclusivo:** Miguel Angel Alvizuri Yucra  
> **Proyecto Independiente de I+D en desarrollo para Trabajo de Egreso en Ingeniería de las Telecomunicaciones — Pontificia Universidad Católica del Perú (PUCP)**  
> **Año:** 2026. Todos los derechos reservados (*All Rights Reserved*).  
> 
> Queda **estrictamente prohibida** la copia, duplicación, distribución, uso comercial o presentación académica parcial o total de este código, arquitectura, benchmarks empíricos (eBPF/XDP +227%), parches de código en C o módulos (CHF, NWDAF, Terminal) por parte de terceros sin autorización expresa y por escrito del autor titular. El uso no autorizado será denunciado ante los comités de integridad académica de la universidad y ante las instancias legales de propiedad intelectual pertinentes (INDECOPI / D. Leg. 822). Ver archivo completo de licencia en [`LICENSE`](LICENSE).

---

## 1. Descripción General del Proyecto
**MAEstro** es una plataforma de grado operador diseñada para la gestión (EMS), tarificación convergente en tiempo real (CHF Nchf), observabilidad autónoma en bucle cerrado (NWDAF Release 16/17 con IA local Ollama) y aceleración de plano de usuario (UPF con eBPF/XDP) sobre redes 5G Standalone puras (3GPP Rel-15 a Rel-18).

### Módulos Principales de Autoría Propietaria:
1. **Aceleración In-Kernel de UPF con eBPF/XDP**: Decapsulación de túneles GTP-U en modo Zero-Copy con +227.30% de incremento en rendimiento TCP Downlink y bypass estricto verificado.
2. **Motor de Tarificación Convergente (CHF Nativo 5G)**: Implementación de la interfaz `Nchf` (3GPP TS 32.291) con control de cuota por tramos (`grant`) de 500 KB y corte automático por saldo cero.
3. **Parche de Idempotencia UDM en Lenguaje C**: Corrección del desbordamiento estático de pool de Open5GS (`UDM context exhaustion`) bajo norma 3GPP TS 29.503, validado bajo estrés continuo de 34,000 peticiones.
4. **NWDAF Closed-Loop con IA Local**: Muestreo continuo de KPIs de red, mitigación autónoma hacia PCF vía socket MML y diagnóstico inteligente con modelo LLM Qwen 2.5 7B en GPU RTX 5070 con soberanía de datos.
5. **Terminal Interactivo 5G (Video Lab)**: Cliente interactivo con conmutación de rebanadas (S-NSSAI), deregister formal NAS y reserva dinámica de QoS 5G+ (5QI=2 GBR) vía interfaz N5 Policy Authorization.

---

## 2. Puesta en Marcha en Laboratorio

### Requisitos:
* **Host**: Windows 11 con Python 3.11, Node.js / pnpm, GPU NVIDIA (opcional para IA local Ollama).
* **Testbed VMs**: Cluster VirtualBox 5G SA (Core VM en `127.0.0.1:2222`, UPF VM en `127.0.0.1:2223`, UE VM en `127.0.0.1:2226`).

### Ejecución Dual (Backend FastAPI + Frontend React):
```powershell
.\run_ems.ps1
```
* **Frontend**: `http://localhost:5173`
* **Backend API**: `http://localhost:8000` (Docs OpenAPI en `/docs`)

---
*Para detalles sobre licencias, autoría o solicitudes de evaluación, consultar el archivo [`LICENSE`](LICENSE).*
