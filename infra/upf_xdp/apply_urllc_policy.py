"""Apply the verified URLLC quota candidates with CHF reconciliation and rollback.

No XDP policy gate is bypassed; this operation alone does not enable XDP.
Run from backend. Only the vehicle, SMF3 and URLLC UPF can be restarted.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'infra' / 'charging'))
sys.path.insert(0, str(ROOT / 'backend'))
from e2e_native import Lab
from app.core.config import get_settings
from app.services import terminal_devices, urllc_xdp

CANDIDATES = ROOT / '.work' / 'urllc-cutover-20261003T230931Z'
VEHICLE = 'imsi-999700000000002'
CLI = '/home/emsadmin/UERANSIM/build/nr-cli'
DB = '/home/emsadmin/maestro-charging/charging.sqlite3'


def accounting(core, owner):
    code = '''import sqlite3,json,sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute("SELECT charging_data_ref,supi,owner_nf,status,reserved_bytes,consumed_bytes,created_at,released_at FROM charging_sessions WHERE owner_nf=? AND supi=?",sys.argv[2:4])]
for r in rows:
 r['release_events']=[dict(e) for e in c.execute("SELECT operation,result_code,created_at FROM charging_events WHERE charging_data_ref=? AND operation='RELEASE'",(r['charging_data_ref'],))]
 r['cdr_count']=c.execute('SELECT count(*) FROM charging_cdrs WHERE charging_data_ref=?',(r['charging_data_ref'],)).fetchone()[0]
print(json.dumps(rows))'''
    return json.loads(core.run(['python3', '-c', code, DB, owner, VEHICLE], sudo=True))


def wait_for(check, message, seconds=45):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', required=True)
    parser.parse_args()
    s = get_settings()
    tag = 'urllc-policy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    local = ROOT / '.work' / tag
    local.mkdir()
    manifest = json.loads((CANDIDATES / 'manifest.json').read_text())
    hosts, remote, timers = {}, {}, []
    result = {'status': 'PREPARING', 'started_at': datetime.now(timezone.utc).isoformat(),
              'remote_backups': remote, 'protected': {}}
    touched = False
    committed = False
    try:
        for name, port in [('core', s.ssh_port), ('upf', s.upf_ssh_port), ('ue', s.ue_ssh_port), ('miot', s.upf2_ssh_port)]:
            hosts[name] = Lab(s, port)
        core, upf, ue = (hosts[x] for x in ('core', 'upf', 'ue'))
        for name, unit in [('core','open5gs-smfd'),('core','open5gs-smfd2'),('upf','open5gs-upfd'),('miot','open5gs-upfd'),('ue','ueransim-ue'),('ue','maestro-ue-sensor')]:
            result['protected'][name + ':' + unit] = hosts[name].run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState'])
        for unit, item in manifest['targets'].items():
            host = core if unit.startswith('open5gs-smfd') else upf
            digest = host.run(['sha256sum', item['path']], sudo=True).split()[0]
            if digest != item['sha256']:
                raise RuntimeError('Configuration drift: ' + unit)
            if not item.get('protected'):
                data = (CANDIDATES / (Path(item['path']).name + '.candidate')).read_bytes()
                if hashlib.sha256(data).hexdigest() != item['candidate_sha256']:
                    raise RuntimeError('Candidate hash mismatch')
        # Bind reconciliation to the actual CHF process database, not a test DB.
        check_db = "from pathlib import Path;import subprocess;pid=subprocess.check_output(['systemctl','show','open5gs-chfd','-p','MainPID','--value'],text=True).strip();e=dict(x.split('=',1) for x in Path('/proc/'+pid+'/environ').read_bytes().decode().split(chr(0)) if '=' in x);print(e.get('CHF_DATABASE_PATH',''))"
        if core.run(['python3','-c',check_db],sudo=True).strip() != DB:
            raise RuntimeError('CHF database path changed')
        config = yaml.safe_load((CANDIDATES/'smf3.yaml.original').read_text())
        owner = config['smf']['chf']['nf_instance_id']
        before = accounting(core, owner)
        refs = {r['charging_data_ref'] for r in before if r['status'] == 'OPEN'}
        if len(refs) != 1:
            raise RuntimeError('Expected one current vehicle charging reference')
        result['accounting_before'] = before
        unit = ue.run(['systemctl','show','maestro-ue-vehicle','-p','ExecStart','--value'])
        if 'vehicle.yaml' not in unit:
            raise RuntimeError('Unrecognized vehicle service')
        result['vehicle_before'] = ue.run([CLI,VEHICLE,'--exec','ps-list'])
        if '5g-plus' not in result['vehicle_before']:
            raise RuntimeError('Vehicle PDU not observed')
        # Backups and independent timers precede the first disruption.
        for name in ('core','upf','ue'):
            host = hosts[name]
            remote[name] = host.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
            nf = 'open5gs-smfd3' if name == 'core' else 'open5gs-upfd-urllc'
            if name != 'ue':
                item = manifest['targets'][nf]
                host.run(['cp','-a',item['path'],remote[name]+'/original.yaml'],sudo=True)
                host.run(['cp','-a',item['binary'],remote[name]+'/original-binary'],sudo=True)
                host.write(remote[name]+'/unit.txt',host.run(['systemctl','cat',nf]))
                host.write(remote[name]+'/candidate.yaml',(CANDIDATES/(Path(item['path']).name+'.candidate')).read_bytes())
                restore = '#!/bin/sh\nset -eu\nsystemctl stop '+nf+'\ncp -a '+shlex.quote(remote[name]+'/original.yaml')+' '+shlex.quote(item['path'])+'\nsystemctl start '+nf+'\n'
            else:
                host.write(remote[name]+'/unit.txt',host.run(['systemctl','cat','maestro-ue-vehicle']))
                restore = '#!/bin/sh\nset -eu\nsystemctl restart maestro-ue-vehicle\n'
            host.write(remote[name]+'/restore.sh',restore,0o700)
            host.run(['/bin/sh','-n',remote[name]+'/restore.sh'])
            timer = tag+'-'+name+'-restore'
            host.run(['systemd-run','--unit='+timer,'--on-active='+('300s' if name=='upf' else '310s' if name=='core' else '330s'),'/bin/sh',remote[name]+'/restore.sh'],sudo=True)
            timers.append((name,timer))
        print('Backups and independent restoration timers ready',flush=True)
        touched = True
        result['release_command'] = ue.run([CLI,VEHICLE,'--exec','deregister normal'])
        ue.run(['systemctl','stop','maestro-ue-vehicle'],sudo=True)
        def released():
            rows = accounting(core, owner)
            active = [r for r in rows if r['charging_data_ref'] in refs]
            if len(active) != len(refs): return None
            if any(r['status'] != 'RELEASED' or r['reserved_bytes'] or r['cdr_count'] != 1 or
                   not any(e['result_code'] == 'RELEASED' for e in r['release_events']) for r in active): return None
            return rows
        result['accounting_released'] = wait_for(released,'Vehicle CHF Release did not reconcile')
        journal_check = '''import json,sys
from pathlib import Path
refs=set(json.loads(sys.argv[1]));found={}
for p in Path('/var/lib/open5gs/chf-journal-smf3').iterdir():
 if p.name.startswith('charging-id'):continue
 rows=[json.loads(x) for x in p.read_text().splitlines() if x]
 for ref in refs:
  matched=[r for r in rows if ref in r.get('uri','')]
  if matched:found[ref]=matched[-1]
print(json.dumps(found))'''
        journals = json.loads(core.run(['python3','-c',journal_check,json.dumps(sorted(refs))],sudo=True))
        if set(journals) != refs or any(x.get('event')!='response_valid' or x.get('status')!=204 or not x.get('uri','').endswith('/release') for x in journals.values()):
            raise RuntimeError('SMF3 has not acknowledged the current Release')
        result['journal_release'] = journals
        print('Vehicle RELEASE confirmed: RELEASED, zero reservation, CDR, HTTP 204',flush=True)
        core.run(['systemctl','stop','open5gs-smfd3'],sudo=True)
        upf.run(['systemctl','stop','open5gs-upfd-urllc'],sudo=True)
        for name,nf in [('core','open5gs-smfd3'),('upf','open5gs-upfd-urllc')]:
            host=hosts[name];item=manifest['targets'][nf]
            if host.run(['sha256sum',item['path']],sudo=True).split()[0] != item['sha256']:
                raise RuntimeError('Configuration drift during release')
            # Overwrite bytes in place; retain actual root:open5gs / 0640 metadata.
            host.run(['cp','--no-preserve=mode,ownership',remote[name]+'/candidate.yaml',item['path']],sudo=True)
            host.run(['chown','--reference='+remote[name]+'/original.yaml',item['path']],sudo=True)
            host.run(['chmod','--reference='+remote[name]+'/original.yaml',item['path']],sudo=True)
            if host.run(['sha256sum',item['path']],sudo=True).split()[0] != item['candidate_sha256']:
                raise RuntimeError('Installed candidate hash mismatch')
        upf.run(['systemctl','start','open5gs-upfd-urllc'],sudo=True)
        core.run(['systemctl','start','open5gs-smfd3'],sudo=True)
        ue.run(['systemctl','start','maestro-ue-vehicle'],sudo=True)
        def session():
            data=yaml.safe_load(ue.run([CLI,VEHICLE,'--exec','ps-list'],check=False))
            if isinstance(data,dict):
                return next((p for p in data.values() if isinstance(p,dict) and p.get('state')=='PS-ACTIVE' and p.get('apn')=='5g-plus'),None)
        result['vehicle_after']=wait_for(session,'Vehicle did not reconnect',60)
        result['kernel_probe']=asyncio.run(terminal_devices.measure('vehicle','probe'))
        if result['kernel_probe']['received'] != 20:
            raise RuntimeError('Kernel MEC probe did not receive all 20 ACKs')
        result['agent_after']=asyncio.run(urllc_xdp.status())
        result['accounting_after']=accounting(core,owner)
        if any(r['status']=='OPEN' or r['reserved_bytes'] for r in result['accounting_after']):
            raise RuntimeError('Unexpected CHF reservation after migration')
        for key,expected in result['protected'].items():
            name,unit=key.split(':')
            if hosts[name].run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState']) != expected:
                raise RuntimeError('Protected service state changed: '+key)
        for unit,item in manifest['targets'].items():
            if item.get('protected'):
                host=core if unit.startswith('open5gs-smfd') else upf
                if host.run(['sha256sum',item['path']],sudo=True).split()[0]!=item['sha256']:
                    raise RuntimeError('Protected configuration changed: '+unit)
        result['status']='URLLC_UNMETERED_KERNEL_VERIFIED'
        committed=True
    finally:
        try:
            if touched and not committed:
                for name in ('upf','core','ue'):
                    hosts[name].run(['/bin/sh',remote[name]+'/restore.sh'],sudo=True)
                result['status']='ROLLED_BACK'
            for name,timer in timers:
                hosts[name].run(['systemctl','stop',timer+'.timer'],sudo=True)
        finally:
            result['finished_at']=datetime.now(timezone.utc).isoformat()
            (local/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps({'status':result['status'],'evidence':str(local)}),flush=True)
            for host in hosts.values():host.client.close()


if __name__ == '__main__':main()
