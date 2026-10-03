"""Staged dual-SMF deployment with remote backup, watchdog rollback and evidence.

Run from backend with its virtualenv: python ../infra/deploy_dual_smf.py prepare
Then apply, inspect/validate the live deployment, and commit (or rollback).
Credentials come from backend/.env; all SSH/SFTP is noninteractive Paramiko.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infra' / 'charging'))
from e2e_native import Lab
from lab_command import get_settings

STATE = ROOT / '.work' / 'dual-smf-deployment.json'
DROPIN = '/etc/systemd/system/open5gs-smfd.service.d/zzzz-dual-smf.conf'
UNIT = '/etc/systemd/system/open5gs-smfd2.service'
CLI = '/home/emsadmin/UERANSIM/build/nr-cli'


def save(state):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')


def evidence(state, name, data):
    path = Path(state['local']) / name
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2), encoding='utf-8')


def active_command(host, unit):
    raw = host.run(['systemctl', 'show', unit, '-p', 'ExecStart', '--value'])
    args = shlex.split(re.search(r'argv\[\]=([^;]+)', raw)[1].strip())
    assert len(args) == 3 and args[1] == '-c', raw
    return args


def info(core, address, port):
    return json.loads(core.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '5',
                               f'http://{address}:{port}/pdu-info?page=0&page_size=100']))


def prepare(core, ue, settings):
    if STATE.exists():
        raise RuntimeError('Deployment state already exists; inspect before preparing again')
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    remote = '/home/emsadmin/backup-pre-smf2-' + tag
    local = ROOT / 'reportes' / 'evidencias' / ('dual-smf-' + tag)
    local.mkdir(parents=True)
    state = {'tag': tag, 'remote': remote, 'local': str(local), 'phase': 'preparing',
             'timer': 'maestro-dual-smf-rollback-' + tag.lower()}
    save(state)
    for unit in ('open5gs-smfd', 'open5gs-amfd', 'open5gs-bsfd', 'open5gs-nssfd', 'open5gs-nrfd', 'open5gs-scpd'):
        assert core.run(['systemctl', 'is-active', unit]).strip() == 'active', unit
    for path in (DROPIN, UNIT, '/etc/open5gs/smf2.yaml'):
        assert core.run(['test', '-e', path], check=False) == ''
        try:
            core.read(path)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError('Refusing to overwrite existing ' + path)
    sockets = core.run(['ss', '-lntup'], sudo=True)
    assert '127.0.0.16:7777' not in sockets
    assert '10.210.50.2:8805' not in sockets
    addresses = json.loads(core.run(['ip', '-j', '-4', 'addr']))
    interfaces = [i['ifname'] for i in addresses if any(a['local'] == '10.210.50.1' for a in i['addr_info'])]
    assert len(interfaces) == 1
    assert not any(a['local'] == '10.210.50.2' for i in addresses for a in i['addr_info'])
    state['interface'] = interfaces[0]
    current = active_command(core, 'open5gs-smfd')
    state['binary'], _, state['active_config'] = current
    environment = core.run(['systemctl', 'show', 'open5gs-smfd', '-p', 'Environment', '--value']).strip()
    assert environment.startswith('LD_LIBRARY_PATH=') and '\n' not in environment
    unit_text = core.run(['systemctl', 'cat', 'open5gs-smfd'])
    environment_files = re.findall(r'^EnvironmentFile=(.+)$', unit_text, re.M)
    original_paths = list(dict.fromkeys(['/etc/open5gs/smf.yaml', '/etc/open5gs/amf.yaml',
                                      '/etc/open5gs/nssf.yaml', '/etc/open5gs/bsf.yaml', current[2]]))
    core.run(['mkdir', '-m', '0700', remote])
    core.run(['mkdir', remote + '/original', remote + '/staged'])
    hashes = {}
    for path in original_paths:
        core.run(['cp', '--parents', '-a', path, remote + '/original'], sudo=True)
        hashes[path] = core.run(['sha256sum', path], sudo=True).split()[0]
    core.run(['tar', '-czf', remote + '/systemd-before.tgz', '/etc/systemd/system',
              '/etc/netplan'], sudo=True)
    dump = core.run(['mongodump', '--db=open5gs', '--gzip', '--archive=' + remote + '/open5gs.archive.gz'], sudo=True, timeout=60)
    core.run(['gzip', '-t', remote + '/open5gs.archive.gz'], sudo=True)
    evidence(state, 'mongodump.log', dump)
    evidence(state, 'sockets-before.txt', sockets)
    evidence(state, 'smf-unit-before.txt', unit_text)
    evidence(state, 'pdu-before.json', info(core, '127.0.0.4', 9090))
    ue_units = ue.run(['systemctl', 'list-units', 'ueransim-ue*', '--state=running', '--no-legend'])
    state['ue_units'] = [line.split()[0] for line in ue_units.splitlines() if line.strip()]
    state['ue_nodes'] = ue.run([CLI, '--dump']).splitlines()
    state['watchdog_active'] = ue.run(['systemctl', 'is-active', 'ueransim-watchdog'], check=False).strip() == 'active'
    state['ue_remote'] = ue.run(['mktemp', '-d', '/home/emsadmin/backup-pre-smf2-ue-' + tag + '-XXXX']).strip()
    restore_ue = '#!/bin/sh\nset -eu\nsystemctl start ' + ' '.join(state['ue_units']) + '\n'
    if state['watchdog_active']:
        restore_ue += 'systemctl start ueransim-watchdog\n'
    ue.write(state['ue_remote'] + '/rollback.sh', restore_ue, 0o700)
    rollback = '#!/bin/sh\nset -eu\nexec >>' + remote + '/rollback.log 2>&1\ndate -Is\n'
    rollback += 'systemctl disable --now open5gs-smfd2.service || true\n'
    rollback += 'systemctl stop open5gs-smfd open5gs-amfd open5gs-pcfd open5gs-bsfd\n'
    for path in original_paths:
        rollback += 'cp -a ' + shlex.quote(remote + '/original' + path) + ' ' + shlex.quote(path) + '\n'
    rollback += 'rm -f ' + shlex.join([DROPIN, UNIT, '/etc/open5gs/smf2.yaml']) + '\n'
    rollback += '/usr/sbin/ip addr del 10.210.50.2/24 dev ' + state['interface'] + ' || true\n'
    rollback += '/usr/sbin/ip addr del 127.0.0.15/32 dev lo || true\n'
    rollback += 'systemctl daemon-reload\nsystemctl restart open5gs-bsfd open5gs-nssfd open5gs-pcfd open5gs-smfd open5gs-amfd\n'
    core.write(remote + '/rollback.sh', rollback, 0o700)
    core.run(['sh', '-n', remote + '/rollback.sh'])
    state['original_sha256'] = hashes
    core.write(remote + '/manifest.json', json.dumps(state, indent=2))
    evidence(state, 'backup-manifest.json', state)
    save(state)
    stage(core, state)


def stage(core, state):
    remote, local = state['remote'], state['local']
    current = [state['binary'], '-c', state['active_config']]
    environment = core.run(['systemctl', 'show', 'open5gs-smfd', '-p', 'Environment', '--value']).strip()
    environment_files = re.findall(r'^EnvironmentFile=(.+)$', core.run(['systemctl', 'cat', 'open5gs-smfd']), re.M)
    # No service/configuration changes have occurred before verified backup + rollback.
    smf1 = yaml.safe_load(core.run(['cat', current[2]], sudo=True))
    smf1['smf']['pfcp']['client']['upf'] = [{'address': '10.210.50.8', 'dnn': 'internet'}]
    smf1['smf']['session'] = [{'subnet': '10.45.0.0/16', 'gateway': '10.45.0.1', 'dnn': 'internet'}]
    smf1['smf']['info'] = [{'s_nssai': [{'sst': 1, 'sd': '000001', 'dnn': ['internet']}]}]
    smf2 = copy.deepcopy(smf1)
    smf2['logger']['file']['path'] = '/var/log/open5gs/smf2.log'
    s = smf2['smf']
    s['sbi']['server'] = [{'address': '127.0.0.15', 'port': 7777}]
    s['pfcp'] = {'server': [{'address': '10.210.50.2'}, {'address': '127.0.0.15'}],
                 'client': {'upf': [{'address': '10.210.50.9', 'dnn': 'corporate'}]}}
    for key in ('gtpc', 'gtpu'):
        s[key]['server'] = [{'address': '127.0.0.15'}]
    s['metrics']['server'] = [{'address': '127.0.0.15', 'port': 9091}]
    s['session'] = [{'subnet': '10.46.0.0/16', 'gateway': '10.46.0.1', 'dnn': 'corporate'}]
    s['info'] = [{'s_nssai': [{'sst': 1, 'sd': '000002', 'dnn': ['corporate']}]}]
    s['dns'] = ['8.8.8.8', '8.8.4.4']
    s.pop('freeDiameter', None)  # Corporate is 5GC-only; do not share Diameter sockets/identity.
    if s.get('chf'):
        s['chf']['nf_instance_id'] = str(uuid.uuid4())
        s['chf']['journal_dir'] = '/var/lib/open5gs/chf-journal-smf2'
    nssf = yaml.safe_load(core.read('/etc/open5gs/nssf.yaml'))
    nssf['nssf']['sbi']['client']['nsi'] = [
        {'uri': 'http://127.0.0.10:7777', 's_nssai': {'sst': 1, 'sd': sd}}
        for sd in ('000001', '000002')]
    bsf = yaml.safe_load(core.read('/etc/open5gs/bsf.yaml'))
    assert bsf['bsf']['sbi']['server'] == [{'address': '127.0.0.15', 'port': 7777}]
    bsf['bsf']['sbi']['server'] = [{'address': '127.0.0.16', 'port': 7777}]
    for name, config in [('smf.yaml', smf1), ('smf2.yaml', smf2), ('nssf.yaml', nssf), ('bsf.yaml', bsf)]:
        text = yaml.safe_dump(config, sort_keys=False)
        assert yaml.safe_load(text) == config
        core.write(remote + '/staged/' + name, text)
        evidence(state, name, text)
    unit = f'''[Unit]
Description=Open5GS SMF 2 Daemon (Corporate Slice)
Wants=network-online.target
After=network-online.target open5gs-nrfd.service open5gs-scpd.service
[Service]
Type=simple
User=open5gs
Group=open5gs
ExecStartPre=+/usr/sbin/ip address replace 10.210.50.2/24 dev {state['interface']}
ExecStartPre=+/usr/sbin/ip address replace 127.0.0.15/32 dev lo
ExecStart={current[0]} -c /etc/open5gs/smf2.yaml
Environment={environment}
'''
    unit += ''.join('EnvironmentFile=' + p + '\n' for p in environment_files)
    unit += 'Restart=on-failure\nRestartSec=2\nRestartPreventExitStatus=1\n[Install]\nWantedBy=multi-user.target\n'
    core.write(remote + '/staged/open5gs-smfd2.service', unit)
    evidence(state, 'open5gs-smfd2.service', unit)
    core.write(remote + '/staged/smf1-override.conf', '[Service]\nExecStart=\nExecStart=' + current[0] + ' -c /etc/open5gs/smf.yaml\n')
    state['phase'] = 'prepared'
    save(state)
    print(json.dumps({'phase': state['phase'], 'backup': remote, 'evidence': str(local)}, indent=2), flush=True)


def apply(core, ue, state):
    assert state['phase'] == 'prepared', state['phase']
    remote = state['remote']
    # Timed fallback remains armed through independent live acceptance.
    core.run(['systemd-run', '--unit=' + state['timer'], '--on-active=30m', '/bin/sh', remote + '/rollback.sh'], sudo=True)
    ue.run(['systemd-run', '--unit=' + state['timer'], '--on-active=31m', '/bin/sh', state['ue_remote'] + '/rollback.sh'], sudo=True)
    state['phase'] = 'applying'
    save(state)
    try:
        if state['watchdog_active']:
            ue.run(['systemctl', 'stop', 'ueransim-watchdog'], sudo=True)
        for supi in state['ue_nodes']:
            ue.run([CLI, supi, '--exec', 'deregister switch-off'], check=False)
        time.sleep(3)
        ue.run(['systemctl', 'stop', *state['ue_units']], sudo=True)
        core.run(['systemd-run', '--unit=maestro-dual-smf-capture', '--property=RuntimeMaxSec=1700',
                  '/usr/bin/tshark', '-i', 'lo', '-f', 'tcp port 7777', '-w', remote + '/nssf_nsselection_smf2.pcap'], sudo=True)
        core.run(['systemd-run', '--unit=maestro-dual-smf-n4', '--property=RuntimeMaxSec=1700',
                  '/usr/bin/tshark', '-i', state['interface'], '-f', 'udp port 8805', '-w', remote + '/n4_dual_smf.pcap'], sudo=True)
        core.run(['systemctl', 'stop', 'open5gs-smfd', 'open5gs-amfd', 'open5gs-pcfd', 'open5gs-bsfd'], sudo=True)
        for name in ('smf.yaml', 'smf2.yaml', 'nssf.yaml', 'bsf.yaml'):
            core.run(['install', '-o', 'root', '-g', 'open5gs', '-m', '0640', remote + '/staged/' + name, '/etc/open5gs/' + name], sudo=True)
        core.run(['install', '-m', '0644', remote + '/staged/open5gs-smfd2.service', UNIT], sudo=True)
        core.run(['install', '-m', '0644', remote + '/staged/smf1-override.conf', DROPIN], sudo=True)
        core.run(['install', '-d', '-o', 'open5gs', '-g', 'open5gs', '-m', '0700', '/var/lib/open5gs/chf-journal-smf2'], sudo=True)
        core.run(['systemctl', 'daemon-reload'], sudo=True)
        core.run(['systemd-analyze', 'verify', UNIT], sudo=True)
        core.run(['systemctl', 'restart', 'open5gs-bsfd', 'open5gs-nssfd', 'open5gs-pcfd'], sudo=True)
        core.run(['systemctl', 'enable', '--now', 'open5gs-smfd2'], sudo=True)
        core.run(['systemctl', 'start', 'open5gs-smfd', 'open5gs-amfd'], sudo=True)
        time.sleep(5)
        for unit in ('open5gs-smfd', 'open5gs-smfd2', 'open5gs-amfd', 'open5gs-bsfd', 'open5gs-nssfd', 'open5gs-pcfd'):
            assert core.run(['systemctl', 'is-active', unit]).strip() == 'active', unit
        assert isinstance(info(core, '127.0.0.4', 9090), dict)
        assert isinstance(info(core, '127.0.0.15', 9091), dict)
        ue.run(['systemctl', 'start', *state['ue_units']], sudo=True)
        if state['watchdog_active']:
            ue.run(['systemctl', 'start', 'ueransim-watchdog'], sudo=True)
        state['phase'] = 'deployed-pending-validation'
        save(state)
        print('DEPLOYED; rollback timers armed for 30 minutes', flush=True)
    except BaseException:
        rollback(core, ue, state)
        raise


def rollback(core, ue, state):
    print(core.run(['/bin/sh', state['remote'] + '/rollback.sh'], sudo=True, check=False), flush=True)
    print(ue.run(['/bin/sh', state['ue_remote'] + '/rollback.sh'], sudo=True, check=False), flush=True)
    for host in (core, ue):
        host.run(['systemctl', 'stop', state['timer'] + '.timer'], sudo=True, check=False)
    state['phase'] = 'rolled-back'
    save(state)


def inspect(core, ue, state):
    for name, args in [
        ('services-after.txt', ['systemctl', 'show', 'open5gs-smfd', 'open5gs-smfd2', 'open5gs-bsfd', 'open5gs-amfd', '-p', 'ActiveState', '-p', 'SubState', '-p', 'NRestarts', '-p', 'ExecStart']),
        ('smf2-startup.log', ['journalctl', '-u', 'open5gs-smfd2', '--no-pager', '-n', '100']),
        ('sockets-after.txt', ['ss', '-lntup']),
    ]:
        text = core.run(args, sudo=True, check=False)
        evidence(state, name, text)
        print(name + '\n' + text, flush=True)
    for name, address, port in [('smf1-pdu.json', '127.0.0.4', 9090), ('smf2-pdu.json', '127.0.0.15', 9091)]:
        data = info(core, address, port)
        evidence(state, name, data)
        print(name + '\n' + json.dumps(data), flush=True)
    for supi in ue.run([CLI, '--dump']).splitlines():
        output = ue.run([CLI, supi, '--exec', 'ps-list'], check=False)
        evidence(state, supi + '-pdu.txt', output)
        print(supi + '\n' + output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'stage', 'apply', 'inspect', 'commit', 'rollback'])
    action = parser.parse_args().action
    settings = get_settings()
    core, ue = Lab(settings, settings.ssh_port), Lab(settings, settings.ue_ssh_port)
    try:
        if action == 'prepare':
            prepare(core, ue, settings)
            return
        state = json.loads(STATE.read_text(encoding='utf-8'))
        if action == 'stage':
            if 'original_sha256' not in state:
                state = json.loads((Path(state['local']) / 'backup-manifest.json').read_text(encoding='utf-8'))
            assert state['phase'] == 'preparing'
            stage(core, state)
        elif action == 'apply':
            apply(core, ue, state)
        elif action == 'inspect':
            inspect(core, ue, state)
        elif action == 'rollback':
            rollback(core, ue, state)
        elif action == 'commit':
            assert state['phase'] == 'validated', 'Acceptance must mark validated first'
            for host in (core, ue):
                host.run(['systemctl', 'stop', state['timer'] + '.timer'], sudo=True)
            state['phase'] = 'committed'
            save(state)
            print('COMMITTED; manual rollback retained at ' + state['remote'] + '/rollback.sh')
    finally:
        core.client.close()
        ue.client.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
