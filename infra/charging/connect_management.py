"""Provision a loopback management listener on the live SBI database only.

Does not change the SMF, UPF, charging accounts or existing SBI process.
Secrets are generated in memory, kept mode 0600 on the VM and never printed.
Run after stage_chf.py from backend using its venv.
"""
import json
import secrets
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import get_settings

ROOT = "/home/emsadmin/maestro-charging"
UNIT = "maestro-chf-management.service"


def main():
    settings = get_settings()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(settings.testbed_host, port=settings.ssh_port, username=settings.ssh_user,
                   password=settings.ssh_password,
                   key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None,
                   look_for_keys=False, allow_agent=False, timeout=10)

    def run(command, script=None, sudo=False):
        stdin, out, err = client.exec_command(("sudo -S -p '' " if sudo else "") + command, timeout=30)
        if sudo and settings.ssh_password:
            stdin.write(settings.ssh_password + "\n")
        if script:
            stdin.write(script)
        stdin.flush()
        stdin.channel.shutdown_write()
        result = out.read().decode()
        if out.channel.recv_exit_status():
            raise RuntimeError("Management provisioning failed (details withheld to protect credentials)")
        return result

    try:
        # Inspect only the active application's database path, never credentials.
        database = json.loads(run("python3 -", '''import pathlib,json
paths=set()
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  cmd=(p/'cmdline').read_bytes()
  if b'hypercorn' not in cmd or b'app.main:app' not in cmd: continue
  env=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\\0') if b'=' in x)
  value=env.get(b'CHF_DATABASE_PATH')
  if value: paths.add(value.decode())
 except (OSError,ValueError): pass
if len(paths)!=1: raise SystemExit(2)
print(json.dumps(paths.pop()))
'''))
        if database != ROOT + "/charging.sqlite3":
            raise RuntimeError("Live SBI database differs from reviewed path; deployment stopped")
        with client.open_sftp() as sftp:
            sftp.stat(database)  # Never create or choose an alternative database.
            directory = ROOT + "/management"
            try:
                sftp.mkdir(directory, 0o700)
            except OSError:
                sftp.stat(directory)
            env_path = directory + "/management.env"
            try:
                sftp.stat(env_path)
                with sftp.open(env_path) as file:
                    if f"CHF_DATABASE_PATH={database}\n" not in file.read().decode():
                        raise RuntimeError("Existing management environment differs")
                sftp.stat(directory + "/reader.token")
            except FileNotFoundError:
                admin, reader = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
                with sftp.open(env_path, "wx") as file:
                    file.write(f"CHF_DATABASE_PATH={database}\nCHF_ADMIN_TOKEN={admin}\nCHF_READER_TOKEN={reader}\n")
                sftp.chmod(env_path, 0o600)
                with sftp.open(directory + "/reader.token", "wx") as file:
                    file.write(reader)
                sftp.chmod(directory + "/reader.token", 0o600)
            unit = Path(__file__).with_name(UNIT).read_text(encoding="utf-8")
            target = "/etc/systemd/system/" + UNIT
            try:
                with sftp.open(target) as file:
                    if file.read().decode() != unit:
                        raise RuntimeError("Existing service differs; refusing to overwrite")
            except FileNotFoundError:
                staging = directory + "/" + UNIT
                with sftp.open(staging, "w") as file:
                    file.write(unit)
                run(f"install -o root -g root -m 644 {staging} {target}", sudo=True)
        run("systemctl daemon-reload", sudo=True)
        run("systemctl start " + UNIT, sudo=True)
        print(run("systemctl is-active " + UNIT).strip())
        print("Loopback management linked to live charging.sqlite3; SBI and NFs unchanged")
    finally:
        client.close()


if __name__ == "__main__":
    main()
