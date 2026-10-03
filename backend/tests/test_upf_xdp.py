from types import SimpleNamespace
from fastapi import HTTPException
import pytest
from app.api.v1.endpoints import upf_xdp


def test_xdp_requires_operator(client, student_headers):
    assert client.get('/api/v1/upf-xdp/status').status_code == 401
    assert client.post('/api/v1/upf-xdp/mode', headers=student_headers,
                       json={'mode': 'xdp'}).status_code == 403


def test_no_fabricated_simulated_counters(client, teacher_headers):
    assert client.get('/api/v1/upf-xdp/status', headers=teacher_headers).status_code == 409


def test_unknown_mode_rejected(client, teacher_headers):
    assert client.post('/api/v1/upf-xdp/mode', headers=teacher_headers,
                       json={'mode': 'shell-command'}).status_code == 422


def test_expired_session_cannot_activate(client, teacher_headers, monkeypatch):
    calls = []
    def request(action):
        calls.append(action)
        return {'available': True, 'lease_seconds': 0, 'mode': 'legacy'}
    monkeypatch.setattr(upf_xdp, 'request', request)
    response = client.post('/api/v1/upf-xdp/mode', headers=teacher_headers, json={'mode': 'xdp'})
    assert response.status_code == 409 and calls == ['status']


def test_legacy_restoration_does_not_require_live_ue(client, teacher_headers, monkeypatch):
    calls = []
    def request(action):
        calls.append(action)
        return {'mode': 'legacy'} if action == 'status' else None
    monkeypatch.setattr(upf_xdp, 'request', request)
    response = client.post('/api/v1/upf-xdp/mode', headers=teacher_headers, json={'mode': 'legacy'})
    assert response.status_code == 200 and calls == ['off', 'status']


def test_changed_ue_rejected(client, teacher_headers, monkeypatch):
    monkeypatch.setattr(upf_xdp, 'request', lambda _: {'lease_seconds': 100, 'ue': '10.45.0.85'})
    class Remote:
        def __init__(self, _): self.settings = SimpleNamespace(ue_ssh_port=2226)
        def _execute_sync(self, *args, **kwargs):
            return 'PDU Session1:\n state: PS-ACTIVE\n apn: internet\n address: 10.45.0.86'
    monkeypatch.setattr(upf_xdp, 'RemoteExecutionAdapter', Remote)
    response = client.post('/api/v1/upf-xdp/mode', headers=teacher_headers, json={'mode': 'xdp'})
    assert response.status_code == 409
