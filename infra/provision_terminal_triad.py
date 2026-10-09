"""Provision two independent lab UEs and private bounded UDP echo responders.

Run from backend with its venv: python ../infra/provision_terminal_triad.py --execute.
Preserves SIM credentials, primary UE, existing slices and CHF account balances.
Backups and a rollback manifest are stored in a unique .work directory.
"""
import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'infra' / 'charging'))
from e2e_native import Lab
from app.core.config import get_settings
from app.services.charging import management_get, management_request

ECHO = '''import socket,ipaddress,sys,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.bind((sys.argv[1],8765))
pool=ipaddress.ip_network(sys.argv[2]); window=time.monotonic(); count=0
while True:
 data,peer=s.recvfrom(256)
 now=time.monotonic()
 if now-window>=1: window=now; count=0
 if len(data)!=64 or ipaddress.ip_address(peer[0]) not in pool or count>=500: continue
 count+=1
 s.sendto(data,peer)
'''


def allow_iot_echo(host, backup_dir):
    """Add only UDP/8765 from the corporate TUN to its gateway; keep isolation."""
    unit = host.run(['systemctl', 'cat', 'maestro-terminal-isolation'])
    path = re.search(r'/opt/maestro-terminal-policy-[a-f0-9]+/rules\.nft', unit)
    if not path:
        raise RuntimeError('Unknown corporate isolation policy; inspect before changing')
    policy_path = path.group(0)
    original = host.run(['cat', policy_path], sudo=True)
    if 'udp dport 8765' in original:
        return
    assert 'chain input {' in original
    host.write(backup_dir + '/isolation-before.nft', original)
    rule = 'iifname "ogstun" ip saddr 10.46.0.0/16 ip daddr 10.46.0.1 udp dport 8765 counter accept'
    declaration = 'type filter hook input priority -10; policy accept;'
    assert declaration in original
    updated = original.replace(declaration, declaration + '\n  ' + rule, 1)
    host.write(backup_dir + '/isolation-after.nft', updated)
    host.run(['nft', '-c', '-f', backup_dir + '/isolation-after.nft'], sudo=True)
    host.run(['install', '-m', '644', backup_dir + '/isolation-after.nft', policy_path], sudo=True)
    live = host.run(['nft', 'list', 'chain', 'inet', 'maestro_terminal', 'input'], sudo=True)
    if 'udp dport 8765' not in live:
        host.run(['nft', 'insert', 'rule', 'inet', 'maestro_terminal', 'input',
                  'iifname', 'ogstun', 'ip', 'saddr', '10.46.0.0/16', 'ip', 'daddr',
                  '10.46.0.1', 'udp', 'dport', '8765', 'counter', 'accept'], sudo=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', required=True)
    parser.parse_args()
    settings = get_settings()
    assert settings.multi_upf_enabled, 'Complete core triad migration first'
    run = 'terminal-triad-' + uuid.uuid4().hex[:10]
    evidence = ROOT / '.work' / run
    evidence.mkdir(parents=True)
    remote = '/home/emsadmin/' + run
    core, ue, upf, corp = [Lab(settings, p) for p in
                           (settings.ssh_port, settings.ue_ssh_port,
                            settings.upf_ssh_port, settings.upf2_ssh_port)]
    hosts = [core, ue, upf, corp]
    manifest = {'run': run, 'remote_backup': remote, 'devices': [], 'units': []}
    try:
        nodes = ue.run(['/home/emsadmin/UERANSIM/build/nr-cli', '--dump'])
        for suffix in ('002', '003'):
            assert 'imsi-999700000000' + suffix not in nodes, 'Target UE already active'
        for host in hosts:
            host.run(['mkdir', '-m', '700', remote])
        for unit in ('maestro-ue-vehicle', 'maestro-ue-sensor'):
            ue.run(['test', '!', '-e', '/etc/systemd/system/' + unit + '.service'])
        for index, name, dnn in [(2, 'vehicle', '5g-plus'), (3, 'sensor', 'corporate')]:
            imsi = f'99970000000000{index}'
            collection = 'db.getSiblingDB("open5gs").subscribers'
            # Keys remain in memory / protected configs; never print them or put them in evidence.
            auth = json.loads(core.run(['mongosh', '--quiet', '--eval',
                f'print(JSON.stringify({collection}.findOne({{imsi:"{imsi}"}}).security))']))
            config = yaml.safe_load(ue.read(f'/home/emsadmin/UERANSIM/config/maestro-ue-{index:02d}.yaml'))
            assert config['supi'] == 'imsi-' + imsi
            config['key'] = auth['k']
            config['op'] = auth.get('opc') or auth['op']
            config['opType'] = 'OPC' if auth.get('opc') else 'OP'
            config['amf'] = auth['amf']
            snssai = {'sst': index, 'sd': index}
            config['sessions'] = [{'type': 'IPv4', 'apn': dnn, 'slice': snssai}]
            config['configured-nssai'] = [{'sst': i, 'sd': i} for i in (1, 2, 3)]
            config['default-nssai'] = [snssai]
            config['imei'] = f'35693803564380{index + 2}'
            config['imeiSv'] = f'437081612581615{index}'
            config_path = remote + '/' + name + '.yaml'
            ue.write(config_path, yaml.safe_dump(config, sort_keys=False))
            js = (f'const c={collection};const d=c.findOne({{imsi:"{imsi}"}});'
                  'const fs=require("fs");'
                  f'fs.writeFileSync("{remote}/{name}-slice.json",EJSON.stringify(d.slice));'
                  f'const p=c.findOne({{imsi:"999700000000001"}}).slice.find(s=>s.sst==={index});'
                  'if(!p)throw Error("Missing canonical slice");p.default_indicator=false;'
                  f'c.updateOne({{imsi:"{imsi}"}},{{$set:{{slice:[...d.slice.filter(s=>s.sst!=={index}),p]}}}});')
            core.write(remote + '/' + name + '.js', js)
            core.run(['mongosh', '--quiet', '--file', remote + '/' + name + '.js'])
            account = management_get('/admin/v1/accounts?supi=imsi-' + imsi)
            if not account['total']:
                management_request('/admin/v1/accounts/imsi-' + imsi, method='PUT',
                                   payload={'supi': 'imsi-' + imsi, 'quotaBytes': 50_000_000, 'enabled': True})
            else:
                credit = management_get('/admin/v1/accounts/imsi-' + imsi)
                if credit['available_bytes'] < 1_000_000:
                    # Lab quota only: preserve consumption and audit an idempotent credit.
                    management_request('/admin/v1/accounts/imsi-' + imsi + '/topup',
                                       payload={'requestId': str(uuid.uuid4()), 'amountBytes': 50_000_000})
            unit = 'maestro-ue-' + name
            unit_text = ('[Unit]\nDescription=MAEstro independent ' + name + ' UE\nAfter=network.target\n'
                         '[Service]\nType=simple\nWorkingDirectory=/home/emsadmin/UERANSIM\n'
                         'ExecStart=/home/emsadmin/UERANSIM/build/nr-ue -c ' + config_path + '\n'
                         'Restart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n')
            ue.write(remote + '/' + unit + '.service', unit_text)
            ue.run(['test', '!', '-e', '/etc/systemd/system/' + unit + '.service'])
            ue.run(['install', '-m', '644', remote + '/' + unit + '.service',
                    '/etc/systemd/system/' + unit + '.service'], sudo=True)
            manifest['devices'].append({'imsi': imsi, 'name': name, 'dnn': dnn, 'unit': unit})
        for host, address, pool, ns, name in [(upf, '172.31.48.2', '10.47.0.0/16', 'maestro-mec', 'mec'),
                                              (corp, '10.46.0.1', '10.46.0.0/16', None, 'iot')]:
            host.write(remote + '/echo.py', ECHO, mode=0o644)
            # Directory remains private; root owns the restricted service process.
            unit = 'maestro-terminal-echo-' + name
            text = ('[Unit]\nDescription=MAEstro bounded private UDP echo\nAfter=network.target\n'
                    '[Service]\nExecStart=/usr/bin/python3 ' + remote + '/echo.py ' + address + ' ' + pool + '\n'
                    + ('NetworkNamespacePath=/run/netns/' + ns + '\n' if ns else '') +
                    'Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nProtectSystem=strict\n'
                    'ProtectHome=read-only\nMemoryMax=32M\nCPUQuota=10%\n'
                    '[Install]\nWantedBy=multi-user.target\n')
            host.write(remote + '/' + unit + '.service', text)
            host.run(['test', '!', '-e', '/etc/systemd/system/' + unit + '.service'])
            host.run(['install', '-m', '644', remote + '/' + unit + '.service', '/etc/systemd/system/' + unit + '.service'], sudo=True)
            host.run(['systemctl', 'daemon-reload'], sudo=True)
            host.run(['systemctl', 'enable', '--now', unit], sudo=True)
            manifest['units'].append(unit)
        allow_iot_echo(corp, remote)
        ue.run(['systemctl', 'daemon-reload'], sudo=True)
        for d in manifest['devices']:
            ue.run(['systemctl', 'enable', '--now', d['unit']], sudo=True)
        for d in manifest['devices']:
            for _ in range(30):
                time.sleep(1)
                native = ue.run(['/home/emsadmin/UERANSIM/build/nr-cli', 'imsi-' + d['imsi'], '--exec', 'ps-list'])
                if 'PS-ACTIVE' in native and d['dnn'] in native:
                    break
            else:
                for created in manifest['devices']:
                    ue.run(['systemctl', 'stop', created['unit']], sudo=True)
                raise RuntimeError('UE acceptance failed; new UE units stopped. See rollback manifest.')
            manifest[d['name'] + '_pdu'] = native
            print(d['name'], native)
        manifest['complete'] = True
    finally:
        (evidence / 'result.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        for h in hosts:
            h.client.close()
        print('Evidence:', evidence)


if __name__ == '__main__':
    main()
