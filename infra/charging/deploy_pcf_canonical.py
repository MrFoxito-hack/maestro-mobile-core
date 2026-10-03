"""Apply the reviewed PCF/UDR source patch to the isolated thesis Core VM.

The script creates a root-owned backup of the affected source/configuration
files before applying the patch.  It never resets unrelated Open5GS changes.
"""
import argparse
import json
import subprocess
import uuid

from e2e_native import Lab, ROOT
from lab_command import get_settings


REPO = "/home/emsadmin/maestro-charging/open5gs"
FILES = [
    "src/pcf/context.h",
    "src/pcf/context.c",
    "src/pcf/nudr-handler.c",
    "src/pcf/init.c",
    "src/pcf/meson.build",
    "src/pcf/sbi-path.c",
    "src/pcf/npcf-handler.c",
    "src/udr/nudr-handler.c",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Use --execute to patch the isolated Core VM")

    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    tag = "pcf-canonical-" + uuid.uuid4().hex[:12]
    source_repo = ROOT / ".work" / "open5gs"
    remote_patch = f"/home/emsadmin/{tag}.patch"
    backup = f"/home/emsadmin/{tag}-backup"
    result = {"tag": tag, "backup": backup, "files": FILES}

    try:
        patch_data = subprocess.check_output(
            ["git", "diff", "--", *FILES], cwd=source_repo)
        if not patch_data:
            raise RuntimeError("No reviewed PCF/UDR changes found locally")
        core.write(remote_patch, patch_data, 0o600)
        remote_changes = core.run(
            ["git", "-C", REPO, "diff", "--", *FILES], check=False)
        if remote_changes.strip():
            raise RuntimeError("Remote PCF/UDR source already has unreviewed changes")
        core.run(["git", "-C", REPO, "apply", "--check", remote_patch])
        core.run(["mkdir", "-m", "700", backup])
        for relative in FILES:
            target = backup + "/" + relative.replace("/", "__")
            core.run(["cp", "--preserve=mode,timestamps", REPO + "/" + relative, target])
        core.run(["cp", "--preserve=mode,timestamps", "/etc/open5gs/pcf.yaml", backup + "/pcf.yaml"], sudo=True)
        core.run(["git", "-C", REPO, "apply", remote_patch])
        result["status"] = "APPLIED"
        print(json.dumps(result, indent=2))
    finally:
        core.client.close()


if __name__ == "__main__":
    main()
