import asyncio
import json
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import HTTPException
from app.core.config import get_settings
from app.services import terminal_devices as devices


def observation(supi='imsi-999700000000002', sst=2, address='10.47.0.3'):
    return {'supi': supi, 'native': {'ps-list': f'''PDU Session1:
  state: PS-ACTIVE
  apn: 5g-plus
  address: {address}
  s-nssai: {{sst: {sst}, sd: 0x000002}}
'''}, 'interfaces': [{'name': 'uesimtun7', 'addresses': [address]}]}


@pytest.mark.parametrize('data', [observation(supi='imsi-999700000000001'), observation(sst=1),
                                 observation(address='10.45.0.3'), {'supi':'imsi-999700000000002'}])
def test_mismatched_identity_slice_or_pool_never_sends(data, monkeypatch):
    monkeypatch.setattr(devices.terminal, 'read_terminal', AsyncMock(return_value=data))
    remote = Mock()
    monkeypatch.setattr(devices.terminal, 'adapter', lambda: remote)
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', True)
    with pytest.raises(HTTPException) as error:
        asyncio.run(devices.measure('vehicle', 'probe'))
    assert error.value.status_code == 409
    remote._execute_sync.assert_not_called()


def test_measure_uses_observed_tun_and_no_retries_or_fabricated_ack(monkeypatch):
    monkeypatch.setattr(devices.terminal, 'read_terminal', AsyncMock(return_value=observation()))
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', True)
    remote = Mock()
    remote._sudo_cmd.side_effect = lambda c: c
    remote._execute_sync.return_value = json.dumps({'sent':20,'received':0,'rtt_ms':None,'loss_pct':100})
    monkeypatch.setattr(devices.terminal, 'adapter', lambda: remote)
    result = asyncio.run(devices.measure('vehicle','probe'))
    assert result['interface'] == 'uesimtun7'
    assert result['received'] == 0 and result['rtt_ms'] is None
    command = remote._execute_sync.call_args.args[0]
    assert 'uesimtun7 10.47.0.3 172.31.48.2 probe 20 20' in command
    assert remote._execute_sync.call_args.kwargs['retries'] == 1


def test_student_cannot_control_another_device(client, student_headers, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(devices, 'measure', call)
    assert client.post('/api/v1/terminal/devices/vehicle/brake', json={'imsi': 'imsi-999700000000005'}, headers=student_headers).status_code == 403
    assert client.get('/api/v1/terminal/devices/sensor?imsi=imsi-999700000000006', headers=student_headers).status_code == 403
    call.assert_not_called()


@pytest.mark.parametrize('payload', [{'packets':1001}, {'pps':201}, {'packets':True},
                                    {'target':'127.0.0.1'}, {'imsi':'imsi-999700000000001'}])
def test_burst_limits_and_no_client_routing(client, teacher_headers, payload, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(devices,'measure',call)
    assert client.post('/api/v1/terminal/devices/sensor/burst',json={'imsi': 'imsi-999700000000003', **payload},headers=teacher_headers).status_code == 422
    call.assert_not_called()


def test_device_endpoints_require_auth(client):
    assert client.get('/api/v1/terminal/devices/sensor').status_code == 401
    assert client.post('/api/v1/terminal/devices/vehicle/brake').status_code == 401


@pytest.mark.parametrize('payload', [{'sensors': 0}, {'sensors': 1001}, {'sensors': True},
                                    {'sensors': 1.5}, {'sensors': 10, 'target': '127.0.0.1'}])
def test_fleet_limits_and_fixed_destination(client, teacher_headers, payload, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(devices, 'measure', call)
    assert client.post('/api/v1/terminal-devices/industrial/telemetry',
                       json={'imsi': 'imsi-999700000000003', **payload}, headers=teacher_headers).status_code == 422
    call.assert_not_called()


def test_fleet_requires_authorized_industrial_identity(client, student_headers, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(devices, 'measure', call)
    url = '/api/v1/terminal-devices/industrial/telemetry'
    assert client.post(url, json={'sensors': 10}).status_code == 401
    assert client.post(url, json={'sensors': 10, 'imsi': 'imsi-999700000000006'}, headers=student_headers).status_code == 403
    call.assert_not_called()


@pytest.mark.parametrize('count', [10, 50, 100, 1000])
def test_fleet_density_is_sent_through_industrial_gateway(client, teacher_headers, monkeypatch, count):
    call = AsyncMock(return_value={'sent': count, 'received': count})
    monkeypatch.setattr(devices, 'measure', call)
    response = client.post('/api/v1/terminal-devices/industrial/telemetry',
                           json={'sensors': count, 'imsi': 'imsi-999700000000003'}, headers=teacher_headers)
    assert response.status_code == 200
    call.assert_awaited_once_with('sensor', 'burst', count, 200, fleet_size=count, imsi='imsi-999700000000003')


def test_fleet_ack_matrix_preserves_missing_and_unsent_sensors(monkeypatch):
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', True)
    monkeypatch.setattr(devices, 'observed', AsyncMock(return_value={
        'interface': 'uesimtun9', 'address': '10.46.0.3'}))
    remote = Mock()
    remote._sudo_cmd.side_effect = lambda c: c
    remote._execute_sync.return_value = json.dumps({
        'sent': 3, 'received': 2, 'sent_sequences': [0, 2, 3], 'acked_sequences': [0, 3]})
    monkeypatch.setattr(devices.terminal, 'adapter', lambda: remote)
    result = asyncio.run(devices.measure('sensor', 'burst', 4, 200, fleet_size=4))
    assert result['sent_sensor_ids'] == [1, 3, 4]
    assert result['acked_sensor_ids'] == [1, 4]
    assert result['virtual_sensors'] == 4 and result['radio_ues'] == 1
    assert 'uesimtun9 10.46.0.3 10.46.0.1 fleet 4 200' in remote._execute_sync.call_args.args[0]
