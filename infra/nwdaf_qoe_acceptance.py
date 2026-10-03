"""Verify paired audiovisual evidence offline, without assigning a shared QER to a UE.

PFCP responses are matched by sequence and reversed IP endpoints. Session
identity uses the request's remote SEID (response SEIDs are local to SMF).
Frame numbers always refer to the unfiltered control.pcap.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

from nwdaf_acceptance import pairs, one, values, frame, stamp


def analyze(directory):
    result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
    actual_hash = hashlib.sha256((directory / 'control.pcap').read_bytes()).hexdigest()
    if actual_hash != result['pcap_sha256']:
        raise ValueError('Original capture hash mismatch')
    packets = json.loads((directory / 'pfcp.json').read_text(encoding='utf-8'), object_pairs_hook=pairs)
    sbi = json.loads((directory / 'sbi.json').read_text(encoding='utf-8'), object_pairs_hook=pairs)
    windows = {}
    for phase in ('baseline', 'closed-loop'):
        observations = [x for x in result['observations'] if x['phase'] == phase and 'before' in x and 'after' in x]
        windows[phase] = (min(x['before']['timestamp'] for x in observations),
                          max(x['after']['timestamp'] for x in observations))
    def phase_at(epoch):
        return next((phase for phase, (start, end) in windows.items() if start <= epoch <= end), 'outside')

    changes = []
    for request in packets:
        if one(request, 'pfcp.msg_type') != '52' or one(request, 'pfcp.dl_mbr') is None:
            continue
        responses = [p for p in packets if one(p, 'pfcp.msg_type') == '53'
                     and one(p, 'pfcp.seqno') == one(request, 'pfcp.seqno')
                     and one(p, 'ip.src') == one(request, 'ip.dst')
                     and one(p, 'ip.dst') == one(request, 'ip.src')
                     and stamp(request) <= stamp(p) <= stamp(request) + 5]
        reply = min(responses, key=stamp) if responses else None
        changes.append({'phase': phase_at(stamp(request)), 'request_frame': frame(request),
                        'response_frame': frame(reply) if reply else None,
                        'request_epoch': stamp(request), 'upf': one(request, 'ip.dst'),
                        'remote_seid': one(request, 'pfcp.seid'), 'sequence': one(request, 'pfcp.seqno'),
                        'qer_id': one(request, 'pfcp.qer_id'),
                        'dl_mbr_kbps': int(one(request, 'pfcp.dl_mbr')),
                        'ul_mbr_kbps': int(one(request, 'pfcp.ul_mbr')),
                        'accepted': reply is not None and one(reply, 'pfcp.cause') == '1',
                        'pfcp_roundtrip_ms': (stamp(reply) - stamp(request)) * 1000 if reply else None})

    headers, n7, n23 = {}, [], []
    for packet in sbi:
        tcp = one(packet, 'tcp.stream')
        for stream in values(packet, 'http2.stream'):
            sid = one(stream, 'http2.streamid')
            path = one(stream, 'http2.headers.path')
            if path:
                headers[tcp, sid] = path
            path = headers.get((tcp, sid), '')
            for data in values(stream, 'http2.data.data'):
                try:
                    body = json.loads(bytes.fromhex(data.replace(':', '')).decode())
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(body, dict):
                    continue
                if path.endswith('/update') and 'smPolicyDecision' in body:
                    n7.append({'phase': phase_at(stamp(packet)), 'frame': frame(packet),
                               'epoch': stamp(packet), 'path': path, 'tcp_stream': tcp,
                               'http2_stream': sid, 'qos': body['smPolicyDecision'].get('qosDecs', {})})
                if 'sliceLoadLevelInfos' in body and one(packet, 'tcp.srcport') == '8085':
                    n23.append({'phase': phase_at(stamp(packet)), 'frame': frame(packet),
                                'epoch': stamp(packet), 'load': body['sliceLoadLevelInfos']})

    sessions = defaultdict(list)
    for change in changes:
        sessions[change['upf'], change['remote_seid'], change['qer_id']].append(change)
    same_sessions_restore = len(sessions) == 2 and all(
        [x['dl_mbr_kbps'] for x in sorted(series, key=lambda x: x['request_epoch'])] == [5000, 20000]
        and all(x['accepted'] and x['phase'] == 'closed-loop' and x['ul_mbr_kbps'] == 1000000 for x in series)
        for series in sessions.values())
    old_ids = {x['decision_id'] for x in result['ledger_before']['items']}
    decisions = defaultdict(set)
    actions = defaultdict(set)
    for event in result['ledger_after']['items']:
        if event['decision_id'] not in old_ids:
            decisions[event['decision_id']].add(event['stage'])
            actions[event['supi']].add(event['action'])
    players = {phase: json.loads((directory / (phase + '-player.json')).read_text(encoding='utf-8'))
               for phase in windows}
    assets = {phase: {x['asset']: x['sha256'] for x in result['transfers'] if x['phase'] == phase}
              for phase in windows}
    comparison = json.loads((directory / 'qoe-comparison.json').read_text(encoding='utf-8'))
    checks = {
        'baseline_no_n7_updates': not any(x['phase'] == 'baseline' for x in n7),
        'baseline_no_mbr_changes': not any(x['phase'] == 'baseline' for x in changes),
        'two_remote_sessions_mitigated_and_restored': same_sessions_restore,
        'n7_both_rates': all(any(x['phase'] == 'closed-loop' and
            any(q.get('maxbrDl') == rate for q in x['qos'].values()) for x in n7)
            for rate in ('5 Mbps', '20 Mbps')),
        'n23_high_then_low': any(a['epoch'] < b['epoch'] and
            any(i.get('loadLevelInformation', -1) > 85 for i in a['load']) and
            any(0 <= i.get('loadLevelInformation', -1) < 70 for i in b['load'])
            for a in n23 if a['phase'] == 'closed-loop' for b in n23 if b['phase'] == 'closed-loop'),
        'ledger_four_complete_decisions': len(decisions) == 4 and all(
            {'DETECTED', 'N7_SENT', 'N7_ACK'} <= stages for stages in decisions.values()),
        'ledger_both_ues_both_actions': len(actions) == 2 and all(a == {'mitigate', 'restore'} for a in actions.values()),
        'both_players_ended': all(p['status'] == 'PLAYED_TO_END' and p['player']['ended'] for p in players.values()),
        'same_verified_media': bool(assets['baseline']) and assets['baseline'] == assets['closed-loop']
            and all(x['verified'] for x in result['transfers']),
        'p1203_ledger_hashes': len(comparison['immutable_ledger_rows']) == 2 and
            {x['input_hash'] for x in comparison['cases']} == {x['input_hash'] for x in comparison['immutable_ledger_rows']},
        'quota_unchanged': result['quota_unchanged'],
        'cleanup': result['cleanup'] == {'ue': 'executed', 'core': 'executed'},
    }
    output = {'run': result['run'], 'paired_control': 'CONFIRMED' if all(checks.values()) else 'FAILED',
              'checks': checks, 'windows_epoch': windows, 'pfcp_changes': changes, 'n7_updates': n7,
              'decision_ids': sorted(decisions), 'media_asset_count_per_case': len(assets['baseline']),
              'scope': 'Paired QoE/control evidence; no per-UE SEID mapping or new throughput measurement claimed.',
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.pcap')}}
    (directory / 'control-verification.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    with (directory / 'qoe-results.csv').open('w', newline='', encoding='utf-8') as handle:
        columns = ['phase', 'mos', 'startup_seconds', 'rebuffer_count', 'rebuffer_seconds', 'played_seconds', 'input_hash']
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader(); writer.writerows(comparison['cases'])
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    output = analyze(args.directory)
    print(json.dumps(output, indent=2))
    raise SystemExit(0 if output['paired_control'] == 'CONFIRMED' else 1)
