"""Explicit live acceptance through MAEstro's actual HTTP API and live CHF DB.

Creates the lab UE account only if absent. Does not reset consumption or CDRs.
The capture and ledger remain visible in MAEstro. No credentials are printed.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml
from e2e_native import Lab, CHF, ROOT
from lab_command import get_settings

sys.path.insert(0, str(ROOT / 'backend'))
from app.core.security import create_token
from app.db import connection


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--replenish-pilot', action='store_true', help='Add 1 MiB to existing lab quota without resetting consumption')
    args = p.parse_args()
    if not args.execute:
        p.error('--execute required')
    settings = get_settings()
    core, ue = Lab(settings, settings.ssh_port), Lab(settings, settings.ue_ssh_port)
    task_id = None
    try:
        supi = yaml.safe_load(ue.read('/home/emsadmin/UERANSIM/config/open5gs-ue.yaml'))['supi']
        if args.replenish_pilot:
            script = ('import sys,json\nfrom pathlib import Path\nsys.path.insert(0,' + repr(CHF) + ')\n'
                      'from app.repository import ChargingRepository\nr=ChargingRepository(Path("/home/emsadmin/maestro-charging/charging.sqlite3"))\n'
                      's=' + repr(supi) + '\na=r.get_account(s)\nassert a\n'
                      'a=r.upsert_account(s,a["quota_bytes"]+1048576,True,"MAEstro-post-acceptance-pilot-credit")\n'
                      'print(json.dumps({k:a[k] for k in ("quota_bytes","consumed_bytes","reserved_bytes","available_bytes")}))')
            print(core.run([CHF + '/.venv/bin/python', '-c', script]))
            return
        with connection() as conn:
            user = conn.execute("SELECT username,role,testbed FROM users WHERE enabled=1 AND role='teacher' ORDER BY username LIMIT 1").fetchone()
        if not user:
            raise RuntimeError('No enabled teacher exists')
        client = httpx.Client(base_url='http://127.0.0.1:8000/api/v1', timeout=120,
                              headers={'Authorization': 'Bearer ' + create_token(user['username'], user['role'])})
        script = ('import sys,json\nfrom pathlib import Path\nsys.path.insert(0,' + repr(CHF) + ')\n'
                  'from app.repository import ChargingRepository\nr=ChargingRepository(Path("/home/emsadmin/maestro-charging/charging.sqlite3"))\n'
                  's=' + repr(supi) + '\na=r.get_account(s)\n'
                  'if a is None: a=r.upsert_account(s,20000,True,"MAEstro-live-acceptance")\n'
                  'print(json.dumps({k:v for k,v in a.items() if k!="supi"}))\n')
        account = json.loads(core.run([CHF + '/.venv/bin/python', '-c', script]))
        if account['consumed_bytes'] or account['reserved_bytes']:
            raise RuntimeError('Existing account is not a fresh test account; no reset performed')
        response = client.post('/traces/subscriber', json={
            'name': 'CHF - aceptación operativa Nchf', 'scenario_id': '5g-sa',
            'testbed_id': user['testbed'] or 'local', 'identifier': supi.removeprefix('imsi-'),
            'duration_seconds': 120, 'max_megabytes': 50, 'include_sbi': True,
            'include_user_plane': True, 'auto_trigger': True,
            'procedures': ['registration', 'authentication', 'pdu-session', 'user-plane']})
        if response.status_code == 422:
            print([{k: v for k, v in e.items() if k in ('loc', 'msg', 'type')} for e in response.json().get('detail', [])])
        response.raise_for_status()
        task_id = response.json()['id']
        print('Capturing live MAEstro task ' + task_id, flush=True)
        time.sleep(3)
        interfaces = json.loads(ue.run(['ip', '-j', '-4', 'addr', 'show']))
        selected = [(i['ifname'], a['local'].rsplit('.', 1)[0] + '.1') for i in interfaces
                    for a in i.get('addr_info', []) if i['ifname'].startswith('uesimtun') and a['local'].startswith(('10.45.', '10.46.'))]
        if not selected:
            raise RuntimeError('No UE PDU interface; retained trace for troubleshooting')
        for interface, gateway in selected:
            ue.run(['ping', '-I', interface, '-s', '1000', '-c', '20', '-i', '0.2', '-W', '1', gateway], check=False, timeout=35)
        time.sleep(4)
        client.post('/traces/' + task_id + '/stop').raise_for_status()
        for _ in range(60):
            response = client.get('/traces/' + task_id)
            response.raise_for_status()
            if response.json()['status'] in {'completed', 'failed', 'interrupted'}:
                break
            time.sleep(1)
        response = client.get('/traces/' + task_id + '/analysis')
        response.raise_for_status()
        analysis = response.json()
        events = analysis.get('events', [])
        nchf = [e for e in events if e.get('charging')]
        summary = {'trace_id': task_id, 'events': len(events), 'nchf_events': len(nchf),
                   'protocols': sorted({e['protocol'] for e in events}),
                   'operations': sorted({e['charging']['operation'] for e in nchf}),
                   'units_decoded': sum(bool(e['charging']['units']) for e in nchf)}
        for collection in ('accounts', 'sessions', 'cdrs'):
            r = client.get('/charging/' + collection)
            r.raise_for_status()
            summary[collection] = r.json()
        (ROOT / '.work' / ('ems-live-' + task_id + '.json')).write_text(json.dumps(summary, indent=2))
        print(json.dumps({k:v for k,v in summary.items() if k not in ('accounts', 'sessions', 'cdrs')}, indent=2))
        assert nchf, 'No correlated Nchf events: capture retained'
        assert {'Create', 'Release'} <= set(summary['operations'])
        assert summary['units_decoded'] > 0
    finally:
        if task_id:
            try:
                client.post('/traces/' + task_id + '/stop')
            except Exception:
                pass
        core.client.close()
        ue.client.close()


if __name__ == '__main__':
    main()
