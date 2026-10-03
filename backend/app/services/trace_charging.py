"""Wire evidence for TS 32.291, never a reconstructed charging ledger.

HTTP/2 DATA is indexed by connection, stream AND sender to avoid mixing a
request's used units with a response's granted units. Only allowlisted numeric
fields are exposed; credentials and subscriber JSON are never returned.
"""
import json
import re


PATH = re.compile(r"^/nchf-convergedcharging/v\d+/chargingdata(?:/([^/?]+)/(update|release))?/?$")


def index_charging(rows):
    streams = {}
    for row in rows:
        key = (row.get('tcp.stream'), row.get('http2.streamid'))
        if not all(key) or not key[1].isdigit():
            continue  # Ambiguous multiple-stream TSV rows must not be invented.
        entry = streams.setdefault(key, {'bodies': {}, 'frames': {}})
        path = row.get('http2.headers.path', '')
        if PATH.fullmatch(path) and row.get('http2.headers.method') == 'POST':
            entry['path'] = path
        sender = (row.get('ip.src') or row.get('ipv6.src'), row.get('tcp.srcport'))
        raw = row.get('http2.data.data', '')
        if raw:
            try:
                data = bytes.fromhex(raw.replace(':', '').replace(',', ''))
            except ValueError:
                continue
            body = entry['bodies'].get(sender, b'')
            if len(body) + len(data) <= 262144:
                entry['bodies'][sender] = body + data
                entry['frames'].setdefault(sender, []).append(row.get('frame.number'))
            else:
                entry['bodies'][sender] = b''
                entry['oversize'] = True
    return streams


def charging_evidence(row, streams):
    entry = streams.get((row.get('tcp.stream'), row.get('http2.streamid')), {})
    match = PATH.fullmatch(entry.get('path', ''))
    if not match:
        return None
    sender = (row.get('ip.src') or row.get('ipv6.src'), row.get('tcp.srcport'))
    result = {'operation': (match[2] or 'create').capitalize(),
              'http_method': row.get('http2.headers.method') or None,
              'http_status': row.get('http2.headers.status') or None,
              'charging_data_ref': match[1], 'units': [],
              'payload_frames': entry.get('frames', {}).get(sender, []),
              'payload_decoded': False}
    try:
        payload = json.loads(entry.get('bodies', {}).get(sender, b''))
    except (ValueError, UnicodeDecodeError):
        return result
    if not isinstance(payload, dict) or entry.get('oversize'):
        return result
    result['payload_decoded'] = True
    for list_name in ('multipleUnitUsage', 'multipleUnitInformation'):
        records = payload.get(list_name, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            for kind in ('requestedUnit', 'grantedUnit', 'usedUnitContainer'):
                units = record.get(kind, [])
                for unit in units if isinstance(units, list) else [units]:
                    if not isinstance(unit, dict):
                        continue
                    values = {k: v for k, v in unit.items() if k in
                              {'totalVolume', 'uplinkVolume', 'downlinkVolume', 'time', 'localSequenceNumber'}
                              and type(v) is int and v >= 0}
                    if values:
                        result['units'].append({'kind': kind, **values,
                            **({'ratingGroup': record['ratingGroup']} if type(record.get('ratingGroup')) is int else {})})
    return result
