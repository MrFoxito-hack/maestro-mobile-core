"""Deploy verified ingestion fixes, retaining backups and rolling back on failure."""
from pathlib import Path
from datetime import datetime, timezone
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

FILES = ('app/main.py', 'app/ingestion/scheduler.py', 'app/ingestion/pm_adapter.py',
         'app/ingestion/export_pm.py',
         'app/ingestion/buckets.py', 'tests/test_scheduler.py', 'tests/test_buckets.py',
         'tests/test_pm_export.py')


def main():
    settings = get_settings()
    host = Lab(settings, settings.ssh_port)
    base = '/home/emsadmin/maestro-charging/nwdaf'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    originals = {}
    try:
        with host.client.open_sftp() as sftp:
            for name in FILES:
                remote = base+'/'+name
                try:
                    originals[name] = host.read(remote)
                except FileNotFoundError:
                    originals[name] = None
                if originals[name] is not None:
                    host.write(remote+'.pre-ingestion-'+stamp, originals[name])
                sftp.put(str(ROOT/'nwdaf'/name),remote)
        print(host.run([base+'/.venv/bin/python','-m','compileall','-q',base+'/app']))
        # Tests import app relative to the service working directory.
        print(host.run(['env','PYTHONPATH='+base,base+'/.venv/bin/python','-m','pytest',
                        base+'/tests/test_scheduler.py',base+'/tests/test_buckets.py',
                        base+'/tests/test_pm_export.py','-q'],timeout=120))
        host.run(['systemctl','restart','maestro-nwdaf'],sudo=True)
        for attempt in range(15):
            try:
                health = json.loads(host.run(['curl','--fail','--silent','--max-time','2',
                                             'http://127.0.0.1:8085/health']))
                if health.get('status') == 'ready':
                    print(json.dumps(health)); return
            except (RuntimeError, ValueError):
                pass
            time.sleep(1)
        raise RuntimeError('NWDAF health failed after ingestion update')
    except Exception:
        # Only restore files modified by this invocation; preserve all others.
        with host.client.open_sftp() as sftp:
            for name, data in originals.items():
                if data is not None:
                    with sftp.open(base+'/'+name,'w') as out:
                        out.write(data)
                # New additive modules are harmless with the old imports.
        host.run(['systemctl','restart','maestro-nwdaf'],sudo=True)
        raise
    finally:
        host.client.close()


if __name__ == '__main__':
    main()
