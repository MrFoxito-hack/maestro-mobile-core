import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import ChargingError
from app.models import ChargingDataRequest
from app.repository import ChargingRepository
from app.service import ChargingService
from conftest import ROOT, SUPI, charging_request


def test_concurrent_retransmissions_produce_one_debit(client, account):
    response = client.post(ROOT, json=charging_request(1))
    service = client.app.state.service
    request = ChargingDataRequest.model_validate(charging_request(2, used=250))
    ref = response.headers['location'].rsplit('/', 1)[1]
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda _: service.update(ref, request).model_dump_json(), range(100)))
    assert len(set(results)) == 1
    assert client.app.state.repository.get_account(SUPI)['consumed_bytes'] == 250


def test_migration_preserves_old_data_and_refuses_to_invent_owner(tmp_path):
    path = tmp_path / 'v1.db'
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE charging_accounts(supi TEXT PRIMARY KEY,quota_bytes INTEGER NOT NULL,
            consumed_bytes INTEGER NOT NULL,enabled INTEGER NOT NULL,created_at TEXT,updated_at TEXT);
        CREATE TABLE charging_sessions(charging_data_ref TEXT PRIMARY KEY,create_key TEXT UNIQUE,
            supi TEXT,rating_group INTEGER,status TEXT,reserved_bytes INTEGER,consumed_bytes INTEGER,
            last_invocation_sequence INTEGER,created_at TEXT,updated_at TEXT,released_at TEXT);
        INSERT INTO charging_accounts VALUES('imsi-999700000000001',2500,100,1,'2026-09-14','2026-09-14');
        INSERT INTO charging_sessions VALUES('legacy','legacy','imsi-999700000000001',1,'OPEN',900,100,1,'2026-09-14','2026-09-14',NULL);
    """)
    conn.close()
    repository = ChargingRepository(path)
    repository.initialize()
    repository.initialize()
    assert repository.get_account(SUPI)['consumed_bytes'] == 100
    assert repository.get_account(SUPI)['reserved_bytes'] == 900
    session = repository.list_records('sessions')['items'][0]
    assert session['owner_nf'] is None
    assert session['observed_bytes'] == session['unclassified_bytes'] == 100
    with pytest.raises(ChargingError, match='legacy session'):
        ChargingService(repository, 1000, 60).update('legacy', ChargingDataRequest.model_validate(charging_request(2)))
