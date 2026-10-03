from app.services.trace_analysis import (
    TSHARK_FIELDS,
    _deduplicate_loopback_sbi,
    _parse_sbi_call,
    build_trace_analysis,
    subscriber_hash,
)
from app.services.telco_kpis import parse_telco_logs


def test_sbi_mapping_never_identifies_loopback_client_as_gnodeb():
    event = _parse_sbi_call(
        {
            "http2.headers.path": "/nnrf-nfm/v1/nf-instances/example",
            "http2.headers.method": "PATCH",
        },
        "127.0.0.1",
        "127.0.0.10",
    )

    assert event is not None
    assert event[0] == "5GC NF"
    assert event[1] == "NRF"


def test_nsmf_pdu_session_is_presented_as_n11_between_amf_and_smf():
    event = _parse_sbi_call(
        {
            "http2.headers.path": "/nsmf-pdusession/v1/sm-contexts",
            "http2.headers.method": "POST",
        },
        "127.0.0.5",
        "127.0.0.4",
    )

    assert event == (
        "AMF",
        "SMF",
        "Nsmf_PDUSession Create Context",
        "N11 / SBI",
        "pdu-session",
    )


def test_loopback_sbi_duplicates_are_collapsed_without_losing_identifiers():
    base = {
        "source_nf": "AMF",
        "target_nf": "SMF",
        "protocol": "HTTP/2",
        "message": "Nsmf_PDUSession Create Context",
        "procedure": "pdu-session",
        "identifiers": [{"kind": "supi", "value": "999700000000001"}],
    }
    events = [
        {**base, "id": "first", "_epoch": 10.0},
        {
            **base,
            "id": "duplicate",
            "_epoch": 10.001,
            "identifiers": [{"kind": "pdu_session_id", "value": "1"}],
        },
        {**base, "id": "later", "_epoch": 11.0},
    ]

    result = _deduplicate_loopback_sbi(events)

    assert [item["id"] for item in result] == ["first", "later"]
    assert {item["kind"] for item in result[0]["identifiers"]} == {
        "supi",
        "pdu_session_id",
    }


def parameters(five_g=False):
    value = {"mcc": "716", "mnc": "10", "tac": 1, "apn_dnn": "internet"}
    if five_g:
        value.update({"sst": 1, "sd": "000001"})
    return value


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["execution_mode"] == "simulated"


