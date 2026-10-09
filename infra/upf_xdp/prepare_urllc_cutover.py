"""Read-only VM inspection and local candidates; never installs or restarts NFs.

Run from backend with its configured .env. Outputs may contain configuration
secrets: keep the generated directory under ignored .work, never commit it.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'infra'))
from multi_upf_stage import Lab, active_config, get_settings


def candidate(config, kind):
    """Only change the quota flag, after proving this NF serves URLLC alone."""
    nf = config[kind]
    sessions = nf.get('session', [])
    if not sessions or any(s.get('dnn') != '5g-plus' or
                           s.get('subnet') != '10.47.0.0/16' for s in sessions):
        raise ValueError('Refusing a shared or non-URLLC session configuration')
    if kind == 'smf':
        slices = [s for info in nf.get('info', []) for s in info.get('s_nssai', [])]
        if not slices or any(s.get('sst') != 2 or str(s.get('sd')) not in ('2', '000002') or
                             s.get('dnn') != ['5g-plus'] for s in slices):
            raise ValueError('SMF must advertise only 02-000002 / 5g-plus')
        peers = nf.get('pfcp', {}).get('client', {}).get('upf', [])
        if len(peers) != 1 or peers[0].get('address') != '10.210.50.22':
            raise ValueError('SMF PFCP peer must be the dedicated URLLC UPF')
        if type(nf.get('chf', {}).get('enabled')) is not bool:
            raise ValueError('Missing explicit CHF policy')
    else:
        for interface in ('pfcp', 'gtpu'):
            servers = nf.get(interface, {}).get('server', [])
            if len(servers) != 1 or servers[0].get('address') != '10.210.50.22':
                raise ValueError('UPF must bind only the URLLC address')
        if type(nf.get('charging_enforcement')) is not bool:
            raise ValueError('Missing explicit UPF charging policy')
    result = deepcopy(config)
    if kind == 'smf':
        result[kind]['chf']['enabled'] = False
    else:
        result[kind]['charging_enforcement'] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / '.work' / ('urllc-cutover-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    manifest = {'status': 'PREPARATION_ONLY', 'remote_mutations': False, 'targets': {},
                'blockers': ['Reconcile vehicle CHF Release before replacing configurations',
                             'Explicit PFCP profile without URR and with open QER without MBR/GBR',
                             'Dedicated native UPF, revocation permissions and watchdog acceptance',
                             'Live UE-MEC canary and eMBB/MIoT regression acceptance']}
    hosts = []
    try:
        core = Lab(settings, settings.ssh_port)
        hosts.append(core)
        upf = Lab(settings, settings.upf_ssh_port)
        hosts.append(upf)
        for host, kind, unit, expected in (
            (core, 'smf', 'open5gs-smfd3', '/etc/open5gs/smf3.yaml'),
            (upf, 'upf', 'open5gs-upfd-urllc', '/etc/open5gs/upf-urllc.yaml'),
        ):
            binary, path, original, config = active_config(host, unit)
            if path != expected:
                raise ValueError('Unexpected live configuration path: ' + path)
            proposed = yaml.safe_dump(candidate(config, kind), sort_keys=False)
            name = Path(path).name
            (output / (name + '.original')).write_text(original, encoding='utf-8', newline='\n')
            (output / (name + '.candidate')).write_text(proposed, encoding='utf-8', newline='\n')
            # Semantic diff avoids obscuring the two changes with YAML formatting.
            normalized = yaml.safe_dump(config, sort_keys=False)
            diff = ''.join(difflib.unified_diff(normalized.splitlines(True), proposed.splitlines(True),
                                               fromfile=path, tofile=path + '.candidate'))
            (output / (name + '.diff')).write_text(diff, encoding='utf-8')
            manifest['targets'][unit] = {'path': path, 'binary': binary,
                'sha256': hashlib.sha256(original.encode()).hexdigest(),
                'candidate_sha256': hashlib.sha256(proposed.encode()).hexdigest()}
        for host, unit, kind in ((core, 'open5gs-smfd', 'smf'), (upf, 'open5gs-upfd', 'upf')):
            _, path, original, config = active_config(host, unit)
            enabled = config[kind].get('chf', {}).get('enabled') if kind == 'smf' else config[kind].get('charging_enforcement')
            if enabled is not True:
                raise ValueError('eMBB charging baseline is not enabled: ' + unit)
            manifest['targets'][unit] = {'path': path, 'charging_enabled': enabled,
                'sha256': hashlib.sha256(original.encode()).hexdigest(), 'protected': True,
                'pid': host.run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip()}
        manifest['agent'] = json.loads(upf.run(['ip', 'netns', 'exec', 'maestro-urllc',
            'python3', '/opt/maestro-urllc-xdp/urllc_xdp_agent.py', 'status'], sudo=True))
        for name, host in (('core', core), ('upf', upf)):
            (output / (name + '-units.txt')).write_text(host.run(['systemctl', 'list-units',
                '--all', '--no-pager', 'open5gs-*', 'maestro-*']), encoding='utf-8')
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps({'status': manifest['status'], 'output': str(output),
                          'agent_available': manifest['agent']['available']}))
    finally:
        for host in hosts:
            host.client.close()


if __name__ == '__main__':
    main()
