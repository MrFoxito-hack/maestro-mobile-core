from fastapi import HTTPException
from app.services import charging


def test_requires_operator(client, student_headers):
    assert client.get('/api/v1/charging/accounts').status_code == 401
    assert client.get('/api/v1/charging/accounts', headers=student_headers).status_code == 403


def test_live_records_mask_nested_identifiers(client, teacher_headers, monkeypatch):
    seen = []
    def get(path):
        seen.append(path)
        return {'items': [{'supi': 'imsi-999700000000001', 'record': {'subscriberIdentifier': 'imsi-999700000000001'}, 'consumed_bytes': 8224}], 'total': 1, 'limit': 25, 'offset': 0}
    monkeypatch.setattr(charging, 'management_get', get)
    response = client.get('/api/v1/charging/accounts?supi=imsi-999700000000001', headers=teacher_headers)
    assert response.status_code == 200
    assert '999700000000001' not in response.text
    assert response.json()['items'][0]['consumed_bytes'] == 8224
    assert response.headers['cache-control'] == 'no-store'
    assert seen == ['/admin/v1/accounts?limit=25&offset=0&supi=imsi-999700000000001']


def test_no_fallback_or_writes(client, teacher_headers, monkeypatch):
    def offline(_):
        raise HTTPException(503, 'unavailable')
    monkeypatch.setattr(charging, 'management_get', offline)
    assert client.get('/api/v1/charging/sessions', headers=teacher_headers).status_code == 503
    assert client.post('/api/v1/charging/accounts', headers=teacher_headers, json={}).status_code == 405
    assert client.get('/api/v1/charging/unknown', headers=teacher_headers).status_code == 422
    assert client.get('/api/v1/charging/accounts?limit=501', headers=teacher_headers).status_code == 422
    assert client.get('/api/v1/charging/accounts?supi=../../etc/passwd', headers=teacher_headers).status_code == 422


def test_status_is_management_not_compliance(client, teacher_headers, monkeypatch):
    monkeypatch.setattr(charging, 'management_get', lambda path: {'interface': 'management', 'status': 'ready', 'profile': 'experimental-rel16-volume-v2'})
    response = client.get('/api/v1/charging/status', headers=teacher_headers)
    assert response.json()['connected'] is True
    assert response.json()['validation'] == 'experimental'


def test_mml_charging_is_real_read_only_and_operator_restricted(client, teacher_headers, student_headers, monkeypatch):
    monkeypatch.setattr(charging, 'management_get', lambda _: {'items': [], 'total': 0, 'limit': 25, 'offset': 0})
    payload = {'scenario_id': '5g-sa', 'component_id': 'chf', 'operation_id': 'chf.accounts', 'parameters': {}}
    assert client.post('/api/v1/operations/execute', json=payload, headers=student_headers).status_code == 403
    result = client.post('/api/v1/operations/execute', json=payload, headers=teacher_headers)
    assert result.status_code == 201
    assert result.json()['source'] == 'chf-management-api'
    assert result.json()['data']['items'] == []
