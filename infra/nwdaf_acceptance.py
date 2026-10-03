"""Offline acceptance from immutable real capture, counters and native ledger.

No synthetic metrics. Frame numbers refer to control.pcap. Duplicate tshark
JSON keys are preserved, including coalesced HTTP/2 frames.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            previous = result[key]
            result[key] = previous + [value] if isinstance(previous, list) else [previous, value]
        else:
            result[key] = value
    return result


def values(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield from v if isinstance(v, list) else [v]
            else:
                yield from values(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from values(v, key)


def one(node, key, default=None):
    return next(values(node, key), default)


def stamp(packet):
    return float(one(packet, 'frame.time_epoch'))


def frame(packet):
    return int(one(packet, 'frame.number'))


def analyze(directory):
    result = json.loads((directory/'result.json').read_text())
    assert hashlib.sha256((directory/'control.pcap').read_bytes()).hexdigest() == result['pcap_sha256']
    pfcp = json.loads((directory/'pfcp.json').read_text(), object_pairs_hook=pairs)
    sbi = json.loads((directory/'sbi.json').read_text(), object_pairs_hook=pairs)
    requests, documents, headers = [], [], {}
    for packet in sbi:
        tcp = one(packet, 'tcp.stream')
        for stream in values(packet, 'http2.stream'):
            sid = one(stream, 'http2.streamid')
            path = one(stream, 'http2.headers.path')
            if path:
                headers[tcp, sid] = path
            for data in values(stream, 'http2.data.data'):
                try:
                    body = json.loads(bytes.fromhex(data.replace(':', '')).decode())
                except (ValueError, UnicodeError):
                    continue
                documents.append({'packet': packet, 'body': body, 'path': headers.get((tcp, sid), ''), 'tcp': tcp, 'stream': sid})
    for packet in pfcp:
        rate = one(packet, 'pfcp.dl_mbr')
        if one(packet, 'pfcp.msg_type') != '52' or rate is None:
            continue
        replies = [p for p in pfcp if one(p, 'pfcp.msg_type') == '53'
                   and one(p, 'pfcp.seqno') == one(packet, 'pfcp.seqno')
                   and one(p, 'ip.src') == one(packet, 'ip.dst')
                   and one(p, 'ip.dst') == one(packet, 'ip.src')
                   and stamp(packet) <= stamp(p) <= stamp(packet)+5]
        requests.append({'packet': packet, 'rate': int(rate)*1000,
                         'reply': min(replies, key=stamp) if replies else None})
    old_ids = {x['decision_id'] for x in result['ledger_before']['items']}
    decisions = defaultdict(list)
    for event in result['ledger_after']['items']:
        if event['decision_id'] not in old_ids:
            decisions[event['decision_id']].append(event)
    transitions = []
    for identifier, events in decisions.items():
        detected = next((x for x in events if x['stage'] == 'DETECTED'), None)
        if not detected:
            continue
        start = int(re.search(r'utc_us=(\d+)', detected['evidence_ref'])[1])/1e6
        target = detected['target_mbr_bps']
        matching = [r for r in requests if r['rate'] == target and start <= stamp(r['packet']) <= start+1]
        n7 = [d for d in documents if d['path'].endswith('/update') and isinstance(d['body'], dict)
              and 'smPolicyDecision' in d['body'] and start <= stamp(d['packet']) <= start+1
              and any(q.get('maxbrDl') == str(target//1000000)+' Mbps'
                      for q in d['body']['smPolicyDecision'].get('qosDecs', {}).values())]
        n23 = [d for d in documents if isinstance(d['body'], dict)
               and 'sliceLoadLevelInfos' in d['body'] and start-2 <= stamp(d['packet']) <= start
               and one(d['packet'], 'tcp.srcport') == '8085'
               and any(i.get('snssais') == [{'sst': 1, 'sd': '000001'}]
                       and (i.get('loadLevelInformation', -1) > 85 if detected['action'] == 'mitigate'
                            else 0 <= i.get('loadLevelInformation', -1) < 70)
                       for i in d['body']['sliceLoadLevelInfos'])]
        request = min(matching, key=lambda r: stamp(r['packet'])) if matching else None
        reply = request['reply'] if request else None
        accepted = reply is not None and one(reply, 'pfcp.cause') == '1'
        latest_n23 = max(n23,key=lambda d:stamp(d['packet'])) if n23 else None
        report_generated = datetime.fromisoformat(latest_n23['body']['timeStampGen']).timestamp() if latest_n23 else None
        transitions.append({'decision_id': identifier, 'action': detected['action'],
            'target_mbr_bps': target, 'detected_epoch': start,
            'ledger_stages': sorted(x['stage'] for x in events),
            'n23_frames': sorted({frame(d['packet']) for d in n23}),
            'n7_frames': sorted({frame(d['packet']) for d in n7}),
            'pfcp_request_frame': frame(request['packet']) if request else None,
            'pfcp_response_frame': frame(reply) if reply else None,
            'qer_id': one(request['packet'], 'pfcp.qer_id') if request else None,
            'pfcp_accepted': accepted,
            'detection_to_pfcp_ack_ms': round((stamp(reply)-start)*1000, 6) if accepted else None,
            'nwdaf_report_generation_to_pfcp_ack_ms': round((stamp(reply)-report_generated)*1000,6) if accepted and report_generated else None,
            'control_verified': bool(n23 and n7 and accepted and {'DETECTED','N7_SENT','N7_ACK'} <= {x['stage'] for x in events})})
    transitions.sort(key=lambda x: x['detected_epoch'])
    mitigate = next((x for x in transitions if x['action'] == 'mitigate'), None)
    restore = next((x for x in transitions if x['action'] == 'restore'), None)
    # Use temporal interior only, never select samples by desired rate.
    traffic = next(t['result'] for t in result['traffic'] if t['phase'] == 'overload')
    flow_start = traffic['start']['timestamp']['timesecs']
    flow_end = flow_start + traffic['end']['sum']['seconds']
    samples = []
    previous = None
    for observation in result['observations']:
        if previous and mitigate and observation['phase'] == 'overload':
            if (previous['ue_counter']['utc'] >= mitigate['detected_epoch']+.1
                    and observation['ue_counter']['utc'] < flow_end-1):
                samples.append(observation)
        previous = observation
    rates = [x['ue_bps']/1e6 for x in samples]
    restored = [x['ue_bps']/1e6 for x in result['observations'] if x['phase'] == 'restored-probe']
    checks = {'n23_n7_pfcp_mitigation': bool(mitigate and mitigate['control_verified']),
              'n23_n7_pfcp_restoration': bool(restore and restore['control_verified']),
              'same_qer': bool(mitigate and restore and mitigate['qer_id'] == restore['qer_id']),
              'ue_5mbps': len(rates) >= 3 and all(4 <= rate <= 6 for rate in rates),
              'ue_rate_restored': bool(restored and max(restored) > 10),
              'quota_unchanged': result['quota_unchanged'],
              'cleanup': all(v in ('stopped','route removed') for v in result['cleanup'].values())}
    def ticks(raw):
        fields = raw.splitlines()[0].rsplit(')',1)[1].split()
        return int(fields[11])+int(fields[12])
    def rss(raw):
        return int(re.search(r'^VmRSS:\s+(\d+)', raw, re.M)[1])
    output = {'run':result['run'], 'gate3':'PASSED' if all(checks.values()) else 'FAILED',
              'checks': checks, 'transitions': transitions,
              'ue_throttled_mbps': {'samples': rates, 'mean': statistics.mean(rates) if rates else None,
                                    'selection': 'complete intervals after mitigation+0.1s and before iperf end-1s'},
              'ue_restored_probe_mbps': restored,
              'pcf_resources': {'cpu_percent_one_core': 100*(ticks(result['pcf_after'])-ticks(result['pcf_before']))/result['clock_ticks']/result['pcf_cpu_elapsed'],
                                'wall_seconds': result['pcf_cpu_elapsed'], 'rss_before_kib': rss(result['pcf_before']),
                                'rss_after_kib': rss(result['pcf_after']), 'scope': 'whole campaign, not marginal C-patch overhead'},
              'gate4': 'INCOMPLETE',
              'gate4_pending': ['causal held-out RMSE at 15/30 minutes','paired audiovisual P.1203 QoE comparison',
                                'full-loop latency includes NWDAF publication and PCF polling; PCF-only latency is not equivalent'],
              'latency_scope': 'PCF threshold detection to accepted PFCP response at core; excludes sampling/polling delay',
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.pcap')}}
    (directory/'analysis.json').write_text(json.dumps(output,indent=2), encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument('run');args=parser.parse_args()
    assert re.fullmatch(r'nwdaf-loop-[a-f0-9]{10}',args.run)
    print(json.dumps(analyze(ROOT/'.work'/args.run),indent=2))
