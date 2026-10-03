"""Install an isolated, bounded DN probe; no Open5GS or firewall changes."""
import argparse
import hashlib
from pathlib import Path
from e2e_native import Lab
from lab_command import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute required')
    source = Path(__file__).with_name('terminal_origin.py').read_bytes()
    digest = hashlib.sha256(source).hexdigest()[:12]
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    try:
        directory = '/opt/maestro-terminal-origin-' + digest
        stage = core.run(['mktemp', '-d', '/home/emsadmin/terminal-origin-XXXXXX']).strip()
        core.write(stage + '/origin.py', source)
        core.run(['install', '-d', '-m', '755', directory], sudo=True)
        core.run(['install', '-m', '644', stage + '/origin.py', directory + '/origin.py'], sudo=True)
        unit = '[Unit]\nDescription=MAEstro private N6 probe origin\nAfter=network-online.target\n\n[Service]\n' + (
            'DynamicUser=yes\nExecStart=/usr/bin/python3 ' + directory + '/origin.py\n'
            'Restart=on-failure\nRestartSec=3\nNoNewPrivileges=yes\nPrivateTmp=yes\n'
            'ProtectSystem=strict\nProtectHome=yes\nMemoryMax=64M\nTasksMax=16\n'
            'RestrictAddressFamilies=AF_INET\n\n[Install]\nWantedBy=multi-user.target\n')
        core.write(stage + '/unit', unit)
        target = '/etc/systemd/system/maestro-terminal-origin.service'
        # Refuse to replace an unrelated unit.
        try:
            old = core.read(target)
        except FileNotFoundError:
            old = b''
        if old and b'Description=MAEstro private N6 probe origin' not in old:
            raise RuntimeError('Existing unit is not owned by this installer')
        core.run(['install', '-m', '644', stage + '/unit', target], sudo=True)
        core.run(['systemctl', 'daemon-reload'], sudo=True)
        core.run(['systemctl', 'enable', '--now', 'maestro-terminal-origin'], sudo=True)
        core.run(['systemctl', 'restart', 'maestro-terminal-origin'], sudo=True)
        routing = '/etc/systemd/system/maestro-terminal-n6-route.service'
        try:
            route_unit = core.read(routing)
        except FileNotFoundError:
            route_unit = b''
        if not route_unit:
            rules = core.run(['ip', '-j', 'rule', 'show'])
            import json
            if any(r.get('priority') == 18090 or r.get('table') == 18090 for r in json.loads(rules)):
                raise RuntimeError('Policy routing priority/table 18090 already in use')
            routes = core.run(['ip', 'route', 'show', 'table', '18090'], check=False)
            if routes.strip() and 'does not exist' not in routes:
                raise RuntimeError('Policy routing table not empty')
            route_unit = (
                '[Unit]\nDescription=MAEstro scoped N6 return route\nAfter=network-online.target\n'
                '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                'ExecStart=/usr/sbin/ip route add table 18090 10.45.0.0/16 via 10.210.50.8 dev enp0s8 onlink\n'
                'ExecStart=/usr/sbin/ip rule add priority 18090 from 10.210.50.1/32 to 10.45.0.0/16 ipproto tcp sport 18090 lookup 18090\n'
                'ExecStop=/usr/sbin/ip rule del priority 18090 from 10.210.50.1/32 to 10.45.0.0/16 ipproto tcp sport 18090 lookup 18090\n'
                'ExecStop=/usr/sbin/ip route del table 18090 10.45.0.0/16 via 10.210.50.8 dev enp0s8\n'
                '[Install]\nWantedBy=multi-user.target\n')
            core.write(stage + '/route-unit', route_unit)
            core.run(['install', '-m', '644', stage + '/route-unit', routing], sudo=True)
            core.run(['systemctl', 'daemon-reload'], sudo=True)
        elif b'Description=MAEstro scoped N6 return route' not in route_unit:
            raise RuntimeError('Foreign route unit')
        core.run(['systemctl', 'enable', '--now', 'maestro-terminal-n6-route'], sudo=True)
        print(core.run(['systemctl', 'is-active', 'maestro-terminal-origin']).strip())
        print('Rollback: sudo systemctl disable --now maestro-terminal-origin maestro-terminal-n6-route')
    finally:
        core.client.close()


if __name__ == '__main__':
    main()
