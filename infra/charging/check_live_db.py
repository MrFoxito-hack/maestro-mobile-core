import sys
from pathlib import Path
import paramiko
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import get_settings

def main():
    settings = get_settings()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(settings.testbed_host, port=settings.ssh_port,
                   username=settings.ssh_user, password=settings.ssh_password,
                   look_for_keys=False, allow_agent=False, timeout=10)
    script = """
import sqlite3, json
c = sqlite3.connect('/home/emsadmin/maestro-charging/charging.sqlite3')
c.row_factory = sqlite3.Row
res = c.execute("UPDATE charging_sessions SET status='RELEASED', reserved_bytes=0, released_at=datetime('now'), closure_reason='EXPIRED' WHERE status='OPEN' AND valid_until < datetime('now')")
c.commit()
print('STALE SESSIONS RELEASED:', res.rowcount)
accounts = [dict(r) for r in c.execute('SELECT * FROM charging_accounts')]
print('ACCOUNTS_AFTER_SWEEP:', json.dumps(accounts, indent=2))
"""
    import shlex
    stdin, stdout, stderr = client.exec_command(f"python3 -c {shlex.quote(script)}")
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()
    print("CODE:", code)
    print("OUT:\n", out)
    if err:
        print("ERR:\n", err)
    client.close()

if __name__ == '__main__':
    main()
