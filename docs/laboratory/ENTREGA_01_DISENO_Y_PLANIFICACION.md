# Entrega 01 — Diseño y planificación experimental

Fecha: 29 de septiembre de 2026.

Estado: implementada y probada en código. No se desplegaron parches Open5GS, no se ejecutaron campañas reales y no se instalaron modelos de IA.

## Resultado

MAEstro incorpora una pantalla `Diagnóstico → Laboratorio 5G`, ruta `/laboratory`, para crear una pregunta e hipótesis, guardar revisiones del diseño, validarlas estáticamente y generar un plan reproducible. Cada plan compara control desactivado y activado en bloques emparejados, con orden aleatorizado y conservado.

La pantalla indica que esta es la etapa de diseño. No ofrece un botón de ejecución. El backend no registra rutas de inicio, cancelación, inyección ni actuación sobre el Core en este módulo.

## Funcionalidades implementadas

- Experimentos privados por usuario y testbed, incluyendo alumnos sin permisos globales de operación.
- Título, pregunta e hipótesis persistidos.
- Descriptor tipado, sin campos arbitrarios, con límites de valores y rechazo de no finitos.
- Guardado de diseños incompletos como revisiones; no pueden planificarse hasta resolver los errores.
- Revisiones numeradas, inmutables por API y triggers SQLite, con hash SHA-256 de JSON canónico UTF-8.
- Planes inmutables y persistentes con semilla, orden, tratamiento y versión del planificador.
- Estados de ejecución, validez e hipótesis separados en el plan; nunca se presentan como resultados medidos.
- Validación de UE diferentes, campos requeridos, niveles únicos y presupuesto mínimo de carga competidora.
- Estimación de número de ensayos, segundos de medición y bytes de carga competidora.
- Advertencias explícitas sobre tráfico audiovisual, preparación, cabeceras, recuperación y preflight faltante.
- Idempotencia transaccional: repetir una solicitud con la misma clave y contenido devuelve la misma entidad; contenido diferente devuelve 409.
- Diario de creación de entidades en la misma transacción de persistencia.
- Reintento de formulario tras timeout conserva la clave mientras se mantenga montado y no cambie el contenido.
- Inventario de capacidades basado en evidencia del repositorio, marcado `live_verified=false`.
- Separación clara entre fallo de slicing simulado y capacidades documentadas.

## Almacenamiento y arranque

El almacén `laboratory.db` se crea junto a la base del EMS al iniciar el backend. Puede configurarse con `EMS_LABORATORY_DATABASE_PATH`. Se rechaza usar la misma ruta que `EMS_DATABASE_PATH`.

SQLite local, WAL, foreign keys, transacciones breves y `user_version=1`. Un esquema futuro no se rebaja silenciosamente. Los planes aún se almacenan como documentos JSON: no existe cola ni worker.

Si el backend está activo sin recarga de código, es necesario reiniciarlo por su procedimiento habitual para incorporar el nuevo router y crear el almacén. Esta entrega no reinició servicios activos. La ruta de frontend queda incorporada por el generador de TanStack durante el build.

Los hashes y triggers no vuelven el almacén inviolable frente al dueño de la base. No se implementan todavía exportación firmada ni permisos de filesystem administrados por el laboratorio.

## API disponible

Todas las rutas requieren autenticación existente.

| Ruta | Comportamiento |
|---|---|
| GET `/api/v1/laboratory/capabilities` | Inventario documental; no sondea el Core |
| GET `/api/v1/laboratory/templates` | Primera plantilla QoE/closed-loop |
| GET `/api/v1/laboratory/experiments` | Hasta 200 experimentos privados, recientes primero |
| POST `/api/v1/laboratory/experiments` | Crear pregunta/hipótesis |
| GET `/api/v1/laboratory/experiments/{id}` | Revisiones y planes privados |
| POST `/api/v1/laboratory/experiments/{id}/revisions` | Guardar descriptor |
| POST `/api/v1/laboratory/revisions/{id}/validate` | Validación estática de revisión existente |
| POST `/api/v1/laboratory/campaigns` | Persistir plan; no ejecuta ni reserva recursos |

Las tres rutas de creación requieren `Idempotency-Key`: 8–128 caracteres alfanuméricos, guion o guion bajo. Los accesos a IDs ajenos devuelven 404. Ni docentes ni administradores reciben acceso implícito a borradores ajenos en esta versión; compartirlos necesita la política de asignación posterior.

## Ejemplo manual de uso

1. Abrir Laboratorio 5G y crear título, pregunta e hipótesis propias.
2. Guardar una revisión incompleta y validarla: aparecen los campos pendientes.
3. Definir aliases `video` y `load` (no constituyen resolución de sesiones reales).
4. Como ejemplo de planificación, usar niveles `0, 5, 10`, dos repeticiones y 30 segundos. No son parámetros aprobados para una campaña científica o ejecución en vivo.
5. Definir presupuesto de tráfico 1.000.000.000 bytes y captura 100.000.000 bytes.
6. Guardar y validar. Se estiman 12 ensayos, 360 segundos de medición y 225.000.000 bytes de carga competidora.
7. Crear plan: aparecen bloques con ambos tratamientos, ordenados según semilla.
8. Recargar página y abrir el experimento: revisiones y plan permanecen.
9. Modificar un campo: la validación anterior deja de habilitar la planificación hasta guardar y validar otra revisión.

