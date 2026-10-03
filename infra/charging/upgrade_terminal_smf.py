"""Upgrade only the versioned SMF; preserve config, tokens, quota and old artifacts."""
import argparse
import asyncio
import hashlib
import re
import time
from e2e_native import Lab, BUILD, ROOT
from lab_command import get_settings
from app.services import terminal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required')
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    ue = Lab(settings, settings.ue_ssh_port)
    drop = '/etc/systemd/system/open5gs-smfd.service.d/90-maestro-charging.conf'
    original = core.read(drop).decode()
    match = re.search(r'ExecStart=(/opt/maestro-charging/maestro-charging-[a-f0-9]+)/open5gs-smfd', original)
    if not match:
        raise RuntimeError('Not the managed native deployment; refusing overwrite')
    previous = match[1]
    binary = core.read(BUILD + '/src/smf/open5gs-smfd')
    tag = 'maestro-charging-' + hashlib.sha256(binary).hexdigest()[:12]
    target = '/opt/maestro-charging/' + tag
    if target == previous:
        print('Already deployed')
        return
    stage = core.run(['mktemp', '-d', '/home/emsadmin/smf-upgrade-XXXXXX']).strip()
    timer = tag + '-rollback'
    armed = False
    try:
        core.run(['systemctl', 'is-active', 'open5gs-smfd'])
        ue.run(['systemctl', 'is-active', 'ueransim-ue'])
        core.run(['test', '!', '-e', target])
        core.write(stage + '/old.conf', original)
        core.write(stage + '/new.conf', original.replace(previous, target))
        core.write(stage + '/open5gs-smfd', binary, 0o700)
        core.write(stage + '/rollback.sh', '#!/bin/sh\nset -eu\ninstall -m 644 ' + stage + '/old.conf ' + drop + '\nsystemctl daemon-reload\nsystemctl restart open5gs-smfd\n', 0o700)
        core.run(['chown', '-R', 'root:root', stage], sudo=True)
        core.run(['cp', '-a', previous, target], sudo=True)
        core.run(['install', '-o', 'root', '-g', 'open5gs', '-m', '750', stage + '/open5gs-smfd', target + '/open5gs-smfd'], sudo=True)
        dependencies = core.run(['env', 'LD_LIBRARY_PATH=' + target + '/lib', 'ldd', target + '/open5gs-smfd'], sudo=True)
        if 'not found' in dependencies:
            raise RuntimeError('Unresolved dependency')
        # Request normal NAS cleanup before restart; never wipe CHF contexts.
        asyncio.run(terminal.airplane(True))
        time.sleep(4)
        core.run(['systemd-run', '--unit=' + timer, '--on-active=120s', '/bin/sh', stage + '/rollback.sh'], sudo=True)
        armed = True
        core.run(['install', '-m', '644', stage + '/new.conf', drop], sudo=True)
        core.run(['systemctl', 'daemon-reload'], sudo=True)
        core.run(['systemctl', 'restart', 'open5gs-smfd'], sudo=True)
        time.sleep(3)
        core.run(['systemctl', 'is-active', 'open5gs-smfd'])
        if core.run(['systemctl', 'show', 'open5gs-smfd', '-p', 'NRestarts', '--value']).strip() != '0':
            raise RuntimeError('Restart loop')
        ue.run(['systemctl', 'start', 'ueransim-ue'], sudo=True)
        ready = False
        for _ in range(20):
            state = asyncio.run(terminal.snapshot())
            if state['registered'] and state['interfaces']:
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError('UE did not recover')
        core.run(['systemctl', 'stop', timer + '.timer'], sudo=True)
        armed = False
        print('SMF deployed: ' + target)
        print('Rollback: sudo /bin/sh ' + stage + '/rollback.sh')
        (ROOT / '.work' / (tag + '-upgrade.txt')).write_text('Previous: ' + previous + '\nCurrent: ' + target + '\nRollback: ' + stage + '/rollback.sh\n')
    except BaseException:
        if armed:
            core.run(['/bin/sh', stage + '/rollback.sh'], sudo=True, check=False)
            core.run(['systemctl', 'stop', timer + '.timer'], sudo=True, check=False)
        raise
    finally:
        ue.run(['systemctl', 'start', 'ueransim-ue'], sudo=True, check=False)
        ue.client.close()
        core.client.close()


if __name__ == '__main__':
    main()
