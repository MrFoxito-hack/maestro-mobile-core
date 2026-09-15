# EMS educativo 4G/5G

El directorio [`chf`](chf/README.md) contiene el primer incremento experimental
del CHF 5G. Se ejecuta como servicio independiente en el puerto local `8081` y
aún no implica integración con el SMF/UPF ni soporte upstream de Open5GS.

Prototipo de plataforma web para gestionar, observar y analizar testbeds Open5GS/srsRAN previamente instalados. El frontend parte de [`satnaing/shadcn-admin`](https://github.com/satnaing/shadcn-admin) (MIT) y conserva su licencia. Incluye perfiles 4G EPC y 5G SA, RBAC, auditoría, topología, configuración YAML, suscriptores y capturas PCAP limitadas.

## Inicio rápido sin Open5GS

Se requiere Docker con Compose:

```bash
docker compose up --build
```

Abrir `http://localhost:8080`. Usuarios iniciales de demostración:

| Usuario | Contraseña | Rol |
|---|---|---|
| `docente` | `teacher-change-me` | Opera escenarios |
| `alumno` | `student-change-me` | Solo observación |
| `admin` | `admin-change-me` | Administración |

Estas credenciales deben cambiarse antes de compartir o desplegar el sistema.

## Desarrollo

Backend:

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/uvicorn app.main:app --reload
```

En Linux, sustituir `.venv/Scripts/` por `.venv/bin/`. La documentación OpenAPI queda en `http://localhost:8000/docs`.

Frontend:

```bash
cd frontend
corepack pnpm install
corepack pnpm run dev
```

## Integración con una VM real

1. Disponer de un Open5GS/srsRAN ya instalado y validado; el EMS no lo aprovisiona.
2. Copiar `backend/.env.example` a `backend/.env` y fijar una clave aleatoria.
3. Usar `EMS_EXECUTION_MODE=local` si el backend corre en la VM del core, o `remote` para conectarse por SSH al VNRT.
4. Declarar únicamente raíces YAML e interfaces necesarias.
5. Crear reglas `sudoers` exactas para `systemctl start/stop` de las unidades presentes en el catálogo. No conceder `sudo systemctl *`.
6. Habilitar MongoDB solo después de confirmar la compatibilidad del esquema con la versión fijada de Open5GS.
7. Habilitar capturas reales solo después de conceder a `tshark` capacidades de captura limitadas; no ejecutar el backend como root.

El adaptador local nunca usa `shell=True` y rechaza unidades fuera del catálogo. En VNRT se recomienda instalar este backend como agente en la VM y exponerlo detrás de HTTPS/VPN. El control opcional de inicio/parada actúa únicamente sobre servicios ya instalados; no despliega el core. No se implementó una terminal root ni ejecución remota arbitraria.

## Pruebas

```bash
cd backend
pytest
```

Las pruebas cubren autenticación, RBAC, ciclo de escenarios, auditoría, respaldo/restauración, enmascaramiento de secretos y PCAP simulado.

## Límites conocidos

- El modo simulado demuestra el flujo web, no emula protocolos celulares.
- El modo `remote` cubre estado y logs por SSH; métricas de host y capturas reales requieren ejecutar el agente/backend dentro de la VM del testbed (`local`) hasta implementar su transporte remoto.
- El CRUD MongoDB está desactivado por defecto y debe validarse contra la versión de Open5GS del laboratorio.
- Las reglas NAT y los YAML concretos dependen del direccionamiento del VNRT; deben agregarse como scripts permitidos después del levantamiento técnico.
- Las métricas RRC requieren habilitar la salida JSON oficial de srsRAN.
- El frontend descarga PCAP mediante autenticación, pero para producción debe servirse todo exclusivamente por HTTPS.
