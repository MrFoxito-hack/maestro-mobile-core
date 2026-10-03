import asyncio
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.db import connection, transaction
from app.models import Role, UserPublic
from app.services.execution import ExecutionError
from app.services.scenarios import CATALOG, scenario_manager
from app.services.operation_mutations import mutation_definitions, execute_mutation


class OperationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OperationDefinition:
    id: str
    label: str
    description: str
    category: str
    executor: str
    mutating: bool = False
    command: str | None = None
    endpoint: str | None = None
    parameters: list[dict[str, Any]] = field(default_factory=list)

    def public(self, role: Role, targets: list[str]) -> dict:
        parameters = []
        for parameter in self.parameters:
            value = dict(parameter)
            if value["id"] == "node_name":
                value["options"] = [
                    {"value": target, "label": target} for target in targets
                ]
                value["required"] = len(targets) > 1
            parameters.append(value)
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "category": self.category,
            "mutating": self.mutating,
            "allowed": (not self.mutating and self.executor != "charging" and not self.id.startswith(("chf.", "nwdaf."))) or role in {Role.admin, Role.teacher},
            "parameters": parameters,
        }


LINES_PARAMETER = {
    "id": "lines",
    "label": "Cantidad de líneas",
    "type": "select",
    "required": True,
    "default": 100,
    "options": [
        {"value": 50, "label": "50 líneas"},
        {"value": 100, "label": "100 líneas"},
        {"value": 200, "label": "200 líneas"},
        {"value": 500, "label": "500 líneas"},
    ],
}
NODE_PARAMETER = {
    "id": "node_name",
    "label": "Instancia UERANSIM",
    "type": "select",
    "required": False,
    "description": "Si existe una sola instancia se selecciona automáticamente.",
}
PSI_PARAMETER = {
    "id": "psi",
    "label": "PDU Session ID (PSI)",
    "type": "number",
    "required": True,
    "default": 1,
    "minimum": 1,
    "maximum": 15,
}
AMF_ID_PARAMETER = {
    "id": "amf_id",
    "label": "ID de AMF",
    "type": "number",
    "required": False,
    "description": "Identificador de la asociación AMF (si se omite, se detecta automáticamente).",
}


COMMON_OPERATIONS = [
    OperationDefinition(
        "system.status",
        "Consultar estado",
        "Obtiene el estado real de la unidad systemd asociada.",
        "O&M Infraestructura",
        "status",
    ),
    OperationDefinition(
        "system.logs",
        "Ver logs recientes",
        "Consulta journalctl sin abrir una terminal en el nodo.",
        "O&M Infraestructura",
        "logs",
        parameters=[LINES_PARAMETER],
    ),
    OperationDefinition(
        "network.endpoints",
        "Verificar interfaces y puertos",
        "Compara los endpoints declarados para la NF con los sockets del host.",
        "O&M Infraestructura",
        "endpoints",
    ),
    OperationDefinition(
        "system.restart",
        "Reiniciar función de red",
        "Reinicia únicamente la unidad declarada en el inventario y verifica su estado final.",
        "O&M Infraestructura",
        "restart",
        mutating=True,
    ),
]


def _native(
    operation_id: str,
    label: str,
    description: str,
    command: str,
    *,
    mutating: bool = False,
    parameters: list[dict[str, Any]] | None = None,
) -> OperationDefinition:
    return OperationDefinition(
        operation_id,
        label,
        description,
        "UERANSIM CLI" if not mutating else "Acciones controladas",
        "ueransim",
        mutating=mutating,
        command=command,
        parameters=parameters or [NODE_PARAMETER],
    )


GNB_OPERATIONS = [
    _native("gnb.status", "Estado NG-RAN", "Estado operativo y asociación del gNodeB.", "status"),
    _native("gnb.info", "Información del gNodeB", "Identidad, PLMN, TAC y configuración efectiva.", "info"),
    _native("gnb.amf-list", "AMF asociados", "Lista las asociaciones AMF conocidas por el gNodeB.", "amf-list"),
    _native(
        "gnb.amf-info",
        "Detalle de AMF",
        "Muestra información de la conexión N2 seleccionada.",
        "amf-info",
        parameters=[NODE_PARAMETER, AMF_ID_PARAMETER],
    ),
    _native("gnb.ue-count", "Contar UE conectados", "Cantidad actual de UE asociados al gNodeB.", "ue-count"),
    _native("gnb.ue-list", "Listar UE conectados", "Lista UE asociados al gNodeB.", "ue-list"),
]

