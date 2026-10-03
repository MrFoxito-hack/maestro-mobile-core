import asyncio
import hashlib
import json
import re
import shutil
import struct
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.models import InterfaceTraceStart, Role, SubscriberTraceStart, TraceStart, UserPublic
from app.services.execution import RemoteExecutionAdapter
from app.services.scenarios import CATALOG, scenario_manager
from app.services.trace_analysis import TSHARK_FIELDS, build_trace_analysis, mask_subscriber, subscriber_hash
from app.services.trace_catalog import (
    SUBSCRIBER_USER_PLANE_FILTER,
    SUBSCRIBER_PROCEDURES,
    TRACE_PROFILES,
    profile,
    public_profiles,
    subscriber_capture_profile,
)
from app.services.trace_repository import trace_repository
from app.services.trace_release16 import POLICY_VERSION


ACTIVE_STATES = {"queued", "preparing", "running", "processing", "stopping"}
TERMINAL_STATES = {"completed", "failed", "interrupted"}


class TraceTaskError(RuntimeError):
    pass


class TraceAccessError(TraceTaskError):
    pass


class TraceTaskService:
    def __init__(self) -> None:
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.monitors: dict[str, asyncio.Task] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.capture_dir = get_settings().capture_dir
        self.legacy_metadata_path = self.capture_dir / "metadata.json"

    async def initialize(self) -> None:
        self._migrate_legacy_metadata()
        for task in trace_repository.list():
            if task["status"] not in ACTIVE_STATES:
                continue
            if task.get("source") == "remote" and task.get("pid"):
                try:
                    state = await scenario_manager.adapter.remote_capture_state(task["id"], task["pid"])
                    if state["status"] == "running":
                        trace_repository.update(task["id"], status="running", size_bytes=state.get("size_bytes", 0))
                        self._schedule_monitor(task["id"])
                    else:
                        await self._process(task["id"])
                except Exception as exc:
                    trace_repository.update(task["id"], status="interrupted", message=str(exc))
            else:
                trace_repository.update(task["id"], status="interrupted", message="El proceso no sobrevivió al reinicio del EMS")

    async def shutdown(self) -> None:
        monitors = list(self.monitors.values())
        for monitor in monitors:
            monitor.cancel()
        if monitors:
            await asyncio.gather(*monitors, return_exceptions=True)
        self.monitors.clear()

    def _schedule_monitor(self, task_id: str) -> None:
        current = self.monitors.get(task_id)
        if current and not current.done():
            return
        self.monitors[task_id] = asyncio.create_task(self._monitor(task_id))

    async def _monitor(self, task_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                task = await self.refresh(task_id)
                if task["status"] not in {"running", "processing"}:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                trace_repository.update(
                    task_id,
                    status="failed",
                    result="monitor_error",
                    message=str(exc),
                    completed_at=self._now(),
                )
            except Exception:
                pass
        finally:
            self.monitors.pop(task_id, None)

    def _migrate_legacy_metadata(self) -> None:
        if not self.legacy_metadata_path.exists() or trace_repository.list():
            return
        try:
            legacy_items = json.loads(self.legacy_metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in legacy_items:
            task_id = item.get("id")
            if not isinstance(task_id, str) or len(task_id) != 32:
                continue
            point_id = item.get("capture_point") or "n2"
            scenario_id = item.get("scenario_id") or "5g-sa"
            point = TRACE_PROFILES.get(scenario_id, {}).get(point_id, {})
            status = item.get("status", "interrupted")
            if status in {"running", "stopping"}:
                status = "interrupted"
            task = {
                "id": task_id,
                "name": item.get("capture_point_label") or f"Captura heredada {task_id[:8]}",
                "trace_type": "interface",
                "scenario_id": scenario_id,
                "testbed_id": "local",
                "owner": item.get("owner", "legacy"),
                "owner_role": "teacher",
                "capture_agent_id": "primary",
                "capture_host": None,
                "node_id": None,
                "component_id": (point.get("nf_ids") or [None])[0],
                "capture_point": point_id,
                "selector_kind": None,
                "selector_hash": None,
                "selector_masked": None,
                "procedures": item.get("procedures", point.get("procedures", [])),
                "interfaces_3gpp": [point.get("interface_3gpp", point_id.upper())],
                "protocols": [item.get("protocol", "unknown").upper()],
                "include_user_plane": False,
                "include_sbi": False,
                "auto_trigger": False,
                "duration_seconds": int(item.get("duration_seconds", 60)),
                "max_megabytes": int(item.get("max_megabytes", 25)),
                "status": status,
                "result": "no_traffic" if not item.get("packet_count") and status == "completed" else None,
                "source": item.get("source", "legacy"),
                "device": item.get("interface"),
                "filter_profile": point_id,
                "pcap_file": item.get("file", f"{task_id}.pcap"),
                "filtered_file": None,
                "analysis_file": None,
                "pid": item.get("pid"),
                "remote_path": item.get("remote_path"),
                "remote_log": item.get("remote_log"),
                "packet_count": int(item.get("packet_count", 0)),
                "size_bytes": int(item.get("size_bytes", 0)),
                "protocol_summary": item.get("protocols", []),
                "message": "Importada desde Trace Studio v1",
                "created_at": item.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "started_at": item.get("created_at"),
                "completed_at": item.get("completed_at"),
            }
            try:
                trace_repository.create(task)
            except Exception:
                continue

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        hidden = {"selector_hash", "pid", "remote_path", "remote_log", "device"}
        result = {key: value for key, value in task.items() if key not in hidden}
        if task.get("scenario_id") == "5g-sa" and task.get("analysis_file") and POLICY_VERSION not in task["analysis_file"]:
            result["result"] = "unknown"
            result["outcome"] = "unknown"
        result["target"] = (
            {"kind": task.get("selector_kind"), "masked": task.get("selector_masked")}
            if task.get("selector_kind")
            else None
        )
        result["artifacts"] = [
            {
                "id": "original",
                "label": "Captura original",
                "available": bool(task.get("pcap_file") and task["status"] in TERMINAL_STATES),
            },
            {
                "id": "filtered",
                "label": "PCAP correlacionado",
                "available": bool(task.get("filtered_file")),
            },
            {
                "id": "evidence",
                "label": "Evidencia JSON",
                "available": bool(task.get("analysis_file")),
            },
        ]
        return result

    @staticmethod
    def _assert_testbed(user: UserPublic, testbed_id: str) -> None:
        if user.role == Role.student and user.testbed != testbed_id:
            raise TraceAccessError("El alumno solo puede operar su testbed asignado")

    @staticmethod
    def _assert_access(task: dict[str, Any], user: UserPublic, *, mutate: bool = False) -> None:
        if user.role in {Role.admin, Role.teacher}:
            return
        if task["owner"] != user.username or task["testbed_id"] != user.testbed:
            raise TraceAccessError("La tarea pertenece a otro usuario o testbed")
        if mutate and task["status"] not in ACTIVE_STATES:
            raise TraceAccessError("La tarea ya no está activa")

    def _apply_quota(self, user: UserPublic, *, testbed_id: str, duration: int, size: int) -> None:
        self._assert_testbed(user, testbed_id)
        if user.role != Role.student:
            return
        if duration > 300 or size > 100:
            raise TraceAccessError("La cuota del alumno es 300 segundos y 100 MB por tarea")
        if trace_repository.count_active(user.username, testbed_id) >= 1:
            raise TraceAccessError("El alumno ya tiene una tarea de captura activa")

    async def capabilities(self, scenario_id: str, user: UserPublic) -> dict[str, Any]:
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)
        scenario = CATALOG[scenario_id]
        status, runtime = await asyncio.gather(
            scenario_manager.status(scenario_id),
            scenario_manager.adapter.runtime_snapshot(),
        )
        components = [component.model_dump() for component in status.components]
        quota = {
            "max_active": 1 if user.role == Role.student else 4,
            "max_duration_seconds": 300 if user.role == Role.student else 900,
            "max_megabytes": 100 if user.role == Role.student else 500,
            "auto_trigger_allowed": False,
        }
        logical_node_ids = [node["id"] for node in scenario["nodes"]]
        subscriber_supported = scenario_id == "5g-sa"
        return {
            "scenario_id": scenario_id,
            "technology": scenario["technology"],
            "testbed_id": user.testbed or "local",
            "capture_agents": [
                {
                    "id": "primary",
                    "label": runtime["hostname"],
                    "source": runtime["source"],
                    "status": "online",
                    "logical_node_ids": logical_node_ids,
                    "node_ids": logical_node_ids,
                }
            ],
            "network_functions": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "node_id": item["node_id"],
                    "interfaces": item["interfaces"],
                    "status": item["status"],
                }
                for item in components
                if item["kind"] != "database"
            ],
            "capture_targets": public_profiles(scenario_id, components),
            "subscriber": {
                "supported": subscriber_supported,
                "enabled": subscriber_supported,
                "identifier_types": [
                    {"id": "imsi", "label": "SUPI (IMSI)"},
                    {"id": "supi", "label": "SUPI"},
                    {"id": "ue-ip", "label": "Dirección IP del UE"},
                ],
                "procedures": SUBSCRIBER_PROCEDURES,
                "default_procedures": ["registration", "authentication", "pdu-session"],
                "supports_user_plane": subscriber_supported,
                "supports_sbi": subscriber_supported,
                "supports_auto_trigger": subscriber_supported and quota["auto_trigger_allowed"],
                "participating_components": ["ue", "gnb", "amf", "ausf", "udm", "smf", "upf"],
                "interfaces": ["N1/N2", "SBI", "N4", "N3", "N6"],
                "n1_note": "N1/NAS se decodifica dentro de NGAP capturado sobre N2.",
            },
            "quota": quota,
            "limits": {
                "min_duration_seconds": 5,
                "max_duration_seconds": quota["max_duration_seconds"],
                "max_megabytes": quota["max_megabytes"],
                "max_concurrent_tasks": quota["max_active"],
            },
        }

    async def create_interface(self, request: InterfaceTraceStart, user: UserPublic) -> dict[str, Any]:
        self._apply_quota(
            user,
            testbed_id=request.testbed_id,
            duration=request.duration_seconds,
            size=request.max_megabytes,
        )
        if request.scenario_id not in CATALOG:
            raise KeyError(request.scenario_id)
        if request.capture_agent_id != "primary":
            raise TraceTaskError("Agente de captura no disponible")
        component = scenario_manager.component(request.scenario_id, request.component_id)
        point = profile(request.scenario_id, request.capture_point)
        if request.component_id not in point["nf_ids"]:
            raise TraceTaskError("La NF seleccionada no participa en esa interfaz")
        if component["node_id"] != request.node_id:
            raise TraceTaskError("La NF no pertenece al nodo lógico seleccionado")
        task = self._new_task(
            request=request,
            user=user,
            trace_type="interface",
            point=point,
            node_id=request.node_id,
            component_id=request.component_id,
            capture_point=request.capture_point,
        )
        if request.identifier and request.identifier_type:
            task["selector_kind"] = request.identifier_type
            task["selector_hash"] = subscriber_hash(request.identifier)
            task["selector_masked"] = mask_subscriber(request.identifier)
        return await self._start_task(task)

    async def create_subscriber(self, request: SubscriberTraceStart, user: UserPublic) -> dict[str, Any]:
        self._apply_quota(
            user,
            testbed_id=request.testbed_id,
            duration=request.duration_seconds,
            size=request.max_megabytes,
        )
        if request.capture_agent_id != "primary":
            raise TraceTaskError("Agente de captura no disponible")
        if request.auto_trigger and user.role not in {Role.admin, Role.teacher}:
            raise TraceAccessError("Solo docente o administrador puede reiniciar automáticamente el UE")
        point = subscriber_capture_profile(request.include_sbi)
        if request.include_user_plane:
            # Both PDU sessions already exist; selecting an APN changes the
            # terminal data path without necessarily producing new signaling.
            point["filter"] += f" or {SUBSCRIBER_USER_PLANE_FILTER}"
            if "N6" not in point["interface_3gpp"]:
                point["interface_3gpp"].append("N6")
        task = self._new_task(
            request=request,
            user=user,
            trace_type="subscriber",
            point=point,
            node_id="distributed",
            component_id=None,
            capture_point="subscriber-5g",
        )
        task["selector_kind"] = request.identifier_type
        task["selector_hash"] = subscriber_hash(request.identifier)
        task["selector_masked"] = mask_subscriber(request.identifier) if request.identifier_type != "ue-ip" else request.identifier
        result = await self._start_task(task)
        if request.auto_trigger and result["status"] == "running":
            try:
                trigger = await self._trigger_registration(request.include_user_plane)
                trace_repository.update(
                    task["id"],
                    message=f"Registration disparado y UE verificado: {trigger}",
                )
                result = self._public(trace_repository.get(task["id"]))
            except Exception as exc:
                trace_repository.update(task["id"], message=f"Captura activa; no se pudo disparar el UE: {exc}")
                result = self._public(trace_repository.get(task["id"]))
        return result

    async def create_legacy(self, request: TraceStart, user: UserPublic) -> dict[str, Any]:
        points = TRACE_PROFILES.get(request.scenario_id)
        if not points:
            raise TraceTaskError("Escenario no soportado")
        point_id = request.capture_point
        if not point_id:
            point_id = next(
                (
                    key
                    for key, item in points.items()
                    if item["device"] == request.interface
                    and request.protocol
                    and request.protocol.lower() in {protocol.lower().replace("-", "") for protocol in item["protocols"]}
                ),
                None,
            )
        if not point_id:
            protocol_aliases = {"ngap": "n2", "pfcp": "n4", "gtpu": "n3", "ip": "n6"}
            point_id = protocol_aliases.get((request.protocol or "").lower())
        if not point_id or point_id not in points:
            raise TraceTaskError("La combinación de interfaz y protocolo no está permitida")
        point = profile(request.scenario_id, point_id)
        component_id = point["nf_ids"][0]
        component = scenario_manager.component(request.scenario_id, component_id)
        payload = InterfaceTraceStart(
            name=request.name,
            scenario_id=request.scenario_id,
            testbed_id=request.testbed_id,
            capture_agent_id=request.capture_agent_id,
            node_id=component["node_id"],
            component_id=component_id,
            capture_point=point_id,
            duration_seconds=request.duration_seconds,
            max_megabytes=request.max_megabytes,
        )
        return await self.create_interface(payload, user)

    def _new_task(
        self,
        *,
        request,
        user: UserPublic,
        trace_type: str,
        point: dict[str, Any],
        node_id: str | None,
        component_id: str | None,
        capture_point: str,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        now = self._now()
        return {
            "id": task_id,
            "name": request.name.strip(),
            "trace_type": trace_type,
            "scenario_id": request.scenario_id,
            "testbed_id": request.testbed_id,
            "owner": user.username,
            "owner_role": user.role.value,
            "capture_agent_id": request.capture_agent_id,
            "capture_host": None,
            "node_id": node_id,
            "component_id": component_id,
            "capture_point": capture_point,
            "selector_kind": None,
            "selector_hash": None,
            "selector_masked": None,
            "procedures": getattr(request, "procedures", point.get("procedures", [])),
            "interfaces_3gpp": point["interface_3gpp"] if isinstance(point["interface_3gpp"], list) else [point["interface_3gpp"]],
            "protocols": point["protocols"],
            "include_user_plane": getattr(request, "include_user_plane", False),
            "include_sbi": getattr(request, "include_sbi", False),
            "auto_trigger": getattr(request, "auto_trigger", False),
            "duration_seconds": request.duration_seconds,
            "max_megabytes": request.max_megabytes,
            "status": "queued",
            "result": None,
            "source": None,
            "device": point["device"],
            "filter_profile": point["filter"],
            "pcap_file": f"{task_id}.pcap",
            "filtered_file": None,
            "analysis_file": None,
            "pid": None,
            "remote_path": None,
            "remote_log": None,
            "packet_count": 0,
            "size_bytes": 0,
            "protocol_summary": [],
            "message": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
        }

    async def _start_task(self, task: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        adapter = scenario_manager.adapter
        runtime = await adapter.runtime_snapshot()
        task["capture_host"] = runtime["hostname"]
        trace_repository.create(task)
        trace_repository.update(task["id"], status="preparing")
        path = settings.capture_dir / task["pcap_file"]
        try:
            if settings.enable_real_captures and isinstance(adapter, RemoteExecutionAdapter):
                handle = await adapter.start_remote_capture(
                    task["id"],
                    task["device"],
                    task["filter_profile"],
                    task["duration_seconds"],
                    task["max_megabytes"] * 1024,
                )
                updated = trace_repository.update(
                    task["id"],
                    status="running",
                    source="remote",
                    pid=handle["pid"],
                    remote_path=handle["remote_path"],
                    remote_log=handle["remote_log"],
                    started_at=self._now(),
                )
                self._schedule_monitor(task["id"])
            elif settings.enable_real_captures:
                if not shutil.which("tshark"):
                    raise TraceTaskError("tshark no está instalado")
                process = await asyncio.create_subprocess_exec(
                    "tshark",
                    "-n",
                    "-i",
                    task["device"],
                    "-a",
                    f"duration:{task['duration_seconds']}",
                    "-a",
                    f"filesize:{task['max_megabytes'] * 1024}",
                    "-f",
                    task["filter_profile"],
                    "-w",
                    str(path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.processes[task["id"]] = process
                updated = trace_repository.update(
                    task["id"], status="running", source="local", pid=process.pid, started_at=self._now()
                )
                self._schedule_monitor(task["id"])
            else:
                self._write_empty_pcap(path)
                updated = trace_repository.update(
                    task["id"],
                    status="completed",
                    source="mock",
                    result="partial" if task["trace_type"] == "subscriber" else "no_traffic",
                    size_bytes=path.stat().st_size,
                    completed_at=self._now(),
                )
                if task["trace_type"] == "subscriber":
                    await self._process_mock(task["id"])
                    updated = trace_repository.get(task["id"])
            return self._public(updated)
        except Exception as exc:
            trace_repository.update(task["id"], status="failed", result="capture_error", message=str(exc), completed_at=self._now())
            raise

    async def _trigger_registration(self, include_user_plane: bool) -> str:
        # Give the remote tshark process enough time to attach to `any` before
        # tearing down the old UE association.
        await asyncio.sleep(0.75)
        # Check if gNB is already running and connected to AMF
        gnb_up = False
        try:
            gnb_stat = await scenario_manager.adapter.native_operation("ueransim-cli", "gnb", {"command": "status"})
            gnb_up = "true" in gnb_stat.get("output", "")
        except Exception:
            gnb_up = False

        if not gnb_up:
            try:
                await scenario_manager.stop_component("5g-sa", "gnb")
                await asyncio.sleep(0.5)
                await scenario_manager.start_component("5g-sa", "gnb")
                for _ in range(8):
                    await asyncio.sleep(1)
                    st = await scenario_manager.adapter.native_operation("ueransim-cli", "gnb", {"command": "status"})
                    if "true" in st.get("output", ""):
                        break
                await asyncio.sleep(2)  # Allow SIB1 broadcast stabilization
            except Exception:
                pass

        await scenario_manager.stop_component("5g-sa", "ue")
        await asyncio.sleep(1)
        await scenario_manager.start_component("5g-sa", "ue")

        status = ""
        recovered = False
        for attempt in range(15):
            await asyncio.sleep(1)
            result = await scenario_manager.adapter.native_operation(
                "ueransim-cli", "ue", {"command": "status"}
            )
            status = result.get("output", "")
            if "RM-REGISTERED" in status and "CM-CONNECTED" in status:
                break
            if "LIMITED-SERVICE" in status and not recovered and attempt >= 3:
                # UERANSIM scanned before SIB1 was available; restart to select category[SUITABLE]
                recovered = True
                await scenario_manager.stop_component("5g-sa", "ue")
                await asyncio.sleep(1)
                await scenario_manager.start_component("5g-sa", "ue")
        else:
            raise TraceTaskError(
                "UERANSIM no alcanzó RM-REGISTERED/CM-CONNECTED después del reinicio"
            )
        if include_user_plane:
            try:
                await scenario_manager.adapter.generate_test_traffic(3)
            except Exception:
                pass
        return "RM-REGISTERED / CM-CONNECTED"

    @staticmethod
    def _write_empty_pcap(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))

    async def refresh(self, task_id: str) -> dict[str, Any]:
        task = trace_repository.get(task_id)
        if task["status"] != "running":
            return task
        adapter = scenario_manager.adapter
        if task["source"] == "remote" and isinstance(adapter, RemoteExecutionAdapter):
            state = await adapter.remote_capture_state(task_id, task["pid"])
            task = trace_repository.update(task_id, size_bytes=state.get("size_bytes", task["size_bytes"]))
            if state["status"] == "completed":
                return await self._process(task_id)
        elif task["source"] == "local":
            process = self.processes.get(task_id)
            if process and process.returncode is not None:
                self.processes.pop(task_id, None)
                if process.returncode == 0:
                    return await self._process(task_id)
                error = (await process.stderr.read()).decode(errors="replace") if process.stderr else ""
                return trace_repository.update(task_id, status="failed", result="capture_error", message=error, completed_at=self._now())
        return task

    async def _process(self, task_id: str) -> dict[str, Any]:
        lock = self.locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            current = trace_repository.get(task_id)
            if current["status"] in TERMINAL_STATES:
                return current
            task = trace_repository.update(task_id, status="processing")
            adapter = scenario_manager.adapter
            path = get_settings().capture_dir / task["pcap_file"]
            try:
                if task["source"] == "remote" and isinstance(adapter, RemoteExecutionAdapter):
                    summary = await adapter.summarize_remote_capture(task_id)
                    await adapter.fetch_remote_capture(task_id, path)
                    tshark_output, tshark_version = await adapter.analyze_remote_capture(task_id)
                else:
                    summary = self._summarize_local(path)
                    tshark_output, tshark_version = self._analyze_local(path)
                logs = await self._log_markers(task.get("started_at"))
                analysis_task = {
                    **task,
                    "scenario_defaults": CATALOG[task["scenario_id"]]["defaults"],
                }
                analysis = build_trace_analysis(
                    analysis_task,
                    tshark_output,
                    log_markers=logs,
                    tshark_version=tshark_version,
                )
                frame_numbers = [event["packet_number"] for event in analysis["events"] if event.get("packet_number")]
                filtered_file = None
                if frame_numbers and task["source"] == "remote" and isinstance(adapter, RemoteExecutionAdapter):
                    remote_filtered = await adapter.export_remote_filtered_capture(task_id, frame_numbers)
                    if remote_filtered:
                        filtered_file = f"{task_id}.filtered.pcap"
                        await adapter.fetch_remote_artifact(remote_filtered, get_settings().capture_dir / filtered_file)
                analysis_file = f"{task_id}.{POLICY_VERSION}.analysis.json" if task["scenario_id"] == "5g-sa" else f"{task_id}.analysis.json"
                analysis_path = get_settings().capture_dir / analysis_file
                analysis["artifacts"] = self._artifact_metadata(path, filtered_file, analysis_file)
                analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
                trace_repository.replace_events(task_id, analysis["events"])
                return trace_repository.update(
                    task_id,
                    status="completed",
                    result=analysis["result"],
                    packet_count=summary["packet_count"],
                    size_bytes=summary["size_bytes"],
                    protocol_summary=summary["protocols"],
                    filtered_file=filtered_file,
                    analysis_file=analysis_file,
                    completed_at=self._now(),
                )
            except Exception as exc:
                return trace_repository.update(
                    task_id,
                    status="failed",
                    result="analysis_error",
                    message=str(exc),
                    completed_at=self._now(),
                )

    async def _process_mock(self, task_id: str) -> None:
        task = trace_repository.get(task_id)
        analysis_task = {**task, "source": "simulated", "scenario_defaults": CATALOG[task["scenario_id"]]["defaults"]}
        analysis = build_trace_analysis(
            analysis_task,
            self._mock_tshark_output(),
            log_markers={"registration": True, "pdu_session": True},
            tshark_version="TShark simulated",
        )
        analysis_file = f"{task_id}.{POLICY_VERSION}.analysis.json" if task["scenario_id"] == "5g-sa" else f"{task_id}.analysis.json"
        analysis_path = get_settings().capture_dir / analysis_file
        analysis["artifacts"] = self._artifact_metadata(
            get_settings().capture_dir / task["pcap_file"], None, analysis_file
        )
        analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        trace_repository.replace_events(task_id, analysis["events"])
        trace_repository.update(
            task_id,
            result=analysis["result"],
            packet_count=len(analysis["events"]),
            protocols=["NGAP", "NAS-5GS", "PFCP", "GTP-U"],
            protocol_summary=[
                {"protocol": "NGAP/NAS-5GS", "packets": 3},
                {"protocol": "PFCP", "packets": 2},
                {"protocol": "GTP-U", "packets": 1},
            ],
            analysis_file=analysis_file,
        )

    async def _log_markers(self, started_at: str | None = None) -> dict[str, bool]:
        try:
            lines = await scenario_manager.adapter.logs("ueransim-ue", 300)
            if started_at:
                cutoff = datetime.fromisoformat(started_at.replace("Z", "+00:00")) - timedelta(seconds=1)
                recent = []
                for line in lines:
                    match = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]", line)
                    if not match:
                        continue
                    timestamp = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
                    if timestamp >= cutoff:
                        recent.append(line)
                lines = recent
            text = "\n".join(lines)
            return {
                "registration": "Initial Registration is successful" in text,
                "pdu_session": "PDU Session establishment is successful" in text,
            }
        except Exception:
            return {}

    @staticmethod
    def _artifact_metadata(original: Path, filtered_file: str | None, analysis_file: str) -> list[dict[str, Any]]:
        artifacts = []
        if original.exists():
            artifacts.append(
                {
                    "id": "original",
                    "file": original.name,
                    "size_bytes": original.stat().st_size,
                    "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                    "media_type": "application/vnd.tcpdump.pcap",
                }
            )
        if filtered_file:
            filtered = original.parent / filtered_file
            if filtered.exists():
                artifacts.append(
                    {
                        "id": "filtered",
                        "file": filtered.name,
                        "size_bytes": filtered.stat().st_size,
                        "sha256": hashlib.sha256(filtered.read_bytes()).hexdigest(),
                        "media_type": "application/vnd.tcpdump.pcap",
                    }
                )
        artifacts.append({"id": "evidence", "file": analysis_file, "media_type": "application/json"})
        return artifacts

    @staticmethod
    def _summarize_local(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise TraceTaskError("La captura local no existe")
        if not shutil.which("tshark"):
            return {"packet_count": 0, "size_bytes": path.stat().st_size, "protocols": []}
        result = subprocess.run(
            ["tshark", "-r", str(path), "-T", "fields", "-e", "_ws.col.Protocol"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        protocols: dict[str, int] = {}
        for line in result.stdout.splitlines():
            if line.strip():
                protocols[line.strip()] = protocols.get(line.strip(), 0) + 1
        return {
            "packet_count": sum(protocols.values()),
            "size_bytes": path.stat().st_size,
            "protocols": [{"protocol": key, "packets": value} for key, value in protocols.items()],
        }

    @staticmethod
    def _analyze_local(path: Path) -> tuple[str, str]:
        executable = shutil.which("tshark")
        windows_tshark = Path("C:/Program Files/Wireshark/tshark.exe")
        if not executable and windows_tshark.is_file():
            executable = str(windows_tshark)
        if not executable:
            return "", "tshark unavailable"
        command = [
            executable, "-n", "-r", str(path), "-d", "tcp.port==7777,http2",
            "-d", "tcp.port==8081,http2", "-d", "tcp.port==18081,http2", "-T", "fields",
            "-E", "separator=/t", "-E", "quote=d", "-E", "occurrence=a", "-E", "aggregator=,",
        ]
        for field in TSHARK_FIELDS:
            command.extend(["-e", field])
        output = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True).stdout
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()[0]
        return output, version

    @staticmethod
    def _mock_tshark_output() -> str:
        def row(values: dict[str, str]) -> str:
            return "\t".join(f'"{values.get(field, "")}"' for field in TSHARK_FIELDS)

        return "\n".join(
            [
                row({"frame.number": "1", "frame.time_epoch": "1700000000.100", "_ws.col.Protocol": "NGAP/NAS-5GS", "_ws.col.Info": "InitialUEMessage, Registration request", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.5", "ngap.RAN_UE_NGAP_ID": "1", "nas_5gs.mm.suci.msin": "0000000001"}),
                row({"frame.number": "2", "frame.time_epoch": "1700000000.200", "_ws.col.Protocol": "NGAP/NAS-5GS", "_ws.col.Info": "DownlinkNASTransport, Authentication request", "ip.src": "127.0.0.5", "ip.dst": "127.0.0.1", "ngap.RAN_UE_NGAP_ID": "1", "ngap.AMF_UE_NGAP_ID": "4"}),
                row({"frame.number": "3", "frame.time_epoch": "1700000000.300", "_ws.col.Protocol": "NGAP/NAS-5GS", "_ws.col.Info": "UplinkNASTransport, Authentication response", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.5", "ngap.RAN_UE_NGAP_ID": "1", "ngap.AMF_UE_NGAP_ID": "4"}),
                row({"frame.number": "4", "frame.time_epoch": "1700000000.500", "_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Establishment Request", "ip.src": "127.0.0.4", "ip.dst": "127.0.0.7", "pfcp.seid": "0x345", "pfcp.f_teid.teid": "0x510c", "pfcp.ue_ip_addr_ipv4": "10.45.0.4", "e212.imsi": "999700000000001"}),
                row({"frame.number": "5", "frame.time_epoch": "1700000000.600", "_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Establishment Response", "ip.src": "127.0.0.7", "ip.dst": "127.0.0.4", "pfcp.seid": "0x345", "pfcp.f_teid.teid": "0x510c"}),
                row({"frame.number": "6", "frame.time_epoch": "1700000000.700", "_ws.col.Protocol": "NGAP", "_ws.col.Info": "PDUSessionResourceSetupResponse", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.5", "ngap.RAN_UE_NGAP_ID": "1", "ngap.AMF_UE_NGAP_ID": "4", "ngap.pDUSessionID": "1", "ngap.gTP_TEID": "0000510c"}),
                row({"frame.number": "7", "frame.time_epoch": "1700000000.900", "_ws.col.Protocol": "GTP", "_ws.col.Info": "G-PDU", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.7", "gtp.teid": "0x510c"}),
            ]
        )

    async def list(
        self,
        user: UserPublic,
        *,
        scenario_id: str | None = None,
        trace_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        owner = user.username if user.role == Role.student else None
        tasks = trace_repository.list(owner=owner, scenario_id=scenario_id, trace_type=trace_type, status=status)
        refreshed = []
        for task in tasks:
            if task["status"] == "running":
                task = await self.refresh(task["id"])
            if user.role != Role.student or task["testbed_id"] == user.testbed:
                refreshed.append(self._public(task))
        return refreshed

    async def get(self, task_id: str, user: UserPublic) -> dict[str, Any]:
        task = await self.refresh(task_id)
        self._assert_access(task, user)
        result = self._public(task)
        if task.get("analysis_file"):
            try:
                analysis = await self.analysis(task_id, user)
                result["outcome"] = analysis["outcome"]
                result["result"] = analysis["result"]
                result["analysis_summary"] = {
                    "outcome": analysis["outcome"],
                    "correlation_status": analysis["correlation_status"],
                    **analysis["stats"],
                }
            except Exception:
                pass
        return result

    async def stop(self, task_id: str, user: UserPublic) -> dict[str, Any]:
        task = trace_repository.get(task_id)
        self._assert_access(task, user, mutate=True)
        if task["status"] != "running":
            return self._public(task)
        trace_repository.update(task_id, status="stopping", message="Detenida por el usuario")
        adapter = scenario_manager.adapter
        if task["source"] == "remote" and isinstance(adapter, RemoteExecutionAdapter):
            state = await adapter.stop_remote_capture(task_id, task["pid"])
            for _ in range(12):
                if state["status"] == "completed":
                    break
                await asyncio.sleep(0.25)
                state = await adapter.remote_capture_state(task_id, task["pid"])
            if state["status"] != "completed":
                return self._public(trace_repository.update(task_id, status="failed", result="stop_timeout", completed_at=self._now()))
        else:
            process = self.processes.pop(task_id, None)
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
        processed = await self._process(task_id)
        return self._public(processed)

    async def analysis(self, task_id: str, user: UserPublic) -> dict[str, Any]:
        task = await self.refresh(task_id)
        self._assert_access(task, user)
        if task["status"] in ACTIVE_STATES:
            raise TraceTaskError("La tarea todavía está capturando o procesando")
        if not task.get("analysis_file"):
            raise FileNotFoundError("La tarea no dispone de análisis")
        path = (get_settings().capture_dir / task["analysis_file"]).resolve()
        if not path.is_relative_to(get_settings().capture_dir.resolve()):
            raise FileNotFoundError("El análisis no está disponible")
        if not path.exists():
            capture_p = (get_settings().capture_dir / task.get("pcap_file", "")).resolve()
            if not capture_p.exists():
                raise FileNotFoundError("El análisis no está disponible")
            analysis = {}
        else:
            try:
                analysis = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                analysis = {}
        if task.get("scenario_id") != "5g-sa" and analysis:
            return analysis
        from app.services.trace_release16 import POLICY_VERSION
        if analysis.get("analysis_policy") == POLICY_VERSION:
            return analysis
        # Preserve the original PCAP and legacy JSON. Reinterpret actual packet
        # evidence, never "repair" old inferred events by renaming them.
        lock = self.locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            filename = f"{task_id}.{POLICY_VERSION}.analysis.json"
            reviewed_path = get_settings().capture_dir / filename
            if reviewed_path.exists():
                return json.loads(reviewed_path.read_text(encoding="utf-8"))
            adapter = scenario_manager.adapter
            capture = (get_settings().capture_dir / task["pcap_file"]).resolve()
            if not capture.is_relative_to(get_settings().capture_dir.resolve()):
                raise TraceTaskError("Ruta de captura inválida")
            if capture.exists() and (shutil.which("tshark") or Path("C:/Program Files/Wireshark/tshark.exe").is_file()) and task.get("source") not in {"mock", "simulated"}:
                output, version = await asyncio.to_thread(self._analyze_local, capture)
            elif task.get("source") == "remote" and isinstance(adapter, RemoteExecutionAdapter):
                output, version = await adapter.analyze_remote_capture(task_id)
            elif task.get("source") in {"mock", "simulated"}:
                output, version = self._mock_tshark_output(), "TShark simulated"
            else:
                capture = (get_settings().capture_dir / task["pcap_file"]).resolve()
                if not capture.is_relative_to(get_settings().capture_dir.resolve()):
                    raise TraceTaskError("Ruta de captura inválida")
                output, version = await asyncio.to_thread(self._analyze_local, capture)
                if version == "tshark unavailable":
                    raise TraceTaskError("Se requiere TShark para revisar esta captura con la política Release 16")
            reviewed = build_trace_analysis({**task, "scenario_defaults": CATALOG["5g-sa"]["defaults"]},
                output, tshark_version=version)
            filtered_file = None
            executable = shutil.which("tshark") or ("C:/Program Files/Wireshark/tshark.exe" if Path("C:/Program Files/Wireshark/tshark.exe").is_file() else None)
            frames = sorted({e["packet_number"] for e in reviewed["events"] if e.get("packet_number")})
            if task.get("trace_type") == "subscriber" and frames and capture.exists() and executable and task.get("source") not in {"mock", "simulated"}:
                filtered_file = f"{task_id}.{POLICY_VERSION}.filtered.pcap"
                display_filter = " or ".join(f"frame.number=={n}" for n in frames)
                try:
                    await asyncio.to_thread(subprocess.run, [executable, "-r", str(capture),
                        "-Y", display_filter,
                        "-w", str((get_settings().capture_dir / filtered_file).resolve())],
                        capture_output=True, timeout=60, check=True)
                except subprocess.CalledProcessError:
                    filtered_file = None
            reviewed["artifacts"] = self._artifact_metadata(capture, filtered_file, filename)
            reviewed["supersedes_analysis"] = task["analysis_file"]
            reviewed_path.write_text(json.dumps(reviewed, indent=2, ensure_ascii=False), encoding="utf-8")
            trace_repository.replace_events(task_id, reviewed["events"])
            trace_repository.update(task_id, analysis_file=filename, filtered_file=filtered_file, result=reviewed["result"])
            return reviewed

    async def decode_frame(self, task_id: str, frame_number: int, user: UserPublic) -> dict[str, Any]:
        if frame_number < 1 or frame_number > 10_000_000:
            raise ValueError("NÃºmero de trama invÃ¡lido")
        task = await self.refresh(task_id)
        self._assert_access(task, user)
        if task["status"] in ACTIVE_STATES:
            raise TraceTaskError("Detenga la captura antes de abrir el detalle de trama")
        capture_root = get_settings().capture_dir.resolve()
        capture = (capture_root / task.get("pcap_file", "")).resolve()
        if not capture.is_relative_to(capture_root) or not capture.exists():
            raise FileNotFoundError("PCAP original no disponible")
        executable = shutil.which("tshark") or (
            "C:/Program Files/Wireshark/tshark.exe"
            if Path("C:/Program Files/Wireshark/tshark.exe").is_file() else None
        )
        if not executable:
            raise TraceTaskError("TShark no estÃ¡ disponible para decodificar la trama")
        command = [
            executable, "-n", "-r", str(capture), "-Y", f"frame.number == {frame_number}",
            "-d", "tcp.port==7777,http2", "-d", "tcp.port==8081,http2",
            "-d", "tcp.port==18081,http2", "-T", "json",
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run, command, capture_output=True, text=True,
                timeout=30, check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise TraceTaskError("La disecciÃ³n de la trama excediÃ³ 30 segundos") from exc
        except subprocess.CalledProcessError as exc:
            raise TraceTaskError((exc.stderr or "TShark no pudo decodificar la trama")[:500]) from exc
        analysis = await self.analysis(task_id, user)
        event = next(
            (item for item in analysis.get("events", []) if item.get("packet_number") == frame_number),
            None,
        )
        from app.services.trace_analysis import build_decode_tree
        return build_decode_tree(result.stdout, frame_number=frame_number, event=event)

    async def artifact(self, task_id: str, artifact: str, user: UserPublic) -> tuple[Path, str, str]:
        if artifact == "evidence":
            await self.analysis(task_id, user)
        task = await self.refresh(task_id)
        self._assert_access(task, user)
        if task["status"] in ACTIVE_STATES:
            raise TraceTaskError("La captura todavía no ha finalizado")
        names = {
            "original": task.get("pcap_file"),
            "filtered": task.get("filtered_file"),
            "evidence": task.get("analysis_file"),
        }
        if artifact not in names or not names[artifact]:
            raise FileNotFoundError("Artefacto no disponible")
        path = (get_settings().capture_dir / names[artifact]).resolve()
        if not path.is_relative_to(get_settings().capture_dir.resolve()) or not path.exists():
            raise FileNotFoundError("Artefacto no encontrado")
        media_type = "application/json" if artifact == "evidence" else "application/vnd.tcpdump.pcap"
        return path, media_type, names[artifact]

    async def delete(self, task_id: str, user: UserPublic) -> None:
        task = trace_repository.get(task_id)
        self._assert_access(task, user)
        if task["status"] in ACTIVE_STATES:
            raise TraceTaskError("Detenga la tarea antes de eliminarla")
        deleted = trace_repository.delete(task_id)
        capture_root = get_settings().capture_dir.resolve()
        for key in ("pcap_file", "filtered_file", "analysis_file"):
            filename = deleted.get(key)
            if not filename:
                continue
            path = (capture_root / filename).resolve()
            if path.is_relative_to(capture_root):
                path.unlink(missing_ok=True)
        adapter = scenario_manager.adapter
        if deleted.get("source") == "remote" and isinstance(adapter, RemoteExecutionAdapter):
            await adapter.delete_remote_capture(task_id)


trace_task_service = TraceTaskService()
