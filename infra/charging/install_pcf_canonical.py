"""Install and activate the canonical PCF/UDR build on the thesis Core VM."""
import argparse
import hashlib
import json
import re
import uuid

from e2e_native import Lab, BUILD
from lab_command import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Use --execute to install the reviewed binaries")
    if not re.fullmatch(r"/home/emsadmin/pcf-canonical-[a-f0-9]{12}-backup", args.backup):
        parser.error("Unexpected backup directory")

    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    tag = "pcf-install-" + uuid.uuid4().hex[:12]
    stage_config = f"/home/emsadmin/{tag}-pcf.yaml"
    build_pcf = BUILD + "/src/pcf/open5gs-pcfd"
    build_udr = BUILD + "/src/udr/open5gs-udrd"
    result = {"tag": tag, "backup": args.backup}
    mutated = False

    try:
        links = core.run(["ldd", build_pcf])
        if re.search(r"libmongo|libbson", links, re.I):
            raise RuntimeError("PCF still links to a MongoDB/BSON library")

        config = core.read("/etc/open5gs/pcf.yaml")
        updated, count = re.subn(
            rb"(?m)^([ \t]*)db_uri:[^\r\n]*$",
            rb"\1# db_uri removed: PCF consumes subscriber policy over Nudr",
            config,
        )
        if count != 1:
            raise RuntimeError(f"Expected one active db_uri in pcf.yaml, found {count}")
        core.write(stage_config, updated, 0o600)
        core.run(["cp", "--preserve=mode,timestamps", "/usr/bin/open5gs-pcfd",
                  args.backup + "/open5gs-pcfd"], sudo=True)
        core.run(["cp", "--preserve=mode,timestamps", "/usr/bin/open5gs-udrd",
                  args.backup + "/open5gs-udrd"], sudo=True)
        mutated = True
        core.run(["install", "-o", "root", "-g", "root", "-m", "0644",
                  stage_config, "/etc/open5gs/pcf.yaml"], sudo=True)
        core.run(["install", "-o", "root", "-g", "root", "-m", "0755",
                  build_pcf, "/usr/bin/open5gs-pcfd"], sudo=True)
        core.run(["install", "-o", "root", "-g", "root", "-m", "0755",
                  build_udr, "/usr/bin/open5gs-udrd"], sudo=True)
        core.run(["systemctl", "daemon-reload"], sudo=True)
        core.run(["systemctl", "restart", "open5gs-udrd", "open5gs-pcfd"], sudo=True)
        if core.run(["systemctl", "is-active", "open5gs-udrd"]).strip() != "active":
            raise RuntimeError("UDR did not become active")
        if core.run(["systemctl", "is-active", "open5gs-pcfd"]).strip() != "active":
            raise RuntimeError("PCF did not become active")

        installed = core.read("/usr/bin/open5gs-pcfd")
        result.update(
            status="ACTIVE",
            pcf_sha256=hashlib.sha256(installed).hexdigest(),
            db_uri_removed=True,
        )
        print(json.dumps(result, indent=2))
    except Exception:
        if mutated:
            core.run(["install", "-o", "root", "-g", "root", "-m", "0644",
                      args.backup + "/pcf.yaml", "/etc/open5gs/pcf.yaml"], sudo=True, check=False)
            core.run(["install", "-o", "root", "-g", "root", "-m", "0755",
                      args.backup + "/open5gs-pcfd", "/usr/bin/open5gs-pcfd"], sudo=True, check=False)
            core.run(["install", "-o", "root", "-g", "root", "-m", "0755",
                      args.backup + "/open5gs-udrd", "/usr/bin/open5gs-udrd"], sudo=True, check=False)
            core.run(["systemctl", "daemon-reload"], sudo=True, check=False)
            core.run(["systemctl", "restart", "open5gs-udrd", "open5gs-pcfd"],
                     sudo=True, check=False)
        raise
    finally:
        core.client.close()


if __name__ == "__main__":
    main()
