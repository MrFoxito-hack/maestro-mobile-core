from uuid import uuid4
from unittest.mock import AsyncMock, Mock
from app.services import terminal
from app.services import terminal_sessions, terminal_media
from copy import deepcopy


def observed(imsi='imsi-999700000000001'):
    # Deliberately reversed tunnel numbers: use DNN + negotiated IP, never tun0=internet.
    return {'supi': imsi, 'native': {'ps-list': '''PDU Session1:
 state: PS-ACTIVE
 apn: internet
 address: 10.45.0.91
 s-nssai: {sst: 1, sd: null}
PDU Session2:
 state: PS-ACTIVE
 apn: corporate
 address: 10.46.0.91
 s-nssai: {sst: 1, sd: null}
'''}, 'interfaces': [
        {'name': 'uesimtun7', 'addresses': ['10.45.0.91'], 'rx_bytes': 10, 'tx_bytes': 20},
        {'name': 'uesimtun3', 'addresses': ['10.46.0.91'], 'rx_bytes': 30, 'tx_bytes': 40}]}


def test_apn_selection_persists_and_is_per_ue(client, teacher_headers, monkeypatch):
    one, two = observed(), observed('imsi-999700000000004')
    operation = AsyncMock(side_effect=lambda imsi=None: deepcopy(two if imsi and imsi.endswith('004') else one))
    monkeypatch.setattr(terminal, 'read_terminal', operation)
    response = client.post('/api/v1/terminal/apn', json={'imsi': one['supi'], 'apn': 'corporate'}, headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()['session']['interface'] == 'uesimtun3'
    assert terminal_sessions.describe(one)['active_apn'] == 'corporate'
    assert terminal_sessions.describe(two)['active_apn'] == 'internet'
    assert terminal_sessions.describe(one)['apn_sessions'][1]['rx_bytes'] == 30
    assert client.post('/api/v1/terminal/apn', json={'apn': 'arbitrary'}, headers=teacher_headers).status_code == 422
    assert client.post('/api/v1/terminal/apn', json={'apn': 'internet', 'imsi': ';id'}, headers=teacher_headers).status_code == 422


def test_mapping_rejects_unknown_ambiguous_and_wrong_pool():
    data = observed()
    assert terminal_sessions.sessions(data)[0]['interface'] == 'uesimtun7'
    data['interfaces'].append(deepcopy(data['interfaces'][0]))
    assert len(terminal_sessions.sessions(data)) == 1
    data['native']['ps-list'] = data['native']['ps-list'].replace('10.46.0.91', '10.45.0.91')
    assert terminal_sessions.sessions(data) == []


def test_single_observed_session_overrides_stale_ui_preference(client, teacher_headers, monkeypatch):
    data = observed('imsi-999700000000006')
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    selected = client.post(
        '/api/v1/terminal/apn',
        json={'imsi': data['supi'], 'apn': 'corporate'},
        headers=teacher_headers,
    )
    assert selected.status_code == 200
    data['native']['ps-list'] = data['native']['ps-list'].split('PDU Session2:')[0]
    data['interfaces'] = data['interfaces'][:1]
    state = terminal_sessions.describe(data)
    assert state['active_apn'] == 'internet'
    assert [session['apn'] for session in state['apn_sessions']] == ['internet']
    data['native']['ps-list'] = 'not: [valid'
    assert terminal_sessions.sessions(data) == []


def test_corporate_portal_uses_observed_interface(client, teacher_headers, monkeypatch):
    data = observed('imsi-999700000000003')
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    remote = AsyncMock()
    remote._run.return_value = '{"service":"maestro-corporate","client_ip":"10.46.0.91"}'
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)
    assert client.get('/api/v1/terminal/corporate/intranet?imsi=imsi-999700000000003', headers=teacher_headers).status_code == 403
    assert client.post('/api/v1/terminal/apn', json={'apn': 'corporate', 'imsi': 'imsi-999700000000003'}, headers=teacher_headers).status_code == 200
    result = client.get('/api/v1/terminal/corporate/intranet?imsi=imsi-999700000000003', headers=teacher_headers)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    assert result.json()['snssai']['sd'] is None
    command = remote._run.call_args.args[0]
    assert '--interface uesimtun3' in command and '10.46.0.1:8080/intranet' in command
    remote._run.return_value = '{"service":"maestro-corporate","client_ip":"10.46.0.55"}'
    assert client.get('/api/v1/terminal/corporate/intranet?imsi=imsi-999700000000003', headers=teacher_headers).status_code == 502


