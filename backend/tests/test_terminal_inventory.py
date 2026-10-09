"""Authorization contracts for the configured six-device inventory."""
import json
import sqlite3
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services import terminal_access as access
from app.services import terminal_inventory as catalog
from app.services import terminal, terminal_sessions, terminal_devices, terminal_runtime


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    path = tmp_path / 'permissions.sqlite3'

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    with connect() as conn:
        conn.execute('CREATE TABLE users (username TEXT PRIMARY KEY, role TEXT, '
                     'testbed TEXT, assigned_imsi TEXT, enabled INTEGER)')
        conn.executemany('INSERT INTO users VALUES(?,?,?,?,?)', [
            ('grupo1', 'student', 'local', 'imsi-999700000000001', 1),
            ('docente', 'teacher', None, None, 1),
            ('unassigned', 'student', 'local', 'imsi-999700000000001', 1),
        ])
        access.migrate(conn)
    monkeypatch.setattr(access, 'connection', connect)
    return connect


def devices(username):
    # Deliberately forged caller fields: access must use persisted authority.
    return access.visible_devices(SimpleNamespace(username=username, role='admin', testbed=None))


def test_group_membership_not_legacy_assigned_imsi_grants_visibility(accounts):
    assert [d['supi'][-3:] for d in devices('grupo1')] == ['001', '002', '003']
    assert [d['supi'][-3:] for d in devices('docente')] == ['001', '002', '003', '004', '005', '006']
    assert devices('unassigned') == []
    assert devices('missing') == []


def test_membership_revocation_survives_repeated_migration(accounts):
    with accounts() as conn:
        conn.execute("DELETE FROM terminal_group_members WHERE username='grupo1'")
        access.migrate(conn)
    assert devices('grupo1') == []


@pytest.mark.parametrize('username', ['grupo1', 'docente'])
@pytest.mark.parametrize('update', ["enabled=0", "testbed='another-lab'"])
def test_revoked_or_cross_testbed_identity_cannot_read_registry(accounts, username, update):
    with accounts() as conn:
        conn.execute('UPDATE users SET ' + update + ' WHERE username=?', (username,))
    assert devices(username) == []


def test_moving_ownership_removes_old_group_access(accounts):
    with accounts() as conn:
        conn.execute("UPDATE terminal_device_owners SET group_id='docente' WHERE device_id='g1-vehicle'")
    assert [d['supi'][-3:] for d in devices('grupo1')] == ['001', '003']


@pytest.mark.parametrize('mutation', ['duplicate_supi', 'duplicate_id', 'unknown_group', 'wrong_service', 'static_ip'])
def test_invalid_identity_or_static_session_data_rejected(mutation):
    data = json.loads(catalog.PATH.read_text(encoding='utf-8'))
    if mutation == 'duplicate_supi':
        data['devices'][1]['supi'] = data['devices'][0]['supi']
    elif mutation == 'duplicate_id':
        data['devices'][1]['id'] = data['devices'][0]['id']
    elif mutation == 'unknown_group':
        data['devices'][0]['group_id'] = 'missing'
    elif mutation == 'wrong_service':
        data['devices'][0]['service'] = 'urllc'
    else:
        data['devices'][0]['address'] = '10.45.0.12'
    with pytest.raises(ValidationError):
        catalog.Inventory.model_validate(data)


def test_upf_join_uses_actual_service_numbering(accounts):
    result = devices('grupo1')
    assert [(d['upf_id'], d['dnn'], d['snssai']) for d in result] == [
        ('upf-01', 'internet', {'sst': 1, 'sd': '000001'}),
        ('upf-03', '5g-plus', {'sst': 2, 'sd': '000002'}),
        ('upf-02', 'corporate', {'sst': 3, 'sd': '000003'}),
    ]
    assert all(d['observation_status'] == 'not_queried' for d in result)
    assert not {'ssh_host', 'ssh_port', 'namespace', 'interface', 'teid', 'address'} & set(result[0])


