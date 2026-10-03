import json

from app.services.trace_analysis import (
    TSHARK_FIELDS,
    build_decode_tree,
    build_trace_analysis,
    subscriber_hash,
)
from app.services.trace_release16 import POLICY_VERSION


def analyze(*rows, **task):
    data = []
    for n, values in enumerate(rows, 1):
        row = {"frame.number": str(n), "frame.time_epoch": str(n), **values}
        data.append("\t".join(f'"{row.get(k, "")}"' for k in TSHARK_FIELDS))
    return build_trace_analysis({"id": "r16-test", "scenario_id": "5g-sa", "procedures": ["registration", "authentication", "pdu-session"], **task}, "\n".join(data), log_markers={"registration": True, "pdu_session": True})


def nas(message, reverse=False, **fields):
    return {"_ws.col.Protocol": "NGAP/NAS-5GS", "_ws.col.Info": message,
            "ip.src": "127.0.0.5" if reverse else "127.0.0.1",
            "ip.dst": "127.0.0.1" if reverse else "127.0.0.5",
            "ngap.RAN_UE_NGAP_ID": "0", **fields}


def test_decode_tree_is_structured_bounded_and_omits_raw_payload():
    packet = [{"_source": {"layers": {
        "frame": {"frame.number": "42", "frame.len": "184"},
        "nas-5gs": {
            "nas_5gs.mm.message_type": "Registration request",
            "e212.imsi": "999700000000001",
            "nas_5gs.raw": "aabbccdd",
        },
        "tcp": {"tcp.payload": "de:ad:be:ef"},
    }}}]
    decoded = build_decode_tree(
        json.dumps(packet),
        frame_number=42,
        event={"protocol": "NGAP/NAS-5GS", "message": "Registration Request"},
    )
    transcript = json.dumps(decoded)
    assert decoded["frame_number"] == 42
    assert decoded["protocol"] == "NGAP/NAS-5GS"
    assert "tcp.payload" not in transcript
    assert "nas_5gs.raw" not in transcript
    assert "999700000000001" not in transcript
    assert "99970" in transcript


def test_nnrf_response_is_attributed_to_scp_instead_of_loopback():
    result = analyze(
        {
            "_ws.col.Protocol": "HTTP2",
            "_ws.col.Info": "HEADERS: PATCH",
            "frame.time_epoch": "1.0",
            "ip.src": "127.0.0.1",
            "ip.dst": "127.0.0.10",
            "tcp.stream": "7",
            "http2.streamid": "13",
            "http2.headers.method": "PATCH",
            "http2.headers.path": "/nnrf-nfm/v1/nf-instances/example",
        },
        {
            "_ws.col.Protocol": "HTTP2",
            "_ws.col.Info": "HEADERS: 204 No Content",
            "frame.time_epoch": "1.1",
            "ip.src": "127.0.0.10",
            "ip.dst": "127.0.0.1",
            "tcp.stream": "7",
            "http2.streamid": "13",
            "http2.headers.status": "204",
        },
    )
    assert result["events"][0]["source_nf"] == "SCP"
    assert result["events"][0]["target_nf"] == "NRF"
    assert result["events"][1]["source_nf"] == "NRF"
    assert result["events"][1]["target_nf"] == "SCP"


def test_no_fabricated_radio_security_pdu_or_rrc():
    a = analyze(nas("Security mode command", True), nas("UplinkNASTransport"),
        nas("InitialContextSetupResponse"), nas("UplinkNASTransport"), nas("PDUSessionResourceSetupResponse"))
    assert len(a["events"]) == 5
    assert not any(e["source_nf"] == "UE" or e["target_nf"] == "UE" for e in a["events"])
    assert a["events"][1]["message"] == "UplinkNASTransport"
    assert a["events"][3]["message"] == "UplinkNASTransport"
    assert a["analysis_policy"] == POLICY_VERSION


def test_aka_response_and_logs_do_not_prove_success():
    a = analyze(nas("Authentication request", True), nas("Authentication response"))
    assert a["outcome"] == "partial"
    assert a["procedures"][1]["status"] == "partial"
    assert all(e["status"] != "success" for e in a["events"])


