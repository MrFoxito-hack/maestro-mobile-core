"""Bounded real traffic prerequisite probe; never declares closed-loop success.

The existing PCF is not replaced or restarted. A private iperf server and
capture expire independently of this controller. Only a missing UE host route
may be added, with an independent cleanup timer. CHF is read-only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from app.services.nwdaf import get_health, nwdaf_request


def accounts(core):
    return json.loads(core.run(['python3', '-c', """import sqlite3,json
c=sqlite3.connect('file:/home/emsadmin/maestro-charging/charging.sqlite3?mode=ro',uri=True)
c.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in c.execute("SELECT a.supi,a.quota_bytes,a.consumed_bytes,COALESCE((SELECT SUM(s.reserved_bytes) FROM charging_sessions s WHERE s.supi=a.supi AND s.status='OPEN'),0) AS reserved_bytes FROM charging_accounts a")]))
"""]))


def tun_sample(upf):
    values = upf.run(['cat', '/sys/class/net/ogstun/statistics/tx_bytes', '/proc/uptime']).splitlines()
    return {'bytes': int(values[0]), 'uptime': float(values[1].split()[0]), 'utc': datetime.now(timezone.utc).isoformat()}


def rate(a, b):
    return (b['bytes'] - a['bytes']) * 8 / (b['uptime'] - a['uptime'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute required: up to 8 seconds of 19 Mbps DL traffic')
    sys.stdout.reconfigure(encoding='utf-8')
    settings = get_settings()
    tag = 'nwdaf-g3-' + uuid.uuid4().hex[:10]
    local = ROOT / '.work' / tag
    local.mkdir(parents=True)
    result = {'run': tag, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'gate3': 'NOT_PASSED', 'scope': 'live prerequisite and traffic probe, no actuator deployment',
              'offered_bps': 19000000, 'duration_seconds': 8, 'budget_bps': 20000000}
    hosts = {}
    units = []
    route = False
    timer = False
    remote = None
    try:
        for name, port in [('core', settings.ssh_port), ('upf', settings.upf_ssh_port), ('ue', settings.ue_ssh_port)]:
            hosts[name] = Lab(settings, port)
        core, upf, ue = (hosts[x] for x in ('core', 'upf', 'ue'))
        supi = 'imsi-999700000000001'
        native = yaml.safe_load(ue.run(['/home/emsadmin/UERANSIM/build/nr-cli', supi, '--exec', 'ps-list']))
        candidates = [p for p in native.values() if isinstance(p, dict) and p.get('state') == 'PS-ACTIVE' and p.get('apn') == 'internet']
        if len(candidates) != 1:
            raise RuntimeError('No unique active Internet PDU for the primary UE')
        pdu = candidates[0]
        address = pdu['address']
        import ipaddress
        if ipaddress.ip_address(address) not in ipaddress.ip_network('10.45.0.0/16'):
            raise RuntimeError('PDU address outside Internet pool')
        links = json.loads(ue.run(['ip', '-j', '-4', 'addr', 'show']))
        interfaces = [i['ifname'] for i in links if i['ifname'].startswith('uesimtun') and any(a['local'] == address for a in i.get('addr_info', []))]
        if len(interfaces) != 1:
            raise RuntimeError('No unique observed PDU interface')
        result['session'] = {'supi': supi, 'pdu': pdu, 'interface': interfaces[0]}
        result['accounts_before'] = accounts(core)
        account = next(a for a in result['accounts_before'] if a['supi'] == supi)
        if account['quota_bytes'] - account['consumed_bytes'] - account['reserved_bytes'] < 30000000:
            raise RuntimeError('Less than 30 MB unreserved quota; preserving CHF service')
        result['health_before'] = get_health()
        result['ledger_before'] = nwdaf_request('/management/v1/closed-loop/history')
        result['service_before'] = core.run(['systemctl', 'show', 'open5gs-pcfd', 'open5gs-smfd', 'open5gs-amfd', '--property=Id,MainPID,ActiveState'])
        result['upf_before'] = upf.run(['systemctl', 'show', 'open5gs-upfd', '--property=Id,MainPID,ActiveState'])
        pid = core.run(['systemctl', 'show', 'open5gs-pcfd', '--property=MainPID', '--value']).strip()
        result['pcf_proc_before'] = core.run(['cat', '/proc/' + pid + '/stat', '/proc/' + pid + '/status'])
        result['clock_ticks'] = int(core.run(['getconf', 'CLK_TCK']).strip())
        remote = core.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-XXXXXX']).strip()
        result['remote_evidence'] = remote
        current_route = core.run(['ip', '-j', 'route', 'get', address])
        result['route_before'] = json.loads(current_route)
        if result['route_before'][0].get('gateway') != '10.210.50.8':
            if core.run(['ip', 'route', 'show', 'exact', address + '/32']).strip():
                raise RuntimeError('Existing conflicting UE route; leaving unchanged')
            core.run(['systemd-run', '--unit=' + tag + '-route-cleanup', '--on-active=100s',
                      '/usr/sbin/ip', 'route', 'del', address + '/32', 'via', '10.210.50.8'], sudo=True)
            timer = True
            core.run(['ip', 'route', 'add', address + '/32', 'via', '10.210.50.8'], sudo=True)
            route = True
        if core.run(['ss', '-H', '-lnt', 'sport = :5209']).strip():
            raise RuntimeError('Test port already occupied')
        for suffix, command, runtime in [
            ('capture', ['/usr/bin/tcpdump', '-i', 'any', '-U', '-s', '0', '-w', remote + '/control.pcap',
                         '(tcp port 8085) or (tcp port 7777 and (host 127.0.0.13 or host 127.0.0.4)) or (udp port 8805)'], 75),
            ('iperf', ['/usr/bin/iperf3', '-s', '-B', '10.210.50.1', '-p', '5209'], 30),
        ]:
            unit = tag + '-' + suffix
            core.run(['systemd-run', '--unit=' + unit, '--collect', '--property=RuntimeMaxSec=' + str(runtime),
                      '--property=TimeoutStopSec=5', '--property=KillSignal=SIGINT', *command], sudo=True)
            units.append(unit)
        time.sleep(1)
        result['health_during_capture'] = get_health()  # This management GET is explicitly NOT a PCF N23 query.
        first = tun_sample(upf)
        begin = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(ue.run, ['timeout', '15', 'iperf3', '--connect-timeout', '3000',
                '-c', '10.210.50.1', '-p', '5209', '-B', address, '-R', '-u', '-b', '19M',
                '-l', '1200', '-t', '8', '-J'], timeout=20, check=False)
            time.sleep(2)
            high_start = tun_sample(upf)
            time.sleep(4)
            high_end = tun_sample(upf)
            raw = future.result()
        (local / 'iperf.json').write_text(raw, encoding='utf-8')
        iperf = json.loads(raw)
        if iperf.get('error'):
            raise RuntimeError('iperf failed: ' + iperf['error'])
        last = tun_sample(upf)
        result['traffic_wall_seconds'] = time.monotonic() - begin
        result['pm_samples'] = [first, high_start, high_end, last]
        result['observed_high_dl_bps'] = rate(high_start, high_end)
        result['observed_high_percent'] = result['observed_high_dl_bps'] / 200000
        quiet_start = tun_sample(upf)
        time.sleep(6)
        quiet_end = tun_sample(upf)
        result['recovery_samples'] = [quiet_start, quiet_end]
        result['observed_recovery_dl_bps'] = rate(quiet_start, quiet_end)
        result['pcf_proc_after'] = core.run(['cat', '/proc/' + pid + '/stat', '/proc/' + pid + '/status'])
        result['health_after'] = get_health()
        result['ledger_after'] = nwdaf_request('/management/v1/closed-loop/history')
        result['accounts_after'] = accounts(core)
        result['quotas_unchanged'] = {a['supi']: a['quota_bytes'] for a in result['accounts_before']} == {a['supi']: a['quota_bytes'] for a in result['accounts_after']}
        result['service_after'] = core.run(['systemctl', 'show', 'open5gs-pcfd', 'open5gs-smfd', 'open5gs-amfd', '--property=Id,MainPID,ActiveState'])
        result['upf_after'] = upf.run(['systemctl', 'show', 'open5gs-upfd', '--property=Id,MainPID,ActiveState'])
        result['service_pids_unchanged'] = result['service_before'] == result['service_after'] and result['upf_before'] == result['upf_after']
        result['probe_status'] = 'COMPLETED'
    except Exception as exc:
        result['probe_status'] = 'FAILED'
        result['error'] = str(exc)
        raise
    finally:
        core = hosts.get('core')
        result['cleanup'] = {}
        if core:
            for unit in reversed(units):
                try:
                    core.run(['systemctl', 'stop', unit], sudo=True)
                    result['cleanup'][unit] = 'stopped'
                except Exception:
                    result['cleanup'][unit] = 'stop failed; independent RuntimeMaxSec remains'
            if route:
                try:
                    core.run(['ip', 'route', 'del', address + '/32', 'via', '10.210.50.8'], sudo=True)
                    result['cleanup']['route'] = 'removed'
                    if timer:
                        core.run(['systemctl', 'stop', tag + '-route-cleanup.timer'], sudo=True)
                except Exception:
                    result['cleanup']['route'] = 'cleanup failed; independent timer remains'
            if remote:
                try:
                    core.run(['chown', settings.ssh_user, remote + '/control.pcap'], sudo=True)
                    core.run(['chmod', '600', remote + '/control.pcap'], sudo=True)
                    data = core.read(remote + '/control.pcap')
                    (local / 'control.pcap').write_bytes(data)
                    result['pcap_sha256'] = hashlib.sha256(data).hexdigest()
                    result['pcap_bytes'] = len(data)
                except Exception as exc:
                    result['cleanup']['capture_export_error'] = type(exc).__name__
        for host in hosts.values():
            host.client.close()
        (local / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'evidence': str(local), 'probe_status': result.get('probe_status'),
                          'gate3': result['gate3'], 'high_percent': result.get('observed_high_percent'),
                          'recovery_bps': result.get('observed_recovery_dl_bps'), 'cleanup': result['cleanup']}, indent=2))


if __name__ == '__main__':
    main()
