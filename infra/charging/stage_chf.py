"""Stage tracked CHF sources in the isolated core workspace, without activation."""
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

import paramiko
from lab_command import get_settings

ROOT = Path(__file__).resolve().parents[2]
REMOTE = PurePosixPath("/home/emsadmin/maestro-charging/chf")
STATE = ROOT / ".work/chf-upload.json"


def main():
    files = subprocess.check_output(["git", "-C", str(ROOT), "ls-files",
                                     "--cached", "--others", "--exclude-standard", "chf"]).decode().splitlines()
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
        with client.open_sftp() as sftp:
            pending = []
            for path in files:
                relative = PurePosixPath(path).relative_to("chf")
                target = REMOTE / relative
                data = (ROOT / path).read_bytes().replace(b"\r\n", b"\n")
                digest = hashlib.sha256(data).hexdigest()
                try:
                    with sftp.open(str(target), "rb") as file:
                        existing = hashlib.sha256(file.read()).hexdigest()
                except FileNotFoundError:
                    existing = None
                if existing and existing not in {digest, previous.get(path)}:
                    raise SystemExit(f"Preserving unknown remote file: {target}")
                pending.append((path, target, data, digest))
            for path, target, data, digest in pending:
                parents = []
                parent = target.parent
                while parent != REMOTE.parent:
                    parents.append(parent)
                    parent = parent.parent
                for parent in reversed(parents):
                    try:
                        sftp.stat(str(parent))
                    except FileNotFoundError:
                        sftp.mkdir(str(parent), mode=0o700)
                with sftp.open(str(target), "wb") as file:
                    file.write(data)
                previous[path] = digest
            print(f"Staged {len(pending)} CHF source files; no service activated")
        STATE.write_text(json.dumps(previous, indent=2) + "\n")
    finally:
        client.close()


if __name__ == "__main__":
    main()
