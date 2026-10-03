from uuid import uuid4

from app.services import nwdaf


def payload():
    return {'imsi': 'imsi-999700000000001', 'session_id': str(uuid4()), 'sequence': 1,
            'profile': '720p', 'state': 'playing', 'startup_seconds': 1.2,
            'played_seconds': 10, 'rebuffer_count': 0, 'rebuffer_seconds': 0, 'received_bytes': 123456}


def test_query_returns_real_player_metrics_without_invented_mos(client, teacher_headers, monkeypatch):
    monkeypatch.setattr(nwdaf, 'get_analytics', lambda *a, **k: None)
    data = payload()
    assert client.post('/api/v1/terminal/media/experience', headers=teacher_headers, json=data).status_code == 200
    # Out-of-order requests must not roll back the last observation.
    old = {**data, 'sequence': 0, 'played_seconds': 0}
    client.post('/api/v1/terminal/media/experience', headers=teacher_headers, json=old)
    response = client.get('/api/v1/nwdaf/analytics/SERVICE_EXPERIENCE', headers=teacher_headers,
                          params={'supi': data['imsi'], 'app_id': 'stream5g'})
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'no_data'
    assert 'svcExps' not in result
    assert result['player_observation']['played_seconds'] == 10
    assert result['player_observation']['source'] == 'browser-player'
    assert result['player_observation']['fresh'] is True
    for supi, app in [('imsi-999700000000004', 'stream5g'), (data['imsi'], 'other')]:
        result = client.get('/api/v1/nwdaf/analytics/SERVICE_EXPERIENCE', headers=teacher_headers,
                            params={'supi': supi, 'app_id': app}).json()
        assert result['player_observation'] is None


def test_student_cannot_report_another_ue_and_invalid_metrics_are_rejected(client, student_headers):
    data = payload()
    assert client.post('/api/v1/terminal/media/experience', headers=student_headers,
                       json={**data, 'imsi': 'imsi-999700000000004'}).status_code == 403
    assert client.post('/api/v1/terminal/media/experience', headers=student_headers,
                       json={**data, 'rebuffer_seconds': -1}).status_code == 422
    assert client.post('/api/v1/terminal/media/experience', json=data).status_code == 401