def test_inventory_requires_authentication(client):
    assert client.get('/api/v1/terminal-inventory').status_code == 401


def test_inventory_http_is_scoped_and_not_shared_cache(client, student_headers, teacher_headers):
    for headers, expected in [(student_headers, 3), (teacher_headers, 6), (student_headers, 3)]:
        response = client.get('/api/v1/terminal-inventory', headers=headers)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        body = response.json()
        assert body['scope'] == 'authorized_configuration'
        assert body['actions_migration_complete'] is True
        assert len(body['devices']) == expected


@pytest.mark.parametrize('role', ['student', 'teacher'])
@pytest.mark.parametrize('number', range(1, 7))
def test_all_action_routes_enforce_six_terminal_matrix(client, student_headers, teacher_headers, monkeypatch, role, number):
    """Exercise real authentication/DB authorization, not a mocked permission function."""
    from app.services import terminal_media, terminal_experience
    supi = f'imsi-999700000000{number:03}'
    headers = student_headers if role == 'student' else teacher_headers
    expected = 403 if role == 'student' and number > 3 else 200
    operations = [
        ('airplane-mode', terminal, 'airplane', {'enabled': True}, {'enabled': True}),
        ('airplane-mode', terminal, 'airplane', {'enabled': False}, {'enabled': False}),
        ('traffic/start', terminal, 'traffic', {}, {}),
        ('traffic/stop', terminal, 'traffic', {}, {}),
        ('traffic/n6-probe', terminal, 'n6_probe', {}, {'completed': True}),
        ('speedtest', terminal, 'run_speedtest', {}, {'status': 'success'}),
        ('topup', terminal, 'topup', {'request_id': '12345678-1234-1234-1234-123456789012'}, {}),
        ('af-boost', terminal, 'af_boost', {'enabled': True}, {'active': True}),
        ('apn', terminal_sessions, 'select', {'apn': 'internet'},
         {'active_apn': 'internet', 'session': {'interface': 'uesimtun19'}}),
    ]
    for route, module, name, body, result in operations:
        operation = AsyncMock(return_value=result)
        monkeypatch.setattr(module, name, operation)
        response = client.post('/api/v1/terminal/' + route, json={**body, 'imsi': supi}, headers=headers)
        assert response.status_code == expected, (route, response.text)
        if expected == 403:
            operation.assert_not_called()
        else:
            operation.assert_awaited_once()
            args, kwargs = operation.await_args
            assert supi in args or kwargs.get('imsi') == supi
    for path, module, name, result in [
        ('status', terminal, 'snapshot', {}),
        ('corporate/intranet', terminal_sessions, 'intranet', {'interface': 'uesimtun19'}),
        ('media/720p/init.mp4', terminal_media, 'fetch', b'media'),
    ]:
        operation = AsyncMock(return_value=result)
        monkeypatch.setattr(module, name, operation)
        response = client.get('/api/v1/terminal/' + path, params={'imsi': supi}, headers=headers)
        assert response.status_code == expected
        if expected == 403:
            operation.assert_not_called()
        else:
            assert supi in operation.await_args.args
            assert response.headers['cache-control'] == 'no-store'
    report = Mock(return_value={'status': 'recorded'})
    monkeypatch.setattr(terminal_experience, 'record', report)
    response = client.post('/api/v1/terminal/media/experience', headers=headers, json={
        'imsi': supi, 'session_id': '12345678-1234-1234-1234-123456789012',
        'sequence': 1, 'profile': '720p', 'state': 'playing', 'played_seconds': 1,
        'rebuffer_count': 0, 'rebuffer_seconds': 0, 'received_bytes': 1})
    assert response.status_code == expected
    assert report.call_count == (expected == 200)