def test_pfcp_response_does_not_prove_nas_accept():
    a = analyze({"_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Establishment Response"})
    assert a["procedures"][2]["status"] == "not-observed"


def test_scp_bsf_nrf_and_http_responses_preserve_actual_endpoints():
    a = analyze({"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.200", "ip.dst": "127.0.0.12",
        "http2.headers.method": "GET", "http2.headers.path": "/nudm-sdm/v2/imsi-999700000000001/am-data"},
        {"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.13", "ip.dst": "127.0.0.15",
         "http2.headers.path": "/nbsf-management/v1/pcfBindings"},
        {"_ws.col.Protocol": "HTTP2", "_ws.col.Info": "HEADERS[1]: 204 No Content", "ip.src": "127.0.0.10", "ip.dst": "127.0.0.13"})
    assert a["events"][0]["source_nf"] == "SCP"
    assert "999700000000001" not in str(a)
    assert a["events"][1]["target_nf"] != "UDR"
    assert a["events"][2]["status"] == "info"
    assert len(a["events"]) == 3


def test_http2_response_uses_indexed_path_and_n5_has_af_endpoint():
    a = analyze(
        {"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.13",
         "tcp.stream": "14", "http2.streamid": "1", "http2.headers.method": "POST",
         "http2.headers.path": "/npcf-policyauthorization/v1/app-sessions"},
        {"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.13", "ip.dst": "127.0.0.1",
         "tcp.stream": "14", "http2.streamid": "1", "http2.headers.status": "201"},
    )
    assert [(e["source_nf"], e["target_nf"]) for e in a["events"]] == [("AF", "PCF"), ("PCF", "AF")]
    assert all(e["interface_3gpp"] == "N5 / Npcf" for e in a["events"])


def test_ngap_qos_modification_exposes_observed_5qi_and_qfi():
    a = analyze(nas("PDUSessionResourceModifyRequest", reverse=True, **{
        "ngap.fiveQI": "2", "ngap.qosFlowIdentifier": "2",
    }))
    assert a["events"][0]["message"] == "N2 PDU Session Resource Modify Request (5QI: 2, QFI: 2)"


def test_subscriber_scope_does_not_include_other_ue_with_same_pdu_id():
    a = analyze(nas("Registration request", **{"e212.imsi": "999700000000001", "nas_5gs.pdu_session_id": "1"}),
        nas("Registration accept", True),
        nas("Registration reject", **{"ngap.RAN_UE_NGAP_ID": "9", "nas_5gs.pdu_session_id": "1"}),
        selector_hash=subscriber_hash("999700000000001"), selector_kind="imsi", procedures=["registration"])
    assert len(a["events"]) == 2
    assert a["outcome"] == "success"
    assert a["standards_review"]["conformance"] == "not-certified"


def test_unknown_or_concealed_suci_never_reconstructed():
    for scheme in ("", "1", "2"):
        a = analyze(nas("Registration request", **{"nas_5gs.mm.suci.msin": "0000000001", "nas_5gs.mm.suci.scheme_id": scheme}),
            selector_hash=subscriber_hash("999700000000001"), selector_kind="imsi")
        assert a["outcome"] == "inconclusive"
        assert not a["events"]


def test_null_suci_requires_on_wire_plmn_and_imsi_format():
    a = analyze(nas("Registration request", **{"nas_5gs.mm.suci.msin": "0000000001",
        "nas_5gs.mm.suci.scheme_id": "0", "nas_5gs.mm.suci.supi_fmt": "0", "e212.mcc": "999", "e212.mnc": "70"}),
        selector_hash=subscriber_hash("999700000000001"), selector_kind="imsi", scenario_defaults={"mcc": "999", "mnc": "70"})
    assert a["target"]["matched"]
    assert len(a["events"]) == 1


