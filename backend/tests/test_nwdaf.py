from fastapi import HTTPException
from app.services import nwdaf


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