@pytest.mark.parametrize('role', ['student', 'teacher'])
@pytest.mark.parametrize('number,kind', [(2, 'vehicle'), (3, 'sensor'), (5, 'vehicle'), (6, 'sensor')])
def test_vertical_action_matrix(client, student_headers, teacher_headers, monkeypatch, role, number, kind):
    supi = f'imsi-999700000000{number:03}'
    headers = student_headers if role == 'student' else teacher_headers
    expected = 403 if role == 'student' and number > 3 else 200
    measure = AsyncMock(return_value={})
    status = AsyncMock(return_value={})
    monkeypatch.setattr(terminal_devices, 'measure', measure)
    monkeypatch.setattr(terminal_devices, 'status', status)
    response = client.get('/api/v1/terminal/devices/' + kind, params={'imsi': supi}, headers=headers)
    assert response.status_code == expected
    routes = ([('/terminal/devices/vehicle/probe', {}), ('/terminal/devices/vehicle/brake', {})]
              if kind == 'vehicle' else [('/terminal/devices/sensor/burst', {'packets': 20}),
                  ('/terminal-devices/industrial/telemetry', {'sensors': 10})])
    for route, body in routes:
        measure.reset_mock()
        response = client.post('/api/v1' + route, json={**body, 'imsi': supi}, headers=headers)
        assert response.status_code == expected
        if expected == 403:
            measure.assert_not_called()
            status.assert_not_called()
        else:
            assert measure.await_args.kwargs['imsi'] == supi
            assert status.await_args.kwargs['imsi'] == supi


def test_missing_identity_never_falls_back_to_primary(client, teacher_headers, monkeypatch):
    remote = Mock()
    monkeypatch.setattr(terminal, 'adapter', remote)
    for path, body in [('speedtest', {}), ('airplane-mode', {'enabled': True}),
                       ('traffic/start', {}), ('traffic/stop', {}), ('traffic/n6-probe', {}),
                       ('apn', {'apn': 'internet'}), ('af-boost', {'enabled': False}),
                       ('devices/vehicle/brake', {}), ('devices/sensor/burst', {})]:
        assert client.post('/api/v1/terminal/' + path, json=body, headers=teacher_headers).status_code == 422
    assert client.get('/api/v1/terminal/status', headers=teacher_headers).status_code == 422
    for operation, args in [(terminal.read_terminal, ()), (terminal.airplane, (False,)),
                            (terminal.traffic, (False,)), (terminal.run_speedtest, ())]:
        with pytest.raises(HTTPException) as error:
            asyncio.run(operation(*args))
        assert error.value.status_code == 422
    remote.assert_not_called()


@pytest.mark.parametrize('username', ['grupo1', 'docente'])
def test_action_authority_is_persisted_and_revocable(accounts, username):
    forged = SimpleNamespace(username=username, role='admin', testbed=None)
    assert access.authorize_device('999700000000001', forged)['supi'] == 'imsi-999700000000001'
    with accounts() as conn:
        conn.execute('UPDATE users SET enabled=0 WHERE username=?', (username,))
    with pytest.raises(HTTPException) as error:
        access.authorize_device('imsi-999700000000001', forged)
    assert error.value.status_code == 403


def observed_device(number):
    device = catalog.public_device(catalog.inventory().devices[number - 1])
    octet = {'internet': 45, '5g-plus': 47, 'corporate': 46}[device['dnn']]
    address = f'10.{octet}.17.{80 + number}'
    interface = f'uesimtun{19 - number}'  # Deliberately unrelated to SUPI order.
    import yaml
    return {'supi': device['supi'], 'interfaces': [{'name': interface, 'addresses': [address]}],
            'native': {'ps-list': yaml.safe_dump({'PDU Session1': {'state': 'PS-ACTIVE',
                'apn': device['dnn'], 'address': address, 's-nssai': device['snssai']}})}}


