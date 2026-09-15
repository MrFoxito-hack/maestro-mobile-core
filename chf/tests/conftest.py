import os
import tempfile
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="maestro-chf-tests-"))
os.environ["CHF_DATABASE_PATH"] = str(TEST_ROOT / "chf.db")
os.environ["CHF_DEFAULT_GRANT_BYTES"] = "1000"
os.environ["CHF_VALIDITY_TIME_SECONDS"] = "60"

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    database = Path(os.environ["CHF_DATABASE_PATH"])
    if database.exists():
        database.unlink()
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def account(client):
    response = client.put(
        "/admin/v1/accounts/imsi-999700000000001",
        json={"supi": "imsi-999700000000001", "quotaBytes": 2500, "enabled": True},
    )
    assert response.status_code == 200
    return response.json()


def charging_request(
    sequence: int,
    *,
    requested: int = 1000,
    used: int | None = None,
    charging_id: int = 100,
):
    unit = {"ratingGroup": 1, "requestedUnit": {"totalVolume": requested}}
    if used is not None:
        unit["usedUnitContainer"] = [
            {"localSequenceNumber": sequence, "totalVolume": used}
        ]
    return {
        "subscriberIdentifier": "imsi-999700000000001",
        "chargingId": charging_id,
        "nfConsumerIdentification": {"nodeFunctionality": "SMF", "nFName": "smf-01"},
        "invocationTimeStamp": "2026-09-14T12:00:00Z",
        "invocationSequenceNumber": sequence,
        "multipleUnitUsage": [unit],
    }
