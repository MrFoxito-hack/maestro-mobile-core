"""Deploy ledger API with backups; never enable PCF policy actuation."""
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    settings = get_settings(); host = Lab(settings,settings.ssh_port)
    base = '/home/emsadmin/maestro-charging/nwdaf'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        with host.client.open_sftp() as f:
            for name in ['app/main.py','app/models.py','app/core/decision_ledger.py','tests/test_decision_ledger.py']:
                remote = base+'/'+name
                try:
                    with f.open(remote) as old: data=old.read()
                except FileNotFoundError: data=None
                if data is not None:
                    with f.open(remote+'.backup-'+stamp,'wx') as backup: backup.write(data)
                f.put(str(ROOT/'nwdaf'/name),remote)
        print(host.run([base+'/.venv/bin/python','-m','compileall','-q',base+'/app']))
        host.run(['systemctl','restart','maestro-nwdaf'],sudo=True)
        print('Ledger API deployed; actuation remains disabled')
    finally: host.client.close()
