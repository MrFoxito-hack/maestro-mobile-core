"""Provision a second independent lab identity; never overwrite an existing UE/account.

SIM keys stay in protected files on VMs. Credit is experimental. No core restart.
"""
import argparse
import asyncio
import json
import secrets
import time
from uuid import uuid4
import yaml
from e2e_native import Lab, ROOT
from lab_command import get_settings
from app.services import terminal
from app.services.charging import management_get, management_request

SUPI = 'imsi-999700000000002'
UNIT = 'ueransim-ue-02'
CONFIG = '/home/emsadmin/UERANSIM/config/maestro-ue-02.yaml'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required: provisions independent UE identity and 50 MB lab account')
    settings = get_settings()
    core, ue = Lab(settings, settings.ssh_port), Lab(settings, settings.ue_ssh_port)
    stage = core.run(['mktemp', '-d', '/home/emsadmin/ue02-provision-XXXXXX']).strip()
    evidence = {'unit': UNIT, 'credit_bytes': 50_000_000, 'status': 'PREPARING'}
    created = False
    try:
        assert management_get('/admin/v1/accounts?supi=' + SUPI)['total'] == 0, 'Existing CHF identity; refusing overwrite'
        ue.run(['test', '!', '-e', CONFIG])
        ue.run(['test', '!', '-e', '/etc/systemd/system/' + UNIT + '.service'])
        config = yaml.safe_load(ue.read('/home/emsadmin/UERANSIM/config/open5gs-ue.yaml'))
        original = config['supi']
        config['supi'] = SUPI
        config['key'], config['op'] = secrets.token_hex(16).upper(), secrets.token_hex(16).upper()
        config['opType'] = 'OPC'
        for name in ('imei', 'imeiSv'):
            if name in config:
                config[name] = None
        assert {s['slice']['sd'] for s in config['sessions']} == {1, 2}, 'Migrate slices before provisioning'
        js = ('const c=db.getSiblingDB("open5gs").subscribers;'
              'if(c.findOne({imsi:' + json.dumps(SUPI[5:]) + '}))throw Error("Identity exists");'
              'const d=c.findOne({imsi:' + json.dumps(original[5:]) + '});if(!d)throw Error("Template missing");'
              'delete d._id;d.imsi=' + json.dumps(SUPI[5:]) + ';d.msisdn=[];'
              'd.security={k:' + json.dumps(config['key']) + ',opc:' + json.dumps(config['op']) + ',op:null,amf:' + json.dumps(str(config.get('amf', '8000'))) + ',sqn:0};'
              'd.slice.forEach(s=>{s._id=new ObjectId();(s.session||[]).forEach(p=>{p._id=new ObjectId();delete p.ue;});});'
              'c.insertOne(d);')
        core.write(stage + '/create.js', js)
        core.run(['chmod', '700', stage])
        core.run(['mongosh', '--quiet', '--file', stage + '/create.js'])
        created = True
        management_request('/admin/v1/accounts/' + SUPI, method='PUT', payload={'supi': SUPI, 'quotaBytes': 50_000_000, 'enabled': True})
        ue.write(CONFIG, yaml.safe_dump(config, sort_keys=False), 0o600)
        remote = ue.run(['mktemp', '-d', '/home/emsadmin/ue02-unit-XXXXXX']).strip()
        unit = ('[Unit]\nDescription=MAEstro independent second UERANSIM UE\nAfter=network-online.target\n'
                '[Service]\nExecStart=/home/emsadmin/UERANSIM/build/nr-ue -c ' + CONFIG + '\n'
                'Restart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n')
        ue.write(remote + '/unit', unit)
        ue.run(['install', '-m', '644', remote + '/unit', '/etc/systemd/system/' + UNIT + '.service'], sudo=True)
        ue.run(['systemctl', 'daemon-reload'], sudo=True)
        ue.run(['systemctl', 'start', UNIT], sudo=True)
        for _ in range(35):
            state = asyncio.run(terminal.snapshot(SUPI))
            if state['registered'] and len(state['apn_sessions']) == 2:
                evidence['sessions'] = state['apn_sessions']
                break
            time.sleep(1)
        else:
            raise RuntimeError('Second UE did not establish both sessions')
        ue.run(['systemctl', 'enable', UNIT], sudo=True)
        evidence['status'] = 'PASS'
        print('PASS: UE-02 registered with two PDU sessions and independent credit')
    except BaseException:
        evidence['status'] = 'INCOMPLETE' if created else 'NOT_APPLIED'
        if created:
            ue.run(['systemctl', 'stop', UNIT], sudo=True, check=False)
        # Preserve accounting/profile evidence; do not erase a potentially used identity.
        raise
    finally:
        core.run(['chown', '-R', 'root:root', stage], sudo=True, check=False)
        path = ROOT / '.work' / ('second-ue-' + uuid4().hex[:10] + '.json')
        path.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print('Evidence: ' + str(path))
        print('UE-02 stop/disable: sudo systemctl disable --now ' + UNIT)
        core.client.close()
        ue.client.close()


if __name__ == '__main__':
    main()