UE_OPERATIONS = [
    _native("ue.status", "Estado de registro", "Estados CM, RM y movilidad del UE.", "status"),
    _native("ue.info", "Información del UE", "Identidad de nodo y configuración efectiva.", "info"),
    _native("ue.coverage", "Cobertura y celdas", "PLMN y celdas visibles para el UE.", "coverage"),
    _native("ue.rls-state", "Estado de enlace radio", "Estado RLS reportado por UERANSIM.", "rls-state"),
    _native("ue.timers", "Temporizadores NAS", "Estado actual de los temporizadores del UE.", "timers"),
    _native("ue.pdu-list", "Listar sesiones PDU", "Sesiones PDU, DNN, S-NSSAI e IP del UE.", "ps-list"),
    _native(
        "ue.pdu-release",
        "Liberar sesión PDU",
        "Solicita la liberación de una sesión PDU concreta.",
        "ps-release",
        mutating=True,
        parameters=[NODE_PARAMETER, PSI_PARAMETER],
    ),
    _native(
        "ue.deregister",
        "Desregistrar UE",
        "Ejecuta deregister switch-off sobre la instancia seleccionada.",
        "deregister",
        mutating=True,
    ),
    OperationDefinition(
        "ue.attach",
        "Conectar y registrar UE (Attach)",
        "Fuerza el registro 5G del UE y negociación de sesión PDU, garantizando estado NORMAL-SERVICE y túnel de datos activo.",
        "Acciones controladas",
        "ue-recover",
        mutating=True,
        parameters=[NODE_PARAMETER],
    ),
]

INFO_OPERATIONS = {
    "amf": [
        OperationDefinition("amf.ue-info", "UE conectados", "Contextos UE activos en el AMF.", "Open5GS InfoAPI", "info", endpoint="ue-info"),
        OperationDefinition("amf.gnb-info", "gNodeB conectados", "Asociaciones N2, PLMN, TAC, slices y UE por gNodeB.", "Open5GS InfoAPI", "info", endpoint="gnb-info"),
    ],
    "smf": [
        OperationDefinition("smf.pdu-info", "Sesiones PDU", "SUPI, DNN, dirección, S-NSSAI, QoS y estado de sesión.", "Open5GS InfoAPI", "info", endpoint="pdu-info"),
    ],
    "smf2": [
        OperationDefinition("smf2.pdu-info", "Sesiones PDU Corporate", "Sesiones de SMF-02: SUPI, DNN, dirección, S-NSSAI y QoS.", "Open5GS InfoAPI", "info", endpoint="pdu-info"),
    ],
}


def operations_for(component: dict) -> list[OperationDefinition]:
    operations = [OperationDefinition(**item) for item in mutation_definitions(component["id"])]
    if component["id"] == "chf":
        for kind, label in (("accounts", "Cuentas de cobro"), ("sessions", "Sesiones de cobro"), ("cdrs", "CDR educativos")):
            operations.append(OperationDefinition(
                "chf." + kind, label,
                "Consulta los primeros 25 registros reales del CHF; listado completo en Tarificación 5G.",
                "Tarificación (solo lectura)", "charging", endpoint=kind))
    elif component["unit"].startswith("open5gs-") or component["unit"].startswith("ueransim-"):
        operations.append(
            OperationDefinition(
                "software.version",
                "Consultar versión",
                "Versión del binario correspondiente a la función de red.",
                "O&M Infraestructura",
                "version",
            )
        )
    operations.extend(INFO_OPERATIONS.get(component["id"], []))
    if component["id"] == "gnb" and component["unit"].startswith("ueransim-"):
        operations.extend(GNB_OPERATIONS)
    if component["id"] == "ue" and component["unit"].startswith("ueransim-"):
        operations.extend(UE_OPERATIONS)
    operations.extend(COMMON_OPERATIONS)
    return sorted(operations, key=lambda item: item.category == "O&M Infraestructura")


