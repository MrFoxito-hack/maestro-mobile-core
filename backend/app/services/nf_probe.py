"""Read-only Linux probe. Executed in memory on the managed testbed, without root.

Input is the server-owned inventory (never a client-supplied command or URL).
Output is bounded JSON; no configuration contents or subscriber identifiers leave
the probe. No exporters, agents, or Prometheus server need installing on the VM.
"""
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def run(args, timeout=3):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("command unavailable")
    return result.stdout


def process(component):
    props = "MainPID,CPUUsageNSec,MemoryCurrent,NRestarts,TasksCurrent,ActiveState"
    data = dict(line.split("=", 1) for line in run([
        "systemctl", "show", component["unit"], "--property=" + props
    ]).splitlines() if "=" in line)
    values = {}
    for key, name, divisor in [("MemoryCurrent", "service_memory_mib", 1048576),
                                ("NRestarts", "auto_restarts", 1),
                                ("TasksCurrent", "tasks", 1)]:
        raw = data.get(key, "")
        if raw.isdigit() and int(raw) < 2**63:
            values[name] = int(raw) / divisor
    pid = int(data.get("MainPID", "0"))
    incarnation = None
    if pid:
        # /proc stat comm may contain spaces/parentheses. Fields start after it.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        start = int(fields[19]) / ticks
        incarnation = f"{Path('/proc/sys/kernel/random/boot_id').read_text().strip()}:{pid}:{fields[19]}"
        values.update(cpu_seconds=(int(fields[11]) + int(fields[12])) / ticks,
                      uptime_seconds=max(0, float(Path('/proc/uptime').read_text().split()[0]) - start),
                      threads=int(fields[17]), virtual_memory_mib=int(fields[20]) / 1048576,
                      rss_mib=int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / 1048576)
        try:
            values["open_fds"] = len(list(Path(f"/proc/{pid}/fd").iterdir()))
        except PermissionError:
            pass
    return values, incarnation


def exporter(component):
    import yaml
    for path in component.get("config_paths", []):
        if not path.startswith("/etc/open5gs/") or not Path(path).is_file():
            continue
        config = yaml.safe_load(Path(path).read_text()) or {}
        config_key = {"smf2": "smf", "upf2": "upf"}.get(component["id"], component["id"])
        settings = (config.get(config_key) or {}).get("metrics", {})
        servers = settings.get("server", []) if isinstance(settings, dict) else []
        if isinstance(servers, dict):
            servers = [servers]
        for server in servers or []:
            address = server.get("address", "127.0.0.1")
            if isinstance(address, list):
                address = address[0]
            address = str(address)
            # Only locally configured numeric addresses. Disable proxy use.
            ip = ipaddress.ip_address(address)
            if ip.is_unspecified:
                address = "::1" if ip.version == 6 else "127.0.0.1"
            host = f"[{address}]" if ip.version == 6 else address
            url = f"http://{host}:{int(server.get('port', 9090))}/metrics"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=2) as response:
                body = response.read(65537)
                if len(body) > 65536:
                    raise RuntimeError("metrics response exceeds 64 KiB")
                return body.decode(), "available"
    return "", "not_configured"


def inspect(component):
    result = {"id": component["id"], "values": {}, "metrics": ""}
    try:
        result["values"], result["incarnation"] = process(component)
    except Exception:
        result["process_status"] = "unavailable"
    try:
        result["metrics"], result["metrics_status"] = exporter(component)
    except Exception:
        result["metrics_status"] = "unreachable"
    result["observed_at"] = time.time()
    return result


def cli_counts(components):
    """Read aggregate gNB/UE status, never send raw IMSIs or UE lists back."""
    import yaml
    cli = Path("/home/emsadmin/UERANSIM/build/nr-cli")
    output = {}
    if not cli.exists() or not any(c["unit"].startswith("ueransim-") for c in components):
        return output
    try:
        nodes = run([str(cli), "--dump"]).splitlines()
    except Exception:
        return output
    # Inventory currently represents one gNB and one UE. Do not silently label
    # the last of multiple processes as if it represented the selected NF.
    for node in nodes[:2]:
        node = node.strip()
        if not node or len(node) > 128:
            continue
        nf = "gnb" if node.startswith("UERANSIM-gnb-") else "ue" if node.startswith("imsi-") else None
        if not nf:
            continue
        matches = [n for n in nodes if n.startswith("UERANSIM-gnb-" if nf == "gnb" else "imsi-")]
        if len(matches) != 1:
            continue
        try:
            data = yaml.safe_load(run([str(cli), node, "--exec", "status"], timeout=1))
            if not isinstance(data, dict):
                continue
            values = output.setdefault(nf, {})
            if nf == "gnb" and isinstance(data.get("is-ngap-up"), bool):
                values["ngap_connected"] = int(data["is-ngap-up"])
            if nf == "ue":
                mm = data.get("mm-state")
                if mm is not None:
                    values["registered"] = int(str(mm).split("/")[0] == "MM-REGISTERED")
                cm = data.get("cm-state")
                if cm is not None:
                    values["connected"] = int(cm == "CM-CONNECTED")
                sessions = data.get("pdu-sessions")
                if isinstance(sessions, list):
                    values["pdu_sessions"] = len(sessions)
        except Exception:
            continue
    return output


def probe(components):
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(inspect, components))
    return {"components": results, "cli": cli_counts(components)}


if __name__ == "__main__":
    print(json.dumps(probe(json.loads(sys.argv[1])), separators=(",", ":")))
