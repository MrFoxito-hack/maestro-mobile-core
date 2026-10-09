"""Offline recovery contracts; no SSH connection or live database access."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('thesis_baseline', Path(__file__).parents[1] / 'thesis_baseline.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


@pytest.mark.parametrize('journal', ['DELETE', 'WAL'])
def test_snapshot_restores_committed_rows_independently_of_live_database(tmp_path, journal):
    source = tmp_path / 'source.db'
    snapshot = tmp_path / 'snapshot.db'
    # Keep the connection open so WAL frames cannot disappear by last-close checkpoint.
    with sqlite3.connect(source) as writer:
        writer.execute('PRAGMA journal_mode=' + journal)
        writer.execute('CREATE TABLE ledger (id INTEGER PRIMARY KEY, value INTEGER)')
        writer.executemany('INSERT INTO ledger VALUES (?,?)', [(1, 123), (2, 456)])
        writer.commit()
        result = baseline.snapshot_database(source, snapshot)
        writer.execute('UPDATE ledger SET value=999 WHERE id=1')
        writer.commit()
    with sqlite3.connect(snapshot) as restored:
        assert restored.execute('SELECT * FROM ledger ORDER BY id').fetchall() == [(1, 123), (2, 456)]
    assert result['integrity'] == 'ok'
    assert result['sha256'] == baseline.digest(snapshot)
    with pytest.raises(FileExistsError):
        baseline.snapshot_database(source, snapshot)


def test_backup_timeout_releases_source_lock(tmp_path):
    source = tmp_path / 'source.db'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE probe (value INTEGER)')
    with pytest.raises(TimeoutError):
        baseline.snapshot_database(source, tmp_path / 'failed.db', deadline_seconds=-1)
    with sqlite3.connect(source, timeout=.1) as conn:
        conn.execute('INSERT INTO probe VALUES (1)')
        conn.commit()
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
