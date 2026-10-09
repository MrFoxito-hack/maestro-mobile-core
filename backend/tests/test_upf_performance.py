from datetime import datetime, timezone
import pytest

from app.services import upf_performance as pm
from app.services.performance import performance_repository
from app.services.upf_inventory import inventory


def observation(timestamp, **updates):
    return {'status': 'measured', 'source_timestamp': timestamp, 'traffic_complete': True,
            'metrics': {'ul_bps': 1000000, 'dl_bps': 2000000, 'ul_pps': 10,
                        'dl_pps': 20, 'active_sessions': 1}, **updates}


def test_no_traffic_for_incomplete_or_missing_observations():
    target = inventory()['targets'][1]
    assert pm.samples(target, observation(1000, status='unavailable')) == []
    rows = pm.samples(target, observation(1000, traffic_complete=False))
    assert rows and {r['counter_id'] for r in rows} == {'upf.triad.sessions'}
    assert {r['object_id'] for r in rows} == {'nf:upf3', pm.interface_id(target)}


@pytest.mark.parametrize('aggregation,expected', [('avg', 3), ('max', 4), ('last', 4)])
def test_triad_standard_query_units_and_isolation(client, teacher_headers, aggregation, expected):
    base = int(datetime.now(timezone.utc).timestamp()) // 300 * 300 - 600
    rows = []
    for index, target in enumerate(inventory()['targets']):
        for offset, multiplier in [(1, 1), (2, 2)]:
            item = observation(base + offset)
            item['metrics']['dl_bps'] *= multiplier * (index + 1)
            rows.extend(pm.samples(target, item))
    performance_repository.insert_samples(rows)
    response = client.post('/api/v1/performance/query', headers=teacher_headers, json={
        'scenario_id': '5g-sa', 'object_ids': ['nf:upf', 'nf:upf3', 'nf:upf2'],
        'counter_ids': ['upf.triad.dl.mbps', 'upf.triad.ul.pps'],
        'range_key': '15m', 'granularity_seconds': 300, 'aggregation': aggregation})
    assert response.status_code == 200
    series = response.json()['series']
    assert len(series) == 6
    for index, target in enumerate(inventory()['targets']):
        rate = next(s for s in series if s['object_id'] == pm.OBJECTS[target['dnn']] and s['unit'] == 'Mbps')
        assert rate['points'][0]['value'] == expected * (index + 1)
    catalog = client.get('/api/v1/performance/catalog/5g-sa', headers=teacher_headers).json()
    assert 'nf:upf3' in {o['id'] for o in catalog['objects']}
    assert 'upf.triad.ul.pps' in {c['id'] for c in catalog['counters']}
