import json
from copy import deepcopy
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.services import charging, nwdaf, pcf_control
from app.services.operations import operations_for
from app.services.subscribers import subscriber_service


def execute(client, headers, component, operation, parameters):
    return client.post('/api/v1/operations/execute', headers=headers, json={
        'scenario_id': '5g-sa', 'component_id': component,
        'operation_id': operation, 'parameters': parameters})


def test_catalog_removes_epc_and_sorts_infrastructure():
    for component in ('mme', 'udm', 'udr', 'chf', 'pcf', 'nwdaf'):
        ops = operations_for({'id': component, 'unit': 'open5gs-' + component + 'd'})
        assert not any(op.id.startswith('mme.') for op in ops)
        infrastructure = [op for op in ops if op.id.startswith(('system.', 'network.', 'software.'))]
        assert all(op.category == 'O&M Infraestructura' for op in infrastructure)
        assert ops[-len(infrastructure):] == infrastructure


def test_subscriber_lifecycle_and_secret_redaction(client, teacher_headers):
    imsi = '999700000000096'
    subscriber_service.memory.pop(imsi, None)
    created = execute(client, teacher_headers, 'udm', 'subscriber.create',
                      {'imsi': 'imsi-' + imsi, 'sst': 3, 'sd': '000003', 'apn_dnn': 'corporate'})
    assert created.status_code == 201, created.text
    assert created.json()['parameters']['key'] == '[REDACTED]'
    assert created.json()['data']['security']['k'] == '[REDACTED]'
    assert '465B5CE8B199B49FAA5F0A2EE238A6BC' not in created.text
    doc = subscriber_service.memory[imsi]
    assert doc['slice'][0]['sd'] == '000003'
    assert doc['slice'][0]['session'][0]['qos']['index'] == 9
    assert execute(client, teacher_headers, 'udr', 'subscriber.create', {'imsi': imsi, 'sst': 3, 'sd': '000003', 'apn_dnn': 'corporate'}).status_code == 409
    changed = execute(client, teacher_headers, 'udr', 'subscriber.update', {'imsi': imsi, 'sst': 1, 'sd': '000001', 'apn_dnn': 'internet'})
    assert changed.status_code == 201
    assert changed.json()['data']['slice'][0]['sd'] == '000001'
    assert execute(client, teacher_headers, 'udm', 'subscriber.delete', {'imsi': imsi}).status_code == 201
    assert imsi not in subscriber_service.memory
    assert execute(client, teacher_headers, 'udm', 'subscriber.delete', {'imsi': imsi}).status_code == 422


@pytest.mark.parametrize('parameters', [
    {'imsi': 'bad'}, {'imsi': 999700000000004},
    {'imsi': '999700000000004', 'sst': 1.5},
    {'imsi': '999700000000004', 'sd': 2},
    {'imsi': '999700000000004', 'key': 'secret'},
    {'imsi': '999700000000004', 'apn_dnn': 'unsupported'},
])
def test_invalid_subscriber_inputs(client, teacher_headers, parameters):
    assert execute(client, teacher_headers, 'udm', 'subscriber.create', parameters).status_code == 422


def test_charging_exact_contract(client, teacher_headers, monkeypatch):
    calls = []
    def request(path, **kwargs):
        calls.append((path, kwargs))
        return {'supi': 'imsi-999700000000004', 'available_bytes': 10, 'reserved_bytes': 2, 'consumed_bytes': 3}
    monkeypatch.setattr(charging, 'management_request', request)
    p = {'imsi': '999700000000004'}
    assert execute(client, teacher_headers, 'chf', 'chf.quota', {**p, 'quota_mb': 0.5}).status_code == 201
    path, args = calls[-1]
    assert path.endswith('/imsi-999700000000004/topup')
    assert args['payload']['amountBytes'] == 524288
    assert UUID(args['payload']['requestId']).version == 4
    assert execute(client, teacher_headers, 'chf', 'chf.state', {**p, 'state': 'SUSPENDED'}).status_code == 201
    assert calls[-1][1] == {'method': 'PUT', 'payload': {'supi': 'imsi-999700000000004', 'enabled': False}}
    assert execute(client, teacher_headers, 'chf', 'chf.balance', p).json()['data']['reserved_bytes'] == 2
    count = len(calls)
    for quota in (0, -1, True, 'nan', 'inf', 0.1):
        assert execute(client, teacher_headers, 'chf', 'chf.quota', {**p, 'quota_mb': quota}).status_code == 422
    assert len(calls) == count


