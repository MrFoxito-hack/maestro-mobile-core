"""Bounded C5 acceptance; never invokes C3 or restarts any SMF/UPF.

Only UE001's spendable byte credit is temporarily reduced. The original quota
is restored in finally and by an independent Core systemd timer. Historical
reservations are retained. Run from backend with --execute.
"""
import argparse
import json
import re
import shlex
import time
from datetime import datetime, timezone
from uuid import uuid4

import yaml

from e2e_native import Lab, ROOT
from lab_command import get_settings

CLI = '/home/emsadmin/UERANSIM/build/nr-cli'
SUPI = 'imsi-999700000000001'
ENV = '/home/emsadmin/maestro-charging/management/management.env'
API = '''import json,pathlib,urllib.request
env=dict(x.split('=',1) for x in pathlib.Path(ENV).read_text().splitlines() if '=' in x and not x.startswith('#'))
req=urllib.request.Request('http://127.0.0.1:8082'+PATH,
 data=json.dumps(BODY).encode() if BODY is not None else None, method=METHOD,
 headers={'Authorization':'Bearer '+env['CHF_ADMIN_TOKEN'].strip("'\\\""),'Content-Type':'application/json'})
print(urllib.request.urlopen(req,timeout=15).read().decode())
'''


def api(core, path, body=None, method='GET'):
    code = f'ENV={ENV!r}\nPATH={path!r}\nBODY={body!r}\nMETHOD={method!r}\n' + API
    return json.loads(core.run(['python3', '-c', code]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', required=True)
    parser.parse_args()
    out = ROOT / '.work/c5-charging/evidence'
    out.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    core, ue = Lab(settings, 2222), Lab(settings, 2226)
    result = {'startedAt': datetime.now(timezone.utc).isoformat(), 'scope': 'C5 CHF and UE001 only'}
    original = None
    changed = False
    timer = 'c5-quota-restore-' + uuid4().hex[:8]
    try:
        result['ready'] = api(core, '/ready')
        result['accountsBefore'] = [api(core, '/admin/v1/accounts/imsi-99970000000000' + str(i)) for i in range(1, 7)]
        policies = [dict(dnn='internet', sst=1, sd='000001', ratingGroup=1, mode='BYTE_QUOTA'),
                    dict(dnn='5g-plus', sst=2, sd='000002', ratingGroup=1, mode='ZERO_RATED')]
        result['policies'] = [api(core, '/admin/v1/service-policies', p, 'PUT') for p in policies]
        result['otherServicesEvidence'] = 'services-latest.json (separate native acceptance)'
        result['pduBefore'] = ue.run([CLI, SUPI, '-e', 'ps-list'])
        pdu = yaml.safe_load(result['pduBefore'])
        active = next(v for v in pdu.values() if v.get('state') == 'PS-ACTIVE' and v.get('apn') == 'internet')
        address = active['address']
        links = json.loads(ue.run(['ip', '-j', '-4', 'address']))
        interface = next(i['ifname'] for i in links if any(a['local'] == address for a in i.get('addr_info', [])))
        result['interface'] = interface
        # Prove reachable HTTP on the UE route before reducing credit.
        result['httpBefore'] = ue.run(['curl', '--interface', address, '--max-time', '8', '-sS', '-o', '/dev/null', '-w', '%{http_code} %{size_download}', 'http://10.210.50.1:18090/probe.bin'], check=False)
        result['pingBefore'] = ue.run(['ping', '-I', interface, '-c', '3', '-W', '1', '10.45.0.1'], check=False)
        assert '3 received' in result['pingBefore'], 'baseline UE traffic is not reachable'
        original = api(core, '/admin/v1/accounts/' + SUPI)
        directory = core.run(['mktemp', '-d', '/home/emsadmin/c5-quota-restore-XXXXXX']).strip()
        restore_body = {'supi': SUPI, 'quotaBytes': original['quota_bytes'], 'enabled': bool(original['enabled'])}
        restore = f'ENV={ENV!r}\nPATH={"/admin/v1/accounts/" + SUPI!r}\nBODY={restore_body!r}\nMETHOD="PUT"\n' + API
        core.write(directory + '/restore.py', restore)
        core.run(['systemd-run', '--unit=' + timer, '--on-active=180s', '/usr/bin/python3', directory + '/restore.py'], sudo=True)
        # Existing grant remains authorized; no stale reservation is reclaimed.
        current = api(core, '/admin/v1/accounts/' + SUPI)
        result['limited'] = api(core, '/admin/v1/accounts/' + SUPI,
            {'supi': SUPI, 'quotaBytes': current['consumed_bytes'] + current['reserved_bytes'], 'enabled': True}, 'PUT')
        changed = True
        result['trafficUntilCut'] = ue.run(['ping', '-I', interface, '-s', '1200', '-c', '600', '-i', '0.01', '-W', '1', '10.45.0.1'], sudo=True, check=False, timeout=30)
        result['accountAfterTraffic'] = api(core, '/admin/v1/accounts/' + SUPI)
        result['pduAfterTraffic'] = ue.run([CLI, SUPI, '-e', 'ps-list'], check=False)
        result['pingAtZero'] = ue.run(['ping', '-I', address, '-c', '5', '-W', '1', '10.45.0.1'], check=False, timeout=10)
        result['cdrsAfterCut'] = api(core, '/admin/v1/cdrs?supi=' + SUPI + '&limit=100')
        topup_started = time.monotonic()
        result['topup'] = api(core, '/admin/v1/accounts/' + SUPI + '/topup',
                             {'requestId': str(uuid4()), 'amountBytes': 1000000}, 'POST')
        for _ in range(40):
            result['pduAfterTopup'] = ue.run([CLI, SUPI, '-e', 'ps-list'], check=False)
            pdus = yaml.safe_load(result['pduAfterTopup']) or {}
            active = next((v for v in pdus.values() if isinstance(v, dict) and v.get('state') == 'PS-ACTIVE' and v.get('apn') == 'internet'), None)
            if active:
                address = active['address']
                break
            time.sleep(0.5)
        result['pduRecoverySeconds'] = time.monotonic() - topup_started
        result['pingAfterTopup'] = ue.run(['ping', '-I', address, '-c', '5', '-W', '1', '10.45.0.1'], check=False, timeout=10)
        result['httpAfterTopup'] = ue.run(['curl', '--interface', address, '--max-time', '8', '-sS', '-o', '/dev/null', '-w', '%{http_code} %{size_download}', 'http://10.210.50.1:18090/probe.bin'], check=False)
        result['httpRecoverySeconds'] = time.monotonic() - topup_started
        assert result['accountAfterTraffic']['available_bytes'] == 0, 'credit was not exhausted'
        assert not result['pduAfterTraffic'].strip(), 'PDU cut was not observed'
        assert result['httpAfterTopup'] == '200 262144', 'HTTP recovery was not observed'
        result['status'] = 'EMBB_PASS'
    except Exception as exc:
        result['status'] = 'INCOMPLETE'
        result['error'] = str(exc)
    finally:
        if changed:
            try:
                result['restored'] = api(core, '/admin/v1/accounts/' + SUPI,
                    {'supi': SUPI, 'quotaBytes': original['quota_bytes'], 'enabled': bool(original['enabled'])}, 'PUT')
                core.run(['systemctl', 'stop', timer + '.timer'], sudo=True, check=False)
            except Exception as exc:
                result['restorationError'] = str(exc)
        result['finishedAt'] = datetime.now(timezone.utc).isoformat()
        (out / 'live-acceptance.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        core.client.close()
        ue.client.close()
    print(json.dumps({k: v for k, v in result.items() if k in ('status', 'error', 'ready', 'restorationError')}))
    if result['status'] == 'INCOMPLETE' or 'restorationError' in result:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