def test_video_denied_on_corporate_before_fetch(client, teacher_headers, monkeypatch):
    data = observed('imsi-999700000000002')
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    monkeypatch.setattr(terminal_media, 'adapter', lambda: object())
    assert client.post('/api/v1/terminal/apn', json={'apn': 'corporate', 'imsi': 'imsi-999700000000002'}, headers=teacher_headers).status_code == 200
    assert client.get('/api/v1/terminal/media/720p/index.m3u8?imsi=imsi-999700000000002', headers=teacher_headers).status_code == 403


def test_missing_pdu_not_replaced_with_default_interface(client, teacher_headers, monkeypatch):
    data = observed('imsi-999700000000005')
    data['native'] = {}
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    assert client.post('/api/v1/terminal/apn', json={'apn': 'corporate', 'imsi': 'imsi-999700000000005'}, headers=teacher_headers).status_code == 409


def test_student_cannot_access_other_group_terminal(client, student_headers):
    foreign_imsi = 'imsi-999700000000004'
    assert client.post('/api/v1/terminal/apn', json={'apn': 'corporate', 'imsi': foreign_imsi}, headers=student_headers).status_code == 403
    assert client.get('/api/v1/terminal/corporate/intranet?imsi=' + foreign_imsi, headers=student_headers).status_code == 403
    assert client.get('/api/v1/terminal/status?imsi=' + foreign_imsi, headers=student_headers).status_code == 403
    for path, payload in [('airplane-mode', {'enabled': True, 'imsi': foreign_imsi}),
                          ('traffic/start', {'imsi': foreign_imsi}),
                          ('traffic/stop', {'imsi': foreign_imsi}),
                          ('topup', {'request_id': str(uuid4()), 'imsi': foreign_imsi})]:
        assert client.post('/api/v1/terminal/' + path, json=payload, headers=student_headers).status_code == 403


def test_student_operates_assigned_ue(client, student_headers, monkeypatch):
    data = observed('imsi-999700000000001')
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    response = client.post('/api/v1/terminal/apn', json={'apn': 'corporate', 'imsi': 'imsi-999700000000001'}, headers=student_headers)
    assert response.status_code == 200
    assert response.json()['session']['interface'] == 'uesimtun3'


def test_airplane_targets_primary_identity_with_multiple_ues(client, teacher_headers, monkeypatch):
    remote = AsyncMock()
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)
    monkeypatch.setattr(terminal, 'traffic', AsyncMock())
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value={'supi': 'imsi-999700000000001'}))
    runtime = AsyncMock(return_value={'unit': 'dedicated-phone.service'})
    monkeypatch.setattr(terminal.terminal_runtime, 'control', runtime)
    response = client.post('/api/v1/terminal/airplane-mode', json={'enabled': True, 'imsi': 'imsi-999700000000001'}, headers=teacher_headers)
    assert response.status_code == 200
    remote.native_operation.assert_awaited_once_with('ueransim-cli', 'ue',
        {'command': 'deregister', 'node_name': 'imsi-999700000000001'})
    assert runtime.await_args_list[-1].args == ('imsi-999700000000001', 'stop')
    terminal.traffic.assert_awaited_once_with(False, imsi='imsi-999700000000001')
    remote.stop_service.assert_not_called()


