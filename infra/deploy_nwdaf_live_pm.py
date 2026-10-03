"""Deploy validated real-counter ingestion without changing database schemas."""
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend')); sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__=='__main__':
    settings=get_settings(); host=Lab(settings,settings.ssh_port)
    base='/home/emsadmin/maestro-charging/nwdaf'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        files=['app/main.py','app/ingestion/live_pm.py','tests/test_live_pm.py']
        for name in files:
            try: old=host.read(base+'/'+name)
            except FileNotFoundError: old=None
            if old is not None: host.write(base+'/'+name+'.pre-live-pm-'+stamp,old)
            with host.client.open_sftp() as sftp: sftp.put(str(ROOT/'nwdaf'/name),base+'/'+name)
        env=host.read(base+'/service.env')
        host.write(base+'/service.env.pre-live-pm-'+stamp,env)
        updated='\n'.join(line for line in env.decode().splitlines() if not line.startswith('NWDAF_CLOSED_LOOP_ENABLED='))+'\nNWDAF_CLOSED_LOOP_ENABLED=1\n'
        with host.client.open_sftp() as sftp,sftp.open(base+'/service.env','w') as out: out.write(updated)
        print(host.run(['sh','-c','cd '+base+' && .venv/bin/python -m pytest tests/test_live_pm.py tests/test_decision_ledger.py -q'],timeout=120))
        host.run(['systemctl','restart','maestro-nwdaf'],sudo=True)
        print('Real PM observation endpoint deployed; database schema unchanged.')
    finally: host.client.close()
