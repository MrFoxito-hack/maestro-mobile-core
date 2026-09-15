"""Run as root inside an audited cloned guest, with its expected IPv4 address.

Only fixes the inherited enp0s8 address. NAT and all unrelated settings stay intact.
Backups are deliberately not overwritten. Use before the baseline experiment.
"""
import ipaddress
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml


def main():
    expected = str(ipaddress.IPv4Interface(sys.argv[1]))
    if expected not in {"10.210.50.9/24", "10.210.50.10/24", "10.210.50.11/24"}:
        raise SystemExit("Not an audited cloned guest address")
    config = Path("/etc/netplan/50-cloud-init.yaml")
    data = yaml.safe_load(config.read_text())
    ethernet = data["network"]["ethernets"]["enp0s8"]
    if ethernet["addresses"] not in (["10.210.50.8/24"], [expected]):
        raise SystemExit("Unexpected network configuration; refusing to overwrite")
    disable = Path("/etc/cloud/cloud.cfg.d/99-maestro-disable-network-config.cfg")
    desired = "network: {config: disabled}\n"
    if disable.exists() and disable.read_text() != desired:
        raise SystemExit("Cloud-init override already exists with another value")
    backup = config.with_suffix(".yaml.maestro-charging-baseline.bak")
    if not backup.exists():
        shutil.copy2(config, backup)
    ethernet["addresses"] = [expected]
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    config.chmod(0o600)
    disable.write_text(desired)
    subprocess.run(["netplan", "generate"], check=True)
    subprocess.run(["netplan", "apply"], check=True)
    deadline = time.monotonic() + 10
    actual = []
    while time.monotonic() < deadline:
        addresses = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr", "show", "enp0s8"]))
        actual = [f"{v['local']}/{v['prefixlen']}" for entry in addresses for v in entry['addr_info']]
        if actual == [expected]:
            break
        time.sleep(0.2)  # bounded readiness polling, not a fixed boot delay
    if actual != [expected]:
        raise SystemExit(f"Address verification failed: {actual}")
    print(json.dumps({"address": expected, "backup": str(backup), "verified": True}))


if __name__ == "__main__":
    main()
