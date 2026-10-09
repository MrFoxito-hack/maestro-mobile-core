"""Persist the existing SMF aliases across networkd carrier changes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from infra.charging.e2e_native import Lab, get_settings

CONFIG = '''# MAEstro existing multi-SMF addresses
[Network]
Address=10.210.50.2/24
Address=10.210.50.18/32

[Route]
Destination=10.210.50.22/32
Gateway=10.210.50.8
'''


def deploy(evidence):
    evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    host = Lab(settings, settings.ssh_port)
    directory = '/etc/systemd/network/10-netplan-enp0s8.network.d'
    target = directory + '/80-maestro-smf.conf'
    try:
        status = host.run(['networkctl', 'status', 'enp0s8', '--no-pager'], sudo=True)
        if 'Network File: /run/systemd/network/10-netplan-enp0s8.network' not in status:
            raise ValueError('unexpected_core_network_owner')
        try:
            previous = host.read(target)
        except FileNotFoundError:
            previous = b''
        if previous and not previous.startswith(b'# MAEstro existing multi-SMF addresses'):
            raise ValueError('foreign_network_dropin')
        stage = host.run(['mktemp', '-d', '/home/emsadmin/c3-core-addresses-XXXXXX']).strip()
        host.write(stage + '/before.conf', previous)
        host.write(stage + '/after.conf', CONFIG)
        host.run(['install', '-d', '-m', '755', directory], sudo=True)
        host.run(['install', '-m', '644', stage + '/after.conf', target], sudo=True)
        host.run(['networkctl', 'reload'], sudo=True)
        host.run(['networkctl', 'reconfigure', 'enp0s8'], sudo=True)
        addresses = json.loads(host.run(['ip', '-j', 'address', 'show', 'dev', 'enp0s8']))
        actual = {row['local'] for dev in addresses for row in dev['addr_info']}
        if not {'10.210.50.1', '10.210.50.2', '10.210.50.18'} <= actual:
            raise RuntimeError('smf_addresses_not_retained')
        routes = [host.run(['ip', 'route', 'get', peer, 'from', source])
                  for peer, source in [('10.210.50.9', '10.210.50.2'), ('10.210.50.22', '10.210.50.18')]]
        result = {'config': target, 'backup': stage + '/before.conf',
                  'sha256': hashlib.sha256(CONFIG.encode()).hexdigest(),
                  'addresses': sorted(actual), 'routes': routes}
        (evidence / 'deployment.json').write_text(json.dumps(result, indent=2))
        return result
    finally:
        host.client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', required=True, action='store_true')
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(deploy(args.evidence), indent=2))
