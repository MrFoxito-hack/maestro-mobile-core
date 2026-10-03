from typing import Any


SUBSCRIBER_USER_PLANE_FILTER = "net 10.45.0.0/16 or net 10.46.0.0/16"

NODE_TRACE_CATALOG: dict[str, dict[str, Any]] = {
    "amf": {
        "label": "AMF",
        "description": "Access and Mobility Management Function",
        "interfaces": [
            {"id": "n2", "label": "N2 Interface Trace", "protocols": ["NGAP", "NGAP/NAS-5GS"]},
            {"id": "sbi", "label": "HTTP Interface Trace (SBI)", "protocols": ["HTTP/2"]},
        ],
        "filter": "sctp port 38412 or tcp port 7777",
    },
    "smf": {
        "label": "SMF",
        "description": "Session Management Function",
        "interfaces": [
            {"id": "n4", "label": "N4 Interface Trace", "protocols": ["PFCP"]},
            {"id": "sbi", "label": "HTTP Interface Trace (SBI/N11/N7/N10/Nchf)", "protocols": ["HTTP/2"]},
        ],
        "filter": "udp port 8805 or tcp port 7777 or tcp port 8081 or tcp port 18081",
    },
    "smf2": {
        "label": "SMF-02 (Corporate)",
        "description": "Session Management Function for SST=1 SD=000002",
        "interfaces": [
            {"id": "n4", "label": "N4 Interface Trace", "protocols": ["PFCP"]},
            {"id": "sbi", "label": "SBI/N11 Interface Trace", "protocols": ["HTTP/2"]},
        ],
        "filter": "(host 10.210.50.2 and udp port 8805) or (host 127.0.0.15 and tcp port 7777)",
    },
    "upf": {
        "label": "UPF",
        "description": "User Plane Function",
        "interfaces": [
            {"id": "n3", "label": "N3 Interface Trace", "protocols": ["GTP-U"]},
            {"id": "n4", "label": "N4 Interface Trace", "protocols": ["PFCP"]},
            {"id": "n6", "label": "N6 Interface Trace", "protocols": ["IPv4", "IPv6", "ICMP", "HTTP"]},
        ],
        "filter": "udp port 2152 or udp port 8805 or net 10.45.0.0/16 or net 10.46.0.0/16",
    },
    "nrf": {
        "label": "NRF", "description": "Network Repository Function",
        "interfaces": [{"id": "sbi", "label": "Nnrf Interface Trace", "protocols": ["HTTP/2"]}],
        "filter": "tcp port 7777",
    },
    "scp": {
        "label": "SCP", "description": "Service Communication Proxy",
        "interfaces": [{"id": "sbi", "label": "SBI Mesh Trace", "protocols": ["HTTP/2"]}],
        "filter": "tcp port 7777",
    },
}


