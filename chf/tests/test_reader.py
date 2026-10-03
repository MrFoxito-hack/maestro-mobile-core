import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from conftest import SUPI

READER = 'reader-for-tests-only-not-a-real-secret-0001'


def test_reader_cannot_change_account_or_reconcile(settings, account):
    settings.reader_token = READER
    # Validate through Pydantic as production does.
    settings = type(settings).model_validate(settings.model_dump())
    with TestClient(create_app(settings, management=True), headers={'Authorization': 'Bearer ' + READER}) as client:
        for kind in ('accounts', 'sessions', 'cdrs', 'ledger'):
            assert client.get('/admin/v1/' + kind).status_code == 200
        assert client.get('/admin/v1/accounts/' + SUPI).status_code == 200
        assert client.put('/admin/v1/accounts/' + SUPI, json={'supi': SUPI, 'quotaBytes': 9999}).status_code == 403
        assert client.post('/admin/v1/sessions/unknown/reconcile', json={'reason': 'test'}).status_code == 403
        assert client.post('/admin/v1/accounts/' + SUPI + '/topup', json={
            'requestId': '00000000-0000-4000-8000-000000000001', 'amountBytes': 50}).status_code == 403


def test_reader_must_be_distinct(settings):
    settings.reader_token = settings.admin_token
    with pytest.raises(ValueError):
        settings.validate_security(management=True)
