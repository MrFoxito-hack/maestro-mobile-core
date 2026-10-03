import json

from test_trace_release16 import analyze
from app.services.trace_catalog import (
    SUBSCRIBER_USER_PLANE_FILTER,
    subscriber_capture_profile,
    TRACE_PROFILES,
)


def row(stream='1', reverse=False, **fields):
    return {'_ws.col.Protocol': 'HTTP2', 'ip.src': '127.0.0.1', 'ip.dst': '127.0.0.1',
            'tcp.srcport': '8081' if reverse else '42000',
            'tcp.dstport': '42000' if reverse else '8081',
            'tcp.stream': '0', 'http2.streamid': stream, **fields}


def body(value):
    return json.dumps(value).encode().hex()


def test_capture_chf_only_with_sbi():
    assert '8081' in TRACE_PROFILES['5g-sa']['sbi']['filter']
    assert 'chf' in TRACE_PROFILES['5g-sa']['sbi']['nf_ids']
    assert '18081' in subscriber_capture_profile(True)['filter']
    assert '8081' not in subscriber_capture_profile(False)['filter']
    assert '10.45.0.0/16' in SUBSCRIBER_USER_PLANE_FILTER
    assert '10.46.0.0/16' in SUBSCRIBER_USER_PLANE_FILTER


def test_chf_operations_and_directional_units():
    packets = []
    for stream, suffix, status, operation in [('1', '', '201', 'Create'), ('3', '/abc/update', '200', 'Update'), ('5', '/abc/release', '204', 'Release')]:
        packets.extend([
            row(stream, **{'http2.headers.method': 'POST', 'http2.headers.path': '/nchf-convergedcharging/v3/chargingdata' + suffix,
                          'http2.data.data': body({'multipleUnitUsage': [{'ratingGroup': 1, 'usedUnitContainer': [{'totalVolume': 42, 'uplinkVolume': 22, 'downlinkVolume': 20}]}]})}),
            row(stream, True, **{'http2.headers.status': status,
                               'http2.data.data': body({'multipleUnitInformation': [{'ratingGroup': 1, 'grantedUnit': {'totalVolume': 100}}]}) if status != '204' else ''})])
    events = analyze(*packets)['events']
    assert len(events) == 6
    for event in events[::2]:
        assert (event['source_nf'], event['target_nf']) == ('SMF', 'CHF')
        assert event['charging']['units'][0]['totalVolume'] == 42
    for event, operation, status in zip(events[1::2], ['Create', 'Update', 'Release'], ['201', '200', '204']):
        assert (event['source_nf'], event['target_nf']) == ('CHF', 'SMF')
        assert event['charging']['operation'] == operation
        assert event['charging']['http_status'] == status
    assert events[-1]['charging']['units'] == []


def test_chf_missing_body_does_not_invent_quota():
    event = analyze(row(**{'http2.headers.method': 'POST', 'http2.headers.path': '/nchf-convergedcharging/v3/chargingdata'}))['events'][0]
    assert event['charging']['units'] == []
    assert not event['charging']['payload_decoded']
