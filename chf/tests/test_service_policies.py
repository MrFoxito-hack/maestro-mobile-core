import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models import ChargingDataRequest
from app.repository import ChargingRepository
from app.service import ChargingService
from conftest import ROOT, SUPI, charging_request
from test_rel16_contract import validator


def policy(admin, mode='ZERO_RATED', dnn='5g-plus', sst=2, sd='000002'):
    response = admin.put('/admin/v1/service-policies', json=dict(
        dnn=dnn, sst=sst, sd=sd, ratingGroup=1, mode=mode, grantBlockSize=10))
    assert response.status_code == 200, response.text


def request(seq, *, messages=False, **kwargs):
    payload = charging_request(seq, **kwargs)
    info = payload['pDUSessionChargingInformation']['pduSessionInformation']
    info.update(dnnId='corporate' if messages else '5g-plus', networkSlicingInfo={
        'sNSSAI': {'sst': 3 if messages else 2, 'sd': '000003' if messages else '000002'}})
    if messages:
        unit = payload['multipleUnitUsage'][0]
        unit['requestedUnit'] = {'serviceSpecificUnits': unit['requestedUnit']['totalVolume']}
        for usage in unit.get('usedUnitContainer', []):
            usage['serviceSpecificUnits'] = usage.pop('totalVolume')
            usage['totalVolume'] = usage['serviceSpecificUnits'] * 92
    return payload


def info(response):
    assert response.status_code in (200, 201), response.text
    return response.json()['multipleUnitInformation'][0]


def test_zero_rated_at_zero_available_with_real_volume_and_replays(client, admin, account):
    policy(admin)
    # Consume the existing positive v2 quota entirely; available credit is zero.
    first = client.post(ROOT, json=charging_request(1, requested=2500, charging_id=101))
    client.post(first.headers['location'] + '/release', json=charging_request(2, used=2500, charging_id=101))
    created = client.post(ROOT, json=request(1))
    assert info(created)['grantedUnit'] == {'totalVolume': 1000}
    assert 'finalUnitIndication' not in info(created)
    path = created.headers['location']
    for seq in (2, 3):
        payload = request(seq, used=1200)
        result = client.post(path + '/update', json=payload)
        assert 'finalUnitIndication' not in info(result)
        assert client.post(path + '/update', json=payload).json() == result.json()
    assert client.post(path + '/release', json=request(4, used=333)).status_code == 204
    cdr = admin.get('/admin/v1/cdrs').json()['items'][0]['record']
    assert cdr['totalBytes'] == 2733 and cdr['debitedBytes'] == 0
    assert cdr['overrunBytes'] == 400
    assert cdr['servicePolicy']['mode'] == 'ZERO_RATED'
    state = admin.get('/admin/v1/accounts/' + SUPI).json()
    assert state['available_bytes'] == state['reserved_bytes'] == 0
    assert state['consumed_bytes'] == 2500


@pytest.mark.parametrize('field,value', [('dnnId', 'internet'), ('sst', 1), ('sd', '000001'), ('ratingGroup', 2)])
def test_exemption_requires_entire_service_key(client, admin, account, field, value):
    policy(admin)
    payload = request(1)
    pdu = payload['pDUSessionChargingInformation']['pduSessionInformation']
    if field == 'dnnId':
        pdu[field] = value
    elif field == 'ratingGroup':
        payload['multipleUnitUsage'][0][field] = value
    else:
        pdu['networkSlicingInfo']['sNSSAI'][field] = value
    assert client.post(ROOT, json=payload).status_code == 201
    assert admin.get('/admin/v1/accounts/' + SUPI).json()['reserved_bytes'] == 1000


def test_policy_is_pinned_across_change_restart_and_release(client, admin, account):
    policy(admin)
    response = client.post(ROOT, json=request(1))
    ref = response.headers['location'].split('/')[-1]
    policy(admin, mode='BYTE_QUOTA')
    repo = ChargingRepository(client.app.state.repository.database_path)
    repo.initialize()
    service = ChargingService(repo, 1000, 60)
    result = service.update(ref, ChargingDataRequest.model_validate(request(2, used=100)))
    assert result.multipleUnitInformation[0].grantedUnit.totalVolume == 1000
    assert repo.get_account(SUPI)['consumed_bytes'] == repo.get_account(SUPI)['reserved_bytes'] == 0
    assert client.post(ROOT, json=request(1, charging_id=102)).status_code == 201
    assert repo.get_account(SUPI)['reserved_bytes'] == 1000