def test_no_time_based_deduplication_and_no_gtpu_direction_guess():
    row = {"_ws.col.Protocol": "HTTP2", "_ws.col.Info": "DATA[1]", "frame.time_epoch": "1.0"}
    a = analyze(row, row, {"_ws.col.Protocol": "GTP", "ip.src": "10.210.50.8", "ip.dst": "10.210.50.10", "gtp.teid": "0x01", "_ws.col.Info": "Echo reply"})
    assert len(a["events"]) == 3
    assert (a["events"][2]["source_nf"], a["events"][2]["target_nf"]) == ("UPF", "gNB")


def test_timer_reference_is_not_runtime_validation():
    a = analyze(nas("Registration request"))
    review = a["standards_review"]
    assert review["timers"] == "not-assessed"
    assert review["timer_references"]["T3510"]["nominal_seconds"] == 15
    assert all(ref["version"].startswith("16.") for ref in review["references"].values())


def test_explicit_4g_not_routed_to_new_policy():
    a = analyze({"_ws.col.Protocol": "S1AP", "_ws.col.Info": "InitialUEMessage"}, scenario_id="4g-epc")
    assert "analysis_policy" not in a


def test_troubleshooting_mode_available_when_subscriber_unmatched():
    # When subscriber selector is not matched, subscriber events are empty (strict privacy/correlation),
    # but troubleshooting_events are provided so network issues can be diagnosed.
    a = analyze(
        {"_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Association Setup Request", "ip.src": "127.0.0.4", "ip.dst": "10.210.50.8"},
        selector_hash=subscriber_hash("999700000000001"), selector_kind="imsi",
    )
    assert a["outcome"] == "inconclusive"
    assert not a["events"]
    assert a["troubleshooting_active"] is True
    assert len(a["troubleshooting_events"]) == 1
    assert a["troubleshooting_events"][0]["troubleshooting"] is True
    assert any(d["title"] == "Modo Diagnóstico (Troubleshooting Activo)" for d in a["diagnostics"])


def test_pdu_session_release_procedure_isolated_from_establishment():
    """Verify that teardown/release signaling is categorized under 'pdu-session-release'
    so filtering by 'pdu-session' (Sesión PDU) does not display old session teardowns,
    while 'all' (Todo) retains every event."""
    a = analyze(
        # Release / Teardown events
        {"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.1", "ip.dst": "127.0.0.12",
         "http2.headers.method": "DELETE", "http2.headers.path": "/nudm-uecm/v1/imsi-999700000000001/registrations/smf-registrations/1",
         "e212.imsi": "999700000000001"},
        {"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.12", "ip.dst": "127.0.0.15",
         "http2.headers.method": "DELETE", "http2.headers.path": "/nudr-dr/v1/subscription-data/imsi-999700000000001/context-data/smf-registrations/1",
         "e212.imsi": "999700000000001"},
        {"_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Deletion Request", "ip.src": "10.210.50.1", "ip.dst": "10.210.50.8"},
        # Establishment events
        {"_ws.col.Protocol": "HTTP2", "ip.src": "127.0.0.5", "ip.dst": "127.0.0.4",
         "http2.headers.method": "POST", "http2.headers.path": "/nsmf-pdusession/v1/sm-contexts",
         "e212.imsi": "999700000000001"},
        {"_ws.col.Protocol": "PFCP", "_ws.col.Info": "PFCP Session Establishment Request", "ip.src": "10.210.50.1", "ip.dst": "10.210.50.8"},
    )
    events = a["events"]
    assert len(events) == 5, f"All 5 events must be present in Todo: got {len(events)}"

    # Release events must have procedure == 'pdu-session-release'
    assert events[0]["procedure"] == "pdu-session-release"
    assert "Deregistration" in events[0]["message"]
    assert events[1]["procedure"] == "pdu-session-release"
    assert "Delete" in events[1]["message"]
    assert events[2]["procedure"] == "pdu-session-release"
    assert "Deletion" in events[2]["message"]

    # Establishment events must have procedure == 'pdu-session'
    assert events[3]["procedure"] == "pdu-session"
    assert events[4]["procedure"] == "pdu-session"

    # When filtering by 'pdu-session', only establishment events match
    pdu_filtered = [e for e in events if e["procedure"] == "pdu-session"]
    assert len(pdu_filtered) == 2
    assert all(e["procedure"] == "pdu-session" for e in pdu_filtered)
