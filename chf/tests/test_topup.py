from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app.repository import ChargingRepository
from app.errors import ChargingError


def test_topup_atomic_idempotent_and_preserves_consumption(tmp_path):
    repo = ChargingRepository(tmp_path / 'charging.db')
    repo.initialize()
    supi = 'imsi-001010000000001'
    repo.upsert_account(supi, 1000, True)
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.topup(supi, 50, request_id, 'test'), range(4)))
    assert all(result['quota_bytes'] == 1050 for result in results)
    assert repo.get_account(supi)['consumed_bytes'] == 0
    with repo.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_ledger WHERE operation='TOPUP'").fetchone()[0] == 1
    with pytest.raises(ChargingError):
        repo.topup(supi, 51, request_id, 'test')


def test_topup_cannot_create_unknown_subscriber(tmp_path):
    repo = ChargingRepository(tmp_path / 'charging.db')
    repo.initialize()
    with pytest.raises(ChargingError):
        repo.topup('imsi-001010000000001', 50, str(uuid4()), 'test')


def test_state_update_preserves_concurrent_topups(tmp_path):
    repo = ChargingRepository(tmp_path / 'charging.db')
    repo.initialize()
    supi = 'imsi-001010000000001'
    repo.upsert_account(supi, 1000, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        topup = pool.submit(repo.topup, supi, 50, str(uuid4()), 'test')
        state = pool.submit(repo.upsert_account, supi, None, False, 'test')
        topup.result()
        state.result()
    result = repo.get_account(supi)
    assert result['quota_bytes'] == 1050
    assert result['enabled'] == 0
    with pytest.raises(ChargingError):
        repo.upsert_account('imsi-001010000000002', None, True)
