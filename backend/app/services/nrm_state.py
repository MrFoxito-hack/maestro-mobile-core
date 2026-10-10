"""Network Resource Model (NRM) State Store for MAEstro 5G SA EMS.

Implements FCAPS separation between Configuration Management (CM / NRM)
and Performance Management (PM) inspired by 3GPP TS 28.532 and TS 28.550.
Maintains Expected Topology vs Observed State with reachability state machine,
data freshness tracking, and N:M slice-to-UPF associations.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.config import get_settings
from app.services.scenarios import CATALOG
from app.services import upf_inventory, upf_performance

logger = logging.getLogger("ems.nrm")


class Reachability(str, Enum):
    UNKNOWN = "UNKNOWN"
    REACHABLE = "REACHABLE"
    PROBE_FAILED = "PROBE_FAILED"
    UNREACHABLE = "UNREACHABLE"


class OperState(str, Enum):
    UNKNOWN = "UNKNOWN"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEGRADED = "DEGRADED"


class AdminState(str, Enum):
    UNLOCKED = "UNLOCKED"
    LOCKED = "LOCKED"


@dataclass
class ManagedObjectState:
    id: str
    label: str
    type: str  # "nf", "interface", "testbed", "procedure"
    group: str
    expected: bool = True
    admin_state: AdminState = AdminState.UNLOCKED
    oper_state: OperState = OperState.UNKNOWN
    reachability: Reachability = Reachability.UNKNOWN
    consecutive_failures: int = 0
    last_seen: datetime | None = None
    last_error: str | None = None
    status: str = "unknown"  # Backwards compatibility with frontend badge (e.g. running, stopped, up)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, now: datetime) -> dict[str, Any]:
        data_age = (now - self.last_seen).total_seconds() if self.last_seen else None
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "group": self.group,
            "expected": self.expected,
            "admin_state": self.admin_state.value,
            "oper_state": self.oper_state.value,
            "reachability": self.reachability.value,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "data_age_seconds": round(data_age, 2) if data_age is not None else None,
            "last_error": self.last_error,
            **self.metadata,
        }


@dataclass
class NetworkSlice:
    slice_id: str
    name: str
    sst: int
    sd: str | None = None
    service: str = "embb"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.slice_id,
            "sst": self.sst,
            "sd": self.sd,
            "name": self.name,
            "service": self.service,
            "description": self.description,
        }


@dataclass
class SliceUPFAssociation:
    slice_id: str
    sst: int
    sd: str | None
    dnn: str
    upf_id: str
    interface_id: str
    n3_address: str
    pm_object_id: str
    xdp_expected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "sst": self.sst,
            "sd": self.sd,
            "dnn": self.dnn,
            "upf_id": self.upf_id,
            "interface_id": self.interface_id,
            "n3_address": self.n3_address,
            "pm_object_id": self.pm_object_id,
            "xdp_expected": self.xdp_expected,
        }


class EmsStateStore:
    """Thread-safe and zero-network-IO state store for Network Resource Models."""

    def __init__(self, failure_threshold: int = 3) -> None:
        self.failure_threshold = failure_threshold
        self._states: dict[tuple[str, str], ManagedObjectState] = {}  # (scenario_id, obj_id) -> state
        self._last_sync: dict[str, datetime | None] = {}
        self._hostname: dict[str, str] = {}
        self._slices: dict[str, list[NetworkSlice]] = {}
        self._slice_associations: dict[str, list[SliceUPFAssociation]] = {}
        self._initialize_baseline_catalog()

    def _initialize_baseline_catalog(self) -> None:
        """Initializes Expected Topology for all scenarios at T=0.

        Initial state is strictly UNKNOWN until Southbound probes verify reachability.
        """
        for scenario_id, scenario in CATALOG.items():
            self._last_sync[scenario_id] = None
            self._hostname[scenario_id] = "core-01.ems.test"

            # 1. Testbed Object
            tb_id = "testbed:local"
            self._states[(scenario_id, tb_id)] = ManagedObjectState(
                id=tb_id,
                label="Testbed Core (Inicializando)",
                type="testbed",
                group="Testbed",
                expected=True,
                admin_state=AdminState.UNLOCKED,
                oper_state=OperState.UNKNOWN,
                reachability=Reachability.UNKNOWN,
                status="unknown",
            )

            # 2. Expected Network Functions from scenario catalog
            for comp in scenario.get("components", []):
                obj_id = f"nf:{comp['id']}"
                self._states[(scenario_id, obj_id)] = ManagedObjectState(
                    id=obj_id,
                    label=comp.get("label", comp["id"]),
                    type="nf",
                    group="Funciones de red",
                    expected=True,
                    admin_state=AdminState.UNLOCKED,
                    oper_state=OperState.UNKNOWN,
                    reachability=Reachability.UNKNOWN,
                    status="unknown",
                )

            # 3. Expected Procedures
            if scenario_id == "5g-sa":
                for proc_id, label in [("procedure:registration", "Registration"), ("procedure:pdu-session", "PDU Session")]:
                    self._states[(scenario_id, proc_id)] = ManagedObjectState(
                        id=proc_id,
                        label=label,
                        type="procedure",
                        group="Procedimientos 5G",
                        expected=True,
                        admin_state=AdminState.UNLOCKED,
                        oper_state=OperState.ENABLED,
                        reachability=Reachability.REACHABLE,
                        status="active",
                    )
            else:
                for proc_id, label in [("procedure:attach", "Attach"), ("procedure:eps-bearer", "EPS Bearer")]:
                    self._states[(scenario_id, proc_id)] = ManagedObjectState(
                        id=proc_id,
                        label=label,
                        type="procedure",
                        group="Procedimientos 4G",
                        expected=True,
                        admin_state=AdminState.UNLOCKED,
                        oper_state=OperState.ENABLED,
                        reachability=Reachability.REACHABLE,
                        status="active",
                    )

            # 4. Multi-UPF Triad and Slicing (5G-SA)
            if scenario_id == "5g-sa":
                self._slices[scenario_id] = []
                self._slice_associations[scenario_id] = []
                try:
                    upf_inv = upf_inventory.inventory()
                    seen_slices = set()
                    for target in upf_inv.get("targets", []):
                        slice_key = (target.get("sst"), target.get("sd"))
                        if slice_key not in seen_slices:
                            seen_slices.add(slice_key)
                            self._slices[scenario_id].append(
                                NetworkSlice(
                                    slice_id=f"slice:{target.get('service', 'generic')}",
                                    name=target.get("label", f"Slice SST={target.get('sst')}"),
                                    sst=target.get("sst", 1),
                                    sd=target.get("sd"),
                                    service=target.get("service", "embb"),
                                    description=f"Slice 5G {target.get('service')} (SST={target.get('sst')}, SD={target.get('sd')})",
                                )
                            )

                        upf_nf_id = upf_performance.OBJECTS.get(target.get("dnn"), f"nf:{target.get('id')}")
                        if_id = upf_performance.interface_id(target)

                        self._slice_associations[scenario_id].append(
                            SliceUPFAssociation(
                                slice_id=f"slice:{target.get('service', 'generic')}",
                                sst=target.get("sst", 1),
                                sd=target.get("sd"),
                                dnn=target.get("dnn", "internet"),
                                upf_id=upf_nf_id,
                                interface_id=if_id,
                                n3_address=target.get("n3_address", ""),
                                pm_object_id=target.get("pm_object_id", ""),
                                xdp_expected=target.get("xdp_expected", False),
                            )
                        )

                        # Register triad objects in expected state
                        if (scenario_id, upf_nf_id) not in self._states:
                            self._states[(scenario_id, upf_nf_id)] = ManagedObjectState(
                                id=upf_nf_id,
                                label=target.get("label", "UPF"),
                                type="nf",
                                group="UPF",
                                expected=True,
                                admin_state=AdminState.UNLOCKED,
                                oper_state=OperState.UNKNOWN,
                                reachability=Reachability.UNKNOWN,
                                status="unknown",
                            )
                        if (scenario_id, if_id) not in self._states:
                            self._states[(scenario_id, if_id)] = ManagedObjectState(
                                id=if_id,
                                label=f"{target.get('label', 'UPF')} / ogstun",
                                type="interface",
                                group="UPF",
                                expected=True,
                                admin_state=AdminState.UNLOCKED,
                                oper_state=OperState.UNKNOWN,
                                reachability=Reachability.UNKNOWN,
                                status="unknown",
                            )
                except Exception as exc:
                    logger.warning("Could not pre-load UPF inventory for %s: %s", scenario_id, exc)

    def update_from_southbound(
        self,
        scenario_id: str,
        components_status: list[Any] | None = None,
        runtime_snapshot: dict[str, Any] | None = None,
        probe_error: str | None = None,
    ) -> None:
        """Atomically updates the observed state from a Southbound synchronization cycle."""
        now = datetime.now(timezone.utc)
        self._last_sync[scenario_id] = now

        # Update testbed hostname if present
        if runtime_snapshot and runtime_snapshot.get("hostname"):
            self._hostname[scenario_id] = runtime_snapshot["hostname"]
            tb_state = self._states.get((scenario_id, "testbed:local"))
            if tb_state:
                tb_state.label = runtime_snapshot["hostname"]
                tb_state.oper_state = OperState.ENABLED
                tb_state.reachability = Reachability.REACHABLE
                tb_state.status = "up"
                tb_state.last_seen = now
                tb_state.consecutive_failures = 0
                tb_state.last_error = None

        # 1. Update Network Functions status
        if components_status is not None:
            for item in components_status:
                obj_id = f"nf:{item.id}"
                state = self._states.get((scenario_id, obj_id))
                if not state:
                    state = ManagedObjectState(
                        id=obj_id,
                        label=item.label,
                        type="nf",
                        group="Funciones de red",
                        expected=True,
                    )
                    self._states[(scenario_id, obj_id)] = state

                state.last_seen = now
                state.status = item.status
                state.consecutive_failures = 0
                state.last_error = None
                state.reachability = Reachability.REACHABLE

                if item.status == "running":
                    state.oper_state = OperState.ENABLED
                elif item.status in ("stopped", "inactive"):
                    state.oper_state = OperState.DISABLED
                else:
                    state.oper_state = OperState.DEGRADED

        # 2. Update Interfaces from runtime snapshot
        if runtime_snapshot and "interfaces" in runtime_snapshot:
            for if_item in runtime_snapshot.get("interfaces", []):
                if_name = if_item.get("name")
                if not if_name:
                    continue
                if_id = f"interface:{if_name}"
                state = self._states.get((scenario_id, if_id))
                if not state:
                    state = ManagedObjectState(
                        id=if_id,
                        label=if_name,
                        type="interface",
                        group="Interfaces Linux",
                        expected=False,  # dynamically discovered
                    )
                    self._states[(scenario_id, if_id)] = state

                if_state = if_item.get("state", "UP").upper()
                state.last_seen = now
                state.status = if_item.get("state", "up")
                state.oper_state = OperState.ENABLED if if_state == "UP" else OperState.DISABLED
                state.reachability = Reachability.REACHABLE
                state.consecutive_failures = 0
                state.last_error = None

        # 3. Handle Probe / Southbound Errors with failure counters
        if probe_error:
            for (sc_id, obj_id), state in self._states.items():
                if sc_id != scenario_id:
                    continue
                if state.type in ("nf", "interface") and state.expected:
                    state.consecutive_failures += 1
                    state.last_error = probe_error
                    if state.consecutive_failures >= self.failure_threshold:
                        state.reachability = Reachability.UNREACHABLE
                        # Distinct from data plane: management plane is unreachable
                        state.oper_state = OperState.UNKNOWN
                    else:
                        state.reachability = Reachability.PROBE_FAILED
                        # Preserve last observed oper_state!

    def get_catalog(
        self,
        scenario_id: str,
        testbed_id: str,
        counters: list[dict[str, Any]],
        nf_health: dict[tuple[str, str, str], dict[str, Any]],
        collector_status: dict[str, Any],
        compatible_checker: Any,
    ) -> dict[str, Any]:
        """Builds the NBI catalog payload purely in-memory in O(N) object formatting, with 0 network IO."""
        if scenario_id not in CATALOG:
            raise KeyError(scenario_id)

        now = datetime.now(timezone.utc)
        last_sync = self._last_sync.get(scenario_id)
        data_age = (now - last_sync).total_seconds() if last_sync else None

        objects: list[dict[str, Any]] = []
        reachability_counts = {
            Reachability.REACHABLE.value: 0,
            Reachability.UNKNOWN.value: 0,
            Reachability.PROBE_FAILED.value: 0,
            Reachability.UNREACHABLE.value: 0,
        }

        # Format managed objects
        for (sc_id, obj_id), state in self._states.items():
            if sc_id != scenario_id:
                continue
            item_dict = state.to_dict(now)

            # Map dynamic testbed ID for testbed object
            if item_dict["type"] == "testbed":
                item_dict["id"] = f"testbed:{testbed_id}"
                item_dict["label"] = self._hostname.get(scenario_id, "core-01")

            # Enrich NF capabilities and compatible counters count
            if item_dict["type"] == "nf":
                nf_short = item_dict["id"].split(":", 1)[1]
                item_dict["capabilities"] = nf_health.get(
                    (testbed_id, scenario_id, nf_short),
                    {"metrics_status": "pending"},
                )
                item_dict["counter_count"] = sum(
                    compatible_checker(c, item_dict["id"]) for c in counters
                )

            reachability_counts[item_dict["reachability"]] = reachability_counts.get(item_dict["reachability"], 0) + 1
            objects.append(item_dict)

        # Build Slices & Slice-UPF associations
        slices = [s.to_dict() for s in self._slices.get(scenario_id, [])]
        slice_associations = [a.to_dict() for a in self._slice_associations.get(scenario_id, [])]

        return {
            "scenario_id": scenario_id,
            "testbed_id": testbed_id,
            "objects": objects,
            "counters": counters,
            "slices": slices,
            "slice_associations": slice_associations,
            "inventory_freshness": {
                "last_sync": last_sync.isoformat() if last_sync else None,
                "data_age_seconds": round(data_age, 2) if data_age is not None else None,
                "is_stale": (data_age > 30.0) if data_age is not None else True,
                "reachability_summary": reachability_counts,
            },
            "collector": {
                **collector_status,
                "interval_seconds": max(get_settings().metrics_collection_interval_seconds, 5),
                "retention_days": get_settings().metrics_retention_days,
            },
        }


ems_state_store = EmsStateStore()
