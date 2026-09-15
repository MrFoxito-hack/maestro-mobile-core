import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.engine import settle
from app.errors import ChargingError
from app.main import create_app
from app.models import ChargingDataRequest
from app.repository import ChargingRepository
from app.service import ChargingService
from conftest import ADMIN_TOKEN, ROOT, SMF_ID, SMF_TOKEN, SUPI, charging_request


def create(client, **kwargs):
    result = client.post(ROOT, json=charging_request(1, **kwargs))
    assert result.status_code == 201, result.text
    return result.headers['location'], result.json()['multipleUnitInformation'][0]


def account_state(admin):
    return admin.get('/admin/v1/accounts/' + SUPI).json()


@pytest.mark.parametrize('used,wanted,debit,grant,overrun', [
    (0, 1000, 0, 1000, 0), (500, 1000, 500, 500, 0),
    (1000, 1000, 1000, 0, 0), (1100, 1000, 1000, 0, 100)])
def test_engine_boundary_cases(used, wanted, debit, grant, overrun):
    result = settle(quota=1000, consumed=0, other_reserved=0, reserved=1000,
                    used=used, wanted=wanted, enabled=True)
    assert (result.debit, result.reservation, result.overrun) == (debit, grant, overrun)
    assert result.available_after >= 0


def test_retry_flag_does_not_change_identity(client, admin, account):
    payload = charging_request(1)
    first = client.post(ROOT, json=payload)
    payload['retransmissionIndicator'] = True
    retry = client.post(ROOT, json=payload)
    assert retry.headers['location'] == first.headers['location']
    assert retry.json() == first.json()
    assert account_state(admin)['reserved_bytes'] == 1000


def test_update_replaces_not_adds_to_unused_reservation(client, admin, account):
    path, _ = create(client)
    result = client.post(path + '/update', json=charging_request(2, used=200, requested=1000))
    assert result.status_code == 200
    assert account_state(admin)['reserved_bytes'] == 1000  # not 1800
    assert account_state(admin)['consumed_bytes'] == 200


def test_positive_final_grant_remains_authorized(client, account):
    _, result = create(client, requested=2500)
    assert result['grantedUnit']['totalVolume'] == 2500
    assert result['resultCode'] == 'SUCCESS'
    assert result['finalUnitIndication']['finalUnitAction'] == 'TERMINATE'


def test_disabled_account_cannot_renew(client, admin, account):
    path, _ = create(client)
    assert admin.put('/admin/v1/accounts/' + SUPI,
                     json={'supi': SUPI, 'quotaBytes': 2500, 'enabled': False}).status_code == 200
    result = client.post(path + '/update', json=charging_request(2, used=100))
    assert result.json()['multipleUnitInformation'][0]['grantedUnit']['totalVolume'] == 0
    assert account_state(admin)['consumed_bytes'] == 100


def test_usage_duplicate_under_new_invocation_is_not_debited_twice(client, admin, account):
    path, _ = create(client)
    payload = charging_request(2, used=300)
    assert client.post(path + '/update', json=payload).status_code == 200
    payload['invocationSequenceNumber'] = 3
    assert client.post(path + '/update', json=payload).status_code == 200
    assert account_state(admin)['consumed_bytes'] == 300


def test_duplicate_usage_with_changed_measurement_rolls_back(client, admin, account):
    path, _ = create(client)
    payload = charging_request(2, used=300)
    assert client.post(path + '/update', json=payload).status_code == 200
    payload['invocationSequenceNumber'] = 3
    payload['multipleUnitUsage'][0]['usedUnitContainer'][0]['totalVolume'] = 301
    assert client.post(path + '/update', json=payload).status_code == 409
    assert account_state(admin)['consumed_bytes'] == 300


def test_overrun_preserved_without_stealing_other_session_reservation(client, admin, account):
    path, _ = create(client, requested=1500)
    create(client, charging_id=101, requested=1000)
    result = client.post(path + '/update', json=charging_request(2, used=1600))
    assert result.status_code == 200
    state = account_state(admin)
    assert (state['consumed_bytes'], state['reserved_bytes'], state['available_bytes']) == (1500, 1000, 0)
    session = next(row for row in admin.get('/admin/v1/sessions').json()['items'] if row['charging_id'] == 100)
    assert (session['observed_bytes'], session['overrun_bytes']) == (1600, 100)
    assert result.json()['multipleUnitInformation'][0]['grantedUnit']['totalVolume'] == 0


