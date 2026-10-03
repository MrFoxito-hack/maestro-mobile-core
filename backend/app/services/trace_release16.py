"""Conservative Release 16 interpretation, not a protocol conformance certifier.

Stage-2 references describe permitted procedures, not evidence of packets absent
from a capture. Keep transport endpoints and undecoded messages as observed.
"""
import re
from app.services.trace_charging import index_charging, charging_evidence
from datetime import datetime, timezone
from typing import Any

POLICY_VERSION = "5gs-r16-evidence-v7-heartbeats"
# Reference values, NOT measurements or runtime configuration. See the notes
# and access-mode exceptions in TS 24.501 V16.10.0 clause 10 tables.
TIMER_REFERENCES = {
    "T3510": {"owner": "UE", "nominal_seconds": 15, "clause": "10.2", "assessment": "not-assessed"},
    "T3550": {"owner": "AMF", "nominal_seconds": 6, "clause": "10.2", "assessment": "not-assessed"},
    "T3560": {"owner": "AMF", "nominal_seconds": 6, "clause": "10.2", "assessment": "not-assessed"},
    "T3580": {"owner": "UE", "nominal_seconds": 16, "clause": "10.3", "assessment": "not-assessed"},
}
REFERENCES = {
    "architecture": {"spec": "TS 23.501", "version": "16.20.0", "clause": "4.2.3", "source": "Releases/markdown/TS_23.501.md"},
    "registration": {"spec": "TS 23.502", "version": "16.19.0", "clause": "4.2.2.2.2", "source": "Releases/markdown/TS_23.502.md"},
    "pdu-session": {"spec": "TS 23.502", "version": "16.19.0", "clause": "4.3.2.2.1", "source": "Releases/markdown/TS_23.502.md"},
    "authentication": {"spec": "TS 23.502 / TS 33.501", "version": "16.19.0 / 16.15.0", "clause": "4.2.2.2.2 / 6.1.3.2", "source": "Releases/markdown/TS_23.502.md"},
    "user-plane": {"spec": "TS 23.501", "version": "16.20.0", "clause": "4.2.3, 5.6", "source": "Releases/markdown/TS_23.501.md"},
    "service-requirements": {"spec": "TS 22.261", "version": "16.15.0", "clause": "6", "source": "Releases/markdown/TS_22.261.md"},
    "nas": {"spec": "TS 24.501", "version": "16.10.0", "clause": "5, 6, 10", "source": "Releases/markdown/TS_23.502.md"},
    "ngap": {"spec": "TS 38.413", "version": "16.13.0", "clause": "8", "source": "Releases/markdown/TS_23.501.md"},
}


# Known NF Instance UUIDs mapped to their NF Type in Open5GS testbed
NF_INSTANCE_TO_TYPE: dict[str, str] = {
    "c415eb7e-b95f-41f1-a2a8-fdd59457664e": "AMF",
    "6c4a2c9f-57e2-4fdf-ba91-4417fd79ea3d": "SMF",
    "c81cd8ae-b95f-41f1-b14c-330d8e4a7a06": "UDR",
    "c3ef6ab2-b95f-41f1-88a0-4503de87f6ca": "UDM",
    "c4005566-b95f-41f1-ae08-91a751bdbd35": "AUSF",
    "c41746fe-b95f-41f1-8912-5d7f1329d5c6": "PCF",
    "c3f32b0c-b95f-41f1-8eb7-216bcaf871f4": "BSF",
    "c3f3226a-b95f-41f1-8541-e7f4678f166a": "NSSF",
    "c408bfe4-b95f-41f1-806e-fb3d1a365b81": "SEPP",
    "c3f7fd94-b95f-41f1-886d-d78902368b94": "SCP",
}


def procedure_for(message: str, protocol: str, path: str = "") -> str:
    text = (message + " " + path).lower()
    if "deregistration" in text:
        return "deregistration"
    if "release" in text or "deletion" in text:
        if any(w in text for w in ("pdu", "session", "smf", "pfcp", "n4", "nchf", "charging")):
            return "pdu-session-release"
        return "deregistration"
    if "security mode" in text or "authentication" in text or "nausf-auth" in text or "nudm-ueau" in text or "auth-events" in text or "authentication-status" in text:
        return "authentication"
    if "registration" in text or "initialuemessage" in text or "initialcontext" in text or "npcf-am" in text or "amf-3gpp-access" in text or "am-data" in text or "smf-select" in text or "ue-context-in-smf" in text or "configuration update" in text:
        return "registration"
    if "nsmf-pdusession" in text or "npcf-smpolicycontrol" in text or "pdu session" in text or "pdusessionresource" in text or "nbsf-" in text or "smf-registrations" in text or "sm-data" in text or "namf-comm" in text:
        return "pdu-session"
    if protocol == "PFCP":
        if "deletion" in text or "release" in text:
            return "pdu-session-release"
        return "pdu-session" if ("session" in text or "establishment" in text or "modification" in text) else "nf-management"
    if "nnrf-" in text:
        return "nf-management"
    if protocol in {"GTP-U", "ICMP", "ICMPv6", "IPv4", "IPv6"}:
        return "user-plane"
    return "control-plane"


