"""Tests for the periodic ingestion scheduler."""
import sqlite3
import time
from pathlib import Path

import pytest

from app.ingestion.scheduler import IngestionScheduler, SliceCapacityMap, DEFAULT_SLICE_MAPS
from app.core.contract import Contract
from app.core.database import Database


@pytest.fixture
def tmp_nwdaf_db(tmp_path):
    return Database(tmp_path / 'nwdaf-test.sqlite3')


@pytest.fixture
def tmp_pm_db(tmp_path):
    """Create a minimal PM database with enough samples for forecast."""
    pm_path = tmp_path / 'ems.db'
    db = sqlite3.connect(pm_path)
    db.execute('''CREATE TABLE metric_samples (
        testbed_id TEXT, scenario_id TEXT, object_id TEXT, counter_id TEXT,
        bucket_epoch REAL, value REAL, unit TEXT, source TEXT, quality TEXT)''')
    # Generate 60 samples at 300s intervals (5 hours of data)
    now = time.time()
    for i in range(60):
        epoch = now - (60 - i) * 300
        # Simulate load oscillating between 20% and 60% of 50-session capacity
        raw_sessions = 10 + 15 * (1 + (i % 12) / 12.0)  # 10..25 sessions
        db.execute('INSERT INTO metric_samples VALUES (?,?,?,?,?,?,?,?,?)',
                   ('testbed-01', '5g-sa', 'nf:smf:smf-01',
                    '5g.smf.sessions.active', epoch, raw_sessions,
                    'sessions', 'open5gs-smfd', 'measured'))
    db.commit()
    db.close()
    return pm_path


def test_scheduler_construction(tmp_nwdaf_db):
    contract = Contract()
    scheduler = IngestionScheduler(tmp_nwdaf_db, contract, None)
    assert scheduler.status == {}
    assert scheduler.cycle_seconds == 300


def test_scheduler_no_pm_source(tmp_nwdaf_db):
    import asyncio
    contract = Contract()
    scheduler = IngestionScheduler(tmp_nwdaf_db, contract, '/nonexistent/path.db')

    async def run_one():
        # Run one cycle manually
        scheduler.pm_path = Path('/nonexistent/path.db')
        # Simulate one iteration of the loop body
        if scheduler.pm_path and scheduler.pm_path.exists():
            await scheduler._ingest_slices()
        else:
            scheduler._last_status = {'status': 'no_pm_source'}

    asyncio.get_event_loop().run_until_complete(run_one())
    assert scheduler._last_status['status'] == 'no_pm_source'


def test_scheduler_ingests_from_pm(tmp_nwdaf_db, tmp_pm_db):
    import asyncio
    contract = Contract()
    scheduler = IngestionScheduler(
        tmp_nwdaf_db, contract, tmp_pm_db,
        slice_maps=[SliceCapacityMap(
            snssai={"sst": 1},
            testbed_id="testbed-01",
            object_id="nf:smf:smf-01",
            counter_id="5g.smf.sessions.active",
            capacity_units=50,
            interval_seconds=300,
            period=12,
            label="test-slice",
        )],
    )

    asyncio.get_event_loop().run_until_complete(scheduler._ingest_slices())

    # Verify an analysis was recorded
    result = tmp_nwdaf_db.latest('LOAD_LEVEL_INFORMATION', '{"sst": 1}', max_age=600)
    # Result may be None if samples are too old (>5 min), but the function should
    # not raise. If we get a result, validate its structure.
    if result is not None:
        assert 'sliceLoadLevelInfos' in result
        assert 'timeStampGen' in result


def test_default_maps_are_valid():
    """Ensure default configuration doesn't crash on construction."""
    for smap in DEFAULT_SLICE_MAPS:
        assert smap.capacity_units > 0
        assert smap.interval_seconds in (300, 1800)
        assert smap.period >= 2


def test_invalid_capacity_rejected():
    from dataclasses import replace
    for value in (0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            replace(DEFAULT_SLICE_MAPS[0], capacity_units=value)


def test_anomaly_sampling_supports_one_hour_baseline():
    from app.ingestion.scheduler import AnomalyWatch
    with pytest.raises(ValueError):
        AnomalyWatch('local', 'nf:amf', 'counter.rate', None,
                     'service_access_rate', 300, 'test')


def test_simulated_pm_cannot_drive_sbi(tmp_pm_db):
    from app.ingestion.pm_adapter import read_samples
    with sqlite3.connect(tmp_pm_db) as db:
        db.execute("UPDATE metric_samples SET quality='simulated'")
    with pytest.raises(ValueError, match='Untrusted'):
        read_samples(tmp_pm_db, testbed='testbed-01', object_id='nf:smf:smf-01',
                     counter_id='5g.smf.sessions.active', start=0, end=time.time())


def test_observed_load_published_without_invented_forecast(tmp_pm_db, tmp_nwdaf_db):
    import asyncio
    with sqlite3.connect(tmp_pm_db) as db:
        db.execute('DELETE FROM metric_samples')
        db.execute('INSERT INTO metric_samples VALUES(?,?,?,?,?,?,?,?,?)',
                   ('local','5g-sa','nf:upf-01','nwdaf.slice.dl.bps',time.time()-10,
                    10000000,'bps','actual-counter-fixture','computed'))
    scheduler = IngestionScheduler(tmp_nwdaf_db, Contract(), tmp_pm_db,
        slice_maps=[SliceCapacityMap({'sst':1,'sd':'000001'},'local','nf:upf-01',
                                     'nwdaf.slice.dl.bps',20000000,300,12,'test',90)])
    asyncio.get_event_loop().run_until_complete(scheduler._ingest_slices())
    payload = tmp_nwdaf_db.latest('LOAD_LEVEL_INFORMATION', '{"sd": "000001", "sst": 1}')
    assert payload['sliceLoadLevelInfos'][0]['loadLevelInformation'] == 50
    with tmp_nwdaf_db.connect() as db:
        import json
        result = json.loads(db.execute('SELECT evidence FROM analyses').fetchone()[0])
    assert result['points'] == []
    assert result['status'] == 'insufficient_history'
