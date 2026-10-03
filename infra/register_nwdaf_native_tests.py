"""Add the policer regression to Meson; no service changes."""
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
    try:
        target = base+'/src/upf/meson.build'
        original = host.read(target).decode()
        if 'nwdaf-qer-policer' in original:
            raise RuntimeError('Test already registered')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        host.write(target+'.pre-nwdaf-test-'+stamp, original)
        host.write(base+'/src/upf/test-qer-policer.c',
                   (ROOT/'nwdaf/native/test-qer-policer.c').read_bytes(), mode=0o644)
        updated = original+'''

# Local research extension, arithmetic regression independent of live NFs.
nwdaf_qer_test = executable('nwdaf-qer-policer', 'test-qer-policer.c',
    include_directories : libpfcp_inc, install : false)
test('nwdaf-qer-policer', nwdaf_qer_test, suite : 'nwdaf')
'''
        with host.client.open_sftp() as sftp:
            with sftp.open(target,'w') as output: output.write(updated.encode())
        print(host.run(['ninja','-C',base+'/build'],timeout=180))
        print(host.run(['meson','test','-C',base+'/build','--suite','nwdaf','--print-errorlogs'],timeout=120))
    finally:
        host.client.close()