def test_terminal_remote_unavailable_is_not_simulated(client, teacher_headers):
    assert client.get('/api/v1/terminal/status?imsi=imsi-999700000000001', headers=teacher_headers).status_code == 503
    assert client.post('/api/v1/terminal/traffic/n6-probe', json={'imsi': 'imsi-999700000000001'}, headers=teacher_headers).status_code == 503


def test_teacher_selector_includes_all_observed_ues_without_inventing_devices(client, teacher_headers, monkeypatch):
    from fastapi import HTTPException
    data = observed('imsi-999700000000001')
    data['available_nodes'] = ['imsi-999700000000001', 'imsi-999700000000004', 'imsi-999700000000001']
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    monkeypatch.setattr(terminal, 'management_get', Mock(side_effect=HTTPException(503)))
    response = client.get('/api/v1/terminal/status?imsi=imsi-999700000000001', headers=teacher_headers)
    assert response.status_code == 200
    assert [n['imsi'] for n in response.json()['available_nodes']] == ['imsi-999700000000001', 'imsi-999700000000004']
    data['available_nodes'] = []
    response = client.get('/api/v1/terminal/status?imsi=imsi-999700000000001', headers=teacher_headers)
    assert response.json()['available_nodes'] == []


def test_n6_incomplete_download_is_not_success(client, teacher_headers, monkeypatch):
    operation = AsyncMock(return_value={'completed': False, 'received_bytes': 1200, 'exit_code': 28})
    monkeypatch.setattr(terminal, 'n6_probe', operation)
    response = client.post('/api/v1/terminal/traffic/n6-probe', json={'imsi': 'imsi-999700000000001'}, headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()['completed'] is False
    assert response.json()['received_bytes'] == 1200
    operation.assert_awaited_once_with(imsi='imsi-999700000000001')


def test_terminal_actions_delegate_and_audit(client, teacher_headers, monkeypatch):
    operation = AsyncMock(return_value={'enabled': True})
    monkeypatch.setattr(terminal, 'airplane', operation)
    response = client.post('/api/v1/terminal/airplane-mode', json={'enabled': True, 'imsi': 'imsi-999700000000001'}, headers=teacher_headers)
    assert response.status_code == 200
    operation.assert_awaited_once_with(True, 'imsi-999700000000001')
    assert client.post('/api/v1/terminal/airplane-mode', json={'enabled': 'false'}, headers=teacher_headers).status_code == 422


def test_af_boost_enables_and_disables(client, teacher_headers, monkeypatch):
    data = observed('imsi-999700000000001')
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    remote = AsyncMock()
    remote._run.return_value = 'HTTP/2 201 Created\r\nLocation: http://127.0.0.13:7777/npcf-policyauthorization/v1/app-sessions/42\r\n\r\n{}'
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)

    # Enable AF Boost
    res = client.post('/api/v1/terminal/af-boost', json={'enabled': True, 'imsi': 'imsi-999700000000001'}, headers=teacher_headers)
    assert res.status_code == 200
    assert res.json()['active'] is True
    assert res.json()['qos'] == '5QI=2'
    assert res.json()['pcc_rule'] == 'pcc-boost-video'

    # Disable AF Boost
    res_off = client.post('/api/v1/terminal/af-boost', json={'enabled': False, 'imsi': 'imsi-999700000000001'}, headers=teacher_headers)
    assert res_off.status_code == 200
    assert res_off.json()['active'] is False
    assert res_off.json()['qos'] == '5QI=9'

    # Status must reflect standard QoS and inactive boost
    res_status = client.get('/api/v1/terminal/status?imsi=imsi-999700000000001', headers=teacher_headers)
    assert res_status.status_code == 200
    assert res_status.json()['af_boost']['active'] is False
    assert res_status.json()['af_boost']['qos'] == '5QI=9'
    assert res_status.json()['pcc_qos'] is None
