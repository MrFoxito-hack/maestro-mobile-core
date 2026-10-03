"""Live acceptance test for the canonical PCF -> UDR Nudr policy path."""
import argparse
import asyncio
import json
import re
import time
import uuid
from pathlib import Path

from e2e_native import Lab, ROOT
from lab_command import get_settings
from app.services import terminal


def decode_http2_data(value):
    decoded = []
    for item in value.split(","):
        candidate = item.replace(":", "").strip()
        if candidate and re.fullmatch(r"[0-9a-fA-F]+", candidate) and len(candidate) % 2 == 0:
            try:
                decoded.append(bytes.fromhex(candidate).decode("utf-8", errors="replace"))
            except ValueError:
                pass
    return "".join(decoded)


async def verify():
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    ue = Lab(settings, settings.ue_ssh_port)
    tag = "pcf-canonical-e2e-" + uuid.uuid4().hex[:12]
    remote_dir = core.run(["mktemp", "-d", f"/home/emsadmin/{tag}-XXXXXX"]).strip()
    pcap = remote_dir + "/pcf-nudr.pcapng"
    unit = tag + "-capture"
    evidence = {"run": tag, "remote_pcap": pcap, "status": "RUNNING"}
    capture_started = False
    shadow_primary_was_active = False
    try:
        assert core.run(["systemctl", "is-active", "open5gs-pcfd"]).strip() == "active"
        assert core.run(["systemctl", "is-active", "open5gs-udrd"]).strip() == "active"
        evidence["ue02_service_state"] = ue.run(
            ["systemctl", "is-active", "ueransim-ue-02"], check=False).strip()
        evidence["shadow_primary_service_state"] = ue.run(
            ["systemctl", "is-active", "ueransim-ue-01"], check=False).strip()
        shadow_primary_was_active = evidence["shadow_primary_service_state"] == "active"

        ue.run(["systemctl", "stop", "ueransim-ue"], sudo=True)
        if shadow_primary_was_active:
            ue.run(["systemctl", "stop", "ueransim-ue-01"], sudo=True)
        await asyncio.sleep(3)
        core.start(
            unit,
            # tcpdump is kept as root explicitly because dumpcap drops to its
            # unprivileged account before opening a private evidence path.
            # Decoding is still performed offline with tshark below.
            ["/usr/bin/tcpdump", "-Z", "root", "-i", "lo", "-U", "-s", "0",
             "-w", pcap, "tcp port 7777 or tcp port 27017"],
            remote_dir,
            properties=("--property=Nice=10",),
        )
        capture_started = True
        for _ in range(10):
            if core.run(["systemctl", "is-active", unit], check=False).strip() == "active":
                break
            await asyncio.sleep(0.5)
        else:
            detail = core.run(
                ["systemctl", "status", unit, "--no-pager", "-l"],
                check=False,
            )
            raise RuntimeError("The SBI capture unit did not start: " + detail[-2000:])
        await asyncio.sleep(1)
        if core.run(["systemctl", "is-active", unit], check=False).strip() != "active":
            detail = core.run(
                ["systemctl", "status", unit, "--no-pager", "-l"], check=False)
            raise RuntimeError("The SBI capture unit exited early: " + detail[-2000:])
        # Open a fresh HTTP/2 connection after packet capture begins so tshark
        # sees the HPACK preface/table and can decode :status=201 reliably.
        core.run(["systemctl", "restart", "open5gs-pcfd"], sudo=True)
        for _ in range(20):
            if core.run(["systemctl", "is-active", "open5gs-pcfd"]).strip() == "active":
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("PCF did not recover after controlled restart")
        ue.run(["systemctl", "start", "ueransim-ue"], sudo=True)

        state = None
        for _ in range(35):
            # A second demonstrator UE may be active on this host.  Querying
            # without a SUPI makes nr-cli intentionally ambiguous.
            state = await terminal.snapshot("imsi-999700000000001")
            if state["registered"] and len(state["apn_sessions"]) == 2:
                break
            await asyncio.sleep(1)
        assert state and state["registered"] and len(state["apn_sessions"]) == 2
        await asyncio.sleep(4)
        core.run(["systemctl", "stop", unit], sudo=True, check=False)
        capture_started = False
        size = core.run(["stat", "-c", "%s", pcap], check=False).strip()
        if not size or not size.isdigit() or int(size) == 0:
            raise RuntimeError("SBI capture file is missing or empty: " + pcap)
        evidence["pcap_bytes"] = int(size)

        fields = core.run([
            "tshark", "-r", pcap, "-d", "tcp.port==7777,http2",
            "-Y", "http2.headers.path || http2.headers.status || http2.data.data",
            "-T", "fields", "-E", "separator=\t", "-E", "occurrence=a",
            "-e", "frame.number", "-e", "ip.src", "-e", "tcp.srcport",
            "-e", "ip.dst", "-e", "tcp.dstport", "-e", "tcp.stream",
            "-e", "http2.headers.method", "-e", "http2.headers.path",
            "-e", "http2.headers.status", "-e", "http2.data.data",
        ], timeout=90)
        records = []
        for line in fields.splitlines():
            parts = line.split("\t")
            parts += [""] * (10 - len(parts))
            payload = decode_http2_data(parts[9])
            records.append({
                "frame": parts[0], "src": parts[1], "sport": parts[2],
                "dst": parts[3], "dport": parts[4], "stream": parts[5],
                "method": parts[6], "path": parts[7], "status": parts[8],
                "payload": payload[:2000],
            })
        evidence["http2_record_count"] = len(records)

        nudr_requests = [r for r in records if r["method"] == "GET" and
                         "/nudr-dr/v1/policy-data/ues/" in r["path"] and
                         "/sm-data" in r["path"] and
                         "imsi-999700000000001" in r["path"]]
        assert len(nudr_requests) >= 2, "No Nudr SM policy GET for both DNNs"
        assert any("dnn=internet" in r["path"] for r in nudr_requests)
        assert any("dnn=corporate" in r["path"] for r in nudr_requests)
        udr_ok = [r for r in records if r["src"] == "127.0.0.20" and r["status"] == "200"]
        pcf_created = [r for r in records if r["src"] == "127.0.0.13" and r["status"] == "201"]
        assert udr_ok, "No HTTP 200 from UDR"
        assert len(pcf_created) >= 2, "No HTTP 201 from PCF for both DNN policies"

        payload_text = "\n".join(
            r["payload"] for r in records
            if r["src"] == "127.0.0.13" and r["payload"])
        assert re.search(r'"(?:5qi|_5qi)"\s*:\s*9', payload_text), \
            "PCF response did not expose 5QI=9 in the captured JSON"

        mongo_from_pcf = core.run([
            "tshark", "-r", pcap,
            "-Y", "ip.addr == 127.0.0.13 && tcp.port == 27017",
            "-T", "fields", "-e", "frame.number",
        ], timeout=60).strip().splitlines()
        assert not mongo_from_pcf, "Observed PCF-addressed MongoDB packets"

        evidence.update(
            status="PASS",
            terminal={
                "registered": state["registered"],
                "sessions": state["apn_sessions"],
            },
            nudr_requests=nudr_requests,
            http_statuses=sorted({r["status"] for r in records if r["status"]}),
            udr_http_200=len(udr_ok),
            pcf_http_201=len(pcf_created),
            pcf_mongodb_packets=0,
            pcf_decision_contains_5qi_9=True,
        )
    except Exception as exc:
        evidence.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if capture_started:
            core.run(["systemctl", "stop", unit], sudo=True, check=False)
        ue.run(["systemctl", "start", "ueransim-ue"], sudo=True, check=False)
        if shadow_primary_was_active:
            ue.run(["systemctl", "start", "ueransim-ue-01"], sudo=True, check=False)
        core.client.close()
        ue.client.close()
        local = ROOT / ".work" / f"{tag}.json"
        local.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"status": evidence["status"], "evidence": str(local),
                          "remote_pcap": pcap}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Use --execute to restart the primary lab UE and capture SBI traffic")
    asyncio.run(verify())


if __name__ == "__main__":
    main()
