"""Upload only reviewed C patch files to the isolated, pinned core source tree.

Never installs binaries or changes systemd. Refuses foreign edits on the VM.
Run from backend/ so the existing lab configuration supplies SSH credentials.
"""
import hashlib
import json
import subprocess
from pathlib import Path

import paramiko

from lab_command import get_settings

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".work/open5gs"
STATE = ROOT / ".work/native-upload.json"
REMOTE = "/home/emsadmin/maestro-charging/open5gs"
PIN = "157f611a530e292e40ec50f9d23f0ef5d4fcd6a6"


def git(*args):
    return subprocess.check_output(["git", "-C", str(SOURCE), *args],
                                   stderr=subprocess.PIPE)


def main():
    if git("rev-parse", "HEAD").decode().strip() != PIN:
        raise SystemExit("Local source does not match pinned upstream")
    paths = set(git("diff", "--name-only", "HEAD").decode().splitlines())
    paths.update(git("ls-files", "--others", "--exclude-standard").decode().splitlines())
    for path in paths:
        if ".." in Path(path).parts or not (path.startswith(("src/smf/", "src/upf/", "lib/sbi/", "tests/"))
                                           or path == "lib/pfcp/handler.c"):
            raise SystemExit(f"Outside native patch scope: {path}")
    settings = get_settings()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(settings.testbed_host, port=settings.ssh_port,
                   username=settings.ssh_user, password=settings.ssh_password,
                   key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                   look_for_keys=False, allow_agent=False, timeout=10)
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        _, out, _ = client.exec_command(f"git -C {REMOTE} rev-parse HEAD")
        if out.read().decode().strip() != PIN:
            raise SystemExit("Remote source does not match pinned upstream")
        with client.open_sftp() as sftp:
            pending = []
            for path in sorted(paths):
                data = (SOURCE / path).read_bytes().replace(b"\r\n", b"\n")
                digest = hashlib.sha256(data).hexdigest()
                try:
                    with sftp.open(f"{REMOTE}/{path}", "rb") as file:
                        remote_hash = hashlib.sha256(file.read().replace(b"\r\n", b"\n")).hexdigest()
                except FileNotFoundError:
                    remote_hash = None
                if remote_hash:
                    try:
                        baseline = hashlib.sha256(git("show", f"HEAD:{path}")).hexdigest()
                    except subprocess.CalledProcessError:
                        baseline = None
                    if remote_hash not in {baseline, previous.get(path), digest}:
                        raise SystemExit(f"Preserving unknown remote edit: {path}")
                pending.append((path, data, digest, remote_hash))
            # Validate ALL targets before uploading any file.
            for path, data, digest, remote_hash in pending:
                if digest != remote_hash:
                    with sftp.open(f"{REMOTE}/{path}", "wb") as file:
                        file.write(data)
                previous[path] = digest
                print(f"{digest[:12]} {path}")
        # Generated transfer manifest, not project source.
        STATE.write_text(json.dumps(previous, indent=2) + "\n")
    finally:
        client.close()


if __name__ == "__main__":
    main()
