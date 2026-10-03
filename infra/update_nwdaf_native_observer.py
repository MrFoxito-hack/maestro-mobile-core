"""Update staged native observer with backup and compile, no live installation."""
from pathlib import Path
from datetime import datetime, timezone
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    settings = get_settings(); host = Lab(settings, settings.ssh_port)
    base = '/home/emsadmin/maestro-charging/open5gs'
    target = base+'/src/pcf/nwdaf-handler.c'
    try:
        original = host.read(target)
        host.write(target+'.pre-slice-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'), original)
        with host.client.open_sftp() as sftp:
            sftp.put(str(ROOT/'nwdaf/native/nwdaf-handler.c'), target)
        print(host.run(['ninja','-C',base+'/build','src/pcf/open5gs-pcfd'], timeout=120))
        print('Observer staged and compiled; installed PCF unchanged.')
    finally:
        host.client.close()
