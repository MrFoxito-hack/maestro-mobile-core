"""Stage the opt-in native observer and compile; does NOT install or restart PCF."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from app.core.config import get_settings


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    settings = get_settings()
    ssh = paramiko.SSHClient(); ssh.load_system_host_keys()
    if not settings.ssh_strict_host_key: ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(settings.testbed_host, port=settings.ssh_port, username=settings.ssh_user,
                password=settings.ssh_password, look_for_keys=False, allow_agent=False, timeout=10)
    base = '/home/emsadmin/maestro-charging/open5gs'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        with ssh.open_sftp() as sftp:
            def read(name):
                with sftp.open(base+'/src/pcf/'+name) as file: return file.read().decode()
            init = read('init.c'); meson = read('meson.build')
            if 'nwdaf-handler' in init or 'nwdaf-handler' in meson:
                raise RuntimeError('Observer already staged: inspect before reapplying')
            begin = '    ogs_fsm_init(&pcf_sm, pcf_state_initial, pcf_state_final, 0);'
            end = '    ogs_fsm_fini(&pcf_sm, 0);'
            assert init.count(begin) == 1 and init.count(end) == 1
            assert meson.count('    nudr-build.c') == 1
            changed = init.replace('#include "metrics.h"', '#include "metrics.h"\n#include "nwdaf-handler.h"')
            changed = changed.replace(begin, begin+'\n\n    if (pcf_nwdaf_open() != OGS_OK)\n        ogs_error("NWDAF observer initialization failed; PCF continues without actuation");')
            changed = changed.replace(end, '    pcf_nwdaf_close();\n'+end)
            # Backups preserve the existing canonical Nudr edits byte-for-byte.
            for name, original, updated in [('init.c',init,changed), ('meson.build',meson,meson.replace('    nudr-build.c','    nwdaf-handler.c\n    nudr-build.c'))]:
                with sftp.open(base+'/src/pcf/'+name+'.pre-nwdaf-'+stamp, 'wx') as file: file.write(original)
                with sftp.open(base+'/src/pcf/'+name, 'w') as file: file.write(updated)
            for name in ('nwdaf-handler.c','nwdaf-handler.h'):
                sftp.put(str(ROOT/'nwdaf/native'/name), base+'/src/pcf/'+name)
        _, stdout, stderr = ssh.exec_command('cd '+base+' && ninja -C build src/pcf/open5gs-pcfd', timeout=600)
        for line in stdout: print(line, end='', flush=True)
        print(stderr.read().decode())
        code = stdout.channel.recv_exit_status()
        if code: raise RuntimeError(f'Native build failed: {code}; staged source retained, installed PCF unchanged')
        print('Native observer compiled; NOT installed. Backup suffix: '+stamp)
    finally: ssh.close()


if __name__ == '__main__': main()
