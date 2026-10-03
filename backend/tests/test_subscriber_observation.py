import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import subscribers
from app.services.execution import RemoteExecutionAdapter
from app.services.scenarios import scenario_manager
from app.services.ue_observation import parse_status


@pytest.fixture
def observation(monkeypatch):
    remote = RemoteExecutionAdapter.__new__(RemoteExecutionAdapter)
    remote._execute_sync = Mock()
    monkeypatch.setattr(scenario_manager, 'adapter', remote)
    monkeypatch.setattr(subscribers, 'get_settings', lambda: SimpleNamespace(execution_mode='remote', ue_ssh_port=2226))
    return subscribers.SubscriberService(), remote._execute_sync


@pytest.mark.parametrize(('rm', 'expected'), [
    ('RM-REGISTERED', True), ('RM-DEREGISTERED', False),
    ('RM-REGISTERED-INITIATED', None), ('', None),
])
def test_registration_requires_exact_observed_state(rm, expected):
    assert parse_status({'status': f'rm-state: {rm}'})['registered'] is expected


def test_queries_ue_host_and_distinguishes_provisioned_profile(observation):
    service, execute = observation
    one, four, unused = '999700000000001', '999700000000004', '999700000000091'
    execute.return_value = json.dumps({'nodes': ['imsi-' + one, 'imsi-' + four, 'imsi-' + one], 'native': {
        'imsi-' + one: {'status': 'rm-state: RM-REGISTERED\ncm-state: CM-IDLE\ncurrent-cell: 1\ncurrent-tac: 1',
                       'ps-list': 'PDU Session1:\n state: PS-ACTIVE\n apn: internet\n address: 10.45.0.45'},
        'imsi-' + four: {'status': 'rm-state: RM-DEREGISTERED'},
    }})
    result = service.get_live_status(imsis=[one, four, unused])
    assert execute.call_count == 1
    assert execute.call_args.kwargs == {'port': 2226, 'retries': 1}
    assert result[one]['registered'] is True  # CM-IDLE does not mean deregistered.
    assert result[one]['active_ip'] == '10.45.0.45'
    assert result[one]['cell_id'] == '1'
    assert result[four]['registered'] is False
    assert result[four]['terminal_available'] is True
    assert result[unused]['registered'] is None
    assert result[unused]['terminal_available'] is False
    assert result[unused]['observation_status'] == 'not_observed'


@pytest.mark.parametrize('payload', [None, 'invalid json', '{"nodes":[],"native":null}'])
def test_unavailable_telemetry_never_becomes_deregistered(observation, payload):
    service, execute = observation
    if payload is None:
        execute.side_effect = RuntimeError('SSH unavailable')
    else:
        execute.return_value = payload
    result = service.get_live_status('999700000000001')['999700000000001']
    assert result['registered'] is None
    assert result['terminal_available'] is None
    assert result['rm_state'] is None


def test_one_failed_ue_does_not_hide_other_observations(observation):
    service, execute = observation
    execute.return_value = json.dumps({'nodes': ['imsi-999700000000001', 'imsi-999700000000004'], 'native': {
        'imsi-999700000000001': {'status': None, 'ps-list': None},
        'imsi-999700000000004': {'status': 'rm-state: RM-REGISTERED'},
    }})
    result = service.get_live_status()
    assert result['999700000000001']['registered'] is None
    assert result['999700000000001']['terminal_available'] is True
    assert result['999700000000004']['registered'] is True


def test_list_retains_sim_profiles_and_masks_keys(observation, monkeypatch):
    service, execute = observation
    monkeypatch.setattr(service, '_remote_mongosh', lambda _: json.dumps([
        {'imsi': '999700000000001', 'security': {'k': 'a' * 32}},
        {'imsi': '999700000000091'},
    ]))
    execute.return_value = json.dumps({'nodes': ['imsi-999700000000001'], 'native': {
        'imsi-999700000000001': {'status': 'rm-state: RM-REGISTERED'},
    }})
    result = service.list()
    assert len(result) == 2
    assert result[0]['terminal_label'] == 'Equipo 1'
    assert result[0]['security']['k'] != 'a' * 32
    assert result[0]['live_status']['registered'] is True
    assert result[1]['live_status']['registered'] is None


def test_status_endpoint_and_simulated_mode_report_unknown(client, teacher_headers):
    response = client.get('/api/v1/subscribers/999700000000001/status', headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()['registered'] is None
    assert client.get('/api/v1/subscribers/invalid/status', headers=teacher_headers).status_code == 422


def test_malformed_yaml_does_not_invent_sessions():
    result = parse_status({'status': 'not: [yaml', 'ps-list': 'PDU Session1: broken'})
    assert result['registered'] is None
    assert result['pdu_sessions'] == []
    assert result['active_ip'] is None
