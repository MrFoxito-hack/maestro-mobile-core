import csv
import hashlib
import io
import re
import json
from datetime import datetime, timezone
from typing import Any


TSHARK_FIELDS = [
    "frame.number",
    "frame.time_epoch",
    "frame.len",
    "_ws.col.Protocol",
    "_ws.col.Info",
    "ip.src",
    "ip.dst",
    "ipv6.src",
    "ipv6.dst",
    "ngap.RAN_UE_NGAP_ID",
    "ngap.AMF_UE_NGAP_ID",
    "nas_5gs.mm.message_type",
    "nas_5gs.sm.message_type",
    "nas_5gs.pdu_session_id",
    "nas_5gs.sm.pdu_addr_inf_ipv4",
    "ngap.pDUSessionID",
    "ngap.gTP_TEID",
    "ngap.fiveQI",
    "ngap.qosFlowIdentifier",
    "pfcp.seid",
    "pfcp.f_seid.ipv4",
    "pfcp.f_teid.teid",
    "pfcp.f_teid.ipv4_addr",
    "pfcp.outer_hdr_creation.teid",
    "pfcp.ue_ip_addr_ipv4",
    "pfcp.qfi_value",
    "gtp.teid",
    "e212.imsi",
    "nas_5gs.mm.suci.msin",
    "http2.streamid",
    "http2.headers.method",
    "http2.headers.path",
    "nas_5gs.mm.suci.scheme_id",
    "nas_5gs.mm.suci.supi_fmt",
    "e212.mcc",
    "e212.mnc",
    "tcp.stream",
    "http2.headers.status",
    "json.value.string",
    "json.key",
    "json.value.number",
    "tcp.srcport",
    "tcp.dstport",
    "http2.data.data",
]


IDENTIFIER_LABELS = {
    "supi": "SUPI/IMSI",
    "ran_ue_ngap_id": "RAN UE NGAP ID",
    "amf_ue_ngap_id": "AMF UE NGAP ID",
    "pdu_session_id": "PDU Session ID",
    "pfcp_seid": "PFCP SEID",
    "gtpu_teid": "GTP-U TEID",
    "ue_ip": "UE IP",
}

ADDRESS_TO_NF = {
    "127.0.0.1": "gNB",
    "127.0.0.2": "MME",
    "127.0.0.3": "SGW-C",
    "127.0.0.4": "SMF",
    "127.0.0.5": "AMF",
    "127.0.0.6": "SGW-U",
    "127.0.0.7": "UPF",
    "127.0.0.10": "NRF",
    "127.0.0.11": "AUSF",
    "127.0.0.12": "UDM",
    "127.0.0.13": "PCF",
    "127.0.0.14": "NSSF",
    # Dual-SMF lab: verified SBI listeners for SMF-02 and the relocated BSF.
    "127.0.0.15": "SMF-02",
    "127.0.0.16": "BSF",
    "10.210.50.2": "SMF-02",
    "127.0.0.20": "UDR",
    "127.0.0.200": "SCP",
    # Distributed 5G multi-host network endpoints:
    "10.210.50.10": "gNB",
    "10.210.50.8": "UPF",
    "10.210.50.9": "UPF",
    "10.210.50.11": "UE",
}


