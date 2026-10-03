# Entrega 06 — Investigador local de laboratorio en GPU RTX 5070

Fecha: 30 de septiembre de 2026. Estado: Asistente local integrado, medido y verificado en GPU; inferencia 100% privada y desconectada de la nube.

Esta entrega formaliza la activación de la **Fase F6** del plan maestro (`PLAN_LABORATORIO_INVESTIGACION_5G.md`), incorporando un asistente local de hipótesis científicas para el análisis de expedientes del laboratorio 5G SA sin alterar la reproducibilidad ni el aislamiento del Core.

---

## 1. Modelo y entorno de ejecución local

- **Motor de inferencia:** Ollama v0.35.0 configurado en bucle local (`127.0.0.1:11434`), con funciones de nube desactivadas y cero llamadas a proveedores externos.
- **Hardware de aceleración:** Tarjeta gráfica **NVIDIA GeForce RTX 5070** (12 227 MiB de VRAM total, arquitectura Blackwell, driver CUDA 13.2).
- **Modelo desplegado:** `qwen2.5:7b-instruct-q4_K_M` (GGUF cuantizado a 4 bits, 7.6B de parámetros).
  - **Digest verificado:** `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
  - **Contexto asignado:** 4 096 tokens.
  - **Offload de capas:** **100% GPU** (28 bloques transformadores ejecutados íntegramente en VRAM).
  - **Evidencia de procedencia:** `data/laboratory/validation/entrega06-model-provenance/`.

---

## 2. Arquitectura de seguridad e integridad

1. **Aislamiento de datos sensibles (Positive Projection):**
   El modelo nunca recibe credenciales, identificadores de abonado (SUPI/IMSI), direcciones IP privadas ni las causas docentes verdaderas inyectadas por el profesor. Solo se le proyecta un documento JSON limpio con métricas inmutables observadas (`startup_delay_seconds`, `p1203_mos`, bytes recibidos y pausas).
2. **Validación estricta de propuestas (Pydantic + Gramática):**
   - El modelo no tiene acceso a terminales, sockets de control ni MML del Core.
   - Todo intento de generar comandos ejecutables (`sudo`, `bash`, `powershell`, `curl`) es rechazado.
   - Las observaciones numéricas deben coincidir de forma exacta con la evidencia del servidor (`ev_02`, `ev_03`); no se admiten números inventados.
   - El lenguaje de las hipótesis debe ser explícitamente tentativo (`"Podría ser que..."`).
3. **Exclusión mutua entre inferencia y experimentación de red:**
   Se incorporó la tabla `lab_ai_slots` en SQLite. Si hay una inferencia en curso o período de enfriamiento en la GPU, el motor bloquea el inicio de campañas reales de tráfico en el Core para garantizar que el rendimiento del host no contamine la medición de paquetes.

---

## 3. Métricas empíricas de rendimiento en RTX 5070

Las mediciones del benchmark (`benchmark.py`) sobre el expediente real `ac1039d04fba4bb3b35323bec7aeabf6` registraron:

| Métrica observada | Valor medido | Observación |
| :--- | :---: | :--- |
| **Consumo de VRAM (en reposo)** | ~1 350 MiB | Sistema operativo y monitores |
| **Consumo de VRAM (con modelo)** | **6 046 – 6 130 MiB** | Modelo + contexto en GPU (~49.5% de VRAM) |
| **VRAM libre remanente** | **> 5 800 MiB** | Margen amplio para evitar *out-of-memory* |
| **Tiempo de respuesta total** | **4.79 s – 6.75 s** | Inferencia de 2 hipótesis estructuradas |
| **Tiempo al primer token (TTFT)** | **0.09 s – 0.29 s** | Respuesta interactiva inmediata |
| **Uso de CPU en inferencia** | **0.0%** | Inferencia totalmente descargada en la GPU |

---

## 4. Integración en la interfaz de usuario

- En la ruta `/laboratory`, dentro de la sección *Evidencias, comparación y cuaderno*, se añadió el componente `InvestigationAssistant`.
- El estudiante o docente puede seleccionar cualquier expediente de campaña y enviar consultas al tutor local.
- La interfaz visualiza:
  - Las hipótesis diferenciadas ($h_1$, $h_2$) con referencias directas y clicables a las evidencias (`#evidence-ev_02`, `#evidence-ev_03`).
  - La indicación explícita de lo que falta por comprobar.
  - La siguiente revisión metodológica sugerida.
  - Botón **"Usar revisión en mi cuaderno"**, que traslada la propuesta al borrador sin ejecutar comandos en el Core.
- **Captura real de la interfaz:** `data/laboratory/validation/entrega06-live-ui/assistant-real.png`.
- **Reporte de aceptación del navegador:** `data/laboratory/validation/entrega06-live-ui/assistant-report.json`.

---

## 5. Verificación de software

- **Pruebas del módulo de investigación:** **19 de 19 aprobadas** (`pytest tests/test_laboratory_investigation.py`).
- **Pruebas de laboratorio:** **164 de 164 aprobadas** (`pytest tests/test_laboratory*.py`).
- **Pruebas de navegador (Vitest + Playwright headless):** **14 de 14 aprobadas** (`pnpm exec vitest run src/features/laboratory --browser.headless`).
- **Compilación frontend:** Aprobada sin errores de TypeScript ni linting (`pnpm run build` en 7.17 s).

---

## 6. Estado de Gates y alcance

- **Fase F6:** **Completada y verificada.** El asistente de IA local opera sobre hardware local medido sin fugas de datos y con validación de evidencia.
- **Gates G2/G4:** Permanecen en estado abierto formal según lo estipulado en la Entrega 05, preservando la validez científica y la separación entre la recuperación operativa de recursos auxiliares y la verificación exhaustiva de políticas de red.
