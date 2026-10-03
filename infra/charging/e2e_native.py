"""Bounded native UE/N4 acceptance in the configured lab, with restoration.

Explicit --execute is required. Uses private transient units, copied configs,
fresh accounting databases and ABI-matched UPF libraries. Never overwrites an
installed binary, subscriber credential, NF config or accounting database.
An independent systemd timer restores stock services if this controller exits.
"""
import argparse
import hashlib
import json
import re
import secrets
import shlex
import sys
import time
import uuid
from pathlib import Path

import paramiko
import yaml

if __package__:
    from .lab_command import get_settings
else:
    from lab_command import get_settings

ROOT = Path(__file__).resolve().parents[2]
REMOTE = '/home/emsadmin/maestro-charging'
BUILD = REMOTE + '/open5gs/build'
CHF = REMOTE + '/chf'


def confirmed_interface(log, interfaces):
    """Select only the TUN acknowledged by this run's UE, never another UE."""
    matches = re.findall(r'TUN interface\[(uesimtun\d+), (10\.45\.\d+\.\d+)\] is up', log)
    if not matches:
        return None
    name, address = matches[-1]
    for interface in interfaces:
        if interface['ifname'] == name and any(
                item.get('local') == address for item in interface.get('addr_info', [])):
            return name
    return None


