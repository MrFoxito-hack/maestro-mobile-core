import sqlite3
import time
import pytest
from app.ingestion.export_pm import export_snapshot


def test_export_preserves_real_values_and_excludes_simulation(tmp_path):
    source = tmp_path/'ems.db'; target = tmp_path/'snapshot.db'
    with sqlite3.connect(source) as db:
        db.execute('''CREATE TABLE metric_samples(id INTEGER PRIMARY KEY,
            collected_at TEXT,bucket_epoch REAL,testbed_id TEXT,scenario_id TEXT,
            object_id TEXT,counter_id TEXT,value REAL,unit TEXT,source TEXT,quality TEXT)''')
        for i, quality in enumerate(('measured','computed','simulated'),1):
            db.execute('INSERT INTO metric_samples VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (i,'2026-09-27',time.time()-1,'local','5g-sa','nf:smf','counter',i,'sessions','fixture',quality))
    before = source.read_bytes()
    result = export_snapshot(source,target)
    assert result['rows'] == 2
    assert source.read_bytes() == before
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT value FROM metric_samples ORDER BY id').fetchall() == [(1.0,),(2.0,)]
    with pytest.raises(ValueError): export_snapshot(source,source)
    with pytest.raises(ValueError): export_snapshot(source,target)


def test_snapshot_age_is_not_hidden_by_scheduler_timestamp(tmp_path):
    from app.ingestion.pm_adapter import snapshot_status
    path = tmp_path/'snapshot.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE export_metadata(exported_at REAL,row_count INTEGER)')
        db.execute('INSERT INTO export_metadata VALUES(1000,42)')
    assert snapshot_status(path, now=1100)['status'] == 'fresh'
    assert snapshot_status(path, now=1200)['status'] == 'stale'
    assert snapshot_status(path, now=900)['status'] == 'stale'