def test_message_blocks_contract_debit_cdr_and_idempotence(client, admin, account):
    policy(admin, 'MESSAGE_QUOTA', 'corporate', 3, '000003')
    assert admin.put('/admin/v1/accounts/' + SUPI + '/messages', json={'quotaMessages': 15}).status_code == 200
    payload = request(1, messages=True, requested=100)
    validator('ChargingDataRequest').validate(payload)
    created = client.post(ROOT, json=payload)
    assert info(created)['grantedUnit'] == {'serviceSpecificUnits': 10}
    validator('ChargingDataResponse').validate(created.json())
    path = created.headers['location']
    payload = request(2, messages=True, used=10, requested=10)
    validator('ChargingDataRequest').validate(payload)
    result = client.post(path + '/update', json=payload)
    assert info(result)['grantedUnit'] == {'serviceSpecificUnits': 5}
    assert info(result)['finalUnitIndication']['finalUnitAction'] == 'TERMINATE'
    assert client.post(path + '/update', json=payload).json() == result.json()
    assert client.post(path + '/release', json=request(3, messages=True, used=5)).status_code == 204
    state = admin.get('/admin/v1/accounts/' + SUPI).json()
    assert state['consumed_bytes'] == state['reserved_bytes'] == 0
    assert state['messages']['consumed_messages'] == 15
    assert state['messages']['available_messages'] == 0
    cdr = admin.get('/admin/v1/cdrs').json()['items'][0]['record']
    assert cdr['debitedMessages'] == cdr['totalMessages'] == 15
    assert cdr['totalBytes'] == 1380 and cdr['debitedBytes'] == 0


def test_message_reservations_concurrent_and_sql_guard(client, admin, account):
    policy(admin, 'MESSAGE_QUOTA', 'corporate', 3, '000003')
    admin.put('/admin/v1/accounts/' + SUPI + '/messages', json={'quotaMessages': 25})
    service = client.app.state.service
    def create(i):
        return service.create(ChargingDataRequest.model_validate(request(1, messages=True, charging_id=i)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(100, 120)))
    assert sum(r.multipleUnitInformation[0].grantedUnit.serviceSpecificUnits for _, r in results) == 25
    assert admin.put('/admin/v1/accounts/' + SUPI + '/messages', json={'quotaMessages': 24}).status_code == 409
    with pytest.raises(sqlite3.IntegrityError), client.app.state.repository.transaction() as conn:
        conn.execute('UPDATE message_accounts SET quota_messages=24 WHERE supi=?', (SUPI,))


def test_message_policy_never_derives_messages_from_bytes(client, admin, account):
    policy(admin, 'MESSAGE_QUOTA', 'corporate', 3, '000003')
    admin.put('/admin/v1/accounts/' + SUPI + '/messages', json={'quotaMessages': 15})
    bad = request(1, messages=True)
    bad['multipleUnitUsage'][0]['requestedUnit'] = {'totalVolume': 1000}
    assert client.post(ROOT, json=bad).status_code == 400
    created = client.post(ROOT, json=request(1, messages=True))
    payload = request(2, messages=True, used=5)
    del payload['multipleUnitUsage'][0]['usedUnitContainer'][0]['serviceSpecificUnits']
    assert client.post(created.headers['location'] + '/update', json=payload).status_code == 400
    assert admin.get('/admin/v1/accounts/' + SUPI).json()['messages']['consumed_messages'] == 0


def test_zero_rated_disabled_account_denied(client, admin, account):
    policy(admin)
    created = client.post(ROOT, json=request(1))
    admin.put('/admin/v1/accounts/' + SUPI, json={'supi': SUPI, 'enabled': False})
    result = client.post(created.headers['location'] + '/update', json=request(2, used=100))
    assert info(result)['resultCode'] == 'END_USER_SERVICE_DENIED'
    assert info(result)['grantedUnit']['totalVolume'] == 0