def test_duplicate_release_single_cdr_and_final_directions(client, admin, account):
    path, _ = create(client)
    payload = charging_request(2, used=500, requested=0)
    payload['multipleUnitUsage'][0]['usedUnitContainer'][0].update(uplinkVolume=300, downlinkVolume=200)
    payload['triggers'] = [{'triggerType': 'FINAL', 'triggerCategory': 'IMMEDIATE_REPORT'}]
    assert client.post(path + '/release', json=payload).status_code == 204
    payload['retransmissionIndicator'] = True
    assert client.post(path + '/release', json=payload).status_code == 204
    records = admin.get('/admin/v1/cdrs').json()
    assert records['total'] == 1
    cdr = records['items'][0]['record']
    assert (cdr['totalBytes'], cdr['uplinkBytes'], cdr['downlinkBytes']) == (500, 300, 200)
    assert cdr['pduSession']['pduSessionID'] == 1
    assert cdr['pduSession']['dnnId'] == 'internet'
    assert cdr['terminationReason'] == 'FINAL'
    assert account_state(admin)['reserved_bytes'] == 0
    assert client.post(path + '/update', json=charging_request(3)).status_code == 410
    assert client.post(path + '/release', json=charging_request(3)).status_code == 410


def test_exact_retry_survives_process_restart(client, settings, account):
    path, _ = create(client)
    payload = charging_request(2, used=250)
    previous = client.post(path + '/update', json=payload)
    with TestClient(create_app(settings), headers={'Authorization': 'Bearer ' + SMF_TOKEN}) as restarted:
        assert restarted.get('/ready').status_code == 200
        assert restarted.post(path + '/update', json=payload).json() == previous.json()
        assert restarted.app.state.repository.get_account(SUPI)['consumed_bytes'] == 250


def test_cannot_reduce_quota_below_commitment(client, admin, account):
    create(client, requested=2000)
    response = admin.put('/admin/v1/accounts/' + SUPI, json={'supi': SUPI, 'quotaBytes': 1999})
    assert response.status_code == 409
    assert account_state(admin)['quota_bytes'] == 2500


def test_sql_guards_and_append_only_ledger(client, account):
    path, _ = create(client)
    repository = client.app.state.repository
    with pytest.raises(sqlite3.IntegrityError), repository.transaction(immediate=True) as conn:
        conn.execute('UPDATE charging_accounts SET quota_bytes=1')
    with pytest.raises(sqlite3.IntegrityError), repository.transaction(immediate=True) as conn:
        conn.execute('DELETE FROM account_ledger')
    with pytest.raises(sqlite3.IntegrityError), repository.transaction(immediate=True) as conn:
        conn.execute('UPDATE charging_events SET used_bytes=0')
    with pytest.raises(sqlite3.IntegrityError), repository.transaction(immediate=True) as conn:
        conn.execute('UPDATE charging_sessions SET reserved_bytes=1000000')


def test_transaction_rolls_back_when_evidence_write_fails(client, admin, account, monkeypatch):
    path, _ = create(client)
    before = account_state(admin)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('injected storage failure')
    monkeypatch.setattr(client.app.state.service, '_event', unavailable)
    result = client.post(path + '/update', json=charging_request(2, used=200))
    assert result.status_code == 503
    assert 'injected' not in result.text
    assert account_state(admin) == before
    with client.app.state.repository.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM usage_events').fetchone()[0] == 0


def test_100_parallel_sessions_do_not_double_spend(settings):
    repository = ChargingRepository(settings.database_path)
    repository.initialize()
    repository.upsert_account(SUPI, 100_000, True)
    service = ChargingService(repository, 2000, 60)
    def reserve(index):
        _, result = service.create(ChargingDataRequest.model_validate(charging_request(1, charging_id=index, requested=2000)))
        return result.multipleUnitInformation[0].grantedUnit.totalVolume
    with ThreadPoolExecutor(max_workers=20) as executor:
        grants = list(executor.map(reserve, range(100)))
    assert sum(grants) == 100_000
    assert repository.get_account(SUPI)['reserved_bytes'] == 100_000
    assert repository.get_account(SUPI)['available_bytes'] == 0


