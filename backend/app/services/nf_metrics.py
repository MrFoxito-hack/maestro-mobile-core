"""Capability-driven NF counters; Open5GS labels are retained, never summed blindly."""
import asyncio
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import time

from app.db import transaction
from app.services.execution import LocalExecutionAdapter, RemoteExecutionAdapter


PROCESS = {
    "cpu_percent": ("CPU utilizada por la función de red", "%", "gauge"),
    "rss_mib": ("Memoria física residente (RAM)", "MiB", "gauge"),
}
CLI = {
    "ngap_connected": ("Asociación NGAP activa (gNB)", "booleano"),
    "registered": ("UE registrado según NAS", "booleano"),
    "connected": ("UE en CM-CONNECTED", "booleano"),
    "pdu_sessions": ("Sesiones PDU en el UE", "sesiones"),
}
# Native HELP remains visible next to these operator-friendly names.
LABELS = {
    "gnb": ("gNodeB conectados al AMF", "gNB"),
    "enb": ("eNodeB conectados al MME", "eNB"),
    "ran_ue": ("Contextos RAN UE en AMF", "UE"),
    "enb_ue": ("Contextos eNB UE en MME", "UE"),
    "amf_session": ("Contextos de sesión AMF", "contextos"),
    "mme_session": ("Sesiones MME", "sesiones"),
    "ues_active": ("UE activos en SMF", "UE"),
    "pfcp_peers_active": ("Peers PFCP activos", "peers"),
    "pfcp_sessions_active": ("Sesiones PFCP activas", "sesiones"),
    "bearers_active": ("Bearers activos", "bearers"),
    "gtp_peers_active": ("Peers GTP activos", "peers"),
    "gtp1_pdpctxs_active": ("Contextos PDP GTPv1 activos", "contextos"),
    "gtp2_sessions_active": ("Sesiones GTPv2 activas", "sesiones"),
    "rm_reginitreq": ("Registro inicial: solicitudes recibidas", "eventos"),
    "rm_reginitsucc": ("Registro inicial: éxitos", "eventos"),
    "rm_reginitfail": ("Registro inicial: fallos por causa", "eventos"),
    "amf_authreq": ("Autenticación: solicitudes", "eventos"),
    "amf_authreject": ("Autenticación: rechazos", "eventos"),
    "amf_authfail": ("Autenticación: fallos por causa", "eventos"),
    "mm_paging5greq": ("Paging 5G: solicitudes", "eventos"),
    "mm_paging5gsucc": ("Paging 5G: éxitos", "eventos"),
    "mm_confupdate": ("Configuration Update: solicitudes", "eventos"),
    "mm_confupdatesucc": ("Configuration Update: éxitos", "eventos"),
    "rm_registeredsubnbr": ("Suscriptores registrados por PLMN / S-NSSAI", "UE"),
    "sm_pdusessioncreationreq": ("Creación PDU Session: solicitudes", "eventos"),
    "sm_pdusessioncreationsucc": ("Creación PDU Session: éxitos", "eventos"),
    "sm_pdusessioncreationfail": ("Creación PDU Session: fallos", "eventos"),
    "sm_sessionnbr": ("Sesiones SMF por PLMN / S-NSSAI", "sesiones"),
    "sm_qos_flow_nbr": ("Flujos QoS por 5QI", "flujos"),
    "upf_sessionnbr": ("Sesiones UPF activas", "sesiones"),
    "upf_qosflows": ("Flujos QoS UPF por DNN", "flujos"),
    "sm_n4sessionestabreq": ("N4: solicitudes de establecimiento", "eventos"),
    "sm_n4sessionestabfail": ("N4: fallos de establecimiento", "eventos"),
    "sm_n4sessionreport": ("N4: reportes de sesión", "eventos"),
    "sm_n4sessionreportsucc": ("N4: reportes de sesión exitosos", "eventos"),
    "pa_policyamassoreq": ("Políticas AM: solicitudes de asociación", "eventos"),
    "pa_policyamassosucc": ("Políticas AM: asociaciones exitosas", "eventos"),
    "pa_policysmassoreq": ("Políticas SM: solicitudes de asociación", "eventos"),
    "pa_policysmassosucc": ("Políticas SM: asociaciones exitosas", "eventos"),
    "pa_sessionnbr": ("Sesiones PCF activas", "sesiones"),
    "gtp_indatapktn3upf": ("N3: paquetes GTP-U recibidos por UPF", "paquetes"),
    "gtp_outdatapktn3upf": ("N3: paquetes GTP-U enviados por UPF", "paquetes"),
}
for mode, label in [("mob", "movilidad"), ("period", "periódico"), ("emerg", "emergencia")]:
    for suffix, action in [("req", "solicitudes"), ("succ", "éxitos"), ("fail", "fallos")]:
        LABELS[f"rm_reg{mode}{suffix}"] = (f"Registro {label}: {action}", "eventos")

SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+([^\s]+)(?:\s+[^\s]+)?$')
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"(?:,|$)')


def parse_metrics(text):
    types, helps = {}, {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) == 4:
                types[parts[2]] = parts[3]
        elif line.startswith("# HELP "):
            parts = line.split(" ", 3)
            if len(parts) == 4:
                helps[parts[2]] = parts[3]
    for line in text.splitlines():
        match = SAMPLE.fullmatch(line.strip())
        if not match:
            continue
        name, raw_labels, raw_value = match.groups()
        # Histogram buckets are not independent counts/KPIs. Deliberately skip
        # until a dedicated histogram query supports their units and boundaries.
        if name.startswith("process_") or types.get(name) not in {"gauge", "counter"}:
            continue
        try:
            value = float(raw_value)
            if not math.isfinite(value) or value < 0:
                continue
            labels = {}
            if raw_labels:
                body = raw_labels[1:-1]
                consumed = 0
                for label in LABEL.finditer(body):
                    if label.start() != consumed:
                        raise ValueError("malformed labels")
                    # Prometheus escapes only \\, \n and \"; JSON handles these.
                    labels[label[1]] = json.loads('"' + label[2] + '"')
                    consumed = label.end()
                if consumed != len(body):
                    raise ValueError("malformed labels")
            # Subscriber identifiers must never become time-series dimensions.
            if any(re.search(r"imsi|supi|msisdn|imei", k, re.I) for k in labels):
                continue
            yield name, labels, value, types[name], helps.get(name, name)
        except (ValueError, TypeError):
            continue


def native_definition(nf, name, labels, kind, help_text):
    # Match against known 3GPP counters in LABELS
    matched_entry = LABELS.get(name)
    if not matched_entry:
        for suffix, entry in LABELS.items():
            if name.endswith("_" + suffix):
                matched_entry = entry
                break
    if not matched_entry:
        # Ignore obscure internal Open5GS metrics that clutter the PM catalog
        return None
    label, unit = matched_entry
    clean_id = f"native.{nf}.{name}"
    if labels:
        encoded = json.dumps(labels, sort_keys=True, separators=(',', ':'))
        clean_id += '.' + hashlib.sha256(encoded.encode()).hexdigest()[:16]
    return {
        "id": clean_id,
        "label": label,
        "unit": unit,
        "kind": kind,
        "category": f"{nf.upper()} · Contadores 3GPP",
        "source": "Open5GS 3GPP",
        "objects": ["nf"],
        "object_ids": [f"nf:{nf}"],
        "description": help_text,
        "native_name": name,
        "dimensions": labels,
    }


def definitions(testbed_id, scenario_id):
    with transaction() as conn:
        rows = conn.execute("SELECT definition,last_seen FROM nf_metric_definitions WHERE testbed_id=? AND scenario_id=?", (testbed_id, scenario_id)).fetchall()
    return [{**json.loads(row["definition"]), "last_seen": row["last_seen"]} for row in rows]


