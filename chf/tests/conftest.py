import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SMF_ID = "00000000-0000-4000-8000-000000000001"
SMF_TOKEN = "test-smf-token-not-for-deployment-0001"
ADMIN_TOKEN = "test-admin-token-not-for-deployment-0001"
SUPI = "imsi-999700000000001"
ROOT = "/nchf-convergedcharging/v3/chargingdata"


@pytest.fixture()
def settings(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "chf.db",
                    default_grant_bytes=1000, validity_time_seconds=60,
                    admin_token=ADMIN_TOKEN, sbi_tokens={SMF_ID: SMF_TOKEN})


@pytest.fixture()
def client(settings):
    with TestClient(create_app(settings), headers={"Authorization": "Bearer " + SMF_TOKEN}) as result:
        yield result


@pytest.fixture()
def admin(settings):
    with TestClient(create_app(settings, management=True),
                    headers={"Authorization": "Bearer " + ADMIN_TOKEN}) as result:
        yield result


@pytest.fixture()
def account(admin):
    response = admin.put("/admin/v1/accounts/" + SUPI,
                         json={"supi": SUPI, "quotaBytes": 2500, "enabled": True})
    assert response.status_code == 200
    return response.json()


def charging_request(sequence, *, requested=1000, used=None, charging_id=100):
    unit = {"ratingGroup": 1, "requestedUnit": {"totalVolume": requested}}
    if used is not None:
        unit["usedUnitContainer"] = [{"localSequenceNumber": sequence, "totalVolume": used}]
    return {
        "subscriberIdentifier": SUPI, "chargingId": charging_id,
        "nfConsumerIdentification": {"nodeFunctionality": "SMF", "nFName": SMF_ID},
        "notifyUri": "http://127.0.0.4:7777/nchf-notify/test",
        "invocationTimeStamp": "2026-09-14T12:00:00Z",
        "invocationSequenceNumber": sequence, "multipleUnitUsage": [unit],
        "pDUSessionChargingInformation": {
            "pduSessionInformation": {"pduSessionID": 1, "dnnId": "internet",
                                     "networkSlicingInfo": {"sNSSAI": {"sst": 1}}}}
    }