@pytest.mark.parametrize('number', range(1, 7))
def test_traffic_and_airplane_are_scoped_to_observed_device(client, monkeypatch, number):
    data = observed_device(number)
    supi = data['supi']
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=deepcopy(data)))
    remote = Mock(_run=AsyncMock(), native_operation=AsyncMock())
    remote._sudo_cmd.side_effect = lambda command: command
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)
    runtime = AsyncMock(return_value={'unit': f'dedicated-{number}.service'})
    monkeypatch.setattr(terminal_runtime, 'control', runtime)
    asyncio.run(terminal.traffic(True, imsi=supi))
    command = remote._run.await_args.args[0]
    assert '--unit=maestro-terminal-traffic-' + supi in command
    assert '-I ' + data['interfaces'][0]['name'] in command
    asyncio.run(terminal.airplane(True, imsi=supi))
    assert remote._run.await_args.args[0] == 'systemctl stop maestro-terminal-traffic-' + supi + '.service'
    remote.native_operation.assert_awaited_once_with('ueransim-cli', 'ue', {'command': 'deregister', 'node_name': supi})
    assert runtime.await_args.args == (supi, 'stop')
    asyncio.run(terminal.airplane(False, imsi=supi))
    assert runtime.await_args.args == (supi, 'start')


@pytest.mark.parametrize('number', range(1, 7))
def test_speed_measurement_uses_dynamic_session_not_primary(client, monkeypatch, number):
    from app.core.config import get_settings
    data = observed_device(number)
    supi = data['supi']
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=deepcopy(data)))
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', True)
    remote = Mock()
    remote._sudo_cmd.side_effect = lambda command: command
    remote._execute_sync.return_value = json.dumps({'sent': 20, 'received': 20, 'rtt_ms': 2.7, 'jitter_ms': .2})
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)
    probe = Mock(return_value={'completed': True, 'received_bytes': 262144, 'duration_seconds': 8,
                              'destination': 'fixed-server', 'local_ip': data['interfaces'][0]['addresses'][0]})
    monkeypatch.setattr(terminal, '_n6_probe_sync', probe)
    result = asyncio.run(terminal.run_speedtest(imsi=supi))
    assert result['imsi'] == supi
    assert result['client_ip'] == data['interfaces'][0]['addresses'][0]
    assert result['interface'] == data['interfaces'][0]['name']
    assert result['upload_mbps'] is None
    if number in (1, 4):
        probe.assert_called_once_with(result['interface'])
        assert result['download_mbps'] == .2621
        remote._execute_sync.assert_not_called()
    else:
        probe.assert_not_called()
        assert result['interface'] + ' ' + result['client_ip'] in remote._execute_sync.call_args.args[0]
        assert result['ping_ms'] == 2.7


@pytest.mark.parametrize('data', [{}, {'supi': 'imsi-999700000000001'}])
def test_wrong_observed_identity_never_runs_an_action(client, monkeypatch, data):
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    remote = Mock()
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)
    for call in [terminal.run_speedtest(imsi='imsi-999700000000002'),
                 terminal.traffic(True, imsi='imsi-999700000000002'),
                 terminal_sessions.select('internet', imsi='imsi-999700000000002')]:
        with pytest.raises(HTTPException) as error:
            asyncio.run(call)
        assert error.value.status_code == 409
    remote._run.assert_not_called()


@pytest.mark.parametrize('number', range(1, 7))
def test_apn_reconfiguration_targets_only_selected_runtime(client, monkeypatch, number):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), 'multi_upf_enabled', True)
    data = observed_device(number)
    supi = data['supi']
    target = catalog.public_device(catalog.inventory().devices[number - 1])['dnn']
    runtime = AsyncMock(return_value={})
    remote = Mock(native_operation=AsyncMock())
    monkeypatch.setattr(terminal_runtime, 'control', runtime)
    monkeypatch.setattr(terminal, 'adapter', lambda: remote)
    monkeypatch.setattr(terminal, 'read_terminal', AsyncMock(return_value=data))
    monkeypatch.setattr(terminal_sessions.asyncio, 'sleep', AsyncMock())
    result = asyncio.run(terminal_sessions._switch_single_session(target, data))
    assert result['interface'] == data['interfaces'][0]['name']
    assert [call.args for call in runtime.await_args_list] == [
        (supi, 'inspect'), (supi, 'stop'), (supi, 'configure'), (supi, 'start')]
    assert runtime.await_args_list[2].kwargs['apn'] == target
    remote.native_operation.assert_awaited_once_with('ueransim-cli', 'ue', {'command': 'deregister', 'node_name': supi})


