"""Validated MML provisioning and live management operations.

Each command is one service transaction. Native N7 acknowledgement does not
claim enforcement at the UPF; unavailable services never produce mock success.
"""
import asyncio
import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models import SubscriberCreate, SubscriberUpdate
from app.services import charging, nwdaf
from app.services.subscribers import subscriber_service

IMSI = dict(id="imsi", label="IMSI", type="text", required=True, pattern=r"^(?:imsi-)?\d{14,15}$")
SST = dict(id="sst", label="SST", type="number", minimum=0, maximum=255)
SD = dict(id="sd", label="SD", type="text", pattern=r"^[0-9a-fA-F]{6}$")
DNN = dict(id="apn_dnn", label="DNN", type="select", options=[
    dict(value=value, label=value) for value in ("internet", "corporate")])


def choice(identifier: str, *values: str | int, default=None) -> dict:
    result = dict(id=identifier, label=identifier.upper(), type="select", required=True,
                  options=[dict(value=value, label=str(value)) for value in values])
    if default is not None:
        result["default"] = default
    return result


def mutation_definitions(component: str) -> list[dict[str, Any]]:
    definitions = {
        "udm": [
            ("subscriber.create", "ADD 5G-SUB", "Crear perfil Milenage y slice en UDR.", True, [IMSI,
                dict(id="key", label="K", type="text", pattern=r"^[0-9a-fA-F]{32}$", secret=True,
                     default="465B5CE8B199B49FAA5F0A2EE238A6BC"),
                dict(id="opc", label="OPC", type="text", pattern=r"^[0-9a-fA-F]{32}$", secret=True,
                     default="E8ED289DEBA952E6B6710CE703365304"),
                dict(id="amf", label="AMF", type="text", pattern=r"^[0-9a-fA-F]{4}$", default="8000"),
                {**SST, "default": 1}, SD, {**DNN, "default": "internet"}]),
            ("subscriber.update", "MOD 5G-SUB", "Actualizar slice o DNN sin reiniciar el Core.", True, [IMSI, SST, SD, DNN]),
            ("subscriber.delete", "RMV 5G-SUB", "Eliminar el perfil de suscriptor del UDR.", True, [IMSI]),
        ],
        "chf": [
            ("chf.quota", "ADD CHF-QUOTA", "Recargar saldo con un identificador de transacción único.", True,
             [IMSI, dict(id="quota_mb", label="Cuota MiB", type="number", required=True,
                         integer=False, minimum=1/1048576, maximum=100_000_000/1048576)]),
            ("chf.state", "MOD CHF-STATE", "Cambiar el estado de la cuenta sin alterar su cuota.", True,
             [IMSI, choice("state", "ACTIVE", "SUSPENDED")]),
            ("chf.balance", "DSP CHF-BALANCE", "Consultar saldo, consumo y reservas.", False, [IMSI]),
        ],
        "pcf": [("pcf.qos", "MOD PCC-QOS", "Actualizar PCC mediante el actuador nativo y confirmar N7.", True,
                 [IMSI, dict(id="five_qi", label="5QI", type="number", default=9, minimum=1, maximum=255),
                  *[dict(id=f"mbr_{direction}_mbps", label=f"MBR {direction.upper()} Mbps", type="number",
                         integer=False, minimum=0.001, maximum=100000) for direction in ("dl", "ul")]])],
        "nwdaf": [
            ("nwdaf.analytics", "DSP NWDAF-ANALYTICS", "Carga observada y predicción por NF y horizonte.", False,
             [choice("nf", "UPF-01", "UPF-02"), choice("horizon", 15, 30, default=15)]),
            ("nwdaf.mode", "SET NWDAF-MODE", "Conmutar el actuador nativo entre modo autónomo y manual.", True,
             [choice("mode", "AUTONOMOUS", "MANUAL")]),
        ],
    }
    group = "udm" if component == "udr" else component
    categories = {"udm": "Aprovisionamiento UDM / UDR", "chf": "Control convergente CHF",
                  "pcf": "Políticas PCF / PCC", "nwdaf": "Analítica NWDAF"}
    return [dict(id=identifier, label=label, description=description, category=categories[group],
                 executor="mutation", mutating=mutating, parameters=parameters)
            for identifier, label, description, mutating, parameters in definitions.get(group, [])]


async def execute_mutation(operation: str, parameters: dict, run_id: str) -> tuple[dict, str]:
    values = dict(parameters)
    imsi = values.pop("imsi", "").removeprefix("imsi-")
    supi = "imsi-" + imsi
    if operation.startswith("subscriber."):
        if operation == "subscriber.create":
            result = await asyncio.to_thread(subscriber_service.create, SubscriberCreate(imsi=imsi, **values))
        elif operation == "subscriber.update":
            if not values:
                raise ValueError("MOD 5G-SUB requiere SST, SD o DNN")
            result = await asyncio.to_thread(subscriber_service.update, imsi, SubscriberUpdate(**values))
        else:
            if not await asyncio.to_thread(subscriber_service.delete, imsi):
                raise ValueError("Suscriptor no encontrado o eliminación no confirmada")
            result = {"imsi": imsi, "deleted": True}
        from app.core.config import get_settings
        settings = get_settings()
        return result, "mongodb-udr" if settings.execution_mode == "remote" or settings.enable_mongo else "simulated-udr"
    if operation.startswith("chf."):
        path = f"/admin/v1/accounts/{supi}"
        if operation == "chf.quota":
            amount = Decimal(str(values["quota_mb"])) * 1048576
            if amount != amount.to_integral_value():
                raise ValueError("QUOTA_MB debe representar una cantidad entera de bytes")
            result = await asyncio.to_thread(charging.management_request, path + "/topup",
                method="POST", payload={"amountBytes": int(amount), "requestId": str(UUID(run_id))})
        elif operation == "chf.state":
            result = await asyncio.to_thread(charging.management_request, path, method="PUT",
                payload={"supi": supi, "enabled": values["state"] == "ACTIVE"})
        else:
            result = await asyncio.to_thread(charging.management_request, path)
        return result, "chf-management-api"
    if operation == "nwdaf.analytics":
        return await asyncio.to_thread(nwdaf.get_nf_analytics, values["nf"], values["horizon"]), "nwdaf-analyticsinfo"
    from app.services.pcf_control import control_request
    payload = {"operation": "mode", **values} if operation == "nwdaf.mode" else {
        "operation": "qos", "supi": supi, **values}
    res = await asyncio.to_thread(control_request, payload)
    if operation == "pcf.qos":
        from app.services.terminal import set_dynamic_pcc_qos
        set_dynamic_pcc_qos(
            supi=supi,
            five_qi=int(values.get("five_qi", 9)),
            mbr_dl_mbps=float(values.get("mbr_dl_mbps", 20.0)),
            mbr_ul_mbps=float(values.get("mbr_ul_mbps", 20.0)),
        )
    return res, "pcf-native-n7"
