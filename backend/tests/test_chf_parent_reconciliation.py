import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from infra.charging.reconcile_missing_accounts import plan, apply


@pytest.fixture
def damaged(tmp_path):
    path = tmp_path / 'chf.sqlite3'
    with sqlite3.connect(path) as c:
        c.executescript('''
        CREATE TABLE charging_accounts(supi TEXT PRIMARY KEY, quota_bytes INTEGER,
          consumed_bytes INTEGER, enabled INTEGER, created_at TEXT, updated_at TEXT);
        CREATE TABLE charging_sessions(charging_data_ref TEXT PRIMARY KEY, supi TEXT,
          status TEXT, consumed_bytes INTEGER, reserved_bytes INTEGER,
          FOREIGN KEY(supi) REFERENCES charging_accounts(supi));
        CREATE TABLE charging_events(id INTEGER PRIMARY KEY, charging_data_ref TEXT,
          FOREIGN KEY(charging_data_ref) REFERENCES charging_sessions(charging_data_ref));
        CREATE TABLE charging_cdrs(id INTEGER PRIMARY KEY, record_json TEXT);
        CREATE TABLE account_ledger(id INTEGER PRIMARY KEY, supi TEXT, charging_data_ref TEXT,
          operation TEXT, actor TEXT, snapshot_json TEXT, created_at TEXT);
        INSERT INTO charging_accounts VALUES('current',1000,300,1,'t0','t1');
        INSERT INTO charging_sessions VALUES('s1','missing','OPEN',200,100);
        INSERT INTO charging_events VALUES(1,'s1');
        INSERT INTO charging_cdrs VALUES(1,'historical');
        ''')
        snapshot = dict(supi='missing', quota_bytes=500, consumed_bytes=200, reserved_bytes=100,
                        enabled=1, created_at='t0', updated_at='t1')
        c.execute('INSERT INTO account_ledger VALUES(1,?,NULL,?,?,?,?)',
                  ('missing', 'UPDATE', 'test', json.dumps({'after': snapshot}), 't1'))
    return path


def test_repair_is_additive_disabled_and_preserves_journal_and_usage(damaged, tmp_path):
    with sqlite3.connect(damaged) as c:
        proposed = plan(c)
        assert proposed['foreign_key_failures'] == 1
        assert c.execute('SELECT count(*) FROM charging_accounts').fetchone()[0] == 1
    backup = tmp_path / 'backup.sqlite3'
    result = apply(damaged, expected=proposed['plan_sha256'], backup=backup)
    assert result['recovered_disabled'] == 1
    with sqlite3.connect(damaged) as c:
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute("SELECT * FROM charging_accounts WHERE supi='current'").fetchone() == ('current', 1000, 300, 1, 't0', 't1')
        assert c.execute("SELECT * FROM charging_accounts WHERE supi='missing'").fetchone() == ('missing', 500, 200, 0, 't0', 't1')
        assert c.execute('SELECT consumed_bytes,reserved_bytes FROM charging_sessions').fetchone() == (200, 100)
        assert c.execute('SELECT count(*) FROM account_ledger').fetchone()[0] == 2
    with sqlite3.connect(backup) as c:
        assert len(c.execute('PRAGMA foreign_key_check').fetchall()) == 1


def test_stale_review_rejects_application(damaged, tmp_path):
    with sqlite3.connect(damaged) as c:
        proposed = plan(c)
        c.execute("UPDATE charging_accounts SET consumed_bytes=301 WHERE supi='current'")
    with pytest.raises(ValueError, match='reviewed_plan_changed'):
        apply(damaged, expected=proposed['plan_sha256'], backup=tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()


def test_missing_ledger_or_mismatched_reservation_cannot_be_guessed(damaged):
    with sqlite3.connect(damaged) as c:
        c.execute('UPDATE charging_sessions SET reserved_bytes=101')
        with pytest.raises(ValueError, match='manual_reconciliation'):
            plan(c)
        c.execute('DELETE FROM account_ledger')
        with pytest.raises(ValueError, match='missing_durable'):
            plan(c)


def test_old_backup_never_overwritten(damaged, tmp_path):
    backup = tmp_path / 'backup'; backup.write_text('preserve')
    with sqlite3.connect(damaged) as c:
        proposed = plan(c)
    with pytest.raises(FileExistsError):
        apply(damaged, expected=proposed['plan_sha256'], backup=backup)
    assert backup.read_text() == 'preserve'