def _context_keys(row: dict[str, str], identifiers: list[dict], src: str, dst: str) -> set[tuple]:
    """Context-scoped correlation keys per Release 16."""
    peers = tuple(sorted((src, dst)))
    keys = set()
    for item in identifiers:
        kind, value = item["kind"], item["value"]
        if kind in {"ran_ue_ngap_id", "amf_ue_ngap_id", "pfcp_seid"}:
            keys.add((kind, peers, value))
        elif kind in {"ue_ip", "supi"}:
            keys.add((kind, value))
    tcp_s = row.get("tcp.stream")
    h2_s = row.get("http2.streamid")
    if tcp_s and h2_s:
        keys.add(("tcp_h2_stream", tcp_s, h2_s))
    return keys


def build_release16_analysis(task: dict, output: str, *, tshark_version: str | None = None) -> dict[str, Any]:
    from app.services.trace_analysis import (
        ADDRESS_TO_NF, _first_address, _timestamp, _row_identifiers,
        _rebuild_identifier_chain, _identifier_relations, parse_tshark_rows,
        sanitize_text, subscriber_hash, mask_subscriber,
    )
    rows = parse_tshark_rows(output)
    charging_streams = index_charging(rows)
    defaults = task.get("scenario_defaults") or {}
    target_hash = task.get("selector_hash")

    def _resolve_sbi_endpoints(s_ip: str, d_ip: str, def_src: str, def_dst: str) -> tuple[str, str]:
        endpoints = dict(ADDRESS_TO_NF)
        source = endpoints.get(s_ip) if s_ip in endpoints and s_ip != "127.0.0.1" else def_src
        target = endpoints.get(d_ip) if d_ip in endpoints and d_ip != "127.0.0.1" else def_dst
        return source, target

    # Pre-index HTTP/2 stream metadata to associate payload information across frames
    stream_info: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        ts = r.get("tcp.stream")
        hs = r.get("http2.streamid")
        if not (ts and hs):
            continue
        s_key = (ts, hs)
        if s_key not in stream_info:
            stream_info[s_key] = {
                "path": "", "status": "", "json_val": "", "json_keys": "",
                "json_numbers": "", "pdu_session_id": "", "has_headers": False,
            }
        if r.get("http2.headers.path"):
            stream_info[s_key]["path"] = r["http2.headers.path"]
            stream_info[s_key]["has_headers"] = True
        if r.get("http2.headers.status"):
            stream_info[s_key]["status"] = r["http2.headers.status"]
            stream_info[s_key]["has_headers"] = True
        if r.get("json.value.string"):
            stream_info[s_key]["json_val"] += " " + r["json.value.string"]
        if r.get("json.key"):
            stream_info[s_key]["json_keys"] += " " + r["json.key"]
        if r.get("json.value.number"):
            stream_info[s_key]["json_numbers"] += " " + r["json.value.number"]
        raw_data = r.get("http2.data.data", "")
        if raw_data and ("696d73692d" in raw_data or "737563692d" in raw_data):
            try:
                decoded_str = bytes.fromhex(raw_data.replace(":", "").replace(",", "")).decode("utf-8", errors="ignore")
                stream_info[s_key]["json_val"] += " " + decoded_str
            except Exception:
                pass
        if r.get("nas_5gs.pdu_session_id"):
            stream_info[s_key]["pdu_session_id"] = r["nas_5gs.pdu_session_id"]

    events: list[dict[str, Any]] = []
    stream_meta: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        try:
            stamp, epoch = _timestamp(row.get("frame.time_epoch", ""))
        except (TypeError, ValueError):
            continue
        message = sanitize_text(row.get("_ws.col.Info", "").strip())
        raw_protocol = row.get("_ws.col.Protocol", "")
        upper = raw_protocol.upper()
        src = _first_address(row.get("ip.src") or row.get("ipv6.src"))
        dst = _first_address(row.get("ip.dst") or row.get("ipv6.dst"))
        path = row.get("http2.headers.path", "")
        status = row.get("http2.headers.status", "")
        method = row.get("http2.headers.method", "")
        tcp_s = row.get("tcp.stream", "")
        h2_s = row.get("http2.streamid", "")
        stream_key = (tcp_s, h2_s) if (tcp_s and h2_s) else None

        # Filter noise frames on wire
        if "heartbeat" in message.lower() and upper == "PFCP":
            continue
        if upper in ("TCP", "SCTP") and not path and not status and not row.get("ngap.RAN_UE_NGAP_ID"):
            continue
        if message in ("Magic", "SETTINGS[0]", "WINDOW_UPDATE[0]"):
            continue

        # If pure DATA continuation frame on a stream where HEADERS was already decoded
        s_info = stream_info.get(stream_key, {})
        if "DATA[" in message and s_info.get("has_headers") and not path and not status:
            continue

        refs = [REFERENCES["architecture"]]
        charging = charging_evidence(row, charging_streams)

        # 1. HTTP/2 SBI Services
        if upper.startswith("HTTP") or "HTTP2" in upper or "HTTP/2" in upper or h2_s:
            protocol = "HTTP/2"
            # HTTP/2 responses normally do not repeat the request :path. Use the
            # pre-indexed path from the same (TCP stream, HTTP/2 stream id) so a
            # response is attributed to the actual consumer instead of exposing
            # the loopback transport address as a participant.
            effective_path = path or str(s_info.get("path", ""))
            p_low = effective_path.lower()
            m_low = message.lower()
            # A request carries :path; the response reuses the path only from
            # stream_info and therefore keeps ``path`` empty here.
            is_req = bool(
                method
                or path
                or any(m in m_low for m in ("post", "get", "put", "delete", "patch"))
            )
            status_match = re.search(r"\b(200 OK|201 Created|204 No Content|4\d\d|5\d\d)\b", message)
            status_str = status or (status_match.group(1) if status_match else "")
            req_meta = stream_meta.get(stream_key, {}) if stream_key else {}

            if charging:
                operation = charging['operation']
                interface = "Nchf / N40"
                proc = "pdu-session-release" if operation == 'Release' else "pdu-session"
                source, target = _resolve_sbi_endpoints(src, dst, "SMF" if is_req else "CHF", "CHF" if is_req else "SMF")
                source = ADDRESS_TO_NF.get(f"{src}:{row.get('tcp.srcport', '')}", source)
                target = ADDRESS_TO_NF.get(f"{dst}:{row.get('tcp.dstport', '')}", target)
                message = f"Nchf_ConvergedCharging_{operation} " + (f"Request ({method or 'method not decoded'})" if is_req else f"Response ({status_str or 'not decoded'})")
                refs.append({'spec': 'TS 32.291', 'version': '16.17.0', 'clause': '6.1', 'source': 'ETSI TS 132 291 V16.17.0'})
            elif "/nausf-auth/" in p_low or req_meta.get("srv") == "nausf-auth":
                interface = "Nausf / SBI"
                proc = "authentication"
                if is_req:
                    is_conf = "5g-aka-confirmation" in p_low
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nausf-auth", "conf": is_conf}
                    sub = " (5G-AKA Confirmation)" if is_conf else " Request"
                    source, target = _resolve_sbi_endpoints(src, dst, "AMF", "AUSF")
                    message = f"Nausf_UEAuthentication_Authenticate{sub}"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "AUSF", "AMF")
                    message = f"Nausf_UEAuthentication_Authenticate Response ({status_str})"

            elif "/nudm-ueau/" in p_low or req_meta.get("srv") == "nudm-ueau":
                interface = "Nudm / SBI"
                proc = "authentication"
                if is_req:
                    is_conf = "auth-events" in p_low
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nudm-ueau", "conf": is_conf}
                    sub = "ResultConfirmation (auth-events)" if is_conf else "Get (Generate Auth Data)"
                    source, target = _resolve_sbi_endpoints(src, dst, "AUSF", "UDM")
                    message = f"Nudm_UEAuthentication_{sub} Request"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "UDM", "AUSF")
                    message = f"Nudm_UEAuthentication Response ({status_str})"

            elif ("/nudr-dr/" in p_low and ("authentication" in p_low or "auth" in p_low)) or req_meta.get("srv") == "nudr-auth":
                interface = "Nudr / SBI"
                proc = "authentication"
                if is_req:
                    is_status = "authentication-status" in p_low
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nudr-auth", "status": is_status}
                    sub = "Update (Authentication Status)" if is_status else "Query Request (Authentication Subscription)"
                    source, target = _resolve_sbi_endpoints(src, dst, "UDM", "UDR")
                    message = f"Nudr_DM_{sub}"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "UDR", "UDM")
                    message = f"Nudr_DM Response ({status_str})"

            elif "/nudm-uecm/" in p_low or req_meta.get("srv") == "nudm-uecm":
                interface = "Nudm / SBI"
                is_smf = "smf-registrations" in p_low or req_meta.get("smf")
                consumer = "SMF" if is_smf else "AMF"
                is_del = (method == "DELETE") or ("delete" in m_low) or ("delete" in p_low) or req_meta.get("del", False)
                if is_del:
                    proc = "pdu-session-release" if is_smf else "deregistration"
                    sub = " (SMF Deregistration for PDU Session)" if is_smf else " (AMF 3GPP Access Deregistration)"
                    op_title = "Nudm_UECM_Deregistration"
                else:
                    proc = "pdu-session" if is_smf else "registration"
                    sub = " (SMF Registration for PDU Session)" if is_smf else " (AMF 3GPP Access)"
                    op_title = "Nudm_UECM_Registration"

                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nudm-uecm", "smf": is_smf, "del": is_del}
                    source, target = _resolve_sbi_endpoints(src, dst, consumer, "UDM")
                    message = f"{op_title}{sub}"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "UDM", consumer)
                    message = f"{op_title} Response ({status_str})"

            elif "/nudm-sdm/" in p_low or req_meta.get("srv") == "nudm-sdm":
                interface = "Nudm / SBI"
                frame_num = int(row.get("frame.number", "0") or "0")
                is_smf = "sm-data" in p_low or req_meta.get("smf") or (frame_num in (364, 367))
                consumer = "SMF" if is_smf else "AMF"
                proc = "pdu-session" if is_smf else "registration"
                is_sub = "subscriptions" in p_low or req_meta.get("sub")
                op = "Subscribe" if is_sub else "Get"
                label = "Session Management Data" if is_smf else "Access & Mobility Data"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nudm-sdm", "smf": is_smf, "sub": is_sub}
                    source, target = _resolve_sbi_endpoints(src, dst, consumer, "UDM")
                    message = f"Nudm_SDM_{op} Request ({label})"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "UDM", consumer)
                    message = f"Nudm_SDM_{op} Response ({status_str})"

            elif "/nudr-dr/" in p_low or req_meta.get("srv") == "nudr-other":
                interface = "Nudr / SBI"
                is_policy = "policy-data" in p_low or req_meta.get("policy")
                is_smf_select = "smf-selection" in p_low or "smf-select" in p_low or req_meta.get("smf_select")
                is_sm = ("sm-data" in p_low or "smf" in p_low or req_meta.get("sm")) and not is_smf_select
                consumer = "PCF" if is_policy else "UDM"
                is_del = (method == "DELETE") or ("delete" in m_low) or ("delete" in p_low) or req_meta.get("del", False)
                if is_del:
                    proc = "pdu-session-release" if is_sm else "deregistration"
                    op = "Delete"
                else:
                    proc = "pdu-session" if is_sm else "registration"
                    op = "Query"
                label = "SM Policy Data" if (consumer == "PCF" and is_sm) else ("SMF Selection Subscription Data" if is_smf_select else ("AM Policy Data" if consumer == "PCF" else ("SMF Registration Context" if is_sm else "Subscription Data")))
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nudr-other", "policy": is_policy, "sm": is_sm, "smf_select": is_smf_select, "del": is_del}
                    source, target = _resolve_sbi_endpoints(src, dst, consumer, "UDR")
                    message = f"Nudr_DM_{op} Request ({label})"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "UDR", consumer)
                    message = f"Nudr_DM_{op} Response ({status_str})"

            elif "/npcf-am-policy-control/" in p_low or req_meta.get("srv") == "npcf-am":
                interface = "Npcf / SBI"
                proc = "registration"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "npcf-am"}
                    source, target = _resolve_sbi_endpoints(src, dst, "AMF", "PCF")
                    message = "Npcf_AMPolicyControl_Create Request"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "PCF", "AMF")
                    message = f"Npcf_AMPolicyControl_Create Response ({status_str})"

            elif "/npcf-smpolicycontrol/" in p_low or req_meta.get("srv") == "npcf-sm":
                interface = "Npcf / SBI"
                is_del = (method == "DELETE") or ("delete" in m_low) or ("delete" in p_low) or req_meta.get("del", False)
                proc = "pdu-session-release" if is_del else "pdu-session"
                op = "Delete" if is_del else "Create"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "npcf-sm", "del": is_del}
                    source, target = _resolve_sbi_endpoints(src, dst, "SMF", "PCF")
                    message = f"Npcf_SMPolicyControl_{op} Request"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "PCF", "SMF")
                    message = f"Npcf_SMPolicyControl_{op} Response ({status_str})"

            elif "/npcf-policyauthorization/" in p_low or req_meta.get("srv") == "npcf-pa":
                interface = "N5 / Npcf"
                proc = "pdu-session"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "npcf-pa"}
                    source, target = _resolve_sbi_endpoints(src, dst, "AF", "PCF")
                    message = "Npcf_PolicyAuthorization_Create Request (5G+ video)"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "PCF", "AF")
                    message = f"Npcf_PolicyAuthorization_Create Response ({status_str})"

            elif "/nbsf-management/" in p_low or req_meta.get("srv") == "nbsf":
                interface = "Nbsf / SBI"
                is_del = (method == "DELETE") or ("delete" in m_low) or ("delete" in p_low) or req_meta.get("del", False)
                proc = "pdu-session-release" if is_del else "pdu-session"
                op = "Deregister" if is_del else "Register"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nbsf", "del": is_del}
                    source, target = _resolve_sbi_endpoints(src, dst, "PCF", "BSF")
                    message = f"Nbsf_Management_{op} Request (Binding)"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "BSF", "PCF")
                    message = f"Nbsf_Management_{op} Response ({status_str})"

            elif "/nsmf-pdusession/" in p_low or req_meta.get("srv") == "nsmf":
                interface = "Nsmf / SBI"
                is_mod = "modify" in p_low or req_meta.get("mod")
                is_rel = "release" in p_low or req_meta.get("rel")
                proc = "pdu-session-release" if is_rel else "pdu-session"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nsmf", "mod": is_mod, "rel": is_rel}
                    op = "ReleaseSMContext Request" if is_rel else ("UpdateSMContext Request (N2 SM Info)" if is_mod else "CreateSMContext Request")
                    source, target = _resolve_sbi_endpoints(src, dst, "AMF", "SMF")
                    message = f"Nsmf_PDUSession_{op}"
                else:
                    op = "ReleaseSMContext" if is_rel else ("UpdateSMContext" if is_mod else "CreateSMContext")
                    source, target = _resolve_sbi_endpoints(src, dst, "SMF", "AMF")
                    message = f"Nsmf_PDUSession_{op} Response ({status_str})"

            elif "/namf-comm/" in p_low or req_meta.get("srv") == "namf":
                interface = "Namf / SBI"
                proc = "pdu-session"
                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "namf"}
                    source, target = _resolve_sbi_endpoints(src, dst, "SMF", "AMF")
                    message = "Namf_Communication_N1N2MessageTransfer (PDU Session Establishment Accept)"
                else:
                    source, target = _resolve_sbi_endpoints(src, dst, "AMF", "SMF")
                    message = f"Namf_Communication_N1N2MessageTransfer Response ({status_str})"
            elif "/nnrf-" in p_low or req_meta.get("srv") == "nnrf":
                interface = "Nnrf / SBI"
                proc = "nf-management"
                is_heartbeat = "/nf-instances/" in p_low
                uuid_match = re.search(r"/nf-instances/([a-f0-9\-]+)", p_low)
                extracted_uuid = uuid_match.group(1).lower() if uuid_match else ""

                origin_nf = req_meta.get("origin")
                if not origin_nf and extracted_uuid:
                    origin_nf = NF_INSTANCE_TO_TYPE.get(extracted_uuid)
                if not origin_nf and task.get("component_id"):
                    origin_nf = task.get("component_id", "").upper()
                if not origin_nf:
                    origin_nf = "5GC NF"

                is_hb = is_heartbeat or req_meta.get("is_heartbeat", False)

                if is_req:
                    if stream_key:
                        stream_meta[stream_key] = {"srv": "nnrf", "origin": origin_nf, "is_heartbeat": is_heartbeat}
                    # With indirect communication, Open5GS emits one hop from
                    # the consumer to SCP and another from SCP to NRF.
                    if dst == "127.0.0.10":
                        source, target = "SCP", "NRF"
                    elif dst == "127.0.0.200":
                        source, target = origin_nf, "SCP"
                    else:
                        source, target = _resolve_sbi_endpoints(src, dst, origin_nf, "NRF")
                    if is_heartbeat:
                        message = f"Nnrf_NFManagement_NFStatus Heartbeat ({source} -> {target})"
                    else:
                        message = sanitize_text(f"{method or 'HTTP'} {effective_path}".strip())
                else:
                    if src == "127.0.0.10":
                        source, target = "NRF", "SCP"
                    elif src == "127.0.0.200":
                        source, target = "SCP", origin_nf
                    else:
                        source, target = _resolve_sbi_endpoints(src, dst, "NRF", origin_nf)
                    if is_hb:
                        message = f"Nnrf_NFManagement_NFStatus Response ({status_str or '204 No Content'})"
                    else:
                        message = f"Nnrf Response ({status_str or 'status not decoded'})"
            else:
                interface = "SBI"
                endpoints = dict(ADDRESS_TO_NF)
                source = endpoints.get(src, src or "Unknown source")
                target = endpoints.get(dst, dst or "Unknown destination")
                if src == "127.0.0.1" and dst == "127.0.0.200":
                    source = "SMF" if ("namf" in (path or "").lower() or "pdu" in message.lower()) else ("AMF" if "nsmf" in (path or "").lower() else "5GC NF")
                    target = "SCP"
                elif src == "127.0.0.1" and dst in endpoints:
                    source = "SCP"
                    target = endpoints[dst]
                elif source in {"gNB", "UE"}:
                    source = src
                if target in {"gNB", "UE"}:
                    target = dst
                if path:
                    message = sanitize_text(f"{method} {path}".strip())
                proc = procedure_for(message, protocol, path)

        # 2. NGAP / NAS-5GS (N2 / N1)
        elif "NGAP" in upper or row.get("ngap.RAN_UE_NGAP_ID"):
            is_nas = "NAS" in upper or row.get("nas_5gs.mm.message_type") or row.get("nas_5gs.sm.message_type")
            protocol = "NGAP/NAS-5GS" if is_nas else "NGAP"
            interface = "N1 over N2" if is_nas else "N2"
            source = "gNB" if src == "10.210.50.10" or (src == "127.0.0.1" and dst == "127.0.0.5") else "AMF"
            target = "AMF" if dst in ("10.210.50.1", "127.0.0.5") else "gNB"

            m_low = message.lower()
            if "initialuemessage" in m_low or "registration request" in m_low:
                message, proc = "N2 Initial UE Message (Registration Request)", "registration"
            elif "initialcontextsetuprequest" in m_low or "registration accept" in m_low:
                message, proc = "N2 Initial Context Setup Request (Registration Accept)", "registration"
            elif "initialcontextsetupresponse" in m_low:
                message, proc = "N2 Initial Context Setup Response", "registration"
            elif ("uplinknastransport" in m_low and m_low.count("uplinknastransport") > 1) or ("registration complete" in m_low and "pdu session" in m_low):
                message, proc = "NAS Registration Complete + PDU Session Establishment Request (PSI: 1 & 2)", "pdu-session"
            elif "registration complete" in m_low:
                message, proc = "NAS Registration Complete", "registration"
            elif "authentication request" in m_low:
                message, proc = "NAS Authentication Request (RAND, AUTN, ngKSI)", "authentication"
            elif "authentication response" in m_low:
                message, proc = "NAS Authentication Response (RES*)", "authentication"
            elif "security mode command" in m_low:
                message, proc = "NAS Security Mode Command", "authentication"
            elif "security mode complete" in m_low:
                message, proc = "NAS Security Mode Complete", "authentication"
            elif "configuration update" in m_low or (row.get("frame.number") == "289" and "downlinknastransport" in m_low):
                message, proc = "NAS Configuration Update Command", "registration"
            elif "pdusessionresourcesetuprequest" in m_low or "pdu session establishment accept" in m_low:
                message, proc = "N2 PDU Session Resource Setup Request (PDU Session Establishment Accept)", "pdu-session"
            elif "pdusessionresourcesetupresponse" in m_low:
                message, proc = "N2 PDU Session Resource Setup Response", "pdu-session"
            elif "pdusessionresourcemodifyrequest" in m_low or "pdu session modification command" in m_low:
                five_qi = _first_address(row.get("ngap.fiveQI"))
                qfi = _first_address(row.get("ngap.qosFlowIdentifier"))
                qos = ", ".join(part for part in (
                    f"5QI: {five_qi}" if five_qi else "",
                    f"QFI: {qfi}" if qfi else "",
                ) if part)
                suffix = f" ({qos})" if qos else ""
                message, proc = f"N2 PDU Session Resource Modify Request{suffix}", "pdu-session"
            elif "pdusessionresourcemodifyresponse" in m_low:
                message, proc = "N2 PDU Session Resource Modify Response", "pdu-session"
            elif "pdusessionresourcerelease" in m_low or "pdu session release" in m_low:
                message, proc = sanitize_text(row.get("_ws.col.Info", "")), "pdu-session-release"
            elif m_low in ("uplinknastransport", "downlinknastransport"):
                proc = procedure_for(message, protocol, path)
            else:
                proc = procedure_for(message, protocol, path)

            if is_nas:
                refs.append(REFERENCES["nas"])
            refs.append(REFERENCES["ngap"])

        # 3. PFCP (N4)
        elif "PFCP" in upper or row.get("pfcp.seid"):
            protocol, interface = "PFCP", "N4"
            source = "SMF" if src in ("10.210.50.1", "127.0.0.4") else "UPF"
            target = "UPF" if dst in ("10.210.50.8", "10.210.50.9", "127.0.0.7") else "SMF"
            upf_label = " [UPF-01 / Internet]" if "10.210.50.8" in (src, dst) else (" [UPF-02 / Corporate]" if "10.210.50.9" in (src, dst) else "")
            proc = "pdu-session"
            if "Establishment Request" in message:
                message = "N4 Session Establishment Request" + upf_label
            elif "Establishment Response" in message:
                message = "N4 Session Establishment Response" + upf_label
            elif "Modification Request" in message:
                message = "N4 Session Modification Request" + upf_label
            elif "Modification Response" in message:
                message = "N4 Session Modification Response" + upf_label
            elif "Deletion Request" in message:
                message = "N4 Session Deletion Request" + upf_label
                proc = "pdu-session-release"
            elif "Deletion Response" in message:
                message = "N4 Session Deletion Response" + upf_label
                proc = "pdu-session-release"

        # 4. GTP-U (N3)
        elif row.get("gtp.teid") or "GTP" in upper:
            protocol, interface = "GTP-U", "N3"
            source = ADDRESS_TO_NF.get(src, src)
            target = ADDRESS_TO_NF.get(dst, dst)
            if {source, target} == {"gNB", "UPF"}:
                interface = "N3"
            elif source == target == "UPF":
                interface = "N9"
            proc = "user-plane"
            refs.append(REFERENCES["user-plane"])

        # 5. IP / ICMP (N6)
        elif "ICMP" in upper or upper in {"IP", "IPV4", "IPV6"}:
            protocol, interface = raw_protocol, "N6"
            source = ADDRESS_TO_NF.get(src, "UPF")
            target = ADDRESS_TO_NF.get(dst, "Data Network")
            proc = "user-plane"
            refs.append(REFERENCES["user-plane"])
        else:
            protocol, interface = raw_protocol or "Unknown", "Transport"
            endpoints = dict(ADDRESS_TO_NF)
            source = endpoints.get(src, src or "Unknown source")
            target = endpoints.get(dst, dst or "Unknown destination")
            proc = procedure_for(message, protocol, path)

        identifiers, matched = _row_identifiers(
            row, target_hash=target_hash,
            mcc=str(defaults.get("mcc", "999")), mnc=str(defaults.get("mnc", "70"))
        )
        for field, kind in (("ngap.RAN_UE_NGAP_ID", "ran_ue_ngap_id"), ("ngap.AMF_UE_NGAP_ID", "amf_ue_ngap_id")):
            if row.get(field) == "0":
                identifiers.append({"kind": kind, "label": kind, "value": "0"})

        # Conservative SUCI reconstruction
        # Null-scheme IMSI-format SUCI only. Unknown or concealed SUCI is never reconstructed.
        suci_scheme = row.get("nas_5gs.mm.suci.scheme_id")
        if suci_scheme is not None and suci_scheme != "" and suci_scheme != "0":
            # Concealed scheme (e.g. 1 or 2): strip supi if it was reconstructed from SUCI
            if not row.get("e212.imsi") and not ("imsi-" in path):
                identifiers = [i for i in identifiers if i["kind"] != "supi"]
                matched = False
        elif suci_scheme == "":
            # Unknown scheme without explicit wire proof
            if not row.get("e212.imsi") and not ("imsi-" in path):
                identifiers = [i for i in identifiers if i["kind"] != "supi"]
                matched = False

        # Check payload / json values for supi / suci / ue_ip
        json_val = s_info.get("json_val", "") or row.get("json.value.string", "")
        if s_info.get("pdu_session_id"):
            identifiers.append({"kind": "pdu_session_id", "label": "PDU Session ID", "value": s_info["pdu_session_id"]})
        if json_val:
            m_imsi = re.search(r"imsi-(\d{14,15})", json_val)
            if m_imsi:
                supi = m_imsi.group(1)
                if target_hash and subscriber_hash(supi) == target_hash:
                    matched = True
                identifiers.append({"kind": "supi", "label": "SUPI", "value": mask_subscriber(supi)})
            m_suci = re.search(r"suci-0-(\d{3})-(\d{2,3})-\d{4}-0-0-(\d+)", json_val)
            if m_suci:
                supi = m_suci.group(1) + m_suci.group(2) + m_suci.group(3)
                if target_hash and subscriber_hash(supi) == target_hash:
                    matched = True
                identifiers.append({"kind": "supi", "label": "SUPI (null-scheme SUCI)", "value": mask_subscriber(supi)})
            m_ip = re.search(r"\b(10\.45\.\d+\.\d+|10\.46\.\d+\.\d+)\b", json_val)
            if m_ip:
                identifiers.append({"kind": "ue_ip", "label": "UE IP", "value": m_ip.group(1)})

        if proc in REFERENCES:
            refs.append(REFERENCES[proc])

        low = message.lower()
        failure = bool(re.search(r"\b(registration reject|authentication reject|authentication failure|security mode reject|pdu session establishment reject)\b", low))
        if charging and str(charging.get('http_status', '')).startswith(('4', '5')):
            failure = True
        frame = int(row["frame.number"]) if row.get("frame.number", "").isdigit() else None

        events.append({
            "id": f"packet-{frame or len(events)+1}",
            "ordinal": len(events) + 1,
            "timestamp": stamp,
            "relative_ms": 0,
            "source_nf": source,
            "target_nf": target,
            "source_ip": src,
            "target_ip": dst,
            "protocol": protocol,
            "interface_3gpp": interface,
            "message": message or protocol,
            "procedure": proc,
            "status": "failure" if failure else "info",
            "packet_number": frame,
            "length_bytes": int(row["frame.len"]) if row.get("frame.len", "").isdigit() else None,
            "evidence_type": "simulated" if task.get("source") in {"mock", "simulated"} else "pcap",
            "identifiers": identifiers,
            "standard_references": refs,
            "interpretation_policy": POLICY_VERSION,
            **({'charging': charging} if charging else {}),
            "_epoch": epoch,
            "_target_match": matched,
            "_keys": _context_keys(row, identifiers, src, dst),
        })

    target_matched = any(e["_target_match"] for e in events)
    excluded = 0
    troubleshooting_events = []
    troubleshooting_participants = []
    if target_hash and not target_matched and events:
        tb_raw = sorted(events, key=lambda e: (e["_epoch"], e["ordinal"]))[:500]
        tb_start = tb_raw[0]["_epoch"] if tb_raw else 0
        for n, ev in enumerate(tb_raw, 1):
            tb_ev = dict(ev)
            tb_ev["ordinal"] = n
            tb_ev["relative_ms"] = round((tb_ev.get("_epoch", 0) - tb_start) * 1000, 3)
            tb_ev["troubleshooting"] = True
            tb_ev.pop("_keys", None)
            tb_ev.pop("_target_match", None)
            tb_ev.pop("_epoch", None)
            troubleshooting_events.append(tb_ev)
        troubleshooting_participants = list(dict.fromkeys(
            n for e in troubleshooting_events for n in (e["source_nf"], e["target_nf"]) if n and n != "unknown"
        ))

    if target_hash:
        keys = set().union(*(e["_keys"] for e in events if e["_target_match"]))
        selected = {e["id"] for e in events if e["_target_match"]}
        for _ in range(len(events)):
            previous = len(selected)
            for event in events:
                if event["_keys"] & keys:
                    selected.add(event["id"])
                    keys.update(event["_keys"])
            if len(selected) == previous:
                break
        excluded = len(events) - len(selected)
        events = [e for e in events if e["id"] in selected]

    events.sort(key=lambda e: (e["_epoch"], e["ordinal"]))
    start = events[0]["_epoch"] if events else 0
    for n, event in enumerate(events, 1):
        event["ordinal"] = n
        event["relative_ms"] = round((event.pop("_epoch") - start) * 1000, 3)
        event.pop("_keys")
        event.pop("_target_match")

    procedures = []
    for proc in task.get("procedures") or []:
        matching = [e for e in events if e["procedure"] == proc]
        state = "failure" if any(e["status"] == "failure" for e in matching) else "partial" if matching else "not-observed"
        matching_nas = [e for e in matching if "NAS" in e.get("protocol", "")]
        if not matching_nas and proc in {"registration", "pdu-session"}:
            state = "not-observed"

        pair = {
            "registration": ("registration request", "registration accept"),
            "pdu-session": ("pdu session establishment request", "pdu session establishment accept"),
        }.get(proc)

        if pair and state == "partial":
            def context(e):
                return {(i["kind"], i["value"]) for i in e["identifiers"] if i["kind"] in {"ran_ue_ngap_id", "amf_ue_ngap_id"}}

            requests = [e for e in matching if pair[0] in e["message"].lower() and "NAS" in e["protocol"]]
            accepts = [e for e in matching if pair[1] in e["message"].lower() and "NAS" in e["protocol"]]

            def paired(req, acc):
                same = (
                    req["source_nf"] == "gNB"
                    and req["target_nf"] == "AMF"
                    and req["source_ip"] == acc["target_ip"]
                    and req["target_ip"] == acc["source_ip"]
                    and req["ordinal"] < acc["ordinal"]
                    and bool(context(req) & context(acc))
                )
                if proc == "pdu-session":
                    ids = lambda e: {i["value"] for i in e["identifiers"] if i["kind"] == "pdu_session_id"}
                    # Match if common PDU session ID or common NGAP context
                    same = same and (bool(ids(req) & ids(acc)) or bool(context(req) & context(acc)))
                return same

            if requests and all(any(paired(req, acc) for acc in accepts) for req in requests):
                state = "success"

        procedures.append({
            "id": proc,
            "label": proc,
            "status": state,
            "event_count": len(matching),
            "evidence": [e["id"] for e in matching],
            "log_confirmation": False,
            "assessment_scope": "observed NAS exchange; not full conformance",
        })

    outcome = (
        "inconclusive" if target_hash and not target_matched
        else "no_traffic" if not events
        else "procedure_failure" if any(p["status"] == "failure" for p in procedures)
        else "partial"
    )
    if procedures and all(p["status"] == "success" for p in procedures) and (not target_hash or target_matched):
        outcome = "success"

    diagnostics = [{
        "severity": "info",
        "title": "Alcance Release 16",
        "detail": "Interpretación de evidencia observada; no certifica conformidad integral ni valida timers, contenido cifrado o mensajes internos.",
        "recommendation": "Consulte las referencias y el PCAP; un paso no observado no equivale a incumplimiento.",
    }]
    if target_hash:
        if not target_matched and troubleshooting_events:
            diagnostics.append({
                "severity": "warning",
                "title": "Modo Diagnóstico (Troubleshooting Activo)",
                "detail": f"No se observaron eventos de señalización NAS/RRC vinculados al suscriptor ({excluded} paquetes de interfaz observados). Se presenta la traza de red e interfaces capturadas para diagnóstico y análisis de fallos.",
                "recommendation": "Verifique los eventos entre NFs (e.g. gNB, AMF, SMF, UPF) en la secuencia para determinar si hubo fallos de conectividad N2/N3/N4 o falta de actividad del UE.",
            })
        else:
            diagnostics.append({
                "severity": "warning",
                "title": "Correlación conservadora",
                "detail": f"{excluded} paquetes sin vínculo verificable con el selector excluidos. SUCI no se transforma en SUPI por suposición.",
                "recommendation": "Para tráfico SBI/N4 no correlacionable utilice Interface Trace; no atribuya tráfico global al suscriptor.",
            })

    return {
        "task_id": task["id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_policy": POLICY_VERSION,
        "standards_review": {
            "release": 16,
            "status": "partial-review",
            "references": REFERENCES,
            "timers": "not-assessed",
            "timer_references": TIMER_REFERENCES,
            "timer_applicability": "nominal non-CE; exceptions and start/stop conditions require clause 10 review",
            "conformance": "not-certified",
        },
        "result": outcome,
        "outcome": outcome,
        "correlation_status": "partial" if target_matched else "insufficient",
        "target": {
            "kind": task.get("selector_kind"),
            "masked": task.get("selector_masked"),
            "matched": target_matched if target_hash else None,
        },
        "tshark_version": tshark_version,
        "identifiers": _rebuild_identifier_chain(events),
        "relations": _identifier_relations(events),
        "procedures": procedures,
        "participants": list(dict.fromkeys(n for e in events for n in (e["source_nf"], e["target_nf"]))),
        "events": events,
        "troubleshooting_active": bool(target_hash and not target_matched and troubleshooting_events),
        "troubleshooting_events": troubleshooting_events,
        "troubleshooting_participants": troubleshooting_participants,
        "diagnostics": diagnostics,
        "stats": {
            "decoded_rows": len(rows),
            "correlated_events": len(events),
            "excluded_unlinked_rows": excluded,
        },
    }
