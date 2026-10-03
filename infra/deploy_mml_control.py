"""Activate the reviewed MML changes with backups and a timed rollback.

Run from backend using .venv/Scripts/python ../infra/deploy_mml_control.py
to review. --execute restarts PCF, CHF management and NWDAF; active PCF contexts
need UE re-registration. It does not restart SMF, AMF, UPFs or the CHF SBI.
"""
import argparse
import json
from pathlib import Path
import shlex
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

BASE = '/home/emsadmin/maestro-charging'
BINARY = BASE + '/open5gs/build/src/pcf/open5gs-pcfd'
DROPIN = '/etc/systemd/system/open5gs-pcfd.service.d/85-maestro-mml.conf'
FILES = ['chf/app/models.py', 'chf/app/repository.py', 'nwdaf/app/main.py']
UNITS = ['maestro-chf-management', 'maestro-nwdaf', 'open5gs-pcfd']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'files': FILES, 'binary': BINARY, 'dropin': DROPIN,
                          'restart': UNITS, 'rollback': 'independent 180 second timer'}, indent=2))
        return
    settings = get_settings()
    host = Lab(settings, settings.ssh_port)
    tag = 'mml-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    remote = BASE + '/' + tag
    evidence = ROOT / '.work' / tag
    evidence.mkdir(parents=True)
    changed = False
    healthy = False
    try:
        host.run(['test', '!', '-e', DROPIN])
        host.run(['mkdir', '-m', '0700', remote])
        pid = host.run(['systemctl', 'show', 'open5gs-pcfd', '--property=MainPID', '--value']).strip()
        host.run(['cp', '/proc/' + pid + '/exe', remote + '/pcf.before'], sudo=True)
        rollback = ['#!/bin/sh', 'set -eu', 'systemctl stop open5gs-pcfd',
                    shlex.join(['cp', remote + '/pcf.before', BINARY]),
                    shlex.join(['rm', '-f', DROPIN])]
        for index, name in enumerate(FILES):
            target = BASE + '/' + name
            original = host.read(target)
            host.write(remote + f'/{index}.before', original)
            host.write(remote + f'/{index}.after', (ROOT / name).read_bytes())
            rollback.append(shlex.join(['install', '-o', 'emsadmin', '-g', 'emsadmin', '-m', '0644',
                                        remote + f'/{index}.before', target]))
        rollback += ['systemctl daemon-reload', shlex.join(['systemctl', 'restart', *UNITS])]
        host.write(remote + '/rollback.sh', '\n'.join(rollback) + '\n', 0o700)
        host.write(remote + '/override.conf', '[Service]\nEnvironment=MAESTRO_MML_CONTROL=1\n')
        host.run(['systemd-run', '--unit=' + tag + '-rollback', '--on-active=180s',
                  '/bin/sh', remote + '/rollback.sh'], sudo=True)
        changed = True
        for index, name in enumerate(FILES):
            host.run(['install', '-o', 'emsadmin', '-g', 'emsadmin', '-m', '0644',
                      remote + f'/{index}.after', BASE + '/' + name], sudo=True)
        host.run(['install', '-m', '0644', remote + '/override.conf', DROPIN], sudo=True)
        host.run(['systemctl', 'daemon-reload'], sudo=True)
        host.run(['systemctl', 'restart', *UNITS], sudo=True)
        from app.services.pcf_control import control_request
        from app.services.charging import management_get
        from app.services.nwdaf import get_health
        for attempt in range(20):
            try:
                assert all(host.run(['systemctl', 'is-active', unit]).strip() == 'active' for unit in UNITS)
                actuator = control_request({'operation': 'status'})
                assert actuator['status'] == 'success'
                health = get_health()
                assert health['slice_maps']
                assert management_get('/admin/v1/accounts?limit=1&offset=0')['items'] is not None
                break
            except Exception:
                if attempt == 19:
                    raise
                time.sleep(1)
        healthy = True
        host.run(['systemctl', 'stop', tag + '-rollback.timer'], sudo=True)
        report = {'status': 'activated', 'actuator': actuator, 'backup': remote,
                  'rollback': 'sudo /bin/sh ' + remote + '/rollback.sh', 'restarted': UNITS}
        (evidence / 'activation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    finally:
        if changed and not healthy:
            host.run(['/bin/sh', remote + '/rollback.sh'], sudo=True)
            host.run(['systemctl', 'stop', tag + '-rollback.timer'], sudo=True, check=False)
        host.client.close()


if __name__ == '__main__':
    main()