def test_remote_errors_are_failures(client, teacher_headers, monkeypatch):
    def unavailable(*args, **kwargs):
        raise HTTPException(409, 'CHF HTTP 409: QUOTA_COMMITTED')
    monkeypatch.setattr(charging, 'management_request', unavailable)
    response = execute(client, teacher_headers, 'chf', 'chf.quota', {'imsi': '999700000000004', 'quota_mb': 50})
    assert response.status_code == 422
    assert 'QUOTA_COMMITTED' in response.json()['detail']


def test_qos_and_mode_dispatch(client, teacher_headers, monkeypatch):
    calls = []
    def control(payload):
        calls.append(payload)
        return {'status': 'success', 'detail': 'N7_ACK'}
    monkeypatch.setattr(pcf_control, 'control_request', control)
    context = {'token': 1, 'expected_version': 0, 'action_id': 'test-action-0001'}
    def fenced(component, operation, parameters):
        return client.post('/api/v1/operations/execute', headers=teacher_headers, json={
            'scenario_id': '5g-sa', 'component_id': component, 'operation_id': operation,
            'parameters': parameters, 'authority': context})
    assert fenced('pcf', 'pcf.qos', {'imsi': '999700000000004', 'mbr_dl_mbps': 1.5}).status_code == 201
    sent = calls[-1]
    assert sent['authority']['token'] == 1
    assert sent['authority']['owner'] == 'local/docente'
    assert {k: v for k, v in sent.items() if k != 'authority'} == {
        'operation': 'qos', 'supi': 'imsi-999700000000004', 'five_qi': 9, 'mbr_dl_mbps': 1.5}
    assert fenced('nwdaf', 'nwdaf.mode', {'mode': 'MANUAL'}).status_code == 201
    assert calls[-1] == {'operation': 'mode', 'mode': 'MANUAL', 'authority': sent['authority']}


def test_nwdaf_resolution_and_missing_forecast(monkeypatch):
    snssai = {'sst': 3, 'sd': '000003'}
    monkeypatch.setattr(nwdaf, 'get_health', lambda: {'slice_maps': [{'object_id': 'nf:upf-02:triad-v1', 'snssai': snssai}]})
    def observed(event, event_filter):
        assert event == 'LOAD_LEVEL_INFORMATION'
        assert json.loads(event_filter) == {'snssais': [snssai]}
        return {'sliceLoadLevelInfos': [{'loadLevelInformation': 42}]}
    monkeypatch.setattr(nwdaf, 'get_analytics', observed)
    prediction = {'snssai': snssai, 'fresh': True, 'evidence': {'model': 'hw-additive-ridge-v1',
                  'points': [{'horizon_seconds': 900, 'value': 51}]}}
    monkeypatch.setattr(nwdaf, 'nwdaf_request', lambda path: {'items': [prediction]})
    assert nwdaf.get_nf_analytics('UPF-02', 15)['predicted_load_percent'] == 51
    assert nwdaf.get_nf_analytics('UPF-02', 30)['prediction_status'] == 'unavailable'
    prediction['fresh'] = False
    assert nwdaf.get_nf_analytics('UPF-02', 15)['predicted_load_percent'] is None
    with pytest.raises(HTTPException):
        nwdaf.get_nf_analytics('UPF-01', 15)


