"""Live dual-SMF acceptance and evidence collection (Paramiko only)."""
import argparse
import json
import shlex
from pathlib import Path
import sys
import time
import hashlib
import yaml
from deploy_dual_smf import STATE, Lab, get_settings, info, evidence, save, CLI

sys.stdout.reconfigure(encoding='utf-8')
state = json.loads(STATE.read_text())
settings = get_settings()
core, ue, gnb = [Lab(settings, port) for port in (settings.ssh_port, settings.ue_ssh_port, settings.gnb_ssh_port)]
action = sys.argv[1]
try:
    if action == 'subscriber':
        query = "print(JSON.stringify(db.subscribers.findOne({imsi:'999700000000021'},{security:0,_id:0})))"
        print(core.run(['mongosh', '--quiet', 'open5gs', '--eval', query], sudo=True), flush=True)
    elif action == 'account21':
        from app.services.charging import management_get, management_request
        from fastapi import HTTPException
        path = '/admin/v1/accounts/imsi-999700000000021'
        try:
            account = management_get(path)
        except HTTPException as exc:
            print(str(exc.detail), flush=True)
            if exc.status_code == 404 or '404' in str(exc.detail):
                account = management_request(path, method='PUT', payload={
                    'supi': 'imsi-999700000000021', 'quotaBytes': 50 * 1024 * 1024, 'enabled': True})
                state['ue21_chf_account_created'] = True
                save(state)
            else:
                raise
        print(json.dumps(account), flush=True)
        evidence(state, 'ue21-chf-account.json', account)
    elif action == 'repair21':
        # The pre-existing subscriber lacks UE-AMBR; UDR rejects am-data (400).
        query = "print(JSON.stringify(db.subscribers.findOne({imsi:'999700000000021'})))"
        original = core.run(['mongosh', '--quiet', 'open5gs', '--eval', query], sudo=True)
        subscriber = json.loads(original)
        assert 'ambr' not in subscriber
        core.write(state['remote'] + '/subscriber21-before.json', original, 0o600)
        undo = "db.subscribers.updateOne({imsi:'999700000000021'},{$unset:{ambr:''}});"
        core.write(state['remote'] + '/restore-subscriber21.js', undo, 0o600)
        old = core.run(['cat', state['remote'] + '/rollback.sh'], sudo=True)
        old = old.replace('date -Is\n', 'date -Is\n' + shlex.join(['mongosh', '--quiet', 'open5gs', '--file', state['remote'] + '/restore-subscriber21.js']) + '\n')
        core.write(state['remote'] + '/rollback-with21.sh', old, 0o700)
        core.run(['install', '-o', 'root', '-m', '0700', state['remote'] + '/rollback-with21.sh', state['remote'] + '/rollback.sh'], sudo=True)
        change = "print(JSON.stringify(db.subscribers.updateOne({imsi:'999700000000021',ambr:{$exists:false}},{$set:{ambr:{downlink:{value:1,unit:3},uplink:{value:1,unit:3}}}})))"
        print(core.run(['mongosh', '--quiet', 'open5gs', '--eval', change], sudo=True), flush=True)
        state['subscriber21_ue_ambr_added'] = True
        save(state)
        ue.run(['systemctl', 'restart', 'ueransim-ue-21'], sudo=True)
    elif action == 'connect21':
        query = "print(JSON.stringify(db.subscribers.findOne({imsi:'999700000000021'})))"
        subscriber = json.loads(core.run(['mongosh', '--quiet', 'open5gs', '--eval', query], sudo=True))
        assert subscriber and subscriber['slice'][0]['sd'] == '000002'
        config = yaml.safe_load(ue.read('/home/emsadmin/UERANSIM/config/maestro-ue-03.yaml'))
        config['supi'] = 'imsi-999700000000021'
        config['key'] = subscriber['security']['k']
        config['op'] = subscriber['security'].get('opc') or subscriber['security']['op']
        config['opType'] = 'OPC' if subscriber['security'].get('opc') else 'OP'
        config['amf'] = subscriber['security'].get('amf', '8000')
        config['sessions'] = [{'type': 'IPv4', 'apn': 'corporate', 'slice': {'sst': 1, 'sd': 2}}]
        config['configured-nssai'] = [{'sst': 1, 'sd': 2}]
        config['default-nssai'] = [{'sst': 1, 'sd': 2}]
        path = '/home/emsadmin/UERANSIM/config/maestro-ue-21.yaml'
        ue.write(path, yaml.safe_dump(config, sort_keys=False), 0o600)
        unit = '''[Unit]
Description=MAEstro UERANSIM UE 21 (Corporate Slice)
After=network-online.target
[Service]
Type=simple
ExecStart=/home/emsadmin/UERANSIM/build/nr-ue -c /home/emsadmin/UERANSIM/config/maestro-ue-21.yaml
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
'''
        ue.write(state['ue_remote'] + '/ueransim-ue-21.service', unit)
        old = ue.read(state['ue_remote'] + '/rollback.sh').decode()
        new = old.replace('set -eu\n', 'set -eu\nsystemctl disable --now ueransim-ue-21.service || true\n')
        ue.write(state['ue_remote'] + '/rollback-with21.sh', new, 0o700)
        ue.run(['cp', state['ue_remote'] + '/rollback-with21.sh', state['ue_remote'] + '/rollback.sh'])
        ue.run(['install', '-m', '0644', state['ue_remote'] + '/ueransim-ue-21.service', '/etc/systemd/system/ueransim-ue-21.service'], sudo=True)
        ue.run(['systemctl', 'daemon-reload'], sudo=True)
        ue.run(['systemctl', 'enable', '--now', 'ueransim-ue-21'], sudo=True)
        for attempt in range(30):
            output = ue.run([CLI, config['supi'], '--exec', 'ps-list'], check=False)
            if 'PS-ACTIVE\n' in output and '10.46.' in output:
                break
            time.sleep(1)
        evidence(state, 'imsi-999700000000021-pdu.txt', output)
        print(output, flush=True)
        assert 'PS-ACTIVE\n' in output and '10.46.' in output
        state['ue21_connected'] = True
        save(state)
    elif action in ('recover', 'acceptance'):
        # The gNB cannot re-select its AMF after the latter restarts: restore N2.
        if not state.get('gnb_rollback'):
            gnb.run(['systemd-run', '--unit=' + state['timer'], '--on-active=27m', '/bin/systemctl', 'restart', 'ueransim-gnb'], sudo=True)
            state['gnb_rollback'] = True
            save(state)
        ue.run(['systemctl', 'stop', 'ueransim-watchdog'], sudo=True)
        terminals = [('imsi-999700000000001', 'ueransim-ue'),
                           ('imsi-999700000000003', 'ueransim-ue-03'),
                           ('imsi-999700000000004', 'ueransim-ue-04'),
                           ('imsi-999700000000005', 'ueransim-ue-05')]
        if action == 'acceptance':
            terminals.append(('imsi-999700000000021', 'ueransim-ue-21'))
        for supi, unit in terminals:
            ue.run([CLI, supi, '--exec', 'deregister switch-off'], check=False)
            ue.run(['systemctl', 'stop', unit], sudo=True)
        time.sleep(1)
        if action == 'acceptance':
            state['sbi_capture'] = 'dual_smf_acceptance_sbi.pcap'
            state['n4_capture'] = 'dual_smf_acceptance_n4.pcap'
            state['capture_units'] = ['maestro-dual-smf-acceptance-sbi', 'maestro-dual-smf-acceptance-n4']
            save(state)
            for unit, interface, bpf, filename in zip(state['capture_units'], ['lo', state['interface']],
                    ['tcp port 7777', 'udp port 8805'], [state['sbi_capture'], state['n4_capture']]):
                core.run(['systemd-run', '--unit=' + unit, '--property=RuntimeMaxSec=600', '/usr/bin/tshark',
                          '-i', interface, '-f', bpf, '-w', '/tmp/' + filename], sudo=True)
            time.sleep(2)
            for unit in state['capture_units']:
                assert core.run(['systemctl', 'is-active', unit]).strip() == 'active'
            # Fresh SCP connections are required to decode HPACK/NRF from capture start.
            core.run(['systemctl', 'stop', 'open5gs-smfd', 'open5gs-smfd2', 'open5gs-amfd', 'open5gs-nssfd', 'open5gs-bsfd', 'open5gs-pcfd'], sudo=True)
            core.run(['systemctl', 'restart', 'open5gs-scpd'], sudo=True)
            core.run(['systemctl', 'start', 'open5gs-nssfd', 'open5gs-bsfd', 'open5gs-pcfd', 'open5gs-smfd', 'open5gs-smfd2', 'open5gs-amfd'], sudo=True)
        else:
            core.run(['systemctl', 'restart', 'open5gs-smfd', 'open5gs-smfd2', 'open5gs-amfd'], sudo=True)
        time.sleep(2)
        gnb.run(['systemctl', 'restart', 'ueransim-gnb'], sudo=True)
        time.sleep(2)
        ue.run(['systemctl', 'start', *[unit for _, unit in terminals]], sudo=True)
        for attempt in range(40):
            one, two = info(core, '127.0.0.4', 9090), info(core, '127.0.0.15', 9091)
            good1 = sum(p['pdu_state'] == 'active' for i in one['items'] for p in i['pdu'])
            good2 = sum(p['pdu_state'] == 'active' for i in two['items'] for p in i['pdu'])
            if good1 >= 4 and good2 >= (4 if action == 'acceptance' else 3):
                break
            time.sleep(1)
        evidence(state, 'recovery-smf1.json', one)
        evidence(state, 'recovery-smf2.json', two)
        if state['watchdog_active']:
            ue.run(['systemctl', 'start', 'ueransim-watchdog'], sudo=True)
        print(json.dumps({'internet_active': good1, 'corporate_active': good2}), flush=True)
        assert good1 >= 4 and good2 >= (4 if action == 'acceptance' else 3)
        if action == 'acceptance':
            state['ue21_connected'] = True
            save(state)
    elif action == 'signal':
        output = core.run(['tshark', '-r', '/tmp/nssf_nsselection_smf2.pcap', '-d', 'tcp.port==7777,http2',
                           '-Y', 'http2.headers.path contains "nnssf"', '-T', 'fields', '-e', 'frame.number', '-e', 'ip.src', '-e', 'ip.dst', '-e', 'tcp.stream', '-e', 'http2.streamid', '-e', 'http2.headers.path'], sudo=True)
        evidence(state, 'nssf-requests.tsv', output)
        print(output, flush=True)
    elif action in ('export', 'decode'):
        for unit in state.get('capture_units', ['maestro-dual-smf-sbi-v2', 'maestro-dual-smf-pfcp-v2']):
            core.run(['systemctl', 'stop', unit], sudo=True, check=False)
        for filename in (state.get('sbi_capture', 'nssf_nsselection_smf2.pcap'), state.get('n4_capture', 'n4_dual_smf.pcap')):
            core.run(['install', '-o', settings.ssh_user, '-m', '0600', '/tmp/' + filename, state['remote'] + '/' + filename], sudo=True)
            with core.client.open_sftp() as sftp:
                sftp.get(state['remote'] + '/' + filename, str(Path(state['local']) / filename))
        decoded = core.run(['tshark', '-r', '/tmp/' + state.get('sbi_capture', 'nssf_nsselection_smf2.pcap'), '-d', 'tcp.port==7777,http2',
                            '-Y', 'http2', '-T', 'json', '--no-duplicate-keys', '-J', 'frame ip tcp http2'], sudo=True, timeout=60)
        packets = json.loads(decoded[decoded.index('['):])
        evidence(state, 'http2-decoded.json', packets)
        nssf = [p for p in packets if '127.0.0.14' in json.dumps(p['_source']['layers'].get('ip', {}))]
        print('Decoded HTTP/2 packets: ' + str(len(packets)), flush=True)
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(state['local']).glob('*.pcap')}
        evidence(state, 'pcap-sha256.json', hashes)
        print(json.dumps(hashes), flush=True)
finally:
    for host in (core, ue, gnb):
        host.client.close()