def test_operator_recovery_requires_explicit_fencing_and_keeps_evidence(client, admin, account):
    path, _ = create(client)
    target = '/admin/v1/sessions/' + path.rsplit('/', 1)[1] + '/reconcile'
    assert admin.post(target, json={'reason': 'consumer stopped after crash'}).status_code == 400
    response = admin.post(target, json={'reason': 'consumer stopped after crash', 'confirmedConsumerStopped': True})
    assert response.status_code == 200
    assert account_state(admin)['reserved_bytes'] == 0
    cdr = admin.get('/admin/v1/cdrs').json()['items'][0]['record']
    assert cdr['evidence'] == 'PARTIAL_FINAL_USAGE_UNKNOWN'


@pytest.mark.parametrize('change', ['notify', 'time', 'identity', 'oversize', 'float', 'directions', 'initial_usage'])
def test_unsupported_or_invalid_inputs_are_not_silently_accepted(client, account, change):
    payload = charging_request(1)
    if change == 'notify':
        payload.pop('notifyUri')
    elif change == 'time':
        payload['multipleUnitUsage'][0]['requestedUnit'] = {'time': 10}
    elif change == 'identity':
        payload['nfConsumerIdentification']['nFName'] = 'smf-01'
    elif change == 'oversize':
        payload['multipleUnitUsage'][0]['requestedUnit']['totalVolume'] = 2**64
    elif change == 'float':
        payload['multipleUnitUsage'][0]['requestedUnit']['totalVolume'] = 1.5
    elif change == 'initial_usage':
        payload = charging_request(1, used=1)
    elif change == 'directions':
        payload['multipleUnitUsage'][0]['usedUnitContainer'] = [dict(localSequenceNumber=1, totalVolume=100, uplinkVolume=100, downlinkVolume=100)]
    response = client.post(ROOT, json=payload)
    assert response.status_code == 400
    assert response.headers['content-type'] == 'application/problem+json'


def test_api_isolation_and_credentials(client, admin, account):
    assert client.get('/admin/v1/accounts/' + SUPI).status_code == 404
    assert admin.post(ROOT, json=charging_request(1)).status_code == 404
    assert client.post(ROOT, json=charging_request(1), headers={'Authorization': ''}).status_code == 401
    assert client.post(ROOT, json=charging_request(1), headers={'Authorization': 'Bearer ' + ADMIN_TOKEN}).status_code == 403
    payload = charging_request(1)
    payload['nfConsumerIdentification']['nFName'] = '00000000-0000-4000-8000-000000000002'
    assert client.post(ROOT, json=payload).status_code == 403
    assert admin.get('/admin/v1/cdrs', headers={'Authorization': 'Bearer ' + SMF_TOKEN}).status_code == 403


def test_body_limit_even_without_content_length(client):
    result = client.post(ROOT, content=iter([b'x'*200000, b'x'*200000]))
    assert result.status_code == 413


def test_invocation_gaps_and_foreign_owners_rejected(client, account):
    path, _ = create(client)
    assert client.post(path + '/update', json=charging_request(3, used=1)).status_code == 409
    payload = charging_request(2, used=1)
    payload['nfConsumerIdentification']['nFName'] = '00000000-0000-4000-8000-000000000002'
    with pytest.raises(ChargingError) as error:
        client.app.state.service.update(path.rsplit('/',1)[1], ChargingDataRequest.model_validate(payload))
    assert error.value.status == 403


def test_zero_usage_does_not_consume_credit(client, admin, account):
    path, _ = create(client)
    for sequence in range(2, 10):
        assert client.post(path + '/update', json=charging_request(sequence, used=0)).status_code == 200
    assert account_state(admin)['consumed_bytes'] == 0
    assert account_state(admin)['reserved_bytes'] == 1000
