"""Install native coordination and supervisor using the existing mTLS identity.

Does not restart NFs. This activates lifecycle maintenance once guarded NFs
are deployed; incomplete effective-policy capabilities stay closed.
"""
import argparse
import json
import re
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from infra.charging.e2e_native import Lab, get_settings
from infra.policy_authority.deploy_relay import install


def deploy(relay_record, evidence):
    settings = get_settings()
    relay = json.loads(relay_record.read_text())
    evidence.mkdir(parents=True, exist_ok=False)
    suffix = uuid.uuid4().hex[:12]
    bundle = '/opt/maestro-native-control-' + suffix
    hosts = {}
    result = {'bundle': bundle, 'accepted': False, 'backups': {}}
    try:
        for name, port in [('core', settings.ssh_port), ('upf', settings.upf_ssh_port), ('upf2', settings.upf2_ssh_port)]:
            host = hosts[name] = Lab(settings, port)
            stage = host.run(['mktemp', '-d', '/home/emsadmin/c3-adapter-XXXXXX']).strip()
            host.run(['mkdir', '-m', '755', bundle], sudo=True)
            for filename in ('native_relay.py', 'native_transport.py', 'native_adapter.py',
                             'native_runtime.py', 'native_registry.py', 'native_effective.py'):
                install(host, bundle + '/' + filename, Path(__file__).with_name(filename).read_bytes(), stage, '644')
            config_name = 'transport.json' if name == 'core' else 'relay.json'
            host.run(['cp', '--preserve=mode,ownership', relay['bundle'] + '/' + config_name, bundle + '/' + config_name], sudo=True)
            if name == 'core':
                install(host, bundle + '/server.py', Path(__file__).with_name('server.py').read_bytes(), stage, '644')
                install(host, bundle + '/policy_authority.py', (ROOT / 'backend/app/services/policy_authority.py').read_bytes(), stage, '644')
                unit = ('[Unit]\nDescription=MAEstro native seven-NF coordinator\nAfter=network-online.target\n'
                        '[Service]\nType=simple\nUser=root\nUMask=0077\n'
                        f'ExecStart=/usr/bin/python3 {bundle}/native_adapter.py --transport {bundle}/transport.json\n'
                        'Restart=always\nRestartSec=1\nRuntimeDirectory=maestro-policy-native\nRuntimeDirectoryMode=0700\n'
                        'StateDirectory=maestro-policy-native\nStateDirectoryMode=0700\nNoNewPrivileges=true\n'
                        'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nReadWritePaths=/run\n'
                        'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n[Install]\nWantedBy=multi-user.target\n')
                units = {'maestro-policy-native': unit,
                         'maestro-policy-authority': Path(__file__).with_name('maestro-policy-authority.service').read_text().replace(
                             '/opt/maestro-policy-authority/server.py', bundle + '/server.py')}
            else:
                old = host.run(['cat', '/etc/systemd/system/maestro-native-relay.service'], sudo=True)
                updated, count = re.subn(r'^ExecStart=/usr/bin/python3 /opt/maestro-native-[^\s]+/native_relay.py [^\n]+$',
                                         f'ExecStart=/usr/bin/python3 {bundle}/native_relay.py {bundle}/relay.json', old,
                                         flags=re.MULTILINE)
                if count != 1:
                    raise ValueError('unexpected_native_relay_execstart')
                updated = updated.replace('RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n',
                                          'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\n')
                units = {'maestro-native-relay': updated}
            result['backups'][name] = {}
            for service, unit in units.items():
                dest = '/etc/systemd/system/' + service + '.service'
                previous = host.run(['python3', '-c',
                    'import pathlib,json,sys;p=pathlib.Path(sys.argv[1]);print(json.dumps(p.read_text() if p.exists() else None))', dest], sudo=True)
                if json.loads(previous) is not None:
                    backup = bundle + '/' + service + '.before.service'
                    host.run(['cp', '--preserve=mode,ownership', dest, backup], sudo=True)
                    result['backups'][name][service] = backup
                install(host, dest, unit, stage, '644')
                host.run(['systemd-analyze', 'verify', dest], sudo=True)
            host.run(['systemctl', 'daemon-reload'], sudo=True)
            for service in units:
                host.run(['systemctl', 'enable', service], sudo=True)
                host.run(['systemctl', 'restart', service], sudo=True)
                host.run(['systemctl', 'is-active', service], sudo=True)
        result['accepted'] = True
        return result
    finally:
        (evidence / 'deployment.json').write_text(json.dumps(result, indent=2), encoding='utf8')
        for host in hosts.values():
            host.client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relay-record', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(deploy(args.relay_record, args.evidence), indent=2))
