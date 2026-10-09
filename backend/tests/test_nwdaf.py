from fastapi import HTTPException
from app.services import nwdaf
import json
import pytest


def test_requires_operator(client, student_headers):
    assert client.get('/api/v1/nwdaf/status').status_code == 401
    assert client.get('/api/v1/nwdaf/status', headers=student_headers).status_code == 403
    assert client.get('/api/v1/nwdaf/predictions', headers=student_headers).status_code == 403


def test_prediction_proxy(client, teacher_headers, monkeypatch):
    paths = []
    def query(path):
        paths.append(path)
        return {'items': [], 'closed_loop_enabled': False}
    monkeypatch.setattr(nwdaf, 'nwdaf_request', query)
    response = client.get('/api/v1/nwdaf/predictions', headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()['items'] == []
    assert paths == ['/management/v1/predictions']
    assert response.headers['cache-control'] == 'no-store'


def test_nwdaf_status_endpoint(client, teacher_headers, monkeypatch):
    monkeypatch.setattr(nwdaf, 'get_health', lambda: {
        'status': 'ready',
        'contract': 'TS 29.520 V16.7.0',
        'profile': 'phase-1-research',
        'closed_loop_enabled': False,
        'ingestion': {'status': 'ok', 'slices_configured': 1},
    })
    response = client.get('/api/v1/nwdaf/status', headers=teacher_headers)
    assert response.status_code == 200
    data = response.json()
    assert data['connected'] is True
    assert data['contract'] == 'TS 29.520 V16.7.0'
    assert data['closed_loop_enabled'] is False
    assert data['ingestion']['status'] == 'ok'
    assert response.headers['cache-control'] == 'no-store'


def test_nwdaf_status_when_offline(client, teacher_headers, monkeypatch):
    def offline():
        raise HTTPException(503, 'offline')
    monkeypatch.setattr(nwdaf, 'get_health', offline)
    response = client.get('/api/v1/nwdaf/status', headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()['connected'] is False


def test_nwdaf_analytics_load_level(client, teacher_headers, monkeypatch):
    recorded_calls = []

    def mock_get_analytics(event_id, event_filter=None, tgt_ue=None):
        recorded_calls.append((event_id, event_filter, tgt_ue))
        return {
            'timeStampGen': '2026-09-27T15:00:00Z',
            'sliceLoadLevelInfos': [{'loadLevelInformation': 42, 'snssais': [{'sst': 1}]}],
        }

    monkeypatch.setattr(nwdaf, 'get_analytics', mock_get_analytics)
    response = client.get(
        '/api/v1/nwdaf/analytics/LOAD_LEVEL_INFORMATION?snssai_sst=1',
        headers=teacher_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data['sliceLoadLevelInfos'][0]['loadLevelInformation'] == 42
    assert len(recorded_calls) == 1
    assert recorded_calls[0][0] == 'LOAD_LEVEL_INFORMATION'


def test_nwdaf_analytics_validation(client, teacher_headers):
    # Invalid event ID should return 422
    assert client.get('/api/v1/nwdaf/analytics/INVALID_EVENT', headers=teacher_headers).status_code == 422
    # Invalid SUPI format should return 422
    assert client.get('/api/v1/nwdaf/analytics/ABNORMAL_BEHAVIOUR?supi=invalid', headers=teacher_headers).status_code == 422


def test_overview_requests_three_complete_snssais_without_relabeling_history(monkeypatch):
    calls = []
    monkeypatch.setattr(nwdaf, 'get_health', lambda: {'closed_loop_enabled': False})
    def analytics(event_id, event_filter):
        target = json.loads(event_filter)['snssais'][0]; calls.append(target)
        return {'sliceLoadLevelInfos': [{'snssais': [target], 'loadLevelInformation': target['sst'] * 10}]}
    monkeypatch.setattr(nwdaf, 'get_analytics', analytics)
    result = nwdaf.get_overview()
    assert calls == [{'sst': 1, 'sd': '000001'}, {'sst': 2, 'sd': '000002'}, {'sst': 3, 'sd': '000003'}]
    assert [v['loadLevelInformation'] for v in result['slice_load']['sliceLoadLevelInfos']] == [10, 20, 30]
    assert all(s['status'] == 'available' for s in result['slices'])


def test_missing_or_failed_slice_does_not_fabricate_zero_or_hide_other_slices(monkeypatch):
    monkeypatch.setattr(nwdaf, 'get_health', lambda: {})
    def analytics(event_id, event_filter):
        sst = json.loads(event_filter)['snssais'][0]['sst']
        if sst == 2: raise HTTPException(503)
        return None if sst == 3 else {'sliceLoadLevelInfos': [{'loadLevelInformation': 12}]}
    monkeypatch.setattr(nwdaf, 'get_analytics', analytics)
    result = nwdaf.get_overview()
    assert [s['status'] for s in result['slices']] == ['available', 'unavailable', 'no_data']
    assert len(result['slice_load']['sliceLoadLevelInfos']) == 1


@pytest.mark.parametrize('nf', ['UPF-01', 'UPF-02', 'UPF-03'])
def test_nf_resolves_versioned_object_id_from_catalog(nf, monkeypatch):
    maps = nwdaf.slice_catalog()
    expected = next(s for s in maps if s['nf'] == nf)
    monkeypatch.setattr(nwdaf, 'get_health', lambda: {'slice_maps': maps})
    seen = []
    def analytics(event_id, event_filter):
        seen.append(json.loads(event_filter)['snssais'][0])
        return {'sliceLoadLevelInfos': [{'loadLevelInformation': 42}]}
    monkeypatch.setattr(nwdaf, 'get_analytics', analytics)
    monkeypatch.setattr(nwdaf, 'nwdaf_request', lambda _: {'items': []})
    result = nwdaf.get_nf_analytics(nf, 15)
    assert seen == [expected['snssai']]
    assert result['prediction_status'] == 'unavailable'
    assert result['predicted_load_percent'] is None


def test_nf_rejects_obsolete_sst_mapping_even_with_same_object(monkeypatch):
    monkeypatch.setattr(nwdaf, 'get_health', lambda: {'slice_maps': [
        {'object_id': 'nf:upf-02:triad-v1', 'snssai': {'sst': 1, 'sd': '000002'}}]})
    with pytest.raises(HTTPException) as error:
        nwdaf.get_nf_analytics('UPF-02', 15)
    assert error.value.status_code == 409


def test_prediction_view_hides_obsolete_slice_without_rewriting_it(client, teacher_headers, monkeypatch):
    old = {'snssai': {'sst': 1, 'sd': '000002'}, 'fresh': False}
    current = {'snssai': {'sst': 3, 'sd': '000003'}, 'fresh': True}
    monkeypatch.setattr(nwdaf, 'nwdaf_request', lambda _: {'items': [old, current]})
    result = client.get('/api/v1/nwdaf/predictions', headers=teacher_headers).json()
    assert result['items'] == [current]
    assert old['snssai'] == {'sst': 1, 'sd': '000002'}
    assert len(result['slices']) == 3
