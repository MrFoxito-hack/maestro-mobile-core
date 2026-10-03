import pytest
from app.laboratory.traffic_evidence import received_udp_bytes


def fixture():
    return {'start': {'test_start': {'protocol': 'UDP', 'reverse': 1}},
            'end': {'sum': {'bytes': 90000000, 'sender': False}},
            'intervals': [{'sum': {'start': i, 'end': i + 1, 'bytes': 1200,
                                  'sender': False, 'omitted': False}} for i in range(10)]}


def test_receiving_intervals_not_ambiguous_iperf39_summary():
    assert received_udp_bytes(fixture()) == 12000


@pytest.mark.parametrize('kind', ['sender', 'gap', 'duplicate', 'short', 'error'])
def test_invalid_receiving_evidence_rejected(kind):
    data = fixture()
    if kind == 'sender': data['intervals'][0]['sum']['sender'] = True
    if kind == 'gap': data['intervals'].pop(3)
    if kind == 'duplicate': data['intervals'].insert(3, data['intervals'][3])
    if kind == 'short': data['intervals'].pop()
    if kind == 'error': data['error'] = 'failed'
    with pytest.raises(ValueError): received_udp_bytes(data)
