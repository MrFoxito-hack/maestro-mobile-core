"""Capture correlated NF observations and optional concurrent native UDP probes.

Traffic proves only the existing Open5GS path. It never enables XDP, acquires a
lease, grants credit, changes QER/URR or declares effective-policy authority.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from infra.charging.e2e_native import Lab, get_settings
from infra.policy_authority.deploy_observers import collect, active_pdus
from infra.policy_authority.native_registry import correlate
from app.services.terminal_devices import PROBE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--probe-vehicles', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'/opt/maestro-observer-[a-f0-9]{12}', args.bundle):
        parser.error('unexpected observer bundle')
    args.evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    hosts = {}
    result = {'c3_accepted': False, 'xdp_enabled_by_this_test': False}
    addresses = {'smf': ['10.210.50.1'], 'smf2': ['10.210.50.2'], 'smf3': ['10.210.50.18']}
    try:
        for group, port in [('core', settings.ssh_port), ('upf', settings.upf_ssh_port),
                            ('upf2', settings.upf2_ssh_port), ('ue', settings.ue_ssh_port)]:
            hosts[group] = Lab(settings, port)
        result['before'] = collect(hosts, args.bundle)
        result['registry_before'] = correlate(result['before'], addresses)
        result['active_pdus'] = sorted(active_pdus(hosts['ue']))
        interfaces = json.loads(hosts['ue'].run(['ip', '-j', 'address', 'show']))
        if args.probe_vehicles:
            vehicles = [r for r in result['registry_before']['sessions']
                        if r['identity']['supi'] in {'imsi-999700000000002', 'imsi-999700000000005'}]
            if len(vehicles) != 2:
                raise RuntimeError('two_native_vehicle_sessions_required')
            for row in vehicles:
                if not row['upf_rules']['qer'] or not row['upf_rules']['urr']:
                    raise RuntimeError('qer_urr_must_be_present')

            def probe(row):
                source = row['ue_ipv4']
                names = [i['ifname'] for i in interfaces if any(a.get('local') == source for a in i.get('addr_info', []))]
                if len(names) != 1 or not re.fullmatch(r'uesimtun\d+', names[0]):
                    raise RuntimeError('native_ue_ip_not_uniquely_bound_to_tun')
                host = Lab(settings, settings.ue_ssh_port)
                try:
                    measured = json.loads(host.run(['python3', '-c', PROBE, names[0], source,
                        '172.31.48.2', 'probe', '100', '100'], sudo=True, timeout=15))
                    return {'supi': row['identity']['supi'], 'session_key': row['session_key'],
                            'interface': names[0], 'source': source, **measured}
                finally:
                    host.client.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                result['probes'] = list(pool.map(probe, vehicles))
            result['after'] = collect(hosts, args.bundle)
            result['registry_after'] = correlate(result['after'], addresses)
            after = {r['session_key']: r for r in result['registry_after']['sessions']}
            result['usage_deltas'] = []
            for row in vehicles:
                current = after[row['session_key']]
                if current['rules_sha256'] != row['rules_sha256']:
                    raise RuntimeError('vehicle_policy_changed_during_probe')
                old_usage = {u['urr_id']: u for u in row['usage']}
                for u in current['usage']:
                    old = old_usage[u['urr_id']]
                    delta = {key: int(u[key]) - int(old[key])
                             for key in ('total_octets', 'ul_octets', 'dl_octets', 'total_packets')}
                    if min(delta.values()) < 0:
                        raise RuntimeError('non_monotonic_native_usage')
                    result['usage_deltas'].append({'supi': row['identity']['supi'], 'urr_id': u['urr_id'], **delta})
            result['native_udp_accounting_verified'] = (
                all(p['received'] == p['sent'] == 100 for p in result['probes']) and
                all(d['total_octets'] > 0 for d in result['usage_deltas']))
        query = '''import socket,json
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
 s.settimeout(6);s.connect('/run/maestro-policy-authority/control.sock')
 s.sendall(b'{"operation":"status"}\\n')
 with s.makefile('rb') as f: print(f.readline(65536).decode())
'''
        result['authority'] = json.loads(hosts['core'].run(['python3', '-c', query], sudo=True))
        result['chf_ready'] = json.loads(hosts['core'].run(['python3', '-c',
            "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8081/ready',timeout=3).read().decode())"]))
        print(json.dumps({k: result[k] for k in ('active_pdus', 'probes', 'usage_deltas',
                         'native_udp_accounting_verified', 'authority', 'chf_ready') if k in result}, indent=2))
    finally:
        (args.evidence / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        for host in hosts.values():
            host.client.close()


if __name__ == '__main__':
    main()
