from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from app.db import transaction
from app.models import Role, UserPublic
from app.services.nf_metrics import NFMetrics, native_definition, parse_metrics
from app.services.performance import compatible, COUNTER_BY_ID, performance_repository, performance_service


EXPOSITION = '''# HELP fivegs_amffunction_rm_reginitreq Registration requests
# TYPE fivegs_amffunction_rm_reginitreq counter
fivegs_amffunction_rm_reginitreq{plmnid="99970",snssai="1"} 12
# HELP peers Active peers
# TYPE peers gauge
peers 1
'''


def test_parser_preserves_labels_and_ignores_invalid_or_sensitive_series():
    values = list(parse_metrics(EXPOSITION + '''# TYPE bad gauge
bad NaN
bad +Inf
bad -1
bad{imsi="private"} 2
# TYPE delay histogram
delay_bucket{le="10"} 3
'''))
    assert len(values) == 2
    assert values[0][1] == {"plmnid": "99970", "snssai": "1"}
    assert list(parse_metrics('# TYPE unused counter\n# HELP unused not emitted')) == []


def test_dimensions_have_stable_distinct_identity():
    first = native_definition('smf', 'sm_sessionnbr', {'plmnid': '', 'snssai': ''}, 'gauge', 'sessions')
    second = native_definition('smf', 'sm_sessionnbr', {'snssai': '1', 'plmnid': '99970'}, 'gauge', 'sessions')
    assert first['id'] != second['id']
    assert second['id'] == native_definition('smf', 'sm_sessionnbr', {'plmnid': '99970', 'snssai': '1'}, 'gauge', 'sessions')['id']
    assert not compatible(second, 'nf:amf')
    assert compatible(second, 'nf:smf')


def test_procedure_compatibility_is_not_just_object_type():
    assert compatible(COUNTER_BY_ID['5g.registration.attempts'], 'procedure:registration')
    assert not compatible(COUNTER_BY_ID['5g.registration.attempts'], 'procedure:pdu-session')


def probe(counter=12, cpu=10, time=1000, incarnation='boot:pid:start'):
    return {'components': [{'id': 'amf', 'incarnation': incarnation, 'observed_at': time, 'values': {'cpu_seconds': cpu, 'rss_mib': 30}, 'metrics_status': 'available', 'metrics': EXPOSITION.replace('} 12', '} ' + str(counter))}]}


def test_rates_skip_first_reset_restart_and_collection_gaps(client):
    nf = NFMetrics()
    now = datetime.now(timezone.utc)
    first = nf.ingest(probe(), 'rate-test', '5g-sa', now)
    assert not any(s['counter_id'].endswith('.rate') for s in first)
    second = nf.ingest(probe(22, 12, 1010), 'rate-test', '5g-sa', now + timedelta(seconds=10))
    assert next(s['value'] for s in second if s['counter_id'].endswith('.rate')) == 1
    assert next(s['value'] for s in second if s['counter_id'].endswith('cpu_percent')) == 20
    for data in [probe(1, 13, 1020), probe(4, 1, 1030, 'boot:other:start'), probe(10, 2, 1300, 'boot:other:start')]:
        assert not any(s['counter_id'].endswith('.rate') for s in nf.ingest(data, 'rate-test', '5g-sa', now))


def test_absent_metrics_do_not_create_zero_samples(client):
    samples = NFMetrics().ingest({'components': [{'id': 'udm', 'values': {}, 'metrics': '', 'metrics_status': 'not_configured'}]}, 'empty-test', '5g-sa', datetime.now(timezone.utc))
    assert samples == []


def test_query_uses_last_for_cumulative_and_scopes_native_catalog(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    nf = NFMetrics()
    rows = nf.ingest(probe(), 'isolated-test', '5g-sa', now - timedelta(seconds=2))
    row = next(r for r in rows if r['counter_id'].startswith('native.amf.fivegs'))
    performance_repository.insert_samples([row, {**row, 'value': 15, 'bucket_epoch': row['bucket_epoch'] + 1, 'collected_at': (now - timedelta(seconds=1)).isoformat()}])
    user = UserPublic(username='isolation-test', role=Role.student, testbed='isolated-test')
    payload = {'scenario_id': '5g-sa', 'object_ids': ['nf:amf'], 'counter_ids': [row['counter_id']], 'start': now-timedelta(seconds=3), 'end': now, 'range_key': '1h', 'granularity_seconds': 86400, 'aggregation': 'sum'}
    result = performance_service.query(payload, user)
    assert result['series'][0]['points'][-1]['value'] == 15
    assert result['series'][0]['aggregation'] == 'last'
    with pytest.raises(ValueError):
        performance_service.query({**payload, 'object_ids': ['nf:smf']}, user)
    other = UserPublic(username='other', role=Role.student, testbed='other-testbed')
    with pytest.raises(ValueError):
        performance_service.query(payload, other)


def test_report_is_docx_with_native_plot_and_missing_data_notice(client, teacher_headers):
    # Isolate the fixture from the background collector running with TestClient.
    # A current sample can share a bucket whose final provenance is a later poll.
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    performance_repository.insert_samples([{'collected_at': now.isoformat(), 'bucket_epoch': int(now.timestamp()), 'testbed_id': 'local', 'scenario_id': '5g-sa', 'object_id': 'nf:amf', 'counter_id': 'core.nf.availability', 'value': 100, 'unit': '%', 'source': 'test-fixture', 'quality': 'simulated'}])
    response = client.post('/api/v1/performance/report', headers=teacher_headers, json={'title': 'EMS test', 'queries': [{'name': 'AMF status', 'object_ids': ['nf:amf'], 'counter_ids': ['core.nf.availability'], 'start': (now-timedelta(seconds=1)).isoformat(), 'end': (now+timedelta(seconds=1)).isoformat()}]})
    assert response.status_code == 200, response.text[:300] if response.status_code != 200 else ''
    with ZipFile(BytesIO(response.content)) as archive:
        assert any(name.startswith('word/media/') for name in archive.namelist())
        document = archive.read('word/document.xml').decode()
        assert 'AMF status' in document
        assert 'test-fixture' in document
        assert 'simulated' in document


def test_report_requires_auth_and_rejects_incompatible_query(client, teacher_headers):
    request = {'queries': [{'name': 'wrong object', 'object_ids': ['nf:amf'], 'counter_ids': ['interface.rx.kbps']}]}
    assert client.post('/api/v1/performance/report', json=request).status_code == 401
    assert client.post('/api/v1/performance/report', headers=teacher_headers, json=request).status_code == 400