El límite de captura se exige, pero no se estima su suficiencia en esta entrega. El presupuesto de tráfico es una comprobación parcial, no una cuota CHF ni autorización de consumo.

## Archivos

- `backend/app/laboratory/`: schemas, inventario, planificador y repositorio.
- `backend/app/api/v1/endpoints/laboratory.py`: API.
- `backend/app/api/v1/router.py`, `backend/app/main.py`: integración.
- `backend/app/core/config.py`, `backend/.env.example`: ruta opcional del almacén.
- `backend/tests/test_laboratory.py`: pruebas.
- `frontend/src/features/laboratory/`: pantalla y pruebas de navegador.
- `frontend/src/routes/_authenticated/laboratory/index.tsx`: ruta.
- `frontend/src/components/layout/data/sidebar-data.ts`: acceso desde navegación.
- `frontend/src/routeTree.gen.ts`: ruta generada.

Los archivos compartidos ya tenían cambios previos a esta entrega. Se añadieron cambios acotados y se conservaron los demás.

## Verificación realizada

| Comprobación | Resultado |
|---|---|
| Backend del laboratorio | 22 pruebas aprobadas |
| Chromium/Vitest del laboratorio | 2 pruebas aprobadas |
| TypeScript `tsc -b` | Aprobado |
| ESLint de pantalla, ruta y pruebas nuevas | Aprobado |
| Vite build final | Aprobado; advertencia de chunks grandes del frontend |
| Revisión de whitespace de integración | Aprobada |
| Suite completa backend | 140 aprobadas, 2 fallidas en módulos de trazas existentes |

Las pruebas cubren persistencia al reabrir el almacén, reintentos concurrentes, conflicto de claves, inmutabilidad, permisos por propietario/testbed, entrada inválida, plan reproducible, presupuesto, autenticación y ausencia de rutas de actuación. Las pruebas de navegador usan API simulada para comprobar invalidación de diseño y reintento tras timeout; no acreditan una campaña de red ni una sesión manual contra el backend desplegado.

La suite general detectó:

1. `test_node_trace_catalog_exposes_supported_csp_nodes_without_bpf_filters`: espera un catálogo sin `smf2`, aunque el catálogo actual ya lo incorpora.
2. `test_dual_smf_trace_attribution`: espera `127.0.0.15 → SMF-02`, mientras `ADDRESS_TO_NF` conserva BSF para esa dirección.

Los archivos que contienen estas discrepancias no fueron editados en la entrega. No se declaró la regresión global aprobada. Resolver con inventario real antes de F3, porque afecta la atribución de evidencia.

Los primeros intentos de Python/Vitest/Vite en sandbox fallaron por el entorno Windows (launcher de Python y consulta `uv_os_get_passwd`). Se repitieron mediante ejecución fuera del sandbox; los resultados anteriores corresponden a esas ejecuciones.

## Estado frente al plan maestro

| Tareas | Avance |
|---|---|
| F0-01/F0-06 | Inventario documental inicial; validación viva pendiente |
| F1-01/F1-02 | Schemas y almacén de diseño implementados |
| F1-03 | Experimentos, revisiones y planes; estados runtime/worker pendientes |
| F1-04 | Borradores privados; asignaciones docentes pendientes |
| F1-05 | Idempotencia/ownership; cuotas de artefactos pendientes |
| F1-06 | Validación estática implementada |
| F1-07 | Pruebas de persistencia, entradas y privacidad; faltan estados runtime |
| F4-01 | Catálogo conceptual y diseño integrados para una plantilla |
| F2/F3/F5/F6/F7 | Pendientes en su alcance de ejecución/evidencia/incidentes/IA/evaluación |

No se cierran G0, G1 ni G4 completos por esta entrega parcial. El siguiente bloque debe completar contratos y permisos de ejecución, luego worker, reserva exclusiva, diario de acciones y recuperación independiente. Solo después se conecta el primer adaptador de campaña real.

## Deuda conocida

- Paginación completa, límites por propietario y exportación de diseños.
- Compartición docente/alumno y asignaciones vigentes.
- Persistencia de claves pendientes del navegador tras cerrar/recargar la pestaña.
- Confirmación al cambiar de experimento con cambios sin guardar.
- UI de inventario de capacidades y resolución real de aliases UE.
- Calibración de carga, presupuesto completo, recuperación y adquisición viva.
- Datos de experimentación inmutables, correlación y análisis estadístico.
- Modelo local y evaluación de GPU.

Ninguno de estos pendientes está representado como capacidad operativa disponible.