@pytest.mark.parametrize('component,operation,parameters', [
    ('udm', 'subscriber.delete', {'imsi': '999700000000004'}),
    ('chf', 'chf.balance', {'imsi': '999700000000004'}),
    ('pcf', 'pcf.qos', {'imsi': '999700000000004'}),
    ('nwdaf', 'nwdaf.mode', {'mode': 'MANUAL'}),
])
def test_operator_permissions(client, student_headers, component, operation, parameters):
    assert execute(client, student_headers, component, operation, parameters).status_code == 403


@pytest.mark.parametrize('suffix,sst,sd,dnn', [
    (1, 1, '000001', 'internet'), (4, 1, '000001', 'internet'),
    (2, 2, '000002', '5g-plus'), (5, 2, '000002', '5g-plus'),
    (3, 3, '000003', 'corporate'), (6, 3, '000003', 'corporate'),
])
def test_c7_six_ues_conflicts_and_lifecycle(client, teacher_headers, monkeypatch, suffix, sst, sd, dnn):
    # Isolated UDR: these are catalog identities, never live subscriptions.
    monkeypatch.setattr(subscriber_service, 'memory', {})
    p = dict(imsi=f'99970000000000{suffix}', sst=sst, sd=sd, apn_dnn=dnn)
    created = execute(client, teacher_headers, 'udm', 'subscriber.create', p)
    assert created.status_code == 201, created.text
    before = deepcopy(subscriber_service.memory)
    conflict = execute(client, teacher_headers, 'udm', 'subscriber.create', p)
    assert conflict.status_code == 409
    result = conflict.json()['result']
    assert result['status'] == 'failed' and 'ya existe' in result['error']
    history = client.get('/api/v1/operations/history', headers=teacher_headers).json()
    assert any(r['id'] == result['id'] and r['status'] == 'failed' for r in history)
    assert subscriber_service.memory == before
    other = dict(sst=2, sd='000002', apn_dnn='5g-plus') if sst != 2 else dict(sst=1, sd='000001', apn_dnn='internet')
    assert execute(client, teacher_headers, 'udm', 'subscriber.update', {**p, **other}).status_code == 409
    assert subscriber_service.memory == before
    assert execute(client, teacher_headers, 'udr', 'subscriber.update', p).status_code == 201
    assert execute(client, teacher_headers, 'udm', 'subscriber.delete', {'imsi': p['imsi']}).status_code == 201
    assert not subscriber_service.memory


@pytest.mark.parametrize('changes', [
    {'sst': 1.5}, {'sst': True}, {'sst': 256}, {'sst': -1}, {'sd': 3},
    {'sd': '00003'}, {'sd': 'GG0003'}, {'apn_dnn': 'unsupported'},
    {'sst': 1}, {'sd': '000002'}, {'apn_dnn': 'internet'}, {'unexpected': 1},
])
@pytest.mark.parametrize('operation', ['subscriber.create', 'subscriber.update'])
def test_c7_invalid_triplet_never_writes(client, teacher_headers, monkeypatch, changes, operation):
    monkeypatch.setattr(subscriber_service, 'memory', {})
    p = {'imsi': '999700000000096', 'sst': 3, 'sd': '000003', 'apn_dnn': 'corporate'}
    assert execute(client, teacher_headers, 'udm', operation, {**p, **changes}).status_code == 422
    assert not subscriber_service.memory


@pytest.mark.parametrize('missing', ['sst', 'sd', 'apn_dnn'])
@pytest.mark.parametrize('operation', ['subscriber.create', 'subscriber.update'])
def test_c7_triplet_is_explicit(client, teacher_headers, missing, operation):
    p = {'imsi': '999700000000096', 'sst': 3, 'sd': '000003', 'apn_dnn': 'corporate'}
    p.pop(missing)
    assert execute(client, teacher_headers, 'udm', operation, p).status_code == 422