TRACE_PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    "5g-sa": {
        "n2": {
            "label": "N2 · AMF ↔ gNodeB",
            "interface_3gpp": "N2",
            "device": "lo",
            "protocols": ["NGAP", "NAS-5GS", "SCTP"],
            "filter": "sctp port 38412",
            "nf_ids": ["amf", "gnb"],
            "procedures": ["NG Setup", "Registration", "Authentication", "PDU Session"],
        },
        "n3": {
            "label": "N3 · UPF ↔ gNodeB",
            "interface_3gpp": "N3",
            "device": "any",
            "protocols": ["GTP-U"],
            "filter": "udp port 2152",
            "nf_ids": ["upf", "gnb"],
            "procedures": ["PDU Session", "User Plane"],
        },
        "n4": {
            "label": "N4 · SMF ↔ UPF",
            "interface_3gpp": "N4",
            "device": "any",
            "protocols": ["PFCP"],
            "filter": "udp port 8805",
            "nf_ids": ["smf", "smf2", "upf", "upf2"],
            "procedures": ["PFCP Association", "PFCP Session"],
        },
        "n6": {
            "label": "N6 · UPF ↔ Data Network",
            "interface_3gpp": "N6",
            "device": "ogstun",
            "protocols": ["IPv4", "IPv6", "ICMP"],
            "filter": "ip or ip6",
            "nf_ids": ["upf"],
            "procedures": ["User Plane", "Internet Access"],
        },
        "sbi": {
            "label": "SBI · Service Based Interface",
            "interface_3gpp": "SBI",
            "device": "lo",
            "protocols": ["HTTP/2", "SBI"],
            "filter": "tcp port 7777 or tcp port 8081 or tcp port 18081",
            "nf_ids": ["amf", "smf", "smf2", "nrf", "scp", "ausf", "udm", "udr", "pcf", "bsf", "nssf", "chf"],
            "procedures": ["NF Discovery", "Authentication", "Session Management"],
        },
    },
    "4g-epc": {
        "s1-mme": {
            "label": "S1-MME · MME ↔ eNodeB",
            "interface_3gpp": "S1-MME",
            "device": "lo",
            "protocols": ["S1AP", "NAS-EPS", "SCTP"],
            "filter": "sctp port 36412",
            "nf_ids": ["mme", "enb"],
            "procedures": ["S1 Setup", "Attach", "Authentication"],
        },
        "s1-u": {
            "label": "S1-U · SGW-U ↔ eNodeB",
            "interface_3gpp": "S1-U",
            "device": "lo",
            "protocols": ["GTP-U"],
            "filter": "udp port 2152",
            "nf_ids": ["sgwu", "enb"],
            "procedures": ["EPS Bearer", "User Plane"],
        },
        "s11": {
            "label": "S11 · MME ↔ SGW-C",
            "interface_3gpp": "S11",
            "device": "lo",
            "protocols": ["GTPv2-C"],
            "filter": "udp port 2123",
            "nf_ids": ["mme", "sgwc"],
            "procedures": ["Create Session", "Modify Bearer"],
        },
        "s6a": {
            "label": "S6a · MME ↔ HSS",
            "interface_3gpp": "S6a",
            "device": "lo",
            "protocols": ["Diameter"],
            "filter": "tcp port 3868 or sctp port 3868",
            "nf_ids": ["mme", "hss"],
            "procedures": ["Authentication Information", "Update Location"],
        },
        "sgi": {
            "label": "SGi · PGW-U ↔ Data Network",
            "interface_3gpp": "SGi",
            "device": "ogstun",
            "protocols": ["IPv4", "IPv6", "ICMP"],
            "filter": "ip or ip6",
            "nf_ids": ["upf"],
            "procedures": ["User Plane", "Internet Access"],
        },
    },
}


SUBSCRIBER_PROCEDURES = [
    {"id": "registration", "label": "Registration", "interfaces": ["N1/N2"]},
    {"id": "authentication", "label": "5G-AKA Authentication", "interfaces": ["N1/N2", "SBI"]},
    {"id": "pdu-session", "label": "PDU Session Establishment", "interfaces": ["N1/N2", "N4", "N3"]},
]


def profile(scenario_id: str, capture_point: str) -> dict[str, Any]:
    try:
        return {"id": capture_point, **TRACE_PROFILES[scenario_id][capture_point]}
    except KeyError as exc:
        raise KeyError(f"Punto de captura no soportado: {scenario_id}/{capture_point}") from exc


def public_profiles(scenario_id: str, components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {component["id"]: component for component in components}
    result = []
    for point_id, item in TRACE_PROFILES.get(scenario_id, {}).items():
        node_ids = sorted({by_id[nf]["node_id"] for nf in item["nf_ids"] if nf in by_id})
        result.append(
            {
                "id": point_id,
                "label": item["label"],
                "capture_agent_id": "primary",
                "interface_3gpp": item["interface_3gpp"],
                "device_label": item["device"],
                "protocols": item["protocols"],
                "nf_ids": item["nf_ids"],
                "node_ids": node_ids,
                "procedures": item["procedures"],
            }
        )
    return result


def subscriber_capture_profile(include_sbi: bool) -> dict[str, Any]:
    ports = ["sctp port 38412", "udp port 8805", "udp port 2152"]
    interfaces = ["N1/N2", "N4", "N3"]
    protocols = ["NGAP", "NAS-5GS", "PFCP", "GTP-U"]
    if include_sbi:
        ports.extend(["tcp port 7777", "tcp port 8081", "tcp port 18081"])
        interfaces.append("SBI")
        protocols.extend(["HTTP/2", "SBI"])
    return {
        "id": "subscriber-5g",
        "label": "Subscriber E2E · N1/N2 + N4 + N3" + (" + SBI" if include_sbi else ""),
        "interface_3gpp": interfaces,
        "device": "any",
        "protocols": protocols,
        "filter": " or ".join(ports),
        "nf_ids": ["ue", "gnb", "amf", "ausf", "udm", "smf", "upf"] + (["chf"] if include_sbi else []),
    }