def test_runtime_uses_explicit_mock(client, teacher_headers):
    response = client.get("/api/v1/runtime/5g-sa", headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()["source"] == "mock"
    assert response.json()["hostname"] == "ems-testbed-mock"


def test_component_health_alarm_has_telco_context(client, teacher_headers):
    stopped = client.post(
        "/api/v1/scenarios/5g-sa/components/amf/stop",
        headers=teacher_headers,
    )
    assert stopped.status_code == 200
    alarms = client.get("/api/v1/alarms/5g-sa", headers=teacher_headers).json()
    alarm = next(item for item in alarms if item["component"] == "amf")
    assert "N2" in alarm["interfaces"]
    assert "Registration" in alarm["procedures"]
    assert "open5gs-amfd" in alarm["evidence"]
    started = client.post(
        "/api/v1/scenarios/5g-sa/components/amf/start",
        headers=teacher_headers,
    )
    assert started.status_code == 200


def test_student_can_read_but_cannot_operate(client, student_headers):
    assert client.get("/api/v1/scenarios", headers=student_headers).status_code == 200
    response = client.post(
        "/api/v1/scenarios/4g-epc/start",
        headers=student_headers,
        json={"parameters": parameters(), "testbed": "local"},
    )
    assert response.status_code == 403


def test_scenario_cycle_and_audit(client, teacher_headers):
    start = client.post(
        "/api/v1/scenarios/5g-sa/start",
        headers=teacher_headers,
        json={"parameters": parameters(True), "testbed": "local"},
    )
    assert start.status_code == 200
    assert start.json()["state"] == "running"
    assert all(item["status"] == "running" for item in start.json()["components"])
    stop = client.post("/api/v1/scenarios/5g-sa/stop", headers=teacher_headers)
    assert stop.status_code == 200
    assert stop.json()["state"] == "stopped"
    audit = client.get("/api/v1/audit", headers=teacher_headers).json()
    assert any(item["action"] == "scenario.start" for item in audit)


def test_config_backup_and_restore(client, teacher_headers):
    first = "amf:\n  name: first\n"
    second = "amf:\n  name: second\n"
    assert client.put("/api/v1/config", headers=teacher_headers, json={"path": "amf.yaml", "content": first}).status_code == 200
    result = client.put("/api/v1/config", headers=teacher_headers, json={"path": "amf.yaml", "content": second})
    assert result.status_code == 200
    assert result.json()["backup"]
    restored = client.post("/api/v1/config/restore?path=amf.yaml", headers=teacher_headers)
    assert restored.status_code == 200
    current = client.get("/api/v1/config?path=amf.yaml", headers=teacher_headers)
    assert "first" in current.json()["content"]


def test_subscriber_secrets_are_masked_and_not_audited(client, teacher_headers):
    payload = {
        "imsi": "716100000000001", "key": "00112233445566778899AABBCCDDEEFF",
        "opc": "FFEEDDCCBBAA99887766554433221100", "amf": "8000",
        "apn_dnn": "internet", "sst": 1, "sd": "000001",
    }
    response = client.post("/api/v1/subscribers", headers=teacher_headers, json=payload)
    assert response.status_code in (201, 409)
    listing = client.get("/api/v1/subscribers", headers=teacher_headers).json()
    serialized = str(listing)
    assert payload["key"] not in serialized
    assert payload["opc"] not in serialized
    audit = client.get("/api/v1/audit", headers=teacher_headers).text
    assert payload["key"] not in audit


def test_simulated_pcap(client, teacher_headers):
    response = client.post("/api/v1/traces", headers=teacher_headers, json={
        "interface": "lo", "protocol": "ngap", "duration_seconds": 5, "max_megabytes": 1,
    })
    assert response.status_code == 201
    trace_id = response.json()["id"]
    download = client.get(f"/api/v1/traces/{trace_id}/download", headers=teacher_headers)
    assert download.status_code == 200
    assert download.content[:4] == bytes.fromhex("d4c3b2a1")


def test_trace_capabilities_are_declarative(client, student_headers):
    response = client.get("/api/v1/traces/capabilities/5g-sa", headers=student_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["capture_agents"][0]["id"] == "primary"
    assert {item["id"] for item in data["capture_targets"]} >= {"n2", "n3", "n4", "n6", "sbi"}
    assert any(item["id"] == "amf" for item in data["network_functions"])
    assert data["subscriber"]["supported"] is True
    assert data["subscriber"]["enabled"] is True
    assert data["subscriber"]["supports_auto_trigger"] is False
    assert data["quota"]["max_active"] == 1
    assert data["limits"]["max_megabytes"] == 100
    serialized = response.text
    assert "sctp port" not in serialized


def test_subscriber_trace_correlates_and_masks_identity(client, teacher_headers, student_headers):
    identifier = "999700000000001"
    response = client.post(
        "/api/v1/traces/subscriber",
        headers=teacher_headers,
        json={
            "name": "Registro y sesión UE laboratorio",
            "scenario_id": "5g-sa",
            "testbed_id": "local",
            "capture_agent_id": "primary",
            "identifier_type": "imsi",
            "identifier": identifier,
            "procedures": ["registration", "authentication", "pdu-session"],
            "include_user_plane": True,
            "include_sbi": False,
            "auto_trigger": False,
            "duration_seconds": 5,
            "max_megabytes": 1,
        },
    )
    assert response.status_code == 201
    task = response.json()
    assert task["trace_type"] == "subscriber"
    assert task["status"] == "completed"
    assert identifier not in response.text
    assert task["target"]["masked"].startswith("99970")

    analysis = client.get(f"/api/v1/traces/{task['id']}/analysis", headers=teacher_headers)
    assert analysis.status_code == 200
    data = analysis.json()
    # This fixture has no observed NAS accept or AKA confirmation. Previously
    # unrelated runtime logs incorrectly made the whole procedure successful.
    assert data["outcome"] == "partial"
    assert data["target"]["matched"] is True
    kinds = {item["kind"] for item in data["identifiers"]}
    assert kinds >= {"supi", "pfcp_seid", "gtpu_teid", "ue_ip"}
    assert data["standards_review"]["conformance"] == "not-certified"
    assert all(event["evidence_type"] == "simulated" for event in data["events"])
    assert identifier not in analysis.text

    forbidden = client.get(f"/api/v1/traces/{task['id']}", headers=student_headers)
    assert forbidden.status_code == 403
    audit = client.get("/api/v1/audit", headers=teacher_headers)
    assert identifier not in audit.text


def test_subscriber_analysis_excludes_identifiers_from_previous_context():
    def row(values):
        return "\t".join(f'"{values.get(field, "")}"' for field in TSHARK_FIELDS)

    identifier = "999700000000001"
    capture = "\n".join(
        [
            row({"frame.number": "1", "frame.time_epoch": "1.0", "_ws.col.Protocol": "NGAP", "_ws.col.Info": "UEContextReleaseComplete", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.5", "ngap.RAN_UE_NGAP_ID": "2", "ngap.AMF_UE_NGAP_ID": "2"}),
            row({"frame.number": "2", "frame.time_epoch": "1.1", "_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Deletion Request", "ip.src": "127.0.0.4", "ip.dst": "127.0.0.7", "pfcp.seid": "0xOLD"}),
            row({"frame.number": "3", "frame.time_epoch": "2.0", "_ws.col.Protocol": "NGAP/NAS-5GS", "_ws.col.Info": "InitialUEMessage, Registration request", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.5", "ngap.RAN_UE_NGAP_ID": "3", "nas_5gs.mm.suci.msin": "0000000001"}),
            row({"frame.number": "4", "frame.time_epoch": "2.1", "_ws.col.Protocol": "NGAP", "_ws.col.Info": "DownlinkNASTransport", "ip.src": "127.0.0.5", "ip.dst": "127.0.0.1", "ngap.RAN_UE_NGAP_ID": "3", "ngap.AMF_UE_NGAP_ID": "3"}),
            row({"frame.number": "5", "frame.time_epoch": "2.2", "_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Establishment Request", "ip.src": "127.0.0.4", "ip.dst": "127.0.0.7", "pfcp.seid": "0xNEW", "e212.imsi": identifier}),
        ]
    )
    analysis = build_trace_analysis(
        {
            "id": "scope-test",
            "selector_kind": "imsi",
            "selector_hash": subscriber_hash(identifier),
            "selector_masked": "99970\u2022\u2022\u2022001",
            "scenario_defaults": {"mcc": "999", "mnc": "70"},
            "procedures": ["registration"],
        },
        capture,
    )
    values = {item["value"] for item in analysis["identifiers"]}
    assert "2" not in values
    assert "0xOLD" not in values
    assert {"3", "0xNEW"} <= values
    assert analysis["events"][0]["relative_ms"] == 0
    assert all("_epoch" not in event and "_target_match" not in event for event in analysis["events"])
    assert analysis["relations"]


def test_successful_procedure_is_independent_from_full_identifier_chain():
    def row(values):
        return "\t".join(f'"{values.get(field, "")}"' for field in TSHARK_FIELDS)

    identifier = "999700000000001"
    capture = "\n".join(
        [
            row(
                {
                    "frame.number": "1",
                    "frame.time_epoch": "1.0",
                    "_ws.col.Protocol": "NGAP/NAS-5GS",
                    "_ws.col.Info": "InitialUEMessage, Registration request",
                    "ip.src": "127.0.0.1",
                    "ip.dst": "127.0.0.5",
                    "nas_5gs.mm.suci.msin": "0000000001",
                }
            ),
            row(
                {
                    "frame.number": "2",
                    "frame.time_epoch": "1.1",
                    "_ws.col.Protocol": "NGAP/NAS-5GS",
                    "_ws.col.Info": "DownlinkNASTransport, Registration accept",
                    "ip.src": "127.0.0.5",
                    "ip.dst": "127.0.0.1",
                }
            ),
        ]
    )
    analysis = build_trace_analysis(
        {
            "id": "procedure-result-test",
            "selector_kind": "imsi",
            "selector_hash": subscriber_hash(identifier),
            "selector_masked": "99970•••001",
            "scenario_defaults": {"mcc": "999", "mnc": "70"},
            "procedures": ["registration"],
        },
        capture,
    )

    assert analysis["outcome"] == "success"
    assert analysis["correlation_status"] == "partial"


def test_student_can_create_and_delete_own_interface_trace(client, student_headers):
    response = client.post(
        "/api/v1/traces/interface",
        headers=student_headers,
        json={
            "name": "N2 del alumno",
            "scenario_id": "5g-sa",
            "testbed_id": "local",
            "capture_agent_id": "primary",
            "node_id": "core",
            "component_id": "amf",
            "capture_point": "n2",
            "duration_seconds": 5,
            "max_megabytes": 1,
        },
    )
    assert response.status_code == 201
    task_id = response.json()["id"]
    listing = client.get("/api/v1/traces", headers=student_headers)
    assert listing.status_code == 200
    assert all(item["owner"] == "grupo1" for item in listing.json())
    deleted = client.delete(f"/api/v1/traces/{task_id}", headers=student_headers)
    assert deleted.status_code == 204


def test_configuration_catalog_and_declared_read(client, teacher_headers):
    catalog_resp = client.get("/api/v1/config/catalog/5g-sa", headers=teacher_headers)
    assert catalog_resp.status_code == 200
    files = catalog_resp.json()
    assert any(item["component_id"] == "amf" for item in files)
    assert any(item["component_id"] == "ue" for item in files)

    ue_file = next(item for item in files if item["component_id"] == "ue")
    read_resp = client.get(
        f"/api/v1/config/declared?scenario_id=5g-sa&component_id=ue&path={ue_file['path']}",
        headers=teacher_headers,
    )
    assert read_resp.status_code == 200
    data = read_resp.json()
    assert data["scenario_id"] == "5g-sa"
    assert data["component_id"] == "ue"
    assert data["redacted_fields"] > 0
    assert "[REDACTED]" in data["content"]
    assert "00112233445566778899AABBCCDDEEFF" not in data["content"]
    assert data["sha256"]


def test_configuration_validate_scenario(client, teacher_headers):
    resp = client.get("/api/v1/config/validate/5g-sa", headers=teacher_headers)
    assert resp.status_code == 200
    result = resp.json()
    assert result["scenario_id"] == "5g-sa"
    assert "summary" in result
    assert "checks" in result
    assert len(result["checks"]) > 0
    assert "baseline_diff" in result


def test_metrics_with_telco_and_history(client, teacher_headers):
    resp = client.get("/api/v1/metrics?scenario_id=5g-sa", headers=teacher_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu_percent" in data
    assert "memory_percent" in data
    assert "interfaces" in data
    assert "telco" in data
    assert "active_nfs" in data["telco"]
    assert "history" in data
    assert isinstance(data["history"], list)


def test_traffic_ping_role_and_execution(client, student_headers, teacher_headers):
    forbidden = client.post("/api/v1/traffic/ping?count=2", headers=student_headers)
    assert forbidden.status_code == 403

    allowed = client.post("/api/v1/traffic/ping?count=2", headers=teacher_headers)
    assert allowed.status_code == 200
    assert allowed.json()["success"] is True


def test_evidence_export_json_and_csv(client, student_headers):
    json_resp = client.get("/api/v1/audit/evidence/5g-sa/json", headers=student_headers)
    assert json_resp.status_code == 200
    data = json_resp.json()
    assert data["testbed"]["scenario_id"] == "5g-sa"
    assert "telco_validation" in data
    assert "alarms" in data
    assert "traces" in data
    assert "kpis" in data

    csv_resp = client.get("/api/v1/audit/evidence/5g-sa/csv", headers=student_headers)
    assert csv_resp.status_code == 200
    assert "TESTBED" in csv_resp.text
    assert "COMPONENTE" in csv_resp.text


def test_experiments_catalog_and_lifecycle(client, student_headers, teacher_headers):
    catalog_resp = client.get("/api/v1/experiments/catalog/5g-sa", headers=student_headers)
    assert catalog_resp.status_code == 200
    catalog = catalog_resp.json()
    ids = [item["id"] for item in catalog]
    assert "5g-f03" in ids
    assert "ops-f01" in ids
    assert "5g-f02" in ids

    # Student forbidden to inject
    forbidden = client.post("/api/v1/experiments/5g-f03/inject?scenario_id=5g-sa", headers=student_headers)
    assert forbidden.status_code == 403

    # Teacher injects 5g-f03 (AMF down)
    inject_resp = client.post("/api/v1/experiments/5g-f03/inject?scenario_id=5g-sa", headers=teacher_headers)
    assert inject_resp.status_code == 200
    assert inject_resp.json()["status"] == "injected"

    # Verify alarms reflect the injection
    alarms_resp = client.get("/api/v1/alarms/5g-sa", headers=student_headers)
    assert alarms_resp.status_code == 200
    alarm_texts = " ".join(a["message"] for a in alarms_resp.json())
    assert "AMF" in alarm_texts

    # Teacher recovers 5g-f03
    recover_resp = client.post("/api/v1/experiments/5g-f03/recover?scenario_id=5g-sa", headers=teacher_headers)
    assert recover_resp.status_code == 200
    assert recover_resp.json()["status"] == "nominal"

    # Teacher injects and recovers ops-f01 (N6 forwarding)
    inj_ops = client.post("/api/v1/experiments/ops-f01/inject?scenario_id=5g-sa", headers=teacher_headers)
    assert inj_ops.status_code == 200
    assert inj_ops.json()["status"] == "injected"

    ops_alarms = client.get("/api/v1/alarms/5g-sa", headers=student_headers)
    assert ops_alarms.status_code == 200
    assert any(a["id"].endswith("forwarding-disabled") for a in ops_alarms.json())

    rec_ops = client.post("/api/v1/experiments/ops-f01/recover?scenario_id=5g-sa", headers=teacher_headers)
    assert rec_ops.status_code == 200
    assert rec_ops.json()["status"] == "nominal"

    # Teacher injects and recovers 5g-f01 (Authentication failure)
    inj_f01 = client.post("/api/v1/experiments/5g-f01/inject?scenario_id=5g-sa", headers=teacher_headers)
    assert inj_f01.status_code == 200
    assert inj_f01.json()["status"] == "injected"

    f01_alarms = client.get("/api/v1/alarms/5g-sa", headers=student_headers)
    assert f01_alarms.status_code == 200
    assert any(a["id"].endswith("authentication-rejected") for a in f01_alarms.json())

    rec_f01 = client.post("/api/v1/experiments/5g-f01/recover?scenario_id=5g-sa", headers=teacher_headers)
    assert rec_f01.status_code == 200
    assert rec_f01.json()["status"] == "nominal"


def test_performance_collector_catalog_and_historical_query(
    client, student_headers, teacher_headers
):
    started = client.post(
        "/api/v1/scenarios/5g-sa/start",
        headers=teacher_headers,
        json={"parameters": parameters(True), "testbed": "local"},
    )
    assert started.status_code == 200

    forbidden = client.post("/api/v1/performance/collect", headers=student_headers)
    assert forbidden.status_code == 403
    collected = client.post("/api/v1/performance/collect", headers=teacher_headers)
    assert collected.status_code == 202
    assert collected.json()["inserted_samples"] > 0

    catalog = client.get(
        "/api/v1/performance/catalog/5g-sa", headers=student_headers
    )
    assert catalog.status_code == 200
    payload = catalog.json()
    assert payload["collector"]["stored_samples"] > 0
    assert any(item["id"] == "host.cpu.percent" for item in payload["counters"])
    assert any(
        item["id"] == "5g.registration.unresolved"
        for item in payload["counters"]
    )
    assert any(item["id"] == "nf:amf" for item in payload["objects"])

    query = client.post(
        "/api/v1/performance/query",
        headers=student_headers,
        json={
            "scenario_id": "5g-sa",
            "object_ids": ["testbed:local"],
            "counter_ids": ["host.cpu.percent", "host.memory.percent"],
            "range_key": "1h",
            "granularity_seconds": 10,
            "aggregation": "avg",
        },
    )
    assert query.status_code == 200
    assert query.json()["sample_count"] >= 2
    assert len(query.json()["series"]) == 2


def test_performance_personal_and_testbed_saved_queries(client, student_headers):
    folder = client.post(
        "/api/v1/performance/folders",
        headers=student_headers,
        json={"name": "KPIs 5GC", "scope": "testbed"},
    )
    assert folder.status_code == 201

    saved = client.post(
        "/api/v1/performance/saved-queries",
        headers=student_headers,
        json={
            "name": "Salud global 5GC",
            "folder_id": folder.json()["id"],
            "scope": "testbed",
            "scenario_id": "5g-sa",
            "object_ids": ["testbed:local"],
            "counter_ids": ["host.cpu.percent"],
            "range_key": "1h",
            "granularity_seconds": 30,
            "aggregation": "avg",
        },
    )
    assert saved.status_code == 201
    listing = client.get(
        "/api/v1/performance/saved-queries", headers=student_headers
    )
    assert listing.status_code == 200
    assert any(item["name"] == "Salud global 5GC" for item in listing.json())
    deleted = client.delete(
        f"/api/v1/performance/saved-queries/{saved.json()['id']}",
        headers=student_headers,
    )
    assert deleted.status_code == 204


def test_telco_log_parser_counts_real_5g_procedures_without_identifiers():
    lines = [
        "Sep 03 13:20:46 host nr-ue[1]: [2026-09-03 13:20:46.247] [nas] [debug] Sending Initial Registration",
        "Sep 03 13:20:46 host nr-ue[1]: [2026-09-03 13:20:46.295] [nas] [info] Initial Registration is successful",
        "Sep 03 13:20:46 host nr-ue[1]: [2026-09-03 13:20:46.295] [nas] [debug] Sending PDU Session Establishment Request",
        "Sep 03 13:20:46 host nr-ue[1]: [2026-09-03 13:20:46.516] [nas] [info] PDU Session establishment is successful PSI[1]",
        "Sep 03 13:22:05 host nr-ue[2]: [2026-09-03 13:22:05.100] [nas] [warning] PDU Session Establishment Reject: missing or unknown DNN",
    ]
    events, current = parse_telco_logs("5g-sa", "ue", lines)
    assert current == {}
    assert [(item["procedure"], item["event_type"]) for item in events] == [
        ("registration", "attempt"),
        ("registration", "success"),
        ("pdu-session", "attempt"),
        ("pdu-session", "success"),
        ("pdu-session", "reject"),
    ]
    registration_success = next(
        item
        for item in events
        if item["procedure"] == "registration" and item["event_type"] == "success"
    )
    pdu_success = next(
        item
        for item in events
        if item["procedure"] == "pdu-session" and item["event_type"] == "success"
    )
    rejected = next(item for item in events if item["event_type"] == "reject")
    assert registration_success["duration_ms"] == 48.0
    assert pdu_success["duration_ms"] == 221.0
    assert rejected["cause"] == "dnn"


def test_node_trace_catalog_exposes_supported_csp_nodes_without_bpf_filters(
    client, teacher_headers
):
    response = client.get(
        "/api/v1/traces/node-catalog/5g-sa", headers=teacher_headers
    )
    assert response.status_code == 200
    catalog = response.json()
    assert {item["id"] for item in catalog} == {"amf", "smf", "smf2", "upf", "nrf", "scp"}
    assert all("filter" not in item for item in catalog)
    assert any(
        interface["id"] == "n2"
        for item in catalog
        if item["id"] == "amf"
        for interface in item["interfaces"]
    )