@pytest.fixture
def runtime_host(tmp_path, monkeypatch):
    """Run the actual remote program against six isolated config files/fake systemd."""
    import os
    import shlex
    import subprocess
    import yaml
    units, paths, mutations = {}, {}, []
    for number in range(1, 7):
        path = tmp_path / f'configuration-{number}.yaml'
        path.write_text(yaml.safe_dump({'supi': f'imsi-999700000000{number:03}',
                                        'sessions': [{'apn': 'old'}]}), encoding='utf-8')
        unit = f'ueransim-lab-{20-number}.service'
        units[unit] = '{ argv[]=/lab/build/nr-ue -c ' + shlex.quote(str(path)) + ' ; ignore_errors=no ; }'
        paths[number] = (unit, path)

    def execute(args, **kwargs):
        if args[1] == 'list-unit-files':
            return '\n'.join(unit + ' enabled' for unit in units)
        if args[1] == 'show':
            return units[args[2]] if 'ExecStart' in args[3] else 'inactive'
        mutations.append(args)
        return ''
    monkeypatch.setattr(subprocess, 'check_output', execute)
    monkeypatch.setattr(os, 'chown', lambda *args: None, raising=False)
    return units, paths, mutations


@pytest.mark.parametrize('number', range(1, 7))
@pytest.mark.parametrize('operation', ['start', 'stop', 'configure'])
def test_remote_program_resolves_config_identity_and_only_mutates_its_unit(runtime_host, monkeypatch, number, operation):
    import sys
    import yaml
    _, paths, mutations = runtime_host
    originals = {n: path.read_bytes() for n, (_, path) in paths.items()}
    supi = f'imsi-999700000000{number:03}'
    monkeypatch.setattr(sys, 'argv', ['remote.py', supi, operation, 'corporate', '3', '3'])
    exec(compile(terminal_runtime.SCRIPT, '<UE control>', 'exec'), {})
    if operation == 'configure':
        assert mutations == []
        selected = yaml.safe_load(paths[number][1].read_text())
        assert selected['supi'] == supi
        assert selected['sessions'][0] == {'type': 'IPv4', 'apn': 'corporate', 'slice': {'sst': 3, 'sd': 3}}
        assert paths[number][1].with_suffix('.yaml.before-terminal-apn').read_bytes() == originals[number]
    else:
        assert mutations == [['systemctl', operation, paths[number][0]]]
    for n, (_, path) in paths.items():
        if n != number or operation != 'configure':
            assert path.read_bytes() == originals[n]


@pytest.mark.parametrize('failure', ['absent', 'ambiguous', 'multi_ue'])
def test_remote_program_fails_closed_without_dedicated_unit(runtime_host, monkeypatch, failure):
    import sys
    units, paths, mutations = runtime_host
    unit, _ = paths[2]
    if failure == 'absent':
        del units[unit]
    elif failure == 'ambiguous':
        units['ueransim-duplicate.service'] = units[unit]
    else:
        units[unit] = units[unit].replace(' ; ignore_errors', ' -n 3 ; ignore_errors')
    monkeypatch.setattr(sys, 'argv', ['remote.py', 'imsi-999700000000002', 'stop'])
    with pytest.raises(SystemExit):
        exec(compile(terminal_runtime.SCRIPT, '<UE control>', 'exec'), {})
    assert mutations == []