def _validate_parameters(definition: OperationDefinition, raw: dict[str, Any]) -> dict[str, Any]:
    declared = {item["id"]: item for item in definition.parameters}
    extras = set(raw) - set(declared)
    if extras:
        raise OperationError(f"Parámetros no permitidos: {', '.join(sorted(extras))}")
    values: dict[str, Any] = {}
    for parameter_id, schema in declared.items():
        value = raw.get(parameter_id, schema.get("default"))
        if schema.get("required") and (value is None or value == ""):
            raise OperationError(f"Falta el parámetro {schema['label']}")
        if value is None or value == "":
            continue
        if schema["type"] == "number":
            if isinstance(value, bool):
                raise OperationError(f"{schema['label']} debe ser numérico")
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise OperationError(f"{schema['label']} debe ser numérico") from exc
            if not math.isfinite(value) or value < schema.get("minimum", value) or value > schema.get("maximum", value):
                raise OperationError(f"{schema['label']} está fuera del rango permitido")
            if schema.get("integer", True):
                if not value.is_integer():
                    raise OperationError(f"{schema['label']} debe ser entero")
                value = int(value)
        elif schema["type"] == "text":
            if not isinstance(value, str):
                raise OperationError(f"{schema['label']} debe ser texto")
            if schema.get("pattern") and not re.fullmatch(schema["pattern"], value):
                raise OperationError(f"Formato inválido para {schema['label']}")
        options = schema.get("options")
        if options and not any(value == item["value"] for item in options):
            raise OperationError(f"Valor no permitido para {schema['label']}")
        if parameter_id == "node_name" and not re.fullmatch(r"[A-Za-z0-9._:-]+", str(value)):
            raise OperationError("Nombre de instancia UERANSIM inválido")
        values[parameter_id] = value
    return values


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in {"k", "key", "opc", "op", "password", "secret"} else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def _safe_output(output: str) -> str:
    output = re.sub(
        r"(?im)\b(key|opc|password|secret)\s*[:=]\s*[^\s,}\]]+",
        lambda match: f"{match.group(1)}: [REDACTED]",
        output,
    )
    limit = get_settings().operation_output_limit
    if len(output) > limit:
        return output[:limit] + "\n[Salida truncada por el EMS]"
    return output