class Lab:
    def __init__(self, settings, port):
        self.settings = settings
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        if not settings.ssh_strict_host_key:
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(settings.testbed_host, port=port, username=settings.ssh_user,
                            password=settings.ssh_password, look_for_keys=False,
                            allow_agent=False, timeout=10,
                            key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None)

    def run(self, args, *, sudo=False, check=True, timeout=30):
        command = shlex.join([str(x) for x in args])
        stdin, out, _ = self.client.exec_command(("sudo -S -p '' " if sudo else '') + command, timeout=timeout)
        out.channel.set_combine_stderr(True)
        if sudo and self.settings.ssh_password:
            stdin.write(self.settings.ssh_password + '\n')
        stdin.flush()
        stdin.channel.shutdown_write()
        data = out.read().decode(errors='replace')
        code = out.channel.recv_exit_status()
        if check and code:
            # Commands never contain credentials; do not echo potential process output.
            raise RuntimeError(f'Command failed ({code}): {args[0:3]}')
        return data

    def read(self, path):
        with self.client.open_sftp() as sftp, sftp.open(path, 'rb') as file:
            return file.read()

    def write(self, path, data, mode=0o600):
        with self.client.open_sftp() as sftp:
            # All outputs are inside unique, freshly-created evidence directories.
            with sftp.open(path, 'wx') as file:
                file.write(data.encode() if isinstance(data, str) else data)
            sftp.chmod(path, mode)

    def start(self, unit, args, directory, *, properties=()):
        return self.run(['systemd-run', '--unit=' + unit, '--collect',
                         '--property=RuntimeMaxSec=420', '--property=TimeoutStopSec=15',
                         '--property=WorkingDirectory=' + directory,
                         '--property=StandardOutput=append:' + directory + '/' + unit + '.log',
                         '--property=StandardError=inherit', *properties, *args], sudo=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--case', choices=['off', 'final', 'renewal', 'long', 'all'], default='all')
    parser.add_argument('--burst', action='store_true', help='Burst packets to exercise reports overlapping an Nchf Update')
    args = parser.parse_args()
    if not args.execute:
        parser.error('Use --execute only for the isolated lab; UE/SMF/UPF-01 temporarily restart')
    settings = get_settings()
    hosts = {name: Lab(settings, port) for name, port in
             [('core', settings.ssh_port), ('upf', settings.upf_ssh_port), ('ue', settings.ue_ssh_port)]}
    core, upf, ue = (hosts[n] for n in ('core', 'upf', 'ue'))
    gnb = Lab(settings, settings.gnb_ssh_port)
    tag = 'chf-e2e-' + uuid.uuid4().hex[:12]
    paths, units, timers, originals = {}, {}, {}, {}
    expected_upfs = []
    result = {'run': tag, 'cases': {}, 'restoration': {}}
    local = ROOT / '.work' / tag
    local.mkdir()
    try:
        for name, host in hosts.items():
            service = {'core': 'open5gs-smfd', 'upf': 'open5gs-upfd', 'ue': 'ueransim-ue'}[name]
            assert host.run(['systemctl', 'is-active', service]).strip() == 'active', name
            originals[name] = service
            paths[name] = host.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-XXXXXX']).strip()
            units[name] = [tag + '-' + name]
        units['core'] += [tag + '-chf', tag + '-pcap']
        # Independent restoration survives a lost local SSH/controller connection.
        for name, host in hosts.items():
            rollback = paths[name] + '/restore.sh'
            host.write(rollback, '#!/bin/sh\n' +
                       'systemctl stop ' + ' '.join(u + '.service' for u in units[name]) + ' 2>/dev/null\n' +
                       'systemctl start ' + originals[name] + '.service\n', 0o700)
            timer = tag + '-restore-' + name
            host.run(['systemd-run', '--unit=' + timer, '--on-active=480s', '/bin/sh', rollback], sudo=True)
            timers[name] = timer
        smf_source = core.read('/etc/open5gs/smf.yaml')
        expected_upfs = [peer['address'] for peer in
                         yaml.safe_load(smf_source)['smf']['pfcp']['client']['upf']
                         if 'address' in peer]
        upf_source = upf.read('/etc/open5gs/upf.yaml')
        ue_source = ue.read('/home/emsadmin/UERANSIM/config/open5gs-ue.yaml')
        ue_config = yaml.safe_load(ue_source)
        sessions = [s for s in ue_config['sessions'] if s.get('apn') == 'internet']
        assert len(sessions) == 1, 'A single existing internet session is required'
        ue_config['sessions'] = sessions
        supi = ue_config['supi']
        assert re.fullmatch(r'imsi-\d{5,15}', supi)
        # Copy the binary and all its build-tree libraries; never use stock SBI/PFCP ABI.
        binary = BUILD + '/src/upf/open5gs-upfd'
        dependencies = core.run(['ldd', binary])
        library_dir = paths['upf'] + '/lib'
        upf.run(['mkdir', '-m', '700', library_dir])
        copied = {}
        for dep in [binary] + re.findall(r'=> (\S+/maestro-charging/open5gs/build/\S+)', dependencies):
            name = dep.rsplit('/', 1)[-1]
            data = core.read(dep)
            target = paths['upf'] + '/open5gs-upfd' if dep == binary else library_dir + '/' + name
            upf.write(target, data, 0o700 if dep == binary else 0o600)
            copied[name] = hashlib.sha256(data).hexdigest()
        check_ldd = upf.run(['env', 'LD_LIBRARY_PATH=' + library_dir, 'ldd', paths['upf'] + '/open5gs-upfd'])
        assert 'not found' not in check_ldd
        assert all(library_dir in line for line in check_ldd.splitlines() if 'libogs' in line or 'libprom.so' in line)
        result['artifacts'] = copied
        result['smf_sha256'] = core.run(['sha256sum', BUILD + '/src/smf/open5gs-smfd']).split()[0]
        print('Staged private binaries/configs; independent restoration armed', flush=True)
        ue.run(['systemctl', 'stop', originals['ue']], sudo=True)
        core.run(['systemctl', 'stop', originals['core']], sudo=True)
        upf.run(['systemctl', 'stop', originals['upf']], sudo=True)
        cases = ['off', 'final', 'renewal'] if args.case == 'all' else [args.case]
        for case in cases:
            quota = 2_300_000 if case == 'long' else 10000 if case == 'final' else 20000
            directories = {}
            for name, host in hosts.items():
                directory = paths[name] + '/' + case
                host.run(['mkdir', '-m', '700', directory])
                directories[name] = directory
            cdir, udir, edir = (directories[n] for n in ('core', 'upf', 'ue'))
            cconfig, uconfig = yaml.safe_load(smf_source), yaml.safe_load(upf_source)
            cconfig['logger'] = {'file': {'path': cdir + '/smf.log'}, 'level': 'info'}
            uconfig['logger'] = {'file': {'path': udir + '/upf.log'}, 'level': 'info'}
            cconfig['smf']['pfcp']['client']['upf'] = [u for u in cconfig['smf']['pfcp']['client']['upf'] if u.get('dnn') == 'internet']
            token, owner = secrets.token_urlsafe(40), str(uuid.uuid4())
            cconfig['smf']['chf'] = {'enabled': case != 'off', 'sbi': [{'addr': '127.0.0.1', 'port': 18081}],
                                    'requested_units': 10000, 'journal_dir': cdir + '/journal',
                                    'timeout_ms': 2000, 'retries': 2, 'nf_instance_id': owner}
            uconfig['upf']['charging_enforcement'] = case != 'off'
            core.write(cdir + '/smf.yaml', yaml.safe_dump(cconfig))
            upf.write(udir + '/upf.yaml', yaml.safe_dump(uconfig))
            ue.write(edir + '/ue.yaml', yaml.safe_dump(ue_config))
            # systemd EnvironmentFile strips unquoted JSON quotes: quote and escape the value.
            env = 'SMF_CHF_TOKEN=' + token + '\nCHF_SBI_TOKENS=\'' + json.dumps({owner: token}) + "'\n"
            env += 'CHF_DATABASE_PATH=' + cdir + '/charging.db\nCHF_SBI_LAB_NO_AUTH=false\n'
            core.write(cdir + '/runtime.env', env)
            init = ('import sys\nfrom pathlib import Path\nsys.path.insert(0,' + repr(CHF) + ')\n'
                    'from app.repository import ChargingRepository\n'
                    'r=ChargingRepository(Path(' + repr(cdir + '/charging.db') + '));r.initialize()\n' +
                    ('r.upsert_account(' + repr(supi) + ',' + str(quota) + ',True,"real-ue-acceptance")\n' if case != 'off' else ''))
            core.write(cdir + '/init.py', init)
            core.run([CHF + '/.venv/bin/python', cdir + '/init.py'])
            core.start(tag + '-chf', [CHF + '/.venv/bin/hypercorn', 'app.main:app', '--bind', '127.0.0.1:18081',
                                      '--access-logfile', '-', '--access-logformat', '%(H)s %(s)s %(r)s'], cdir,
                       properties=['--property=EnvironmentFile=' + cdir + '/runtime.env', '--property=Environment=PYTHONPATH=' + CHF])
            core.start(tag + '-pcap', ['/usr/bin/tcpdump', '-Z', 'root', '-i', 'any', '-U', '-s', '0', '-w', cdir + '/n4-sbi.pcap',
                                       'udp port 8805 or tcp port 18081 or sctp port 38412'], cdir)
            upf.start(tag + '-upf', [paths['upf'] + '/open5gs-upfd', '-c', udir + '/upf.yaml'], udir,
                      properties=['--property=Environment=LD_LIBRARY_PATH=' + library_dir])
            core.start(tag + '-core', [BUILD + '/src/smf/open5gs-smfd', '-c', cdir + '/smf.yaml'], cdir,
                       properties=['--property=EnvironmentFile=' + cdir + '/runtime.env'])
            time.sleep(4)
            core.run(['systemctl', 'is-active', tag + '-core', tag + '-chf'])
            upf.run(['systemctl', 'is-active', tag + '-upf'])
            # Reset simulator radio context between explicit UE process restarts.
            gnb.run(['systemctl', 'restart', 'ueransim-gnb'], sudo=True)
            ue.start(tag + '-ue', ['/home/emsadmin/UERANSIM/build/nr-ue', '-c', edir + '/ue.yaml'], edir)
            deadline = time.monotonic() + 50
            interface = None
            while time.monotonic() < deadline:
                interfaces = json.loads(ue.run(['ip', '-j', '-4', 'addr', 'show']))
                try:
                    process_log = ue.read(edir + '/' + tag + '-ue.log').decode(errors='replace')
                except FileNotFoundError:
                    process_log = ''
                interface = confirmed_interface(process_log, interfaces)
                if interface:
                    break
                time.sleep(1)
            assert interface, 'UE did not establish the requested internet PDU session'
            result.setdefault('interfaces', {})[case] = interface
            received_count = 0
            if case == 'long':
                # Exercise report-window rollover independently of TCP retransmission
                # stalls with tiny 10 KB grants. Video is tested at the live 500 KB grant.
                ping = ue.run(['ping', '-I', interface, '-s', '1000', '-c', '2400', '-i', '0.03', '-W', '1', '10.45.0.1'], check=False, timeout=120)
                (local / 'long-ping.txt').write_text(ping, encoding='utf-8')
                received = re.search(r'(\d+) packets transmitted, (\d+) received', ping)
                assert received and 256 < int(received[2]) < 2400, 'No sustained traffic followed by cut'
                received_count = int(received[2])
            else:
                ping = ue.run(['ping', '-I', interface, '-s', '1000', '-c', '20', '-i', '0.01' if args.burst else '0.4', '-W', '1', '10.45.0.1'], check=False, timeout=35)
                (local / (case + '-ping.txt')).write_text(ping, encoding='utf-8')
                received = re.search(r'(\d+) packets transmitted, (\d+) received', ping)
                assert received and int(received[2]) > 0, 'No delivered UE traffic before quota exhaustion'
                received_count = int(received[2])
            if case == 'off':
                assert received_count == 20, 'CHF-off forwarding regression'
            # Stop new simulator retry attempts before taking the final snapshot;
            # leave SMF/CHF running so already-started cancellations can settle.
            ue.run(['systemctl', 'stop', tag + '-ue'], sudo=True)
            # Allow N4 final report and Nchf settlement, then inspect the real DB.
            time.sleep(3)
            query = ('import json,sqlite3\nc=sqlite3.connect(' + repr(cdir + '/charging.db') + ');c.row_factory=sqlite3.Row\n'
                     'print(json.dumps({t:[dict(x) for x in c.execute("SELECT * FROM "+t)] for t in '
                     '["charging_accounts","charging_sessions","charging_cdrs","charging_events"]}))\n')
            core.write(cdir + '/query.py', query)
            data = json.loads(core.run(['python3', cdir + '/query.py']))
            if case == 'off':
                assert not data['charging_sessions'] and not data['charging_events']
                result['cases'][case] = {'status': 'PASS', 'received': 20, 'charging_requests': 0}
            else:
                # Preserve failure evidence BEFORE assertions.
                (local / (case + '-accounting.json')).write_text(json.dumps(data, indent=2))
                used_sessions = [s for s in data['charging_sessions'] if s['observed_bytes'] > 0]
                assert len(used_sessions) == 1, 'Expected one traffic-bearing session'
                session = used_sessions[0]
                # UERANSIM retries a configured session after NAS release. These
                # denied attempts must ALSO close, never leave orphan contexts.
                assert all(s['status'] == 'RELEASED' and s['reserved_bytes'] == 0 for s in data['charging_sessions']), 'Orphan accounting context'
                assert session['status'] == 'RELEASED' and session['reserved_bytes'] == 0, 'Native Release incomplete'
                assert session['consumed_bytes'] == quota, 'Missing final consumption'
                assert session['observed_bytes'] >= quota
                assert session['observed_bytes'] == session['uplink_bytes'] + session['downlink_bytes']
                assert len(data['charging_cdrs']) == len(data['charging_sessions'])
                assert received_count < (2400 if case == 'long' else 20), 'Quota did not stop subsequent traffic'
                ue_log = ue.read(edir + '/' + tag + '-ue.log').decode(errors='replace')
                assert 'PDU Session Release Command received' in ue_log, 'UE did not receive NAS Release'
                result['cases'][case] = {'status': 'PASS', 'received': received_count,
                                         'traffic': 'ICMP replies',
                                         'nas_release_received': True,
                                         'closed_zero_credit_attempts': len(data['charging_sessions']) - 1,
                                         **{k: session[k] for k in ('observed_bytes', 'consumed_bytes', 'overrun_bytes', 'reserved_bytes')}}
                if case == 'long':
                    updates = sum(event['operation'] == 'UPDATE' for event in data['charging_events'])
                    assert updates > 256, 'Long case did not cross old memory limit'
                    result['cases'][case]['updates'] = updates
                if args.burst and case == 'renewal':
                    smf_log = core.read(cdir + '/smf.log').decode(errors='replace')
                    queued = smf_log.count('CHF usage queued behind pending Update')
                    result['cases'][case]['queued_reports'] = queued
                    assert queued > 0, 'Burst did not exercise concurrent Update; not accepted as a queue regression test'
            print(json.dumps({case: result['cases'][case]}), flush=True)
            for name in ('ue', 'core', 'upf'):
                hosts[name].run(['systemctl', 'stop', *[u + '.service' for u in units[name]]], sudo=True, check=False)
            time.sleep(2)
    finally:
        # All evidence stays at unique per-host paths; never deletes/overwrites a prior run.
        result['evidence'] = paths
        for name in ('ue', 'core', 'upf'):
            host = hosts[name]
            if name in units:
                host.run(['systemctl', 'stop', *[u + '.service' for u in units[name]]], sudo=True, check=False)
        for name in ('upf', 'core', 'ue'):
            if name in originals:
                if name == 'ue':
                    # Active processes do not imply PFCP readiness. Starting UE
                    # early can select a different UPF while Internet UPF joins.
                    pid = core.run(['systemctl', 'show', '-p', 'MainPID', '--value',
                                    originals['core']], check=False).strip()
                    ready = False
                    for _ in range(30):
                        log = core.run(['journalctl', '_PID=' + pid, '-b', '--no-pager',
                                        '-n', '500'], sudo=True, check=False)
                        ready = bool(expected_upfs) and all(
                            'PFCP associated [' + address + ']' in log for address in expected_upfs)
                        if ready:
                            break
                        time.sleep(1)
                    result['restoration']['pfcp_ready'] = ready
                    gnb.run(['systemctl', 'restart', 'ueransim-gnb'], sudo=True, check=False)
                hosts[name].run(['systemctl', 'start', originals[name]], sudo=True, check=False)
                result['restoration'][name] = hosts[name].run(['systemctl', 'is-active', originals[name]], check=False).strip()
        if all(result['restoration'].get(n) == 'active' for n in originals):
            for name, timer in timers.items():
                hosts[name].run(['systemctl', 'stop', timer + '.timer'], sudo=True, check=False)
        (local / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'evidence': str(local), 'restoration': result['restoration']}), flush=True)
        for host in hosts.values():
            host.client.close()
        gnb.client.close()


if __name__ == '__main__':
    main()
