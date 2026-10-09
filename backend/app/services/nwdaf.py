"""Read-only NWDAF proxy over the configured core's SSH channel.

Follows the same pattern as the CHF management proxy: no browser-supplied
hosts, paths, or shell commands. No invented analytics or fallback data.
"""
import http.client
import json

import paramiko
from fastapi import HTTPException

from app.core.config import get_settings
from app.services.upf_inventory import inventory


def slice_catalog():
    labels = {'embb': 'eMBB · Internet', 'urllc': 'URLLC · 5G-Plus / V2X',
              'miot': 'MIoT · Corporate / Sensores'}
    return [{'nf': t['id'].upper(), 'snssai': {'sst': t['sst'], 'sd': t['sd']},
             'object_id': t['pm_object_id'], 'dnn': t['dnn'], 'label': labels[t['service']]}
            for t in inventory()['targets']]


def nwdaf_request(path: str, method: str = "GET", body: str | None = None):
    """Issue an HTTP request to the NWDAF service via SSH tunnel on the core VM."""
    settings = get_settings()
    if settings.execution_mode != "remote":
        raise HTTPException(503, "NWDAF no conectado; requiere modo remoto")
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connection = http.client.HTTPConnection("127.0.0.1", settings.nwdaf_port, timeout=8)
    try:
        ssh.connect(settings.testbed_host, port=settings.ssh_port,
                    username=settings.ssh_user, password=settings.ssh_password,
                    key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                    look_for_keys=False, allow_agent=False, timeout=8,
                    auth_timeout=8, banner_timeout=8)
        # Read the NWDAF token from the configured file on the core VM
        with ssh.open_sftp() as sftp:
            with sftp.open(settings.nwdaf_token_file, "r") as file:
                token = file.read(1025).decode().strip()
        if not 24 <= len(token) <= 1024 or not token.isascii():
            raise ValueError("invalid NWDAF credential")
        channel = ssh.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", settings.nwdaf_port), ("127.0.0.1", 0), timeout=8)
        channel.settimeout(8)
        connection.sock = channel
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response too large")
        if response.status == 204:
            return None
        if response.status != 200:
            raise ValueError(f"NWDAF returned {response.status}")
        return json.loads(raw)
    except (OSError, paramiko.SSHException, ValueError, http.client.HTTPException):
        raise HTTPException(503, "NWDAF no disponible; verifique que el servicio esté activo en la VM del Core") from None
    finally:
        connection.close()
        ssh.close()


def get_health():
    """Retrieve NWDAF health status."""
    health = nwdaf_request("/health")
    from app.services.pcf_control import control_request
    try:
        actuator = control_request({"operation": "status"})
        health["closed_loop_enabled"] = actuator["mode"] == "AUTONOMOUS"
        health["actuator_mode"] = actuator["mode"]
    except Exception:
        # The environment flag is startup configuration, not runtime evidence.
        health["closed_loop_enabled"] = None
        health["actuator_mode"] = "UNAVAILABLE"
    return health


def get_analytics(event_id: str, event_filter: str | None = None,
                  tgt_ue: str | None = None):
    """Query Nnwdaf_AnalyticsInfo for a specific analytics ID."""
    from urllib.parse import urlencode
    params = {"event-id": event_id}
    if event_filter:
        params["event-filter"] = event_filter
    if tgt_ue:
        params["tgt-ue"] = tgt_ue
    path = "/nnwdaf-analyticsinfo/v1/analytics?" + urlencode(params)
    return nwdaf_request(path)


def get_overview():
    """Build an aggregated overview for the EMS dashboard."""
    health = get_health()
    # This NWDAF profile accepts exactly one complete S-NSSAI per request.
    slices, reports = [], []
    for target in slice_catalog():
        try:
            data = get_analytics('LOAD_LEVEL_INFORMATION', event_filter=json.dumps({'snssais': [target['snssai']]}))
            slices.append({**target, 'status': 'available' if data else 'no_data', 'analytics': data})
            reports.extend((data or {}).get('sliceLoadLevelInfos', []))
        except HTTPException:
            slices.append({**target, 'status': 'unavailable', 'analytics': None})

    return {
        "service": health,
        "slice_load": {'sliceLoadLevelInfos': reports},
        "slices": slices,
        "closed_loop_enabled": health.get("closed_loop_enabled", False),
    }


def get_nf_analytics(nf: str, horizon: int) -> dict:
    """Resolve the NF through the configured PM map; never invent predictions."""
    health = get_health()
    maps = health.get("slice_maps", [])
    target = next((item for item in slice_catalog() if item['nf'] == nf.upper()), None)
    matches = [entry for entry in maps if target and entry.get('object_id') == target['object_id']
               and entry.get('snssai') == target['snssai']]
    if len(matches) != 1:
        raise HTTPException(409, "NWDAF requiere un mapeo PM único para la NF indicada")
    snssai = matches[0]["snssai"]
    observed = get_analytics("LOAD_LEVEL_INFORMATION", json.dumps({"snssais": [snssai]}))
    predictions = nwdaf_request("/management/v1/predictions")
    items = [item for item in predictions.get("items", []) if item.get("snssai") == snssai]
    evidence = items[0].get("evidence", {}) if items else {}
    point = next((point for point in evidence.get("points", []) if point.get("horizon_seconds") == horizon * 60), None)
    fresh = bool(items and items[0].get("fresh"))
    reports = (observed or {}).get("sliceLoadLevelInfos", [])
    return {"nf": nf, "snssai": snssai, "horizon_minutes": horizon,
            "observed_load_percent": reports[0].get("loadLevelInformation") if reports else None,
            "predicted_load_percent": point.get("value") if point and fresh else None,
            "prediction_status": "available" if point and fresh else "unavailable",
            "model": evidence.get("model"), "observed_at": (observed or {}).get("timeStampGen"),
            "prediction_created_at": items[0].get("created") if items else None}
