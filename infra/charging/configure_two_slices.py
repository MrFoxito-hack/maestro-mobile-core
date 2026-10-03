"""Transactional lab migration to two observed S-NSSAIs, with per-host watchdogs.

Explicit --execute only. Does not change SIM secrets, CHF balance, N3/N4 or policies.
"""
import argparse
import asyncio
import hashlib
import json
import re
import shlex
import time
from pathlib import Path
from uuid import uuid4
import yaml
from e2e_native import Lab, ROOT
from lab_command import get_settings
from app.services import terminal

SLICES = [{'sst': 1, 'sd': '000001'}, {'sst': 1, 'sd': '000002'}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required: restarts the lab control plane and UE')
    settings = get_settings()
    hosts = {n: Lab(settings, p) for n, p in [('core', settings.ssh_port),
             ('gnb', settings.gnb_ssh_port), ('ue', settings.ue_ssh_port)]}
    core, gnb, ue = (hosts[n] for n in ('core', 'gnb', 'ue'))
    tag = 'maestro-slices-' + uuid4().hex[:10]
    stage, backups, timers = {}, {}, []
    local = ROOT / '.work' / tag
    local.mkdir()
    result = {'slices': SLICES, 'status': 'PREPARING'}
    mutated = False
    capture = False
    try:
        live_nodes = ue.run(['/home/emsadmin/UERANSIM/build/nr-cli', '--dump']).splitlines()
        if len([n for n in live_nodes if n.strip().startswith('imsi-')]) != 1:
            raise RuntimeError('Migration requires only the primary UE running; stop secondary UEs first')
        for name, host in hosts.items():
            stage[name] = host.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-XXXXXX']).strip()
            backups[name] = []
        executable = core.run(['systemctl', 'show', 'open5gs-smfd', '-p', 'ExecStart'])
        match = re.search(r'-c (/opt/maestro-charging/maestro-charging-[a-f0-9]+/smf.yaml)', executable)
        if not match:
            raise RuntimeError('Unknown active SMF configuration; refusing migration')
        active_smf = match[1]
        files = {'core': ['/etc/open5gs/amf.yaml', '/etc/open5gs/smf.yaml', active_smf, '/etc/open5gs/nssf.yaml'],
                 'gnb': ['/home/emsadmin/UERANSIM/config/open5gs-gnb.yaml'],
                 'ue': ['/home/emsadmin/UERANSIM/config/open5gs-ue.yaml']}
        supi = None
        for name, paths in files.items():
            host = hosts[name]
            for index, path in enumerate(paths):
                raw = host.run(['cat', path], sudo=True)
                config = yaml.safe_load(raw)
                if name == 'ue':
                    supi = config['supi']
                    assert re.fullmatch(r'imsi-\d{15}', supi)
                    # The subscription/core support both slices, while the UE
                    # requests only the selected DNN.
                    config['sessions'] = [{
                        'type': 'IPv4', 'apn': 'internet',
                        'slice': {'sst': 1, 'sd': 1},
                    }]
                    config['configured-nssai'] = [{'sst': 1, 'sd': 1}, {'sst': 1, 'sd': 2}]
                    config['default-nssai'] = [{'sst': 1, 'sd': 1}]
                elif name == 'gnb':
                    config['slices'] = [{'sst': 1, 'sd': 1}, {'sst': 1, 'sd': 2}]
                elif 'amf' in config:
                    for plmn in config['amf']['plmn_support']:
                        plmn['s_nssai'] = SLICES
                elif 'smf' in config:
                    config['smf']['info'] = [{'s_nssai': [dict(s, dnn=[dnn]) for s, dnn in zip(SLICES, ['internet', 'corporate'])]}]
                elif 'nssf' in config:
                    client = config['nssf']['sbi']['client']
                    template = client['nsi'][0]
                    client['nsi'] = [dict(template, s_nssai=s) for s in SLICES]
                backup, candidate = stage[name] + f'/original-{index}.yaml', stage[name] + f'/candidate-{index}.yaml'
                host.run(['cp', '-a', path, backup], sudo=True)
                host.write(candidate, yaml.safe_dump(config, sort_keys=False))
                backups[name].append((path, backup, candidate))
        assert supi
        mongo = 'db.getSiblingDB("open5gs").subscribers'
        selector = json.dumps({'imsi': supi.removeprefix('imsi-')})
        backup_js = ('const fs=require("fs");const c=' + mongo + ';const d=c.findOne(' + selector + ');'
                     'if(!d)throw Error("Subscriber missing");'
                     'fs.writeFileSync(' + json.dumps(stage['core'] + '/slice.ejson') + ',EJSON.stringify(d.slice),{mode:384});'
                     'const sessions=d.slice.flatMap(s=>s.session||[]);'
                     'const names=["internet","corporate"];'
                     'if(names.some(n=>sessions.filter(s=>s.name===n).length!==1))throw Error("DNN ambiguous");'
                     'const slices=names.map((n,i)=>({sst:1,sd:i===0?"000001":"000002",default_indicator:i===0,'
                     'session:sessions.filter(s=>s.name===n),_id:new ObjectId()}));'
                     'fs.writeFileSync(' + json.dumps(stage['core'] + '/new-slice.ejson') + ',EJSON.stringify(slices),{mode:384});')
        core.write(stage['core'] + '/prepare.js', backup_js)
        core.run(['mongosh', '--quiet', '--file', stage['core'] + '/prepare.js'])
        for action, filename in [('apply', 'new-slice.ejson'), ('restore', 'slice.ejson')]:
            js = 'const fs=require("fs"); const r=' + mongo + '.updateOne(' + selector + ',{$set:{slice:EJSON.parse(fs.readFileSync(' + json.dumps(stage['core'] + '/' + filename) + ',"utf8"))}});if(r.matchedCount!==1)throw Error("Subscriber missing");'
            core.write(stage['core'] + '/' + action + '.js', js)
        for name, host in hosts.items():
            rollback = '#!/bin/sh\nset -eu\n'
            for path, backup, _ in backups[name]:
                rollback += shlex.join(['cp', '-a', backup, path]) + '\n'
            if name == 'core':
                rollback += shlex.join(['mongosh', '--quiet', '--file', stage[name] + '/restore.js']) + '\n'
                rollback += 'systemctl restart open5gs-nssfd open5gs-smfd open5gs-amfd\n'
            else:
                rollback += 'systemctl restart ueransim-' + name + '\n'
            host.write(stage[name] + '/rollback.sh', rollback, 0o700)
            host.run(['chown', '-R', 'root:root', stage[name]], sudo=True)
            timer = tag + '-' + name
            delay = {'core': 300, 'gnb': 315, 'ue': 330}[name]
            host.run(['systemd-run', '--unit=' + timer, '--on-active=' + str(delay) + 's', '/bin/sh', stage[name] + '/rollback.sh'], sudo=True)
            timers.append((name, timer))
        asyncio.run(terminal.airplane(True))
        time.sleep(3)
        mutated = True
        for name, host in hosts.items():
            for path, _, candidate in backups[name]:
                # Copy contents only; preserve original ownership/mode on the target.
                host.run(['cp', candidate, path], sudo=True)
        core.run(['mongosh', '--quiet', '--file', stage['core'] + '/apply.js'], sudo=True)
        core.run(['systemd-run', '--unit=' + tag + '-capture', '--collect', '--property=RuntimeMaxSec=220',
                  '/usr/bin/tcpdump', '-Z', 'root', '-i', 'any', '-U', '-s', '0', '-w', stage['core'] + '/slices.pcap',
                  'sctp port 38412 or udp port 8805 or tcp port 7777 or tcp port 8081'], sudo=True)
        capture = True
        core.run(['systemctl', 'restart', 'open5gs-nssfd', 'open5gs-smfd', 'open5gs-amfd'], sudo=True)
        pid = core.run(['systemctl', 'show', 'open5gs-smfd', '-p', 'MainPID', '--value']).strip()
        for _ in range(30):
            logs = core.run(['journalctl', '_PID=' + pid, '-b', '-n', '500', '--no-pager'], sudo=True)
            if all('PFCP associated [' + ip + ']' in logs for ip in ('10.210.50.8', '10.210.50.9')):
                break
            time.sleep(1)
        else:
            raise RuntimeError('UPFs did not associate')
        gnb.run(['systemctl', 'restart', 'ueransim-gnb'], sudo=True)
        time.sleep(3)
        ue.run(['systemctl', 'start', 'ueransim-ue'], sudo=True)
        for _ in range(45):
            state = asyncio.run(terminal.snapshot())
            sessions = {s['apn']: s for s in state.get('apn_sessions', [])}
            if state['registered'] and all(sessions.get(d, {}).get('snssai') == {'sst': 1, 'sd': n}
                                           for d, n in [('internet', 1), ('corporate', 2)]):
                result['sessions'] = sessions
                break
            time.sleep(1)
        else:
            raise RuntimeError('UE did not negotiate both target S-NSSAIs')
        result['status'] = 'PASS'
        for name, timer in timers:
            hosts[name].run(['systemctl', 'stop', timer + '.timer'], sudo=True)
        print('PASS: both target S-NSSAIs negotiated; watchdogs cancelled', flush=True)
    except BaseException:
        result['status'] = 'ROLLED_BACK' if mutated else 'NOT_APPLIED'
        restored = True
        if mutated:
            for name in ('core', 'gnb', 'ue'):
                try:
                    hosts[name].run(['/bin/sh', stage[name] + '/rollback.sh'], sudo=True)
                except Exception:
                    restored = False
                time.sleep(3)
        if restored:
            for name, timer in timers:
                hosts[name].run(['systemctl', 'stop', timer + '.timer'], sudo=True, check=False)
        else:
            result['status'] = 'ROLLBACK_PENDING_WATCHDOG'
        raise
    finally:
        if capture:
            core.run(['systemctl', 'stop', tag + '-capture'], sudo=True, check=False)
        result['remote_evidence'] = stage
        (local / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print('Evidence: ' + str(local), flush=True)
        for name, directory in stage.items():
            print(name + ' rollback: sudo /bin/sh ' + directory + '/rollback.sh', flush=True)
        for host in hosts.values():
            host.client.close()


if __name__ == '__main__':
    main()