class OperationsService:
    async def _recover_ue(self, node_name: str | None = None) -> tuple[str, dict]:
        gnb_status = ""
        try:
            gnb_res = await scenario_manager.adapter.native_operation("ueransim-cli", "gnb", {"command": "status"})
            gnb_status = gnb_res.get("output", "")
        except Exception:
            pass
        if "true" not in gnb_status:
            try:
                await scenario_manager.adapter.start_service("ueransim-gnb")
                for _ in range(8):
                    await asyncio.sleep(1)
                    st = await scenario_manager.adapter.native_operation("ueransim-cli", "gnb", {"command": "status"})
                    if "true" in st.get("output", ""):
                        break
                await asyncio.sleep(2)
            except Exception:
                pass

        await scenario_manager.adapter.stop_service("ueransim-ue")
        await asyncio.sleep(1)
        await scenario_manager.adapter.start_service("ueransim-ue")

        status_out = ""
        node_arg = {"node_name": node_name} if node_name else {}
        for attempt in range(12):
            await asyncio.sleep(1)
            res = await scenario_manager.adapter.native_operation("ueransim-cli", "ue", {**node_arg, "command": "status"})
            status_out = res.get("output", "")
            if "RM-REGISTERED" in status_out and "NORMAL-SERVICE" in status_out:
                break
            if "LIMITED-SERVICE" in status_out and attempt >= 2:
                await scenario_manager.adapter.stop_service("ueransim-ue")
                await asyncio.sleep(1)
                await scenario_manager.adapter.start_service("ueransim-ue")

        ps_out = ""
        try:
            ps_res = await scenario_manager.adapter.native_operation("ueransim-cli", "ue", {**node_arg, "command": "ps-list"})
            ps_out = ps_res.get("output", "")
        except Exception:
            pass

        registered = "RM-REGISTERED" in status_out and "NORMAL-SERVICE" in status_out
        pdu_active = "state: PS-ACTIVE" in ps_out or "PS-ACTIVE" in ps_out

        lines = [
            "Reconexión y Registro 5G (Attach) Completado",
            "---------------------------------------------",
            f"Estado 5G MM         : {'MM-REGISTERED / NORMAL-SERVICE' if registered else 'DESREGISTRADO'}",
            f"Estado Conexión (CM) : {'CM-CONNECTED' if 'CM-CONNECTED' in status_out else 'CM-IDLE'}",
            f"Estado Registro (RM) : {'RM-REGISTERED' if 'RM-REGISTERED' in status_out else 'RM-DEREGISTERED'}",
            f"Sesión PDU           : {'ACTIVA' if pdu_active else 'PENDIENTE'}",
        ]
        if status_out.strip():
            lines.append("")
            lines.append("Detalle de Registro (UERANSIM):")
            lines.append(status_out.strip())
        if ps_out.strip():
            lines.append("")
            lines.append("Detalle de Sesión PDU:")
            lines.append(ps_out.strip())

        return "\n".join(lines), {
            "registered": registered,
            "pdu_active": pdu_active,
            "status": status_out,
            "ps_list": ps_out,
            "service": "ueransim-ue",
        }

    async def catalog(self, scenario_id: str, user: UserPublic) -> dict:
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)
        targets: dict[str, list[str]] = {"gnb": [], "ue": []}
        discovery_error = None
        if scenario_id == "5g-sa":
            try:
                result = await scenario_manager.adapter.native_operation(
                    "ueransim-nodes", "gnb", {}
                )
                nodes = result.get("data", {}).get("nodes", [])
                targets["ue"] = [node for node in nodes if node.lower().startswith("imsi-")]
                targets["gnb"] = [node for node in nodes if not node.lower().startswith("imsi-")]
            except Exception as exc:
                discovery_error = str(exc)

        status = await scenario_manager.status(scenario_id)
        status_by_id = {item.id: item.status for item in status.components}
        components = []
        for component in CATALOG[scenario_id]["components"]:
            component_targets = targets.get(component["id"], [])
            components.append(
                {
                    "id": component["id"],
                    "label": component["label"],
                    "kind": component["kind"],
                    "node_id": component["node_id"],
                    "unit": component["unit"],
                    "status": status_by_id.get(component["id"], "unknown"),
                    "interfaces": component.get("interfaces", []),
                    "targets": component_targets,
                    "operations": [
                        item.public(user.role, component_targets)
                        for item in operations_for(component)
                    ],
                }
            )
        return {
            "scenario_id": scenario_id,
            "scenario_name": CATALOG[scenario_id]["name"],
            "execution_mode": get_settings().execution_mode,
            "components": components,
            "native_discovery": {
                "available": discovery_error is None,
                "message": discovery_error,
            },
        }

    async def execute(
        self,
        scenario_id: str,
        component_id: str,
        operation_id: str,
        raw_parameters: dict[str, Any],
        user: UserPublic,
    ) -> dict:
        if scenario_id not in CATALOG:
            raise KeyError("scenario")
        component = next(
            (item for item in CATALOG[scenario_id]["components"] if item["id"] == component_id),
            None,
        )
        if not component:
            raise KeyError("component")
        definition = next(
            (item for item in operations_for(component) if item.id == operation_id),
            None,
        )
        if not definition:
            raise KeyError("operation")
        if (definition.mutating or definition.executor == "charging" or definition.id.startswith(("chf.", "nwdaf."))) and user.role not in {Role.admin, Role.teacher}:
            raise PermissionError("Esta operación requiere rol docente o administrador")

        parameters = _validate_parameters(definition, raw_parameters)
        started = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        run_id = uuid.uuid4().hex
        source = get_settings().execution_mode
        status = "success"
        output = ""
        data: Any = None
        error = None
        try:
            if definition.executor == "mutation":
                data, source = await execute_mutation(operation_id, parameters, run_id)
                output = json.dumps(_safe_value(data), ensure_ascii=False, indent=2)
            elif definition.executor == "status":
                state = await scenario_manager.adapter.service_status(component["unit"])
                output = f"{component['unit']}: {state}"
                data = {"unit": component["unit"], "state": state}
                source = "systemd"
            elif definition.executor == "logs":
                lines = await scenario_manager.adapter.logs(
                    component["unit"], int(parameters.get("lines", 100))
                )
                output = "\n".join(lines) or "No se encontraron entradas recientes."
                data = {"line_count": len(lines)}
                source = "journalctl"
            elif definition.executor == "endpoints":
                snapshot = await scenario_manager.adapter.runtime_snapshot()
                expected = component.get("expected_endpoints", [])
                sockets = snapshot.get("listening_ports", [])
                checks = []
                for endpoint in expected:
                    matching = [item for item in sockets if item.get("port") == endpoint.get("port")]
                    checks.append({**endpoint, "listening": bool(matching), "observed": matching})
                data = {"checks": checks, "host": snapshot.get("hostname"), "source": snapshot.get("source")}
                if not checks:
                    output = "La NF no tiene endpoints declarados; revise sus interfaces y configuración."
                else:
                    output = "\n".join(
                        f"[{('OK' if item['listening'] else 'NO DETECTADO')}] {item['interface']} "
                        f"{item['protocol']}://{item['address']}:{item['port']}"
                        for item in checks
                    )
                source = "ss/ip"
            elif definition.executor == "ue-recover":
                output, data = await self._recover_ue(parameters.get("node_name"))
                source = "ueransim-auto-recovery"
            elif definition.executor == "restart":
                if component["id"] == "ue" or component["unit"] == "ueransim-ue":
                    output, data = await self._recover_ue(parameters.get("node_name"))
                    source = "ueransim-auto-recovery"
                elif component["id"] == "gnb" or component["unit"] == "ueransim-gnb":
                    await scenario_manager.adapter.stop_service("ueransim-gnb")
                    await asyncio.sleep(0.5)
                    await scenario_manager.adapter.start_service("ueransim-gnb")
                    for _ in range(8):
                        await asyncio.sleep(1)
                        st = await scenario_manager.adapter.native_operation("ueransim-cli", "gnb", {"command": "status"})
                        if "true" in st.get("output", ""):
                            break
                    await asyncio.sleep(2)
                    await self._recover_ue()
                    state = await scenario_manager.adapter.service_status(component["unit"])
                    output = f"Reinicio completado. {component['unit']}: {state}\nUE sincronizado automáticamente en NORMAL-SERVICE."
                    data = {"unit": component["unit"], "state": state}
                    source = "systemd"
                else:
                    await scenario_manager.adapter.stop_service(component["unit"])
                    await scenario_manager.adapter.start_service(component["unit"])
                    state = await scenario_manager.adapter.service_status(component["unit"])
                    output = f"Reinicio completado. {component['unit']}: {state}"
                    data = {"unit": component["unit"], "state": state}
                    source = "systemd"
            elif definition.executor == "version":
                result = await scenario_manager.adapter.native_operation(
                    "software-version", component_id, {}
                )
                output, data = result.get("output", ""), result.get("data")
                source = "native-cli"
            elif definition.executor == "charging":
                from app.services.charging import records
                data = await asyncio.to_thread(records, definition.endpoint, None, 25, 0)
                output = json.dumps(data, ensure_ascii=False, indent=2)
                source = "chf-management-api"
            elif definition.executor == "info":
                result = await scenario_manager.adapter.native_operation(
                    "open5gs-info", component_id, {"endpoint": definition.endpoint}
                )
                output, data = result.get("output", ""), result.get("data")
                source = "open5gs-infoapi"
            elif definition.executor == "ueransim":
                result = await scenario_manager.adapter.native_operation(
                    "ueransim-cli",
                    component_id,
                    {**parameters, "command": definition.command},
                )
                output, data = result.get("output", ""), result.get("data")
                source = "ueransim-nr-cli"
            else:
                raise OperationError("Ejecutor no reconocido")
        except Exception as exc:
            status = "failed"
            error = _safe_output(str(getattr(exc, "detail", str(exc))))
            output = error

        completed = datetime.now(timezone.utc)
        duration_ms = max(0, round((time.perf_counter() - started_clock) * 1000))
        output = _safe_output(output)
        data = _safe_value(data)
        response = {
            "id": run_id,
            "scenario_id": scenario_id,
            "testbed_id": user.testbed or "local",
            "component_id": component_id,
            "component_label": component["label"],
            "operation_id": operation_id,
            "operation_label": definition.label,
            "category": definition.category,
            "mutating": definition.mutating,
            "username": user.username,
            "role": user.role.value,
            "parameters": _safe_value(parameters),
            "status": status,
            "source": source,
            "output": output,
            "data": data,
            "error": error,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "duration_ms": duration_ms,
        }
        with transaction() as conn:
            conn.execute(
                """INSERT INTO operation_runs(
                    id,scenario_id,testbed_id,component_id,component_label,
                    operation_id,operation_label,category,mutating,username,role,
                    parameters,status,source,output,data,error,started_at,completed_at,duration_ms
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, scenario_id, response["testbed_id"], component_id,
                    component["label"], operation_id, definition.label,
                    definition.category, int(definition.mutating), user.username,
                    user.role.value, json.dumps(response["parameters"]), status,
                    source, output, json.dumps(data) if data is not None else None,
                    error, response["started_at"], response["completed_at"], duration_ms,
                ),
            )
        if status == "failed":
            raise OperationError(error or "La operación falló")
        return response

    def history(self, user: UserPublic, scenario_id: str | None, limit: int) -> list[dict]:
        where = []
        values: list[Any] = []
        if scenario_id:
            where.append("scenario_id=?")
            values.append(scenario_id)
        if user.role == Role.student:
            where.append("username=?")
            values.append(user.username)
        elif user.testbed:
            where.append("testbed_id=?")
            values.append(user.testbed)
        clause = " WHERE " + " AND ".join(where) if where else ""
        values.append(min(max(limit, 1), 200))
        with connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM operation_runs{clause} ORDER BY started_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [
            {
                **dict(row),
                "mutating": bool(row["mutating"]),
                "parameters": json.loads(row["parameters"]),
                "data": json.loads(row["data"]) if row["data"] else None,
            }
            for row in rows
        ]


operations_service = OperationsService()
