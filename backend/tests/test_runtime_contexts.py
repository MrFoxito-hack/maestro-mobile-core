import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import get_settings
from app.services.execution import RemoteExecutionAdapter, ExecutionError
from app.services.catalog import load_catalog
from app.services import observability
from app.models import ComponentStatus


def remote():
    adapter = RemoteExecutionAdapter.__new__(RemoteExecutionAdapter)
    adapter.settings = get_settings()
    adapter._sudo_cmd = lambda command: 'sudo -n ' + command
    return adapter


@pytest.mark.parametrize('fail_port', [None, 2224])
def test_runtime_observes_netns_independently_and_keeps_partial_evidence(fail_port):
    adapter = remote(); calls = []
    async def run(command, port):
        calls.append((command, port))
        if port == fail_port: raise RuntimeError('private SSH diagnostic')
        if command.endswith('hostname'): return 'lab'
        if command.endswith('address show'): return '[]'
        address = '10.210.50.22' if 'netns exec maestro-urllc' in command else '10.210.50.8'
        return f'udp UNCONN 0 0 {address}:8805 0.0.0.0:*'
    adapter._run = run
    result = asyncio.run(adapter.runtime_snapshot())
    hosts = {h['id']: h for h in result['hosts']}
    urllc = hosts['upf-urllc']
    assert urllc['listening_ports'][0]['address'] == '10.210.50.22'
    assert urllc['namespace'] == 'maestro-urllc'
    assert ('sudo -n ip netns exec maestro-urllc ss -H -lnutS', 2223) in calls
    assert hosts['upf-vm']['observation_status'] == 'observed'
    assert hosts['core']['observation_status'] == 'observed'
    assert result['complete'] == (fail_port is None)
    if fail_port:
        assert hosts['upf-vm2']['observation_status'] == 'unavailable'
        assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('component,path,port', [
    ('smf3', '/etc/open5gs/smf3.yaml', 2222),
    ('upf3', '/etc/open5gs/upf-urllc.yaml', 2223),
    ('upf2', '/etc/open5gs/upf.yaml', 2224),
    ('upf', '/etc/open5gs/upf.yaml', 2223)])
def test_config_routing_uses_catalog_not_filename_substrings(component, path, port):
    assert remote()._config_port(path, component) == port


def test_config_path_shared_by_two_hosts_requires_component():
    with pytest.raises(ExecutionError):
        remote()._config_port('/etc/open5gs/upf.yaml')


@pytest.mark.parametrize('observed,context', [(True, 'correct'), (True, 'wrong'), (False, 'correct')])
def test_alarm_socket_evidence_is_namespace_scoped_and_unknown_not_down(client, monkeypatch, observed, context):
    configured = next(c for c in load_catalog()['5g-sa']['components'] if c['id'] == 'upf3')
    component = ComponentStatus(**{**configured, 'status': 'running'})
    sockets = [{k: e[k] for k in ('protocol', 'address', 'port')} for e in component.expected_endpoints]
    host = {'id': 'upf-urllc', 'observation_status': 'observed' if observed else 'unavailable',
            'listening_ports': sockets if context == 'correct' else []}
    adapter = SimpleNamespace(runtime_snapshot=AsyncMock(return_value={
        'source': 'remote', 'hosts': [host], 'listening_ports': sockets}),
        get_ip_forward=AsyncMock(return_value=True))
    monkeypatch.setattr(observability.scenario_manager, 'adapter', adapter)
    monkeypatch.setattr(observability.scenario_manager, 'status', AsyncMock(return_value=SimpleNamespace(components=[component])))
    evaluated = set()
    alarms = asyncio.run(observability.collect_alarms('5g-sa', evaluated))
    ports = [a for a in alarms if ':port:' in a['id']]
    assert len(ports) == (2 if observed and context == 'wrong' else 0)
    assert any(':port:' in key for key in evaluated) == observed
