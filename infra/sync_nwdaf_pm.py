"""Explicit bounded PM snapshot bridge. Default one-shot; --watch repeats.

Does not select a slice capacity, rewrite PM history, or enable PCF actuation.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid
import paramiko
from nwdaf_upf_pm import collect, merge_snapshot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import get_settings
# Load exporter without shadowing the backend's `app` package.
import importlib.util
spec = importlib.util.spec_from_file_location('pm_export', ROOT/'nwdaf/app/ingestion/export_pm.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sync():
    settings = get_settings()
    source = settings.database_path
    if not source.is_absolute(): source = ROOT/'backend'/source
    ssh = paramiko.SSHClient(); ssh.load_system_host_keys()
    if not settings.ssh_strict_host_key: ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        history = ROOT/'backend/data/nwdaf-upf-pm.sqlite3'
        upf_status = collect(settings, history)
        ssh.connect(settings.testbed_host,port=settings.ssh_port,username=settings.ssh_user,
                    password=settings.ssh_password,look_for_keys=False,allow_agent=False,timeout=10)
        with tempfile.TemporaryDirectory(prefix='maestro-pm-') as temp:
            snapshot = Path(temp)/'pm.sqlite3'
            metadata = module.export_snapshot(source,snapshot)
            merge_snapshot(history,snapshot)
            metadata['upf_measurements'] = upf_status
            remote = '/home/emsadmin/maestro-charging/nwdaf/data/pm-source.sqlite3'
            staging = remote+'.'+uuid.uuid4().hex
            with ssh.open_sftp() as sftp:
                sftp.put(str(snapshot),staging)
                sftp.chmod(staging,0o600)
                sftp.posix_rename(staging,remote)
            print(json.dumps(metadata),flush=True)
    finally: ssh.close()


if __name__ == '__main__':
    while True:
        sync()
        if '--watch' not in sys.argv: break
        time.sleep(60)
