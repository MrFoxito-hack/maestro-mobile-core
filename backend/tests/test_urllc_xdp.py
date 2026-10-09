import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import upf_xdp as endpoint
from app.services import terminal_devices, urllc_xdp as control


def snapshot(**changes):
    return {'available': True, 'slice': 'urllc', 'namespace': 'maestro-urllc',
            'effective_mode': 'xdp', 'session_generation': '1234', 'ue': '10.47.0.3',
            'confirmed': True, 'counters': {
                'ul_redirect_requested': {'packets': 20},
                'dl_redirect_requested': {'packets': 20}}, **changes}


@pytest.mark.parametrize('body', [
    {'mode':'xdp','slice':'embb'}, {'mode':'xdp','slice':'miot'},
    {'mode':'xdp','slice':'urllc','interface':'enp0s8'},
    {'mode':'xdp','slice':'urllc','imsi':'imsi-999700000000001'},
])
def test_browser_cannot_select_other_targets(client, teacher_headers, monkeypatch, body):
    change = AsyncMock()
    monkeypatch.setattr(control, 'switch', change)
    assert client.post('/api/v1/upf-xdp/mode', headers=teacher_headers, json=body).status_code == 422
    change.assert_not_called()


def test_explicit_urllc_bypasses_only_old_blanket_guard(client, teacher_headers, monkeypatch):
    monkeypatch.setattr(endpoint, 'get_settings', lambda: SimpleNamespace(multi_upf_enabled=True))
    monkeypatch.setattr(endpoint, 'request', lambda _: pytest.fail('Legacy root agent must not run'))
    change = AsyncMock(return_value=snapshot())
    monkeypatch.setattr(control, 'switch', change)
    assert client.post('/api/v1/upf-xdp/mode', headers=teacher_headers,
                       json={'mode':'xdp','slice':'urllc'}).status_code == 200
    change.assert_awaited_once_with('xdp')


def test_ambiguous_off_in_triad_only_targets_urllc(client, teacher_headers, monkeypatch):
    monkeypatch.setattr(endpoint, 'get_settings', lambda: SimpleNamespace(multi_upf_enabled=True))
    monkeypatch.setattr(endpoint, 'request', lambda _: pytest.fail('Root agent must not run'))
    change = AsyncMock(return_value=snapshot(effective_mode='kernel'))
    monkeypatch.setattr(control, 'switch', change)
    assert client.post('/api/v1/upf-xdp/mode', headers=teacher_headers,
                       json={'mode':'legacy'}).status_code == 200
    change.assert_awaited_once_with('legacy')


@pytest.mark.parametrize('mode', ['kernel','legacy'])
def test_recovery_does_not_require_a_live_ue_or_policy(monkeypatch, mode):
    observed = AsyncMock(side_effect=HTTPException(409))
    monkeypatch.setattr(terminal_devices, 'observed', observed)
    request = Mock(return_value=snapshot(effective_mode='kernel'))
    monkeypatch.setattr(control, 'request', request)
    assert asyncio.run(control.switch(mode))['effective_mode'] == 'kernel'
    request.assert_called_once_with('off'); observed.assert_not_called()


@pytest.mark.parametrize('available,ue', [(False,'10.47.0.3'),(True,'10.47.0.4')])
def test_ineligible_or_changed_pdu_cannot_activate(monkeypatch, available, ue):
    monkeypatch.setattr(control, 'status', AsyncMock(return_value=snapshot(available=available,ue=ue)))
    monkeypatch.setattr(terminal_devices, 'observed', AsyncMock(return_value={'address':'10.47.0.3'}))
    request=Mock();monkeypatch.setattr(control, 'request', request)
    with pytest.raises(HTTPException) as e: asyncio.run(control.switch('xdp'))
    assert e.value.status_code == 409
    request.assert_not_called()


@pytest.mark.parametrize('failure', ['none','loss','no_bpf','new_generation','unreachable'])
def test_activation_needs_real_ack_counters_and_same_generation(monkeypatch, failure):
    before=snapshot(counters={d+'_redirect_requested':{'packets':0} for d in ('ul','dl')})
    after=snapshot()
    if failure=='no_bpf': after=before
    if failure=='new_generation': after=snapshot(session_generation='5678')
    monkeypatch.setattr(control, 'status', AsyncMock(side_effect=[before,after]))
    monkeypatch.setattr(terminal_devices, 'observed', AsyncMock(return_value={'address':'10.47.0.3'}))
    probe=AsyncMock(return_value={'source_ip':'10.47.0.3','sent':20,'received':19 if failure=='loss' else 20})
    if failure=='unreachable':probe.side_effect=HTTPException(503)
    monkeypatch.setattr(terminal_devices, 'measure', probe)
    request=Mock(return_value=snapshot());monkeypatch.setattr(control,'request',request)
    if failure=='none':
        assert asyncio.run(control.switch('xdp'))['verification']['received']==20
        assert [c.args[0] for c in request.call_args_list]==['on','confirm']
    else:
        with pytest.raises(HTTPException):asyncio.run(control.switch('xdp'))
        assert [c.args[0] for c in request.call_args_list]==['on','off']


def test_remote_command_is_scoped_and_never_retried(monkeypatch):
    import json
    monkeypatch.setattr(control, 'get_settings',lambda:SimpleNamespace(
        execution_mode='remote', multi_upf_enabled=True,upf_ssh_port=2223))
    remote=Mock();remote._sudo_cmd.side_effect=lambda c:c
    remote._execute_sync.return_value=json.dumps(snapshot())
    monkeypatch.setattr(control,'RemoteExecutionAdapter',lambda _:remote)
    control.request('on',ue='10.47.0.3')
    call=remote._execute_sync.call_args
    assert call.args[0]=='/usr/sbin/ip netns exec maestro-urllc /usr/bin/python3 '+control.AGENT+' on --ue 10.47.0.3'
    assert call.kwargs=={'port':2223,'retries':1}
    for ue in ['10.45.0.3','10.46.0.3','10.47.0.3; reboot']:
        with pytest.raises(HTTPException):control.request('on',ue=ue)
    assert remote._execute_sync.call_count==1


def test_vehicle_capability_is_observed_and_sensor_cannot_claim_it(monkeypatch):
    monkeypatch.setattr(terminal_devices,'observed',AsyncMock(return_value={'address':'10.47.0.3'}))
    state=AsyncMock(return_value=snapshot());monkeypatch.setattr(control,'device_status',state)
    assert asyncio.run(terminal_devices.status('vehicle'))['xdp']['available']
    assert not asyncio.run(terminal_devices.status('sensor'))['xdp']['available']
    assert state.await_count==1


def test_vehicle_preserves_policy_block_reason(monkeypatch):
    monkeypatch.setattr(terminal_devices,'observed',AsyncMock(return_value={'address':'10.47.0.3'}))
    state=AsyncMock(return_value={'available':False,'reason':'Charging enforcement is active'})
    monkeypatch.setattr(control,'device_status',state)
    assert asyncio.run(terminal_devices.status('vehicle'))['xdp']['reason']=='Charging enforcement is active'
