import asyncio
import json
import shutil
import struct
import time
import uuid
from datetime import datetime, timezone

import psutil

from app.core.config import get_settings
from app.models import Severity, TraceStart
from app.services.execution import RemoteExecutionAdapter
from app.services.scenarios import scenario_manager


CAPTURE_POINTS = {
    "5g-sa": {
        "n2": {"label": "N2 · AMF ↔ gNodeB", "interface": "lo", "protocol": "ngap", "filter": "sctp port 38412", "procedures": ["NG Setup", "Registration", "PDU Session"]},
        "n3": {"label": "N3 · UPF ↔ gNodeB", "interface": "any", "protocol": "gtpu", "filter": "udp port 2152", "procedures": ["Tráfico de usuario"]},
        "n4": {"label": "N4 · SMF ↔ UPF", "interface": "any", "protocol": "pfcp", "filter": "udp port 8805", "procedures": ["PFCP Session Establishment"]},
        "n6": {"label": "N6 · UPF ↔ Data Network", "interface": "ogstun", "protocol": "ip", "filter": "ip or ip6", "procedures": ["Conectividad de usuario"]},
        "sbi": {"label": "SBI · Service Based Interface", "interface": "lo", "protocol": "sbi", "filter": "tcp port 7777", "procedures": ["Mensajes HTTP/2 entre NFs"]},
    },
    "4g-epc": {
        "s1-mme": {"label": "S1-MME · MME ↔ eNodeB", "interface": "lo", "protocol": "s1ap", "filter": "sctp port 36412", "procedures": ["S1 Setup", "Attach"]},
        "s1-u": {"label": "S1-U · SGW-U ↔ eNodeB", "interface": "lo", "protocol": "gtpu", "filter": "udp port 2152", "procedures": ["Tráfico de usuario"]},
        "s11": {"label": "S11 · MME ↔ SGW-C", "interface": "lo", "protocol": "gtpv2", "filter": "udp port 2123", "procedures": ["Create Session", "Modify Bearer"]},
        "s6a": {"label": "S6a · MME ↔ HSS", "interface": "lo", "protocol": "diameter", "filter": "tcp port 3868 or sctp port 3868", "procedures": ["Authentication Information", "Update Location"]},
        "sgi": {"label": "SGi · PGW-U ↔ Data Network", "interface": "ogstun", "protocol": "ip", "filter": "ip or ip6", "procedures": ["Conectividad de usuario"]},
    },
}


