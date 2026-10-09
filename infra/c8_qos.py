"""Audited QoS: pre-UERANSIM admission TBF + strict PRIO over RLS UDP.

No 5QI enforcement is implied. Reapply after a UE restart (UDP ports change).
Modifies selected eMBB/MIoT TUNs and enp0s8. SSH uses enp0s3.
Run c8_scheduler.py apply as well to reproduce the accepted CPU priorities.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import re

from c8_remote import CAMPAIGN_ROOT, LoggedLab, get_settings

DEVICE = 'enp0s8'
UNITS = {'urllc': ['maestro-ue-vehicle', 'ueransim-ue-05'],
         'miot': ['maestro-ue-sensor', 'ueransim-ue-06'],
         'embb': ['ueransim-ue', 'ueransim-ue-04']}


def bindings(host):
    sockets = host.run(['ss', '-u', '-a', '-n', '-p'], sudo=True)
    result = {}
    for kind, units in UNITS.items():
        result[kind] = []
        for unit in units:
            pid = int(host.run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']))
            ports = [int(m.group(1)) for line in sockets.splitlines()
                     if f'pid={pid},' in line
                     for m in [re.search(r'0\.0\.0\.0:(\d+)\s', line)] if m]
            if len(ports) != 1:
                raise ValueError(f'nonunique_transport_socket:{unit}:{ports}')
            result[kind].append({'unit': unit, 'pid': pid, 'port': ports[0]})
    return result


def snapshot(host):
    return {'bindings': bindings(host),
            'qdisc': json.loads(host.run(['tc', '-j', '-s', 'qdisc', 'show', 'dev', DEVICE], sudo=True)),
            'classes': host.run(['tc', '-s', 'class', 'show', 'dev', DEVICE], sudo=True),
            'filters': json.loads(host.run(['tc', '-j', '-s', 'filter', 'show', 'dev', DEVICE, 'parent', '20:'], sudo=True) or '[]'),
            'udp': host.run(['nstat', '-az', 'UdpInErrors', 'UdpRcvbufErrors', 'UdpSndbufErrors']),
            'sockets': host.run(['ss', '-u', '-a', '-n', '-p', '-m'], sudo=True),
            'all_qdiscs': json.loads(host.run(['tc', '-j', '-s', 'qdisc'], sudo=True))}


def assert_ready(state, sessions):
    root = [q for q in state['qdisc'] if q.get('root')]
    if len(root) != 1 or root[0]['kind'] != 'prio' or root[0]['handle'] != '20:':
        raise ValueError('missing_transport_priority')
    for band, kind in enumerate(['urllc', 'miot', 'embb'], 1):
        for binding in state['bindings'][kind]:
            if not any(f.get('options', {}).get('classid') == f'20:{band}' and
                       f.get('options', {}).get('keys', {}).get('src_port') == binding['port'] and
                       f.get('options', {}).get('keys', {}).get('dst_port') == 4997 and
                       f.get('options', {}).get('keys', {}).get('dst_ip') == '10.210.50.10'
                       for f in state['filters']):
                raise ValueError('stale_transport_filter_' + kind)
    for kind in ['embb','miot']:
        q = [q for q in state['all_qdiscs'] if q.get('dev') == sessions[kind]['interface'] and q.get('root')]
        if len(q) != 1 or q[0]['kind'] != 'tbf' or q[0]['handle'] != '30:':
            raise ValueError('missing_preadmission_shaper_' + kind)
        if q[0]['options']['rate']*8 != {'embb':500000,'miot':40000}[kind]:
            raise ValueError('noncanonical_preadmission_rate_' + kind)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['apply', 'status', 'restore'])
    p.add_argument('--rate', default='500kbit', help='eMBB pre-UERANSIM rate')
    p.add_argument('--miot-rate', default='40kbit')
    a = p.parse_args()
    out = CAMPAIGN_ROOT/'setup/oe4'
    out.mkdir(parents=True, exist_ok=True)
    host = LoggedLab(get_settings(), 2226, out)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    record = {'action': a.action, 'at': stamp, 'rate': a.rate, 'miot_rate': a.miot_rate, 'before': snapshot(host)}
    path = out/f'qos-{stamp}-{a.action}.json'
    path.write_text(json.dumps(record, indent=2)+'\n')
    try:
        if a.action == 'apply':
            from c8_acquire import sessions
            record['sessions'] = asyncio.run(sessions())
            roots = [q for q in record['before']['qdisc'] if q.get('root')]
            if len(roots) != 1 or roots[0]['kind'] not in ('fq_codel', 'tbf', 'prio'):
                raise ValueError('unrecognized_existing_qdisc')
            if roots[0]['kind'] != 'fq_codel' and roots[0]['handle'] not in ('10:', '20:'):
                raise ValueError('unowned_existing_qdisc')
            host.run(['tc', 'qdisc', 'replace', 'dev', DEVICE, 'root', 'handle', '20:',
                      'prio', 'bands', '3', 'priomap', *(['1']*16)], sudo=True)
            for band, limit in [(1, 8), (2, 8), (3, 16)]:
                host.run(['tc', 'qdisc', 'replace', 'dev', DEVICE, 'parent', f'20:{band}',
                          'handle', f'{20+band}:', 'pfifo', 'limit', str(limit)], sudo=True)
            for band, kind in enumerate(['urllc', 'miot', 'embb'], 1):
                for i, item in enumerate(record['before']['bindings'][kind]):
                    host.run(['tc', 'filter', 'replace', 'dev', DEVICE, 'parent', '20:',
                              'protocol', 'ip', 'pref', str(band*10+i), 'handle', '1', 'flower',
                              'ip_proto', 'udp', 'dst_ip', '10.210.50.10', 'dst_port', '4997',
                              'src_port', str(item['port']), 'classid', f'20:{band}'], sudo=True)
            for kind, rate, burst, limit in [('embb', a.rate, '1400', '6140'),
                                             ('miot', a.miot_rate, '184', '460')]:
                interface = record['sessions'][kind]['interface']
                host.run(['tc', 'qdisc', 'replace', 'dev', interface, 'root', 'handle', '30:',
                          'tbf', 'rate', rate, 'burst', burst, 'limit', limit], sudo=True)
        elif a.action == 'restore':
            host.run(['tc', 'qdisc', 'replace', 'dev', DEVICE, 'root', 'fq_codel'], sudo=True)
            for q in record['before']['all_qdiscs']:
                if q['kind'] == 'tbf' and q['handle'] == '30:' and q['dev'].startswith('uesimtun'):
                    host.run(['tc', 'qdisc', 'replace', 'dev', q['dev'], 'root', 'fq_codel'], sudo=True)
        record['after'] = snapshot(host)
        record['success'] = True
    finally:
        path.write_text(json.dumps(record, indent=2)+'\n')
        host.client.close()
    print(json.dumps({'evidence': str(path), 'success': record.get('success', False)}))


if __name__ == '__main__':
    main()
