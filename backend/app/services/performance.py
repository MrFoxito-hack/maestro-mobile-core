import asyncio
import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.db import transaction
from app.models import Role, UserPublic
from app.services.observability import collect_alarms, metrics_service
from app.services.scenarios import CATALOG, scenario_manager
from app.services.telco_kpis import collect_telco_kpis
from app.services.nf_metrics import definitions as nf_definitions, nf_metrics


COUNTERS: list[dict[str, Any]] = [
    {"id": "host.cpu.percent", "label": "CPU utilizada", "category": "Recursos del host", "unit": "%", "kind": "gauge", "source": "runtime", "objects": ["testbed"]},
    {"id": "host.memory.percent", "label": "Memoria utilizada", "category": "Recursos del host", "unit": "%", "kind": "gauge", "source": "runtime", "objects": ["testbed"]},
    {"id": "core.nf.active", "label": "Funciones de red activas", "category": "Disponibilidad del core", "unit": "NFs", "kind": "gauge", "source": "systemd", "objects": ["testbed"]},
    {"id": "core.nf.availability", "label": "Disponibilidad de la NF", "category": "Disponibilidad del core", "unit": "%", "kind": "gauge", "source": "systemd", "objects": ["nf"]},
    {"id": "interface.rx.kbps", "label": "Throughput N3/N6 recibido", "category": "Tráfico por interfaz", "unit": "Kbps", "kind": "gauge", "source": "interface", "objects": ["interface", "nf"], "object_ids": ["interface:ogstun", "interface:ogstun_corp", "interface:ogstun2", "nf:upf", "nf:upf2"]},
    {"id": "interface.tx.kbps", "label": "Throughput N3/N6 transmitido", "category": "Tráfico por interfaz", "unit": "Kbps", "kind": "gauge", "source": "interface", "objects": ["interface", "nf"], "object_ids": ["interface:ogstun", "interface:ogstun_corp", "interface:ogstun2", "nf:upf", "nf:upf2"]},
    {"id": "interface.rx.bytes", "label": "Volumen N3/N6 recibido acumulado", "category": "Tráfico por interfaz", "unit": "bytes", "kind": "counter", "source": "interface", "objects": ["interface", "nf"], "object_ids": ["interface:ogstun", "interface:ogstun_corp", "interface:ogstun2", "nf:upf", "nf:upf2"]},
    {"id": "interface.tx.bytes", "label": "Volumen N3/N6 transmitido acumulado", "category": "Tráfico por interfaz", "unit": "bytes", "kind": "counter", "source": "interface", "objects": ["interface", "nf"], "object_ids": ["interface:ogstun", "interface:ogstun_corp", "interface:ogstun2", "nf:upf", "nf:upf2"]},
    {"id": "5g.ue.registered", "label": "UE registrados actualmente", "category": "5G Mobility Management", "unit": "UE", "kind": "gauge", "source": "AMF journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.attempts", "label": "Intentos de Registration", "category": "5G Mobility Management", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.successes", "label": "Registration exitosos", "category": "5G Mobility Management", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.rejects", "label": "Registration rechazados", "category": "5G Mobility Management", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.unresolved", "label": "Registration sin resultado confirmado", "category": "5G Mobility Management", "unit": "eventos", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.success_rate", "label": "Tasa de éxito de Registration", "category": "5G Mobility Management", "unit": "%", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.latency_ms", "label": "Latencia de Registration", "category": "5G Mobility Management", "unit": "ms", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.reject_authentication", "label": "Rechazos por autenticación", "category": "5G Registration · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.reject_unknown-subscriber", "label": "Rechazos por suscriptor desconocido", "category": "5G Registration · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.reject_slice", "label": "Rechazos por S-NSSAI/slice", "category": "5G Registration · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.registration.reject_other", "label": "Otros rechazos de Registration", "category": "5G Registration · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.active", "label": "PDU Sessions activas", "category": "5G Session Management", "unit": "sesiones", "kind": "gauge", "source": "SMF journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.attempts", "label": "Intentos de PDU Session", "category": "5G Session Management", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.successes", "label": "PDU Sessions exitosas", "category": "5G Session Management", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.rejects", "label": "PDU Sessions rechazadas", "category": "5G Session Management", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.unresolved", "label": "PDU Sessions sin resultado confirmado", "category": "5G Session Management", "unit": "eventos", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.success_rate", "label": "Tasa de éxito de PDU Session", "category": "5G Session Management", "unit": "%", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.latency_ms", "label": "Latencia de PDU Session", "category": "5G Session Management", "unit": "ms", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.reject_dnn", "label": "Rechazos por DNN/APN", "category": "5G PDU Session · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.reject_slice", "label": "Rechazos por S-NSSAI/slice", "category": "5G PDU Session · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "5g.pdu.reject_other", "label": "Otros rechazos de PDU Session", "category": "5G PDU Session · causas", "unit": "eventos", "kind": "counter", "source": "UERANSIM NAS", "objects": ["procedure"], "scenarios": ["5g-sa"]},
    {"id": "4g.ue.attached", "label": "UE attached actualmente", "category": "4G Mobility Management", "unit": "UE", "kind": "gauge", "source": "MME journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.attach.attempts", "label": "Intentos de Attach", "category": "4G Mobility Management", "unit": "eventos", "kind": "counter", "source": "srsUE NAS", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.attach.successes", "label": "Attach exitosos", "category": "4G Mobility Management", "unit": "eventos", "kind": "counter", "source": "srsUE NAS", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.attach.rejects", "label": "Attach rechazados", "category": "4G Mobility Management", "unit": "eventos", "kind": "counter", "source": "srsUE NAS", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.attach.unresolved", "label": "Attach sin resultado confirmado", "category": "4G Mobility Management", "unit": "eventos", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.attach.success_rate", "label": "Tasa de éxito de Attach", "category": "4G Mobility Management", "unit": "%", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.attach.latency_ms", "label": "Latencia de Attach", "category": "4G Mobility Management", "unit": "ms", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.active", "label": "Bearers EPS activos", "category": "4G Session Management", "unit": "bearers", "kind": "gauge", "source": "SMF journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.attempts", "label": "Intentos de EPS bearer", "category": "4G Session Management", "unit": "eventos", "kind": "counter", "source": "srsUE NAS", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.successes", "label": "EPS bearers exitosos", "category": "4G Session Management", "unit": "eventos", "kind": "counter", "source": "srsUE NAS", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.rejects", "label": "EPS bearers rechazados", "category": "4G Session Management", "unit": "eventos", "kind": "counter", "source": "srsUE NAS", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.unresolved", "label": "EPS bearers sin resultado confirmado", "category": "4G Session Management", "unit": "eventos", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.success_rate", "label": "Tasa de éxito de EPS bearer", "category": "4G Session Management", "unit": "%", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "4g.eps.latency_ms", "label": "Latencia de EPS bearer", "category": "4G Session Management", "unit": "ms", "kind": "gauge", "source": "computed from journal", "objects": ["procedure"], "scenarios": ["4g-epc"]},
    {"id": "alarms.active", "label": "Alarmas activas", "category": "Calidad y fallas", "unit": "alarmas", "kind": "gauge", "source": "alarm-engine", "objects": ["testbed"]},
    {"id": "alarms.critical", "label": "Alarmas críticas", "category": "Calidad y fallas", "unit": "alarmas", "kind": "gauge", "source": "alarm-engine", "objects": ["testbed"]},
]

OBJECT_ALIASES: dict[str, list[str]] = {
    "nf:upf": ["interface:ogstun"],
    "nf:upf2": ["interface:ogstun_corp", "interface:ogstun2"],
}

COUNTER_BY_ID = {item["id"]: item for item in COUNTERS}
for item in COUNTERS:
    if "procedure" in item["objects"]:
        prefix = item["id"]
        procedure = ("registration" if prefix.startswith("5g.registration") or prefix == "5g.ue.registered"
                     else "pdu-session" if prefix.startswith("5g.pdu")
                     else "attach" if prefix.startswith("4g.attach") or prefix == "4g.ue.attached"
                     else "eps-bearer")
        item["object_ids"] = [f"procedure:{procedure}"]
        if item["kind"] == "counter" or "latency" in prefix or "rate" in prefix or "unresolved" in prefix or "reject" in prefix:
            item["min_granularity_seconds"] = 1800
            item["supported_granularities"] = [1800]
        else:
            item["min_granularity_seconds"] = 300
            item["supported_granularities"] = [300, 1800]
    else:
        item["min_granularity_seconds"] = 300
        item["supported_granularities"] = [300, 1800]


def compatible(counter, object_id):
    if counter.get("object_ids"):
        if object_id in counter["object_ids"]:
            return True
        for alias in OBJECT_ALIASES.get(object_id, []):
            if alias in counter["object_ids"]:
                return True
        return False
    return object_id.split(":", 1)[0] in counter.get("objects", [])
RANGE_SECONDS = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
AGGREGATIONS = {"avg", "min", "max", "sum", "last"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_query(row) -> dict[str, Any]:
    item = dict(row)
    item["object_ids"] = json.loads(item["object_ids"])
    item["counter_ids"] = json.loads(item["counter_ids"])
    return item


class PerformanceRepository:
    def insert_samples(self, samples: list[dict[str, Any]]) -> int:
        if not samples:
            return 0
        with transaction() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO metric_samples(
                collected_at,bucket_epoch,testbed_id,scenario_id,object_id,counter_id,value,unit,source,quality
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        item["collected_at"], item["bucket_epoch"], item["testbed_id"],
                        item["scenario_id"], item["object_id"], item["counter_id"],
                        item["value"], item["unit"], item["source"], item.get("quality", "measured"),
                    )
                    for item in samples
                ],
            )
            return conn.total_changes - before

    def purge(self, cutoff_epoch: int) -> int:
        with transaction() as conn:
            cursor = conn.execute("DELETE FROM metric_samples WHERE bucket_epoch < ?", (cutoff_epoch,))
            return cursor.rowcount

    def raw_samples(self, *, testbed_id: str, scenario_id: str, object_ids: list[str], counter_ids: list[str], start: int, end: int) -> list[dict[str, Any]]:
        object_marks = ",".join("?" for _ in object_ids)
        counter_marks = ",".join("?" for _ in counter_ids)
        sql = f"""SELECT bucket_epoch,object_id,counter_id,value,unit,source,quality
        FROM metric_samples WHERE testbed_id=? AND scenario_id=?
        AND object_id IN ({object_marks}) AND counter_id IN ({counter_marks})
        AND bucket_epoch BETWEEN ? AND ? ORDER BY bucket_epoch ASC LIMIT 200001"""
        params = [testbed_id, scenario_id, *object_ids, *counter_ids, start, end]
        with transaction() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        if len(rows) > 200000:
            raise ValueError("La consulta supera 200 000 muestras. Reduzca el rango, los objetos o los contadores.")
        return rows

    def folders(self, user: UserPublic) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                """SELECT * FROM kpi_folders WHERE owner=? OR (scope='testbed' AND testbed_id=?)
                ORDER BY scope,name""", (user.username, user.testbed or "local")
            ).fetchall()
        return [dict(row) for row in rows]

    def create_folder(self, *, name: str, scope: str, user: UserPublic) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex, "name": name, "owner": user.username, "testbed_id": user.testbed or "local", "scope": scope, "created_at": _now().isoformat()}
        with transaction() as conn:
            conn.execute("INSERT INTO kpi_folders(id,name,owner,testbed_id,scope,created_at) VALUES(?,?,?,?,?,?)", tuple(item.values()))
        return item

    def queries(self, user: UserPublic) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                """SELECT * FROM kpi_queries WHERE owner=? OR (scope='testbed' AND testbed_id=?)
                ORDER BY updated_at DESC""", (user.username, user.testbed or "local")
            ).fetchall()
        return [_public_query(row) for row in rows]

    def save_query(self, payload: dict[str, Any], user: UserPublic) -> dict[str, Any]:
        now = _now().isoformat()
        query_id = uuid.uuid4().hex
        values = (
            query_id, payload["name"], payload.get("folder_id"), user.username,
            user.testbed or "local", payload["scenario_id"], payload["scope"],
            json.dumps(payload["object_ids"]), json.dumps(payload["counter_ids"]),
            payload["range_key"], payload["granularity_seconds"], payload["aggregation"], now, now,
        )
        with transaction() as conn:
            if payload.get("folder_id"):
                folder = conn.execute("SELECT owner,testbed_id,scope FROM kpi_folders WHERE id=?", (payload["folder_id"],)).fetchone()
                if not folder or folder["testbed_id"] != (user.testbed or "local") or (folder["owner"] != user.username and folder["scope"] != "testbed"):
                    raise PermissionError("Carpeta no accesible")
            conn.execute("""INSERT INTO kpi_queries(id,name,folder_id,owner,testbed_id,scenario_id,scope,
            object_ids,counter_ids,range_key,granularity_seconds,aggregation,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            row = conn.execute("SELECT * FROM kpi_queries WHERE id=?", (query_id,)).fetchone()
        return _public_query(row)

    def delete_query(self, query_id: str, user: UserPublic) -> None:
        with transaction() as conn:
            row = conn.execute("SELECT owner FROM kpi_queries WHERE id=?", (query_id,)).fetchone()
            if not row:
                raise KeyError(query_id)
            if row["owner"] != user.username and user.role not in {Role.admin, Role.teacher}:
                raise PermissionError("La consulta pertenece a otro usuario")
            conn.execute("DELETE FROM kpi_queries WHERE id=?", (query_id,))

    def begin_run(self, testbed_id: str, scenario_id: str) -> int:
        with transaction() as conn:
            cursor = conn.execute("INSERT INTO collector_runs(started_at,testbed_id,scenario_id,status) VALUES(?,?,?,'running')", (_now().isoformat(), testbed_id, scenario_id))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, count: int, error: str | None = None) -> None:
        with transaction() as conn:
            conn.execute("UPDATE collector_runs SET completed_at=?,status=?,sample_count=?,error=? WHERE id=?", (_now().isoformat(), status, count, error, run_id))

    def status(self) -> dict[str, Any]:
        with transaction() as conn:
            row = conn.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT 1").fetchone()
            count = conn.execute("SELECT COUNT(*) AS n FROM metric_samples").fetchone()["n"]
        return {"last_run": dict(row) if row else None, "stored_samples": count}


performance_repository = PerformanceRepository()


class MetricsCollector:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.collect_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.stop_event.clear()
        await self.collect_once()
        self.task = asyncio.create_task(self._loop(), name="ems-metrics-collector")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self) -> None:
        interval = max(get_settings().metrics_collection_interval_seconds, 5)
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(interval)
                await self.collect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1)

    async def collect_once(self) -> int:
        async with self.collect_lock:
            return await self._collect_once()

    async def _collect_once(self) -> int:
        total = 0
        testbed_id = "local"
        for scenario_id in CATALOG:
            try:
                status = await scenario_manager.status(scenario_id)
            except Exception:
                continue
            if status.state.value == "stopped":
                continue
            run_id = performance_repository.begin_run(testbed_id, scenario_id)
            try:
                metrics, alarms, telco = await asyncio.gather(
                    metrics_service.snapshot(scenario_id),
                    collect_alarms(scenario_id),
                    collect_telco_kpis(testbed_id, scenario_id),
                )
                samples = self._samples(testbed_id, scenario_id, status, metrics, alarms, telco)
                probe_error = None
                try:
                    probe = await nf_metrics.probe(scenario_manager.adapter, CATALOG[scenario_id]["components"])
                    samples.extend(nf_metrics.ingest(probe, testbed_id, scenario_id, _now()))
                except Exception as exc:
                    probe_error = "Sondeo NF no disponible: " + type(exc).__name__
                count = performance_repository.insert_samples(samples)
                performance_repository.finish_run(run_id, "partial" if probe_error else "success", count, probe_error)
                total += count
            except Exception as exc:
                performance_repository.finish_run(run_id, "failed", 0, str(exc)[:500])
        cutoff = int((_now() - timedelta(days=get_settings().metrics_retention_days)).timestamp())
        performance_repository.purge(cutoff)
        return total

    @staticmethod
    def _samples(testbed_id: str, scenario_id: str, status, metrics: dict[str, Any], alarms: list[dict[str, Any]], telco: dict[str, Any]) -> list[dict[str, Any]]:
        now = _now()
        epoch = int(now.timestamp())
        common = {"collected_at": now.isoformat(), "bucket_epoch": epoch, "testbed_id": testbed_id, "scenario_id": scenario_id}
        values: list[tuple[str, str, float, str, str, str]] = [
            (f"testbed:{testbed_id}", "host.cpu.percent", metrics["cpu_percent"], "%", "runtime", "measured"),
            (f"testbed:{testbed_id}", "host.memory.percent", metrics["memory_percent"], "%", "runtime", "measured"),
            (f"testbed:{testbed_id}", "core.nf.active", metrics.get("telco", {}).get("active_nfs", 0), "NFs", "systemd", "measured"),
            (f"testbed:{testbed_id}", "alarms.active", len(alarms), "alarmas", "alarm-engine", "computed"),
            (f"testbed:{testbed_id}", "alarms.critical", sum(a["severity"] == "critical" for a in alarms), "alarmas", "alarm-engine", "computed"),
        ]
        values.extend(MetricsCollector._telco_values(scenario_id, telco))
        for component in status.components:
            values.append((f"nf:{component.id}", "core.nf.availability", 100 if component.status == "running" else 0, "%", "systemd", "measured"))
        for name, data in metrics.get("interfaces", {}).items():
            values.extend([
                (f"interface:{name}", "interface.rx.kbps", data.get("rx_kbps", 0), "Kbps", "interface", "measured"),
                (f"interface:{name}", "interface.tx.kbps", data.get("tx_kbps", 0), "Kbps", "interface", "measured"),
                (f"interface:{name}", "interface.rx.bytes", data.get("rx_bytes", 0), "bytes", "interface", "measured"),
                (f"interface:{name}", "interface.tx.bytes", data.get("tx_bytes", 0), "bytes", "interface", "measured"),
            ])
        return [{**common, "object_id": obj, "counter_id": counter, "value": float(value), "unit": unit, "source": source, "quality": quality} for obj, counter, value, unit, source, quality in values]

    @staticmethod
    def _telco_values(scenario_id: str, telco: dict[str, Any]) -> list[tuple[str, str, float, str, str, str]]:
        summary = telco.get("summary", {})
        current = telco.get("current", {})
        values: list[tuple[str, str, float, str, str, str]] = []
        if scenario_id == "5g-sa":
            definitions = (("registration", "5g.registration"), ("pdu-session", "5g.pdu"))
            if "amf" in current:
                values.append(("procedure:registration", "5g.ue.registered", current["amf"], "UE", "AMF journal", "measured"))
            if "smf" in current:
                values.append(("procedure:pdu-session", "5g.pdu.active", current["smf"], "sesiones", "SMF journal", "measured"))
        else:
            definitions = (("attach", "4g.attach"), ("eps-bearer", "4g.eps"))
            if "mme" in current:
                values.append(("procedure:attach", "4g.ue.attached", current["mme"], "UE", "MME journal", "measured"))
            if "smf" in current:
                values.append(("procedure:eps-bearer", "4g.eps.active", current["smf"], "bearers", "SMF journal", "measured"))
        for procedure, prefix in definitions:
            metrics = summary.get(procedure, {})
            for suffix, key, unit, kind in (
                ("attempts", "attempt", "eventos", "measured"),
                ("successes", "success", "eventos", "measured"),
                ("rejects", "reject", "eventos", "measured"),
                ("unresolved", "unresolved", "eventos", "computed"),
                ("success_rate", "success_rate", "%", "computed"),
                ("latency_ms", "latency_ms", "ms", "computed"),
            ):
                if key not in metrics:
                    continue
                values.append((f"procedure:{procedure}", f"{prefix}.{suffix}", metrics[key], unit, "systemd-journal", kind))
            if scenario_id == "5g-sa":
                causes = ("authentication", "unknown-subscriber", "slice", "other") if procedure == "registration" else ("dnn", "slice", "other")
                for cause in causes:
                    values.append((f"procedure:{procedure}", f"{prefix}.reject_{cause}", metrics.get(f"reject_{cause}", 0), "eventos", "systemd-journal", "measured"))
        return values


metrics_collector = MetricsCollector()


class PerformanceService:
    async def catalog(self, scenario_id: str, user: UserPublic) -> dict[str, Any]:
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)
        status = await scenario_manager.status(scenario_id)
        runtime = await scenario_manager.adapter.runtime_snapshot()
        testbed_id = user.testbed or "local"
        objects = [{"id": f"testbed:{testbed_id}", "label": runtime["hostname"], "type": "testbed", "group": "Testbed"}]
        objects.extend({"id": f"nf:{item.id}", "label": item.label, "type": "nf", "group": "Funciones de red", "status": item.status} for item in status.components)
        objects.extend({"id": f"interface:{item['name']}", "label": item["name"], "type": "interface", "group": "Interfaces Linux", "status": item.get("state")} for item in runtime.get("interfaces", []))
        if scenario_id == "5g-sa":
            objects.extend([
                {"id": "procedure:registration", "label": "Registration", "type": "procedure", "group": "Procedimientos 5G"},
                {"id": "procedure:pdu-session", "label": "PDU Session", "type": "procedure", "group": "Procedimientos 5G"},
            ])
        else:
            objects.extend([
                {"id": "procedure:attach", "label": "Attach", "type": "procedure", "group": "Procedimientos 4G"},
                {"id": "procedure:eps-bearer", "label": "EPS Bearer", "type": "procedure", "group": "Procedimientos 4G"},
            ])
        counters = [item for item in COUNTERS if scenario_id in item.get("scenarios", [scenario_id])]
        counters = counters + nf_definitions(testbed_id, scenario_id)
        for obj in objects:
            if obj["type"] == "nf":
                obj["capabilities"] = nf_metrics.health.get((testbed_id, scenario_id, obj["id"][3:]), {"metrics_status": "pending"})
                obj["counter_count"] = sum(compatible(c, obj["id"]) for c in counters)
        return {"scenario_id": scenario_id, "testbed_id": testbed_id, "objects": objects, "counters": counters, "collector": {**performance_repository.status(), "interval_seconds": max(get_settings().metrics_collection_interval_seconds, 5), "retention_days": get_settings().metrics_retention_days}}

    def query(self, payload: dict[str, Any], user: UserPublic) -> dict[str, Any]:
        testbed_id = user.testbed or "local"
        object_ids = list(dict.fromkeys(payload["object_ids"]))
        counter_ids = list(dict.fromkeys(payload["counter_ids"]))
        scenario_id = payload["scenario_id"]
        if scenario_id not in CATALOG:
            raise ValueError("Escenario no válido")
        counter_by_id = {item["id"]: item for item in [*COUNTERS, *nf_definitions(testbed_id, scenario_id)] if scenario_id in item.get("scenarios", [scenario_id])}
        unknown = [item for item in counter_ids if item not in counter_by_id]
        if unknown:
            raise ValueError("Contadores desconocidos: " + ", ".join(unknown))
        unusable = [
            counter_id
            for counter_id in counter_ids
            if not any(
                compatible(counter_by_id[counter_id], object_id)
                for object_id in object_ids
            )
        ]
        if unusable:
            raise ValueError(
                "Los contadores no son compatibles con los objetos seleccionados: "
                + ", ".join(unusable)
            )
        end = int((payload.get("end") or _now()).timestamp())
        start = int((payload.get("start") or datetime.fromtimestamp(end - RANGE_SECONDS[payload["range_key"]], tz=timezone.utc)).timestamp())
        if start >= end or end - start > 7 * 86400:
            raise ValueError("Seleccione un rango válido de hasta 7 días")
        granularity = payload["granularity_seconds"]
        db_object_ids = list(object_ids)
        reverse_alias_map = {}
        for obj_id in object_ids:
            for alias in OBJECT_ALIASES.get(obj_id, []):
                if alias not in db_object_ids:
                    db_object_ids.append(alias)
                reverse_alias_map[alias] = obj_id

        rows = performance_repository.raw_samples(testbed_id=testbed_id, scenario_id=payload["scenario_id"], object_ids=db_object_ids, counter_ids=counter_ids, start=start, end=end)
        grouped: dict[tuple[str, str, int], list[float]] = defaultdict(list)
        metadata: dict[tuple[str, str], tuple[str, str, str]] = {}
        for row in rows:
            obj_id = row["object_id"]
            target_obj = reverse_alias_map.get(obj_id, obj_id)
            if target_obj not in object_ids:
                continue
            if not compatible(counter_by_id[row["counter_id"]], obj_id) and not compatible(counter_by_id[row["counter_id"]], target_obj):
                continue
            if row["counter_id"].endswith(".latency_ms") and not 0 <= row["value"] <= 60_000:
                continue
            bucket = row["bucket_epoch"] // granularity * granularity
            grouped[(target_obj, row["counter_id"], bucket)].append(row["value"])
            metadata[(target_obj, row["counter_id"])] = (row["unit"], row["source"], row["quality"])
        aggregation = payload["aggregation"]
        def aggregate(values: list[float], counter_id: str) -> float:
            # Summing/averaging snapshots of a cumulative counter invents events.
            if counter_by_id[counter_id]["kind"] == "counter": return values[-1]
            if aggregation == "last": return values[-1]
            if aggregation == "min": return min(values)
            if aggregation == "max": return max(values)
            if aggregation == "sum": return sum(values)
            return sum(values) / len(values)
        series = []
        for object_id in object_ids:
            for counter_id in counter_ids:
                points = [{"timestamp": datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat(), "epoch": bucket, "value": round(aggregate(values, counter_id), 4)} for (obj, counter, bucket), values in grouped.items() if obj == object_id and counter == counter_id]
                if not points:
                    continue
                unit, source, quality = metadata[(object_id, counter_id)]
                series.append({"id": f"{object_id}|{counter_id}", "object_id": object_id, "counter_id": counter_id, "label": f"{object_id.split(':', 1)[-1]} · {counter_by_id[counter_id]['label']}", "unit": unit, "source": source, "quality": quality, "kind": counter_by_id[counter_id]["kind"], "aggregation": "last" if counter_by_id[counter_id]["kind"] == "counter" else aggregation, "points": sorted(points, key=lambda p: p["epoch"])})
        missing = [{"object_id": obj, "counter_id": counter} for obj in object_ids for counter in counter_ids if compatible(counter_by_id[counter], obj) and (obj, counter) not in metadata]
        return {"testbed_id": testbed_id, "scenario_id": payload["scenario_id"], "start": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(), "end": datetime.fromtimestamp(end, tz=timezone.utc).isoformat(), "granularity_seconds": granularity, "aggregation": aggregation, "sample_count": len(rows), "series": series, "missing_series": missing, "notes": ["Los contadores acumulados conservan la última muestra por intervalo; no se suman sus instantáneas.", "Las métricas nativas de una NF compartida entre 4G y 5G miden el proceso completo."]}


performance_service = PerformanceService()