def mask_subscriber(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 7:
        return "•" * len(value)
    return f"{value[:5]}{'•' * max(len(value) - 8, 3)}{value[-3:]}"


def subscriber_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitize_text(value: str) -> str:
    return re.sub(r"(?<!\d)(\d{14,15})(?!\d)", lambda match: mask_subscriber(match.group(1)) or "", value)


DECODE_EXCLUDED_FIELDS = {
    "data.data", "tcp.payload", "tcp.segment_data", "http2.data.data",
}


def build_decode_tree(raw_json: str, *, frame_number: int, event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert one TShark JSON packet into a bounded, non-hex protocol tree."""
    try:
        packets = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("TShark no devolviÃ³ JSON vÃ¡lido") from exc
    if not isinstance(packets, list) or len(packets) != 1:
        raise ValueError("La trama solicitada no existe en el PCAP")
    layers = packets[0].get("_source", {}).get("layers", {})
    if not isinstance(layers, dict):
        raise ValueError("La trama no contiene capas decodificables")

    count = 0

    def clean(name: str, value: Any, depth: int = 0) -> dict[str, Any] | None:
        nonlocal count
        if depth > 12 or count >= 2500:
            return None
        lowered = name.lower()
        if (
            name in DECODE_EXCLUDED_FIELDS
            or lowered.endswith("_raw")
            or lowered.endswith(".raw")
            or "raw bytes" in lowered
        ):
            return None
        count += 1
        label = name.removesuffix("_tree").replace("_", " ")
        node: dict[str, Any] = {"name": name, "label": label}
        if isinstance(value, dict):
            children = [child for key, item in value.items() if (child := clean(str(key), item, depth + 1))]
            if children:
                node["children"] = children
        elif isinstance(value, list):
            children = [child for index, item in enumerate(value) if (child := clean(f"{name}[{index}]", item, depth + 1))]
            if children:
                node["children"] = children
        else:
            text_value = sanitize_text(str(value))[:1000]
            node["value"] = text_value
            node["label"] = f"{label}: {text_value}"
        return node

    tree = [node for name, value in layers.items() if (node := clean(str(name), value))]
    return {
        "frame_number": frame_number,
        "protocol": (event or {}).get("protocol") or "Unknown",
        "message_type": (event or {}).get("message") or f"Frame {frame_number}",
        "timestamp": (event or {}).get("timestamp"),
        "tree": tree,
        "truncated": count >= 2500,
    }


def _values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _hex(value: str) -> str:
    try:
        return f"0x{int(value, 16):08x}" if not value.lower().startswith("0x") else f"0x{int(value, 16):08x}"
    except ValueError:
        return value


def _timestamp(epoch: str) -> tuple[str, float]:
    numeric = float(epoch)
    return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat(), numeric


def _nf_for_address(address: str, *, protocol: str, source: bool) -> str:
    if address in ADDRESS_TO_NF:
        return ADDRESS_TO_NF[address]
    if address.startswith("10.45."):
        return "UE"
    if protocol == "GTP-U":
        return "gNB" if source else "UPF"
    return "Testbed"


def _first_address(value: str | None) -> str:
    values = _values(value)
    return values[0] if values else ""


def _sbi_nf_for_address(address: str, fallback: str = "5GC NF") -> str:
    """Resolve an SBI endpoint without ever presenting a RAN node as an NF."""
    resolved = ADDRESS_TO_NF.get(address, fallback)
    return fallback if resolved in {"gNB", "eNB", "UE"} else resolved


def _protocol_and_interface(protocol_text: str, row: dict[str, str]) -> tuple[str, str]:
    upper = protocol_text.upper()
    if "NGAP" in upper or row.get("ngap.RAN_UE_NGAP_ID"):
        return ("NGAP/NAS-5GS" if "NAS" in upper or row.get("nas_5gs.mm.message_type") else "NGAP", "N1/N2")
    if "PFCP" in upper or row.get("pfcp.seid"):
        return "PFCP", "N4"
    if "GTP" in upper or row.get("gtp.teid"):
        return "GTP-U", "N3"
    if "HTTP2" in upper or row.get("http2.streamid"):
        return "HTTP/2", "SBI"
    if "ICMP" in upper or "IP" in upper:
        return protocol_text or "IP", "N6"
    return protocol_text or "Unknown", "Host"


def _procedure(info: str, protocol: str) -> str:
    lowered = info.lower()
    if "authentication" in lowered or "security mode" in lowered:
        return "authentication"
    if "registration" in lowered or "initialuemessage" in lowered or "initial context" in lowered:
        return "registration"
    if "pdu" in lowered or "session establishment" in lowered or (protocol == "PFCP" and "session" in lowered):
        return "pdu-session"
    if protocol in {"GTP-U", "ICMP", "IPv4", "IPv6"}:
        return "user-plane"
    if "ng setup" in lowered:
        return "registration"
    return "control-plane"


def _event_status(info: str) -> str:
    lowered = info.lower()
    if any(word in lowered for word in ("reject", "failure", "failed", "error", "timeout")):
        return "failure"
    if any(word in lowered for word in ("accept", "response", "successful", "complete")):
        return "success"
    if any(word in lowered for word in ("request", "command", "initial")):
        return "pending"
    return "info"


def parse_tshark_rows(text: str) -> list[dict[str, str]]:
    reader = csv.reader(io.StringIO(text), delimiter="\t", quotechar='"')
    rows = []
    for values in reader:
        if not values or not any(values):
            continue
        padded = [*values, *([""] * max(0, len(TSHARK_FIELDS) - len(values)))]
        rows.append(dict(zip(TSHARK_FIELDS, padded)))
    return rows


def _row_identifiers(
    row: dict[str, str],
    *,
    target_hash: str | None,
    mcc: str,
    mnc: str,
) -> tuple[list[dict[str, Any]], bool]:
    identifiers: list[dict[str, Any]] = []
    target_matched = False

    def add(kind: str, value: str, raw_for_hash: str | None = None) -> None:
        nonlocal target_matched
        if not value or value in {"0", "0x0000000000000000"}:
            return
        display = mask_subscriber(value) if kind == "supi" else value
        if kind == "gtpu_teid":
            display = _hex(value)
        identifiers.append({"kind": kind, "label": IDENTIFIER_LABELS[kind], "value": display})
        candidate = raw_for_hash or (value if kind == "supi" else None)
        if target_hash and candidate and subscriber_hash(candidate) == target_hash:
            target_matched = True

    for value in _values(row.get("e212.imsi")):
        if re.fullmatch(r"\d{14,15}", value):
            add("supi", value)
    for msin in _values(row.get("nas_5gs.mm.suci.msin")):
        if msin.isdigit():
            reconstructed = f"{mcc}{mnc}{msin}"
            add("supi", reconstructed)
    path = row.get("http2.headers.path") or ""
    if path:
        imsi_match = re.search(r"imsi-(\d{14,15})", path)
        if imsi_match:
            add("supi", imsi_match.group(1))
        suci_match = re.search(r"suci-0-(\d{3})-(\d{2,3})-\d{4}-0-0-(\d+)", path)
        if suci_match:
            add("supi", suci_match.group(1) + suci_match.group(2) + suci_match.group(3))
    for value in _values(row.get("ngap.RAN_UE_NGAP_ID")):
        add("ran_ue_ngap_id", value)
    for value in _values(row.get("ngap.AMF_UE_NGAP_ID")):
        add("amf_ue_ngap_id", value)
    for field in ("nas_5gs.pdu_session_id", "ngap.pDUSessionID"):
        for value in _values(row.get(field)):
            add("pdu_session_id", value)
    for value in _values(row.get("pfcp.seid")):
        add("pfcp_seid", value)
    for field in ("ngap.gTP_TEID", "pfcp.f_teid.teid", "pfcp.outer_hdr_creation.teid", "gtp.teid"):
        for value in _values(row.get(field)):
            add("gtpu_teid", value)
    for field in ("nas_5gs.sm.pdu_addr_inf_ipv4", "pfcp.ue_ip_addr_ipv4"):
        for value in _values(row.get(field)):
            add("ue_ip", value, value if target_hash else None)
    deduplicated = []
    seen = set()
    for item in identifiers:
        key = (item["kind"], item["value"])
        if key not in seen:
            seen.add(key)
            deduplicated.append(item)
    return deduplicated, target_matched



def _clean_nas_name(info: str) -> str:
    parts = [p.strip() for p in info.split(",") if p.strip()]
    for p in parts:
        low = p.lower()
        if any(
            key in low
            for key in [
                "registration request",
                "registration accept",
                "registration complete",
                "authentication request",
                "authentication response",
                "security mode command",
                "security mode complete",
                "pdu session",
                "deregistration",
            ]
        ):
            return p
    for p in parts:
        low = p.lower()
        if "initialuemessage" in low:
            return p
    return parts[-1] if parts else info


def _parse_sbi_call(row: dict[str, str], src_ip: str, dst_ip: str) -> tuple[str, str, str, str, str] | None:
    path = row.get("http2.headers.path") or ""
    if not path:
        return None
    method = row.get("http2.headers.method") or ""
    p = path.lower()

    source_nf = _sbi_nf_for_address(src_ip)
    target_nf = _sbi_nf_for_address(dst_ip, "NRF")
    procedure = "control-plane"
    interface = "SBI"
    smf_nf = "SMF-02" if "127.0.0.15" in (src_ip, dst_ip) else "SMF"

    if "nausf-auth" in p:
        source_nf, target_nf = "AMF", "AUSF"
        clean_msg = f"Nausf_UEAuthentication ({method or 'POST'})"
        procedure = "authentication"
    elif "nudm-ueau" in p:
        source_nf, target_nf = "AUSF", "UDM"
        clean_msg = "Nudm_UEAuthentication (Generate Auth Data)"
        procedure = "authentication"
    elif "nudm-sdm" in p:
        source_nf = smf_nf if {"127.0.0.4", "127.0.0.15"}.intersection((src_ip, dst_ip)) else "AMF"
        target_nf = "UDM"
        clean_msg = f"Nudm_SDM Subscription ({method or 'GET'})"
        procedure = "registration"
    elif "nudm-uecm" in p:
        source_nf = smf_nf if {"127.0.0.4", "127.0.0.15"}.intersection((src_ip, dst_ip)) else "AMF"
        target_nf = "UDM"
        clean_msg = f"Nudm_UECM Registration ({method or 'PUT'})"
        procedure = "registration"
    elif "nudr-" in p or "nbsf-" in p:
        source_nf = "UDM" if "nudr" in p else "SMF"
        target_nf = "UDR"
        clean_msg = "Nudr_DM / Nbsf_Management"
        procedure = "registration"
    elif "nsmf-pdusession" in p:
        source_nf, target_nf = "AMF", smf_nf
        action = "Release" if "release" in p else "Modify" if "modify" in p else "Create"
        clean_msg = f"Nsmf_PDUSession {action} Context"
        procedure = "pdu-session"
        # N11 is the 3GPP service-based interface between AMF and SMF.
        interface = "N11 / SBI"
    elif "npcf-smpolicycontrol" in p:
        source_nf, target_nf = smf_nf, "PCF"
        clean_msg = "Npcf_SMPolicyControl (SM Policy)"
        procedure = "pdu-session"
    elif "nnssf-nsselection" in p:
        source_nf, target_nf = "AMF", "NSSF"
        clean_msg = "Nnssf_NSSelection (PDU Session Slice)"
        interface = "N22 / SBI"
        procedure = "pdu-session"
    elif "nnrf-disc" in p:
        source_nf, target_nf = "AMF", "NRF"
        clean_msg = "Nnrf_NFDiscovery Request"
    elif "nnrf-nfm" in p:
        source_nf = _sbi_nf_for_address(src_ip)
        target_nf = "NRF"
        clean_msg = f"Nnrf_NFManagement ({method or 'HEARTBEAT'})"
    else:
        clean_msg = f"SBI {method} {path.split('/')[-1]}"

    return source_nf, target_nf, clean_msg, interface, procedure


def _is_registration_start(event: dict[str, Any]) -> bool:
    message = event["message"].lower()
    return event["procedure"] == "registration" and (
        "registration request" in message or "initialuemessage" in message
    )


def _is_old_context_cleanup(event: dict[str, Any]) -> bool:
    message = event["message"].lower()
    return event["protocol"] == "PFCP" and "session deletion" in message


def _scope_subscriber_events(
    events: list[dict[str, Any]], *, target_required: bool, requested: list[str]
) -> list[dict[str, Any]]:
    """Limit a subscriber trace to the registration epoch containing its identity.

    A controlled UE restart can first emit release/deletion messages for the old
    context. Those packets are valid evidence, but their SEIDs/TEIDs must not be
    attributed to the new subscriber session.
    """
    if not target_required:
        return events
    anchors = [index for index, event in enumerate(events) if event.get("_target_match")]
    if not anchors:
        return events
    anchor = anchors[0]
    starts = [index for index, event in enumerate(events[: anchor + 1]) if _is_registration_start(event)]
    start = starts[-1] if starts else 0
    establishment = next(
        (
            index
            for index, event in enumerate(events[start:], start=start)
            if "session establishment" in event["message"].lower()
            and event.get("_target_match")
        ),
        len(events),
    )
    scoped = []
    for index, event in enumerate(events[start:], start=start):
        message = event["message"].lower()
        if index < establishment and (
            _is_old_context_cleanup(event)
            or "release context" in message
            or "session deletion" in message
        ):
            continue
        if event["procedure"] in requested or event.get("identifiers"):
            scoped.append(event)
    return scoped


def _deduplicate_loopback_sbi(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the duplicate copy Linux `any` emits for loopback SBI traffic."""
    result: list[dict[str, Any]] = []
    last_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    for event in events:
        if event["protocol"] != "HTTP/2":
            result.append(event)
            continue
        key = (
            event["source_nf"],
            event["target_nf"],
            event["protocol"],
            event["message"],
            event["procedure"],
        )
        previous = last_by_key.get(key)
        if previous and event["_epoch"] - previous["_epoch"] <= 0.003:
            known = {
                (item["kind"], item["value"])
                for item in previous.get("identifiers", [])
            }
            previous.setdefault("identifiers", []).extend(
                item
                for item in event.get("identifiers", [])
                if (item["kind"], item["value"]) not in known
            )
            continue
        result.append(event)
        last_by_key[key] = event
    return result


def _rebuild_identifier_chain(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        for identifier in event.get("identifiers", []):
            key = (identifier["kind"], identifier["value"])
            if key in seen:
                continue
            seen.add(key)
            chain.append(
                {
                    **identifier,
                    "source": "packet",
                    "evidence_event_id": event["id"],
                    "packet_number": event.get("packet_number"),
                    "confidence": "direct",
                }
            )
    return chain


def _identifier_relations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: dict[tuple[str, str], set[str]] = {}
    for event in events:
        for identifier in event.get("identifiers", []):
            evidence.setdefault((identifier["kind"], identifier["value"]), set()).add(event["id"])

    relations = []
    ordered_kinds = list(IDENTIFIER_LABELS)
    for left_kind, right_kind in zip(ordered_kinds, ordered_kinds[1:]):
        left = [key for key in evidence if key[0] == left_kind]
        right = [key for key in evidence if key[0] == right_kind]
        for left_key in left:
            for right_key in right:
                shared = evidence[left_key] & evidence[right_key]
                if not shared:
                    continue
                relations.append(
                    {
                        "from": {"kind": left_key[0], "value": left_key[1]},
                        "to": {"kind": right_key[0], "value": right_key[1]},
                        "confidence": "direct",
                        "evidence": sorted(shared),
                    }
                )
    return relations

def build_trace_analysis(
    task: dict[str, Any],
    tshark_output: str,
    *,
    log_markers: dict[str, bool] | None = None,
    tshark_version: str | None = None,
) -> dict[str, Any]:
    if task.get("scenario_id") == "5g-sa":
        from app.services.trace_release16 import build_release16_analysis
        return build_release16_analysis(task, tshark_output, tshark_version=tshark_version)
    rows = parse_tshark_rows(tshark_output)
    defaults = task.get("scenario_defaults") or {"mcc": "999", "mnc": "70"}
    mcc, mnc = str(defaults.get("mcc", "999")), str(defaults.get("mnc", "70"))
    events: list[dict[str, Any]] = []
    target_matched = task.get("selector_kind") == "ue-ip" and not task.get("selector_hash")
    first_epoch: float | None = None

    for row in rows:
        info = sanitize_text(row.get("_ws.col.Info", "").strip())
        protocol, interface = _protocol_and_interface(row.get("_ws.col.Protocol", ""), row)
        if protocol == "SCTP" or (protocol == "PFCP" and "heartbeat" in info.lower()):
            continue
        if not info and protocol == "Unknown":
            continue
        if interface == "Host" and not row.get("http2.headers.path"):
            continue
        try:
            timestamp, epoch = _timestamp(row.get("frame.time_epoch", ""))
        except (TypeError, ValueError):
            continue
        if first_epoch is None:
            first_epoch = epoch
        identifiers, row_matches = _row_identifiers(
            row,
            target_hash=task.get("selector_hash"),
            mcc=mcc,
            mnc=mnc,
        )
        target_matched = target_matched or row_matches
        src_ip = _first_address(row.get("ip.src") or row.get("ipv6.src"))
        dst_ip = _first_address(row.get("ip.dst") or row.get("ipv6.dst"))
        source_nf = _nf_for_address(src_ip, protocol=protocol, source=True)
        target_nf = _nf_for_address(dst_ip, protocol=protocol, source=False)
        if "NGAP" in protocol or "NAS" in protocol:
            info = _clean_nas_name(info)
        procedure = _procedure(info, protocol)

        sbi = _parse_sbi_call(row, src_ip, dst_ip)
        if sbi:
            source_nf, target_nf, info, interface, procedure = sbi
            protocol = "HTTP/2"
        elif protocol == "HTTP/2" and any(
            marker in info.lower()
            for marker in (
                "data[",
                "headers[",
                "204 no content",
                "window_update",
                "settings",
            )
        ):
            continue

        frame_number = int(row["frame.number"]) if row.get("frame.number", "").isdigit() else None
        event_id = f"packet-{frame_number or len(events) + 1}"
        # Correlated UE radio access hop (NR-Uu)
        pre_hop = None
        post_hop = None
        lowered = info.lower()
        if "nas" in protocol.lower() or row.get("nas_5gs.mm.message_type") or "initialuemessage" in lowered:
            clean_nas = _clean_nas_name(info)
            if any(k in lowered for k in ["initialuemessage", "registration request", "authentication response", "security mode complete", "pdu session establishment request"]):
                pre_hop = ("UE", "gNB", clean_nas, "NR-Uu", "NR-Uu (Radio)")
            elif any(k in lowered for k in ["downlinknastransport", "authentication request", "security mode command", "registration accept", "initialcontextsetup"]):
                post_hop = ("gNB", "UE", clean_nas, "NR-Uu", "NR-Uu (Radio)")
        elif protocol in {"ICMP", "GTP-U"} or "ping" in lowered:
            if "request" in lowered:
                pre_hop = ("UE", "gNB", "Ping ICMP Echo Request", "NR-Uu", "NR-Uu (Radio)")
                source_nf, target_nf, interface = "gNB", "UPF", "N3"
            elif "reply" in lowered:
                source_nf, target_nf, interface = "UPF", "gNB", "N3"
                post_hop = ("gNB", "UE", "Ping ICMP Echo Reply", "NR-Uu", "NR-Uu (Radio)")

        if pre_hop:
            events.append(
                {
                    "id": f"{event_id}-radio-pre",
                    "ordinal": len(events) + 1,
                    "timestamp": timestamp,
                    "relative_ms": round((epoch - first_epoch) * 1000, 3),
                    "source_nf": pre_hop[0],
                    "target_nf": pre_hop[1],
                    "interface_3gpp": pre_hop[3],
                    "protocol": pre_hop[4],
                    "message": pre_hop[2],
                    "procedure": procedure,
                    "status": _event_status(info),
                    "packet_number": frame_number,
                    "evidence_type": "pcap",
                    "identifiers": identifiers,
                    "_epoch": epoch,
                    "_target_match": row_matches,
                }
            )

        events.append(
            {
                "id": event_id,
                "ordinal": len(events) + 1,
                "timestamp": timestamp,
                "relative_ms": round((epoch - first_epoch) * 1000, 3),
                "source_nf": source_nf,
                "target_nf": target_nf,
                "interface_3gpp": interface,
                "protocol": protocol,
                "message": info or protocol,
                "procedure": procedure,
                "status": _event_status(info),
                "packet_number": frame_number,
                "evidence_type": "pcap",
                "identifiers": identifiers,
                "_epoch": epoch,
                "_target_match": row_matches,
            }
        )

        if post_hop:
            events.append(
                {
                    "id": f"{event_id}-radio-post",
                    "ordinal": len(events) + 1,
                    "timestamp": timestamp,
                    "relative_ms": round((epoch - first_epoch) * 1000, 3),
                    "source_nf": post_hop[0],
                    "target_nf": post_hop[1],
                    "interface_3gpp": post_hop[3],
                    "protocol": post_hop[4],
                    "message": post_hop[2],
                    "procedure": procedure,
                    "status": _event_status(info),
                    "packet_number": frame_number,
                    "evidence_type": "pcap",
                    "identifiers": identifiers,
                    "_epoch": epoch,
                    "_target_match": row_matches,
                }
            )

    target_required = bool(task.get("selector_hash"))
    events = _scope_subscriber_events(
        events,
        target_required=target_required,
        requested=list(task.get("procedures") or []),
    )
    events = _deduplicate_loopback_sbi(events)
    if events:
        scoped_start = events[0]["_epoch"]
        for ordinal, event in enumerate(events, start=1):
            event["ordinal"] = ordinal
            event["relative_ms"] = round((event["_epoch"] - scoped_start) * 1000, 3)
    chain = _rebuild_identifier_chain(events)
    relations = _identifier_relations(events)
    for event in events:
        event.pop("_epoch", None)
        event.pop("_target_match", None)

    requested = task.get("procedures") or []
    log_markers = log_markers or {}
    procedure_results = []
    for procedure in requested:
        matching = [event for event in events if event["procedure"] == procedure]
        failed = any(event["status"] == "failure" for event in matching)
        if procedure == "registration":
            success = bool(log_markers.get("registration")) or any("registration accept" in event["message"].lower() for event in matching)
        elif procedure == "authentication":
            texts = " ".join(event["message"].lower() for event in matching)
            success = "authentication request" in texts and "authentication response" in texts and not failed
        elif procedure == "pdu-session":
            success = bool(log_markers.get("pdu_session")) or (
                any("session establishment request" in event["message"].lower() for event in matching)
                and any("session establishment response" in event["message"].lower() for event in matching)
            )
        else:
            success = any(event["protocol"] in {"GTP-U", "ICMP", "IPv4", "IPv6"} for event in matching)
        status = "failure" if failed else "success" if success else "partial" if matching else "not-observed"
        procedure_results.append(
            {
                "id": procedure,
                "label": {
                    "registration": "Registration",
                    "authentication": "5G-AKA Authentication",
                    "pdu-session": "PDU Session Establishment",
                    "user-plane": "User Plane",
                }.get(procedure, procedure),
                "status": status,
                "event_count": len(matching),
                "evidence": [event["id"] for event in matching[:8]],
                "log_confirmation": bool(log_markers.get(procedure.replace("-", "_"))),
            }
        )

    present_kinds = {item["kind"] for item in chain}
    required_kinds = {
        "supi",
        "ran_ue_ngap_id",
        "amf_ue_ngap_id",
        "pdu_session_id",
        "pfcp_seid",
        "gtpu_teid",
        "ue_ip",
    }
    missing = [IDENTIFIER_LABELS[kind] for kind in IDENTIFIER_LABELS if kind in required_kinds - present_kinds]
    has_failures = any(item["status"] == "failure" for item in procedure_results)
    successful_procedures = [item for item in procedure_results if item["status"] == "success"]
    if not events:
        result = "no_traffic"
    elif target_required and not target_matched:
        result = "inconclusive"
    elif has_failures:
        result = "procedure_failure"
    elif procedure_results and len(successful_procedures) == len(procedure_results):
        result = "success"
    else:
        result = "partial"

    diagnostics = []
    if missing:
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Correlación parcial",
                "detail": "No se observaron: " + ", ".join(missing),
                "recommendation": "Genere tráfico de usuario durante la captura o amplíe la duración de la tarea.",
            }
        )
    if target_required and not target_matched:
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Identidad objetivo no confirmada",
                "detail": "La captura contiene señalización, pero no una identidad que coincida criptográficamente con el selector.",
                "recommendation": "Fuerce un nuevo Registration del UE seleccionado dentro de la ventana de captura.",
            }
        )
    for item in procedure_results:
        if item["status"] == "failure":
            diagnostics.append(
                {
                    "severity": "critical",
                    "title": f"{item['label']} falló",
                    "detail": "Se observó un mensaje de rechazo o falla dentro del procedimiento.",
                    "recommendation": "Abra el evento asociado, revise la causa NAS/NGAP y contraste la configuración del suscriptor.",
                }
            )

    participants = []
    for name in ["UE", "gNB", "AMF", "AUSF", "UDM", "SMF", "UPF", "Data Network"]:
        if any(event["source_nf"] == name or event["target_nf"] == name for event in events):
            participants.append(name)

    return {
        "task_id": task["id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "outcome": result,
        "correlation_status": "complete" if not missing else "partial",
        "target": {
            "kind": task.get("selector_kind"),
            "masked": task.get("selector_masked"),
            "matched": target_matched if target_required else None,
        },
        "tshark_version": tshark_version,
        "identifiers": chain,
        "relations": relations,
        "procedures": procedure_results,
        "participants": participants,
        "events": events,
        "diagnostics": diagnostics,
        "stats": {
            "decoded_rows": len(rows),
            "correlated_events": len(events),
            "direct_identifiers": len(chain),
        },
    }
