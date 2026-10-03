"""Correlate HTTP/2 exchanges by TCP connection + stream; never by frame order alone."""
import json
import re
from pathlib import Path
from urllib.parse import unquote
from deploy_dual_smf import STATE, evidence

state = json.loads(STATE.read_text())
packets = json.loads((Path(state['local']) / 'http2-decoded.json').read_text())


def values(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield from (v if isinstance(v, list) else [v])
            else:
                yield from values(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from values(v, key)


transactions = {}
for packet in packets:
    layers = packet['_source']['layers']
    frame = int(layers['frame']['frame.number'])
    tcp = layers['tcp']
    ip = layers['ip']
    for stream in values(layers.get('http2', {}), 'http2.stream'):
        streamid = stream.get('http2.streamid', '0')
        if streamid == '0':
            continue
        key = (tcp['tcp.stream'], streamid)
        tx = transactions.setdefault(key, {'tcp_stream': key[0], 'http2_stream': key[1], 'frames': [], 'request_body': '', 'response_body': ''})
        tx['frames'].append(frame)
        if tcp['tcp.srcport'] == '7777':
            side = 'response'
        else:
            side = 'request'
        for name in ('path', 'method', 'status'):
            for value in values(stream, 'http2.headers.' + name):
                tx[name] = value
                tx[name + '_frame'] = frame
        if 'path_frame' in tx and side == 'request':
            tx['destination'] = ip['ip.dst']
        for raw in values(stream, 'http2.data.data'):
            tx[side + '_body'] += bytes.fromhex(raw.replace(':', '')).decode(errors='replace')

result = {'nssf': [], 'nrf_discovery': [], 'nrf_profiles': [], 'n11_create': []}
for tx in transactions.values():
    path = tx.get('path', '')
    if 'nnssf-nsselection' in path and tx.get('destination') == '127.0.0.14':
        result['nssf'].append({**tx, 'path': unquote(path)})
    elif 'nnrf-disc' in path and 'target-nf-type=SMF' in path and tx.get('destination') == '127.0.0.10':
        result['nrf_discovery'].append({**tx, 'path': unquote(path)})
    elif 'nnrf-nfm' in path and tx.get('method') == 'PUT' and tx.get('destination') == '127.0.0.10':
        try:
            profile = json.loads(tx['request_body'])
        except ValueError:
            continue
        if profile.get('nfType') == 'SMF':
            result['nrf_profiles'].append({**tx, 'profile': profile})
    elif path.rstrip('/').endswith('/nsmf-pdusession/v1/sm-contexts') and tx.get('destination') in ('127.0.0.4', '127.0.0.15'):
        # Only the identifiers and route required for evidence, no NAS byte dumps.
        result['n11_create'].append({k: v for k, v in tx.items() if k not in ('request_body', 'response_body')} | {
            'supi': re.findall(r'"supi"\s*:\s*"([^"]+)"', tx['request_body']),
            'dnn': re.findall(r'"dnn"\s*:\s*"([^"]+)"', tx['request_body'])})

evidence(state, 'signalling-correlated.json', result)
print(json.dumps({
    'nssf_count': len(result['nssf']),
    'nssf_examples': [{k: t[k] for k in ('tcp_stream', 'http2_stream', 'path_frame', 'status', 'status_frame', 'response_body')} for t in result['nssf'][:2]],
    'profiles': [{'nfInstanceId': t['profile']['nfInstanceId'], 'ipv4Addresses': t['profile'].get('ipv4Addresses'), 'smfInfo': t['profile'].get('smfInfo'), 'status': t.get('status'), 'frames': t['frames']} for t in result['nrf_profiles']],
    'discovery_count': len(result['nrf_discovery']),
    'ue21_context_last': [t for t in result['n11_create'] if 'imsi-999700000000021' in t['supi'] and t.get('status') == '201'][-1:],
}, indent=2))
