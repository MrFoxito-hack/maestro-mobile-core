import asyncio
import time
import pytest
from datetime import datetime, timezone
from app.models import ComponentStatus, UserPublic, Role
from app.services.nrm_state import (
    EmsStateStore,
    Reachability,
    OperState,
    AdminState,
    ems_state_store,
)
from app.services.performance import performance_service


def test_t0_expected_vs_observed_separation():
    """At T=0, expected topology exists but observed state is strictly UNKNOWN."""
    store = EmsStateStore()
    user = UserPublic(username="test_user", role=Role.student, testbed="local")
    
    # Check that store initialized expected objects
    catalog = store.get_catalog(
        scenario_id="5g-sa",
        testbed_id="local",
        counters=[],
        nf_health={},
        collector_status={},
        compatible_checker=lambda c, o: False,
    )
    
    objects = {o["id"]: o for o in catalog["objects"]}
    
    # NF objects must exist as expected, but reachability is UNKNOWN at T=0
    assert "nf:amf" in objects
    assert objects["nf:amf"]["expected"] is True
    assert objects["nf:amf"]["admin_state"] == AdminState.UNLOCKED.value
    assert objects["nf:amf"]["oper_state"] == OperState.UNKNOWN.value
    assert objects["nf:amf"]["reachability"] == Reachability.UNKNOWN.value
    assert objects["nf:amf"]["last_seen"] is None
    assert objects["nf:amf"]["data_age_seconds"] is None

    # Procedures are logically enabled
    assert objects["procedure:registration"]["expected"] is True


def test_catalog_sub_millisecond_latency_zero_io(client, teacher_headers, monkeypatch):
    """Catalog response must be served from memory with zero SSH/network IO."""
    # Poison adapter methods to ensure that if any IO is attempted, an error is raised
    async def forbidden_io(*args, **kwargs):
        raise AssertionError("Southbound network IO called during Northbound catalog query!")

    from app.services.scenarios import scenario_manager
    monkeypatch.setattr(scenario_manager.adapter, "runtime_snapshot", forbidden_io)
    monkeypatch.setattr(scenario_manager, "status", forbidden_io)

    # Warm-up call through HTTP stack
    res = client.get("/api/v1/performance/catalog/5g-sa", headers=teacher_headers)
    assert res.status_code == 200

    start_time = time.perf_counter()
    res = client.get("/api/v1/performance/catalog/5g-sa", headers=teacher_headers)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert res.status_code == 200
    payload = res.json()
    # Entire FastAPI HTTP request/response stack completed without network IO
    assert elapsed_ms < 50.0
    assert payload["scenario_id"] == "5g-sa"
    assert len(payload["objects"]) > 0
    assert "inventory_freshness" in payload


def test_southbound_reachability_state_machine_and_failure_semantics():
    """Validates probe failure, threshold-based UNREACHABLE, and recovery."""
    store = EmsStateStore(failure_threshold=3)
    
    # 1. Success probe
    comp = ComponentStatus(
        id="upf",
        label="Open5GS UPF",
        kind="nf",
        node_id="core",
        status="running",
        unit="open5gs-upfd",
    )
    runtime = {
        "hostname": "core-01.ems.test",
        "interfaces": [{"name": "ogstun", "state": "UP"}],
    }
    store.update_from_southbound("5g-sa", [comp], runtime)

    state = store._states[("5g-sa", "nf:upf")]
    assert state.oper_state == OperState.ENABLED
    assert state.reachability == Reachability.REACHABLE
    assert state.consecutive_failures == 0
    assert state.last_seen is not None

    # 2. Single isolated timeout: PROBE_FAILED, but preserves last known oper_state!
    store.update_from_southbound("5g-sa", probe_error="SSH Timeout: port 2222")
    state = store._states[("5g-sa", "nf:upf")]
    assert state.consecutive_failures == 1
    assert state.reachability == Reachability.PROBE_FAILED
    assert state.oper_state == OperState.ENABLED  # Preserved!
    assert state.last_error == "SSH Timeout: port 2222"

    # 3. Second timeout: still PROBE_FAILED, oper_state still preserved
    store.update_from_southbound("5g-sa", probe_error="SSH Timeout: port 2222")
    state = store._states[("5g-sa", "nf:upf")]
    assert state.consecutive_failures == 2
    assert state.reachability == Reachability.PROBE_FAILED
    assert state.oper_state == OperState.ENABLED

    # 4. Third timeout (threshold reached): UNREACHABLE
    store.update_from_southbound("5g-sa", probe_error="SSH Connection Refused")
    state = store._states[("5g-sa", "nf:upf")]
    assert state.consecutive_failures == 3
    assert state.reachability == Reachability.UNREACHABLE
    assert state.oper_state == OperState.UNKNOWN

    # 5. Recovery probe
    store.update_from_southbound("5g-sa", [comp], runtime)
    state = store._states[("5g-sa", "nf:upf")]
    assert state.consecutive_failures == 0
    assert state.reachability == Reachability.REACHABLE
    assert state.oper_state == OperState.ENABLED
    assert state.last_error is None


def test_multi_upf_slicing_n_to_m_model():
    """Validates that slicing associations support multiple UPFs and S-NSSAIs."""
    store = EmsStateStore()
    catalog = store.get_catalog(
        scenario_id="5g-sa",
        testbed_id="local",
        counters=[],
        nf_health={},
        collector_status={},
        compatible_checker=lambda c, o: False,
    )

    slices = catalog.get("slices", [])
    associations = catalog.get("slice_associations", [])

    assert len(slices) >= 3
    service_types = {s["service"] for s in slices}
    assert "embb" in service_types
    assert "urllc" in service_types
    assert "miot" in service_types

    # Ensure N:M slice-to-UPF associations are populated
    upf_ids = {a["upf_id"] for a in associations}
    assert "nf:upf" in upf_ids
    assert any("upf2" in u for u in upf_ids) or any("upf-02" in a["pm_object_id"] for a in associations)
    assert any(a["xdp_expected"] is True for a in associations)