class NFMetrics:
    def __init__(self):
        self.previous = {}
        self.health = {}

    async def probe(self, adapter, components):
        if not isinstance(adapter, (RemoteExecutionAdapter, LocalExecutionAdapter)):
            return {"components": [], "cli": {}}
        inventory = [{key: item.get(key, []) for key in ("id", "unit", "config_paths")} for item in components]
        code = Path(__file__).with_name("nf_probe.py").read_text(encoding="utf-8")
        program = "import base64;exec(base64.b64decode(" + repr(base64.b64encode(code.encode()).decode()) + "))"
        args = ["python3", "-c", program, json.dumps(inventory)]
        if isinstance(adapter, RemoteExecutionAdapter):
            output = await adapter._run(shlex.join(args))
        else:
            output = await adapter._run(*args)
        return json.loads(output)

    def ingest(self, payload, testbed_id, scenario_id, now):
        samples, found = [], {}
        common = {"testbed_id": testbed_id, "scenario_id": scenario_id, "collected_at": now.isoformat(), "bucket_epoch": int(now.timestamp())}

        def add(nf, definition, value, quality="measured"):
            if not isinstance(value, (float, int)) or not math.isfinite(value):
                return
            found[definition["id"]] = definition
            samples.append({**common, "object_id": f"nf:{nf}", "counter_id": definition["id"], "value": value, "unit": definition["unit"], "source": definition["source"], "quality": quality})

        for component in payload.get("components", []):
            nf = component["id"]
            self.health[(testbed_id, scenario_id, nf)] = {"metrics_status": component.get("metrics_status", "unavailable"), "process_status": component.get("process_status", "available"), "checked_at": now.isoformat()}
            values = dict(component.get("values", {}))
            key = (testbed_id, scenario_id, nf)
            observed = component.get("observed_at", now.timestamp())
            previous = self.previous.get(key)
            incarnation = component.get("incarnation")
            elapsed = observed - previous["time"] if previous else 0
            same = previous and incarnation and incarnation == previous["incarnation"] and 0 < elapsed <= 120
            if same and "cpu_seconds" in values and "cpu_seconds" in previous["values"]:
                delta = values["cpu_seconds"] - previous["values"]["cpu_seconds"]
                if delta >= 0:
                    values["cpu_percent"] = 100 * delta / elapsed
            for name, value in values.items():
                if name not in PROCESS:
                    continue
                label, unit, kind = PROCESS[name]
                definition = {"id": f"nf.process.{nf}.{name}", "label": label, "unit": unit, "kind": kind,
                              "category": "Recursos del proceso", "source": "systemd /proc", "objects": ["nf"], "object_ids": [f"nf:{nf}"],
                              "description": "Proceso principal de la NF. CPU por delta sin mezclar reinicios; memoria del proceso no equivale a tráfico N3/N6."}
                add(nf, definition, value, "computed" if name == "cpu_percent" else "measured")
            native = {}
            for name, labels, value, kind, help_text in list(parse_metrics(component.get("metrics", "")))[:256]:
                definition = native_definition(nf, name, labels, kind, help_text)
                if not definition:
                    continue
                add(nf, definition, value)
                native[definition["id"]] = value
                old = previous['native'].get(definition['id']) if same else None
                if kind == 'counter' and old is not None and value >= old:
                    rate = {**definition, 'id': definition['id'] + '.rate',
                            'label': definition['label'] + ' · tasa por segundo',
                            'unit': definition['unit'] + '/s', 'kind': 'gauge'}
                    add(nf, rate, (value - old) / elapsed, 'computed')
            self.previous[key] = {"time": observed, "incarnation": incarnation, "values": values, "native": native}
        for nf, values in payload.get("cli", {}).items():
            for name, value in values.items():
                if name not in CLI:
                    continue
                label, unit = CLI[name]
                add(nf, {"id": f"nf.ueransim.{nf}.{name}", "label": label, "unit": unit, "kind": "gauge", "category": "UERANSIM · Estado nativo", "source": "UERANSIM nr-cli", "objects": ["nf"], "object_ids": [f"nf:{nf}"], "description": "Estado informado por nr-cli; no representa una medición RF de una radio real."}, value)
        with transaction() as conn:
            conn.executemany("""INSERT INTO nf_metric_definitions(testbed_id,scenario_id,counter_id,definition,last_seen) VALUES(?,?,?,?,?)
                ON CONFLICT(testbed_id,scenario_id,counter_id) DO UPDATE SET definition=excluded.definition,last_seen=excluded.last_seen""",
                [(testbed_id, scenario_id, key, json.dumps(value), now.isoformat()) for key, value in found.items()])
        return samples


nf_metrics = NFMetrics()