class TraceService:
    def __init__(self) -> None:
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.metadata: dict[str, dict] = {}
        self.metadata_path = get_settings().capture_dir / "metadata.json"
        self._load()

    def _load(self) -> None:
        if not self.metadata_path.exists():
            return
        try:
            items = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            self.metadata = {item["id"]: item for item in items}
            for item in self.metadata.values():
                if item["status"] in {"running", "stopping"}:
                    item["status"] = "interrupted"
        except (OSError, json.JSONDecodeError, KeyError):
            self.metadata = {}

    def _save(self) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(list(self.metadata.values()), indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.metadata_path)

    def options(self, scenario_id: str) -> list[dict]:
        return [{"id": key, **value, "filter": None} for key, value in CAPTURE_POINTS[scenario_id].items()]

    @staticmethod
    def _write_empty_pcap(path) -> None:
        path.write_bytes(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))

    def _resolve_request(self, request: TraceStart) -> dict:
        points = CAPTURE_POINTS.get(request.scenario_id)
        if not points:
            raise ValueError("Escenario no soportado")
        if request.capture_point:
            point = points.get(request.capture_point)
            if not point:
                raise ValueError("Punto de captura no soportado")
            return {"capture_point": request.capture_point, **point}
        if not request.interface or not request.protocol:
            raise ValueError("Debe indicar capture_point o interface/protocol")
        match = next(
            (
                {"capture_point": key, **point}
                for key, point in points.items()
                if point["interface"] == request.interface and point["protocol"] == request.protocol.lower()
            ),
            None,
        )
        if not match:
            raise ValueError("La combinación de interfaz y protocolo no está permitida")
        return match

    async def start(self, request: TraceStart, username: str) -> dict:
        settings = get_settings()
        point = self._resolve_request(request)
        if point["interface"] not in settings.allowed_interfaces:
            raise ValueError("Interfaz no permitida")
        trace_id = uuid.uuid4().hex
        path = settings.capture_dir / f"{trace_id}.pcap"
        meta = {
            "id": trace_id,
            "scenario_id": request.scenario_id,
            "capture_point": point["capture_point"],
            "capture_point_label": point["label"],
            "interface": point["interface"],
            "protocol": point["protocol"],
            "procedures": point["procedures"],
            "status": "running",
            "owner": username,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": request.duration_seconds,
            "max_megabytes": request.max_megabytes,
            "file": path.name,
            "packet_count": 0,
            "size_bytes": 0,
            "protocols": [],
        }
        adapter = scenario_manager.adapter
        if settings.enable_real_captures and isinstance(adapter, RemoteExecutionAdapter):
            handle = await adapter.start_remote_capture(
                trace_id,
                point["interface"],
                point["filter"],
                request.duration_seconds,
                request.max_megabytes * 1024,
            )
            meta.update(handle)
            meta["source"] = "remote"
        elif settings.enable_real_captures:
            if not shutil.which("tshark"):
                raise RuntimeError("tshark no está instalado")
            process = await asyncio.create_subprocess_exec(
                "tshark", "-n", "-i", point["interface"],
                "-a", f"duration:{request.duration_seconds}",
                "-a", f"filesize:{request.max_megabytes * 1024}",
                "-f", point["filter"], "-w", str(path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self.processes[trace_id] = process
            meta["source"] = "local"
        else:
            self._write_empty_pcap(path)
            meta.update({"status": "completed", "source": "mock"})
        self.metadata[trace_id] = meta
        self._save()
        return meta

    async def _finalize_remote(self, meta: dict) -> dict:
        adapter = scenario_manager.adapter
        if not isinstance(adapter, RemoteExecutionAdapter):
            return meta
        summary = await adapter.summarize_remote_capture(meta["id"])
        await adapter.fetch_remote_capture(meta["id"], get_settings().capture_dir / meta["file"])
        meta.update(summary)
        meta["status"] = "completed"
        meta["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return meta

    async def refresh(self, meta: dict) -> dict:
        if meta["status"] != "running":
            return meta
        if meta.get("source") == "remote":
            adapter = scenario_manager.adapter
            if not isinstance(adapter, RemoteExecutionAdapter):
                return meta
            state = await adapter.remote_capture_state(meta["id"], meta["pid"])
            meta.update(state)
            if state["status"] == "completed":
                return await self._finalize_remote(meta)
        else:
            process = self.processes.get(meta["id"])
            if process and process.returncode is not None:
                meta["status"] = "completed" if process.returncode == 0 else "failed"
                self.processes.pop(meta["id"], None)
                self._save()
        return meta

    async def stop(self, trace_id: str) -> dict:
        if trace_id not in self.metadata:
            raise KeyError(trace_id)
        meta = self.metadata[trace_id]
        if meta["status"] != "running":
            return meta
        if meta.get("source") == "remote":
            adapter = scenario_manager.adapter
            if not isinstance(adapter, RemoteExecutionAdapter):
                raise RuntimeError("El adaptador remoto no está disponible")
            state = await adapter.stop_remote_capture(trace_id, meta["pid"])
            for _ in range(10):
                if state["status"] == "completed":
                    break
                await asyncio.sleep(0.25)
                state = await adapter.remote_capture_state(trace_id, meta["pid"])
            meta.update(state)
            if state["status"] == "completed":
                return await self._finalize_remote(meta)
            meta["status"] = "stopping"
        else:
            process = self.processes.pop(trace_id, None)
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            meta["status"] = "completed"
        self._save()
        return meta

    async def list(self) -> list[dict]:
        for meta in list(self.metadata.values()):
            await self.refresh(meta)
        return sorted(self.metadata.values(), key=lambda item: item["created_at"], reverse=True)

    async def ensure_local_file(self, trace_id: str) -> dict:
        if trace_id not in self.metadata:
            raise KeyError(trace_id)
        meta = await self.refresh(self.metadata[trace_id])
        if meta["status"] not in {"completed", "interrupted"}:
            raise RuntimeError("La captura aún no ha finalizado")
        path = get_settings().capture_dir / meta["file"]
        if not path.exists() and meta.get("source") == "remote":
            adapter = scenario_manager.adapter
            if not isinstance(adapter, RemoteExecutionAdapter):
                raise RuntimeError("El adaptador remoto no está disponible")
            await adapter.fetch_remote_capture(trace_id, path)
        return meta


class MetricsService:
    def __init__(self) -> None:
        self.previous: dict[str, tuple[float, int, int]] = {}
        self.history: list[dict] = []

    async def snapshot(self, scenario_id: str = "5g-sa") -> dict:
        now = time.monotonic()
        adapter = scenario_manager.adapter
        try:
            kpis = await adapter.remote_kpis()
            raw_interfaces = kpis.get("interfaces", {})
            cpu_percent = round(min(float(kpis.get("load_1m", 0.0)) * 25.0, 100.0), 1)
            memory_percent = float(kpis.get("memory_percent", 0.0))
            source = "remote" if isinstance(adapter, RemoteExecutionAdapter) else "local" if hasattr(adapter, "allowed_units") else "mock"
        except Exception:
            raw_interfaces = {}
            for name, counters in psutil.net_io_counters(pernic=True).items():
                if name in get_settings().allowed_interfaces:
                    raw_interfaces[name] = {"rx_bytes": counters.bytes_recv, "tx_bytes": counters.bytes_sent}
            cpu_percent = psutil.cpu_percent(interval=None)
            memory_percent = psutil.virtual_memory().percent
            source = "host_fallback"

        interfaces = {}
        for name, counters in raw_interfaces.items():
            rx_bytes = counters["rx_bytes"]
            tx_bytes = counters["tx_bytes"]
            old = self.previous.get(name)
            elapsed = now - old[0] if old else 0
            rx_kbps = round((rx_bytes - old[1]) * 8 / elapsed / 1_000, 2) if elapsed and elapsed > 0 and rx_bytes >= old[1] else 0.0
            tx_kbps = round((tx_bytes - old[2]) * 8 / elapsed / 1_000, 2) if elapsed and elapsed > 0 and tx_bytes >= old[2] else 0.0
            rx_mbps = round(rx_kbps / 1_000, 3)
            tx_mbps = round(tx_kbps / 1_000, 3)
            interfaces[name] = {
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_kbps": rx_kbps,
                "tx_kbps": tx_kbps,
                "rx_mbps": rx_mbps,
                "tx_mbps": tx_mbps,
            }
            self.previous[name] = (now, rx_bytes, tx_bytes)

        try:
            status = await scenario_manager.status(scenario_id)
            active_nfs = sum(1 for c in status.components if c.status == "running")
            total_nfs = len(status.components)
            ue_active = any(c.id == "ue" and c.status == "running" for c in status.components)
            ue_registered = 1 if ue_active else 0
            if scenario_id == "5g-sa" and ue_active:
                tun_count = len([k for k in raw_interfaces if k.startswith("uesimtun")])
                if get_settings().multi_upf_enabled or tun_count >= 2 or get_settings().execution_mode == "remote":
                    pdu_active = 3
                    slices_active = 3
                    slices_detail = ["eMBB", "URLLC", "MIoT"]
                else:
                    pdu_active = 1
                    slices_active = 1
                    slices_detail = ["eMBB"]
            else:
                pdu_active = 1 if ue_active else 0
                slices_active = 1 if ue_active else 0
                slices_detail = ["eMBB"] if ue_active else []
        except Exception:
            active_nfs, total_nfs, ue_registered, pdu_active = 0, 0, 0, 0
            slices_active = 0
            slices_detail = []

        ogstun_data = interfaces.get("ogstun", {})
        lo_data = interfaces.get("lo", {})
        sample = {
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "ogstun_kbps": round(ogstun_data.get("rx_kbps", 0) + ogstun_data.get("tx_kbps", 0), 2),
            "lo_kbps": round(lo_data.get("rx_kbps", 0) + lo_data.get("tx_kbps", 0), 2),
            "cpu": cpu_percent,
            "mem": memory_percent,
        }
        self.history.append(sample)
        if len(self.history) > 25:
            self.history.pop(0)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "interfaces": interfaces,
            "telco": {
                "active_nfs": active_nfs,
                "total_nfs": total_nfs,
                "ue_registered": ue_registered,
                "pdu_sessions": pdu_active,
                "slices_active": slices_active,
                "slices_detail": slices_detail,
            },
            "history": self.history,
        }


async def collect_alarms(scenario_id: str, evaluated: set[str] | None = None) -> list[dict]:
    evaluated = evaluated if evaluated is not None else set()
    status, runtime = await asyncio.gather(
        scenario_manager.status(scenario_id),
        scenario_manager.adapter.runtime_snapshot(),
    )
    alarms = []
    observed_at = datetime.now(timezone.utc).isoformat()
    for component in status.components:
        if component.status in {"running", "stopped", "failed"}:
            evaluated.add(f"{scenario_id}:{component.id}:service-down")
        if component.status in {"stopped", "failed"}:
            severity = Severity.critical if component.kind in {"database", "core"} else Severity.major
            alarms.append({"id": f"{scenario_id}:{component.id}:service-down", "scenario_id": scenario_id, "component": component.id, "network_function": component.label, "node_id": component.node_id, "severity": severity, "state": "active", "probable_cause": "serviceUnavailable", "interfaces": component.interfaces, "procedures": component.procedures, "message": f"{component.label} no está activo", "evidence": f"{component.unit} = {component.status}", "recommendation": f"Revisar el servicio {component.unit} y sus dependencias", "observed_at": observed_at})

    if runtime["source"] != "mock":
        observed = {(item["protocol"], item["address"], item["port"]) for item in runtime["listening_ports"]}
        contexts = {h['id']: h for h in runtime.get('hosts', [])}
        for component in status.components:
            if component.status != "running":
                continue
            for endpoint in component.expected_endpoints:
                context = contexts.get(endpoint.get('runtime_id', 'core'))
                if contexts and (context is None or context.get('observation_status') != 'observed'):
                    continue  # Unknown observation neither opens nor resolves a port alarm.
                scoped = {(p['protocol'], p['address'], p['port']) for p in context['listening_ports']} if context else observed
                evaluated.add(f"{scenario_id}:{component.id}:port:{endpoint['protocol']}:{endpoint['port']}")
                key = (endpoint["protocol"], endpoint["address"], endpoint["port"])
                if key in scoped:
                    continue
                interface_name = endpoint.get("interface", "desconocida")
                alarms.append({"id": f"{scenario_id}:{component.id}:port:{endpoint['protocol']}:{endpoint['port']}", "scenario_id": scenario_id, "component": component.id, "network_function": component.label, "node_id": component.node_id, "severity": Severity.major, "state": "active", "probable_cause": "communicationsSubsystemFailure", "interfaces": [interface_name], "procedures": component.procedures, "message": f"Endpoint {interface_name} de {component.label} no está en escucha", "evidence": f"No se encontró {endpoint['protocol']}://{endpoint['address']}:{endpoint['port']}", "recommendation": f"Revisar {component.config_paths[0] if component.config_paths else component.unit} y la dirección configurada", "observed_at": observed_at})

        if scenario_id == "5g-sa":
            by_id = {component.id: component for component in status.components}
            log_jobs = []
            for component_id in ("gnb", "ue"):
                component = by_id.get(component_id)
                if component and component.status == "running":
                    log_jobs.append((component_id, scenario_manager.adapter.logs(component.unit, 250)))
            if log_jobs:
                results = await asyncio.gather(*(job for _, job in log_jobs))
                current_logs = {name: _current_activation_text(lines) for (name, _), lines in zip(log_jobs, results)}
                checks = [
                    ("gnb", "NG Setup procedure is successful", ["Association terminated", "AMF selection failed"], "N2", "NG Setup", "gNodeB sin asociación NGAP confirmada"),
                    ("ue", "Initial Registration is successful", ["Sending Initial Registration"], "N1/N2", "Registration", "UE sin registro 5G confirmado"),
                    ("ue", "PDU Session establishment is successful", ["Sending PDU Session Establishment Request"], "N1/N4", "PDU Session Establishment", "UE sin PDU Session confirmada"),
                ]
                for component_id, marker, invalidators, interface_name, procedure, message in checks:
                    if component_id not in current_logs:
                        continue
                    procedure_key = f"{scenario_id}:{component_id}:procedure:{procedure.lower().replace(' ', '-')}"
                    if _marker_is_current(current_logs[component_id], marker, invalidators):
                        evaluated.add(procedure_key)
                        continue
                    # An empty/truncated log is not evidence of failure or recovery.
                    if not any(token in current_logs[component_id] for token in invalidators):
                        continue
                    evaluated.add(procedure_key)
                    component = by_id[component_id]
                    alarms.append({"id": f"{scenario_id}:{component_id}:procedure:{procedure.lower().replace(' ', '-')}", "scenario_id": scenario_id, "component": component_id, "network_function": component.label, "node_id": component.node_id, "severity": Severity.major, "state": "active", "probable_cause": "procedureFailure", "interfaces": interface_name.split("/"), "procedures": [procedure], "message": message, "evidence": f"No aparece '{marker}' desde la última activación del servicio", "recommendation": f"Revisar logs de {component.label} y capturar {interface_name}", "observed_at": observed_at})

    try:
        ip_forward_active = await scenario_manager.adapter.get_ip_forward()
        evaluated.add(f"{scenario_id}:ops-f01:forwarding-disabled")
        if not ip_forward_active:
            upf_comp = next((c for c in status.components if c.id == "upf"), None)
            alarms.append({
                "id": f"{scenario_id}:ops-f01:forwarding-disabled",
                "scenario_id": scenario_id,
                "component": "upf",
                "network_function": upf_comp.label if upf_comp else "UPF",
                "node_id": upf_comp.node_id if upf_comp else "node-upf",
                "severity": Severity.critical,
                "state": "active",
                "probable_cause": "communicationsSubsystemFailure",
                "interfaces": ["N6"],
                "procedures": ["User Plane Forwarding", "Internet Access"],
                "message": "Aislamiento de Plano de Usuario: net.ipv4.ip_forward=0 en host Linux",
                "evidence": "sysctl net.ipv4.ip_forward=0. El tráfico N6 del UE hacia Internet está bloqueado.",
                "recommendation": "Restablecer sysctl -w net.ipv4.ip_forward=1 y verificar reglas iptables de NAT",
                "observed_at": observed_at,
            })
    except Exception:
        pass

    try:
        from app.services.experiments import experiments_service
        exp_f01 = experiments_service.active_state.get("5g-f01", {})
        if scenario_id == "5g-sa":
            evaluated.add(f"{scenario_id}:5g-f01:authentication-rejected")
        if scenario_id == "5g-sa" and exp_f01.get("status") == "injected":
            udm_comp = next((c for c in status.components if c.id == "udm"), None)
            alarms.append({
                "id": f"{scenario_id}:5g-f01:authentication-rejected",
                "scenario_id": scenario_id,
                "component": "udm",
                "network_function": udm_comp.label if udm_comp else "UDM",
                "node_id": udm_comp.node_id if udm_comp else "node-udm",
                "severity": Severity.critical,
                "state": "active",
                "probable_cause": "authenticationFailure",
                "interfaces": ["N12", "N13"],
                "procedures": ["Authentication", "5G-AKA", "Registration"],
                "message": "Fallo de Autenticación 5G-AKA: SUPI 999700000000001 no registrado en UDM",
                "evidence": "MongoDB: IMSI 999700000000001 desprovisionado. Causa 5GMM #2 (IMSI unknown in UDM).",
                "recommendation": "Verificar aprovisionamiento de credenciales Ki/OPc en módulo de Suscriptores",
                "observed_at": observed_at,
            })
    except Exception:
        pass

    return alarms


def _current_activation_text(lines: list[str]) -> str:
    start = 0
    for index, line in enumerate(lines):
        if "Started UERANSIM simulated" in line:
            start = index
    return "\n".join(lines[start:])


def _marker_is_current(text: str, marker: str, invalidators: list[str]) -> bool:
    marker_position = text.rfind(marker)
    if marker_position < 0:
        return False
    return marker_position > max((text.rfind(item) for item in invalidators), default=-1)


from app.services.trace_tasks import trace_task_service as trace_service


metrics_service = MetricsService()
