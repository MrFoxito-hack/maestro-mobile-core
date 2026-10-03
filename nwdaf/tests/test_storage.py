import sqlite3
import pytest
from app.core.database import Database
from app.ingestion.pm_adapter import read_samples, readonly
from app.ingestion.chf_adapter import read_account


def test_wal_immutable_idempotent_and_restart(tmp_path):
    path = tmp_path/'analytics.sqlite3'; db = Database(path)
    db.record('LOAD_LEVEL_INFORMATION', 's1', {'x':1}, {'model':'v1'}, {'sliceLoadLevelInfos': []})
    db.record('LOAD_LEVEL_INFORMATION', 's1', {'x':1}, {'model':'v1'}, {'sliceLoadLevelInfos': []})
    with db.connect() as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute('SELECT COUNT(*) FROM analyses').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError): conn.execute('DELETE FROM analyses')
    assert Database(path).latest('LOAD_LEVEL_INFORMATION','s1') is not None


def test_source_readers_are_readonly_and_scoped(tmp_path):
    path = tmp_path/'source.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE metric_samples(testbed_id,scenario_id,object_id,counter_id,bucket_epoch,value,unit,source,quality);
        INSERT INTO metric_samples VALUES('lab','5g-sa','nf:amf','x',300,2,'eventos','native','measured');
        CREATE TABLE charging_accounts(supi,quota_bytes,consumed_bytes,enabled);
        INSERT INTO charging_accounts VALUES('imsi-test',100,30,1);
        CREATE TABLE charging_sessions(supi,reserved_bytes,status);
        INSERT INTO charging_sessions VALUES('imsi-test',20,'OPEN');''')
    rows = read_samples(path, testbed='lab', object_id='nf:amf', counter_id='x', start=0, end=600)
    assert rows[0]['value'] == 2
    assert read_account(path, 'imsi-test')['available_bytes'] == 50
    with readonly(path) as db:
        with pytest.raises(sqlite3.OperationalError): db.execute('DELETE FROM metric_samples')
    with pytest.raises(FileNotFoundError): read_account(tmp_path/'missing.db', 'imsi-test')
