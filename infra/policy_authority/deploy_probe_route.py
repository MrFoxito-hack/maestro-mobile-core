"""Permit only established native ICMP probe replies in corporate isolation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from infra.charging.e2e_native import Lab, get_settings


def deploy(evidence):
    evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    host = Lab(settings, settings.upf2_ssh_port)
    try:
        unit = host.run(['systemctl', 'cat', 'maestro-terminal-isolation'])
        if 'Description=MAEstro terminal DNN isolation' not in unit:
            raise ValueError('foreign_isolation_service')
        match = re.search(r'^ExecStart=/usr/sbin/nft -f (/opt/maestro-terminal-policy-[a-f0-9]+/rules\.nft)$', unit, re.M)
        if not match:
            raise ValueError('unknown_isolation_policy')
        path = match[1]
        original = host.run(['cat', path], sudo=True)
        declaration = 'type filter hook input priority -10; policy accept;'
        rule = ('iifname "ogstun" ip saddr 10.46.0.0/16 ip daddr 10.46.0.1 '
                'ct state established icmp type echo-reply counter accept')
        if declaration not in original:
            raise ValueError('unknown_isolation_input_chain')
        updated = original if rule in original else original.replace(declaration, declaration + '\n  ' + rule, 1)
        stage = host.run(['mktemp', '-d', '/home/emsadmin/c3-probe-route-XXXXXX']).strip()
        host.write(stage + '/before.nft', original)
        host.write(stage + '/after.nft', updated)
        host.run(['nft', '--check', '-f', stage + '/after.nft'], sudo=True)
        host.run(['install', '-m', '644', stage + '/after.nft', path], sudo=True)
        live = host.run(['nft', 'list', 'chain', 'inet', 'maestro_terminal', 'input'], sudo=True)
        if 'ct state established icmp type echo-reply' not in live:
            host.run(['nft', 'insert', 'rule', 'inet', 'maestro_terminal', 'input',
                      'iifname', 'ogstun', 'ip', 'saddr', '10.46.0.0/16', 'ip', 'daddr', '10.46.0.1',
                      'ct', 'state', 'established', 'icmp', 'type', 'echo-reply', 'counter', 'accept'], sudo=True)
        result = {'backup': stage + '/before.nft', 'policy': path,
                  'before_sha256': hashlib.sha256(original.encode()).hexdigest(),
                  'after_sha256': hashlib.sha256(updated.encode()).hexdigest(),
                  'live': host.run(['nft', 'list', 'table', 'inet', 'maestro_terminal'], sudo=True)}
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
