"""Runtime schema. Plans stay immutable; execution has separate mutable state."""
from contextlib import contextmanager
import os
import time


@contextmanager
def schema_lock(path):
    """Serialize the entire migration sequence across API and worker processes."""
    with open(str(path) + '.schema.lock', 'a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.025)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def migrate_real(db):
    """SQLite's documented table rebuild; preserve IDs, children and journals."""
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('PRAGMA user_version').fetchone()[0] >= 4:
            db.commit()
            return
        for table in ('lab_assignments', 'lab_executions'):
            sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            sql = sql.replace('CREATE TABLE ' + table, 'CREATE TABLE ' + table + '_v4', 1)
            sql = sql.replace("CHECK(mode='dry_run')", "CHECK(mode IN ('dry_run','real'))")
            db.execute(sql)
            db.execute(f'INSERT INTO {table}_v4 SELECT * FROM {table}')
            db.execute(f'DROP TABLE {table}')
            db.execute(f'ALTER TABLE {table}_v4 RENAME TO {table}')
        db.execute("ALTER TABLE lab_executions ADD COLUMN real_context TEXT NOT NULL DEFAULT '{}'")
        db.execute("ALTER TABLE lab_executions ADD COLUMN outcome TEXT NOT NULL DEFAULT '{}'")
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Laboratory migration has dangling references')
        db.execute('PRAGMA user_version=4')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')

RUNTIME_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS lab_assignments (
    id TEXT PRIMARY KEY, grantor TEXT NOT NULL, owner TEXT NOT NULL, testbed TEXT NOT NULL,
    template TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='dry_run'),
    observed_ue TEXT NOT NULL, competing_ue TEXT NOT NULL,
    max_runs INTEGER NOT NULL, max_load_mbps REAL NOT NULL,
    max_traffic_bytes INTEGER NOT NULL, max_capture_bytes INTEGER NOT NULL,
    max_jobs INTEGER NOT NULL, used_jobs INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_executions (
    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL UNIQUE REFERENCES campaigns(id),
    assignment_id TEXT NOT NULL REFERENCES lab_assignments(id),
    owner TEXT NOT NULL, testbed TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='dry_run'),
    status TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0, worker_id TEXT, token TEXT NOT NULL,
    error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    finished_at TEXT, baseline TEXT NOT NULL DEFAULT 'nominal'
);
CREATE TABLE IF NOT EXISTS lab_leases (
    resource TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE REFERENCES lab_executions(id),
    token TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL REFERENCES lab_executions(id),
    event TEXT NOT NULL, cursor INTEGER NOT NULL, token TEXT NOT NULL,
    detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lab_journal_execution ON lab_journal(execution_id,id);
CREATE TABLE IF NOT EXISTS lab_sandbox (
    resource TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS lab_journal_no_update BEFORE UPDATE ON lab_journal
    BEGIN SELECT RAISE(ABORT, 'append-only journal'); END;
CREATE TRIGGER IF NOT EXISTS lab_journal_no_delete BEFORE DELETE ON lab_journal
    BEGIN SELECT RAISE(ABORT, 'append-only journal'); END;
PRAGMA user_version=2;
COMMIT;
"""

INVESTIGATION_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS lab_ai_requests (
 id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL REFERENCES lab_evidence(id),
 question TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
 expires_at REAL NOT NULL, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS lab_ai_slots (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 request_id TEXT NOT NULL REFERENCES lab_ai_requests(id), expires_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS lab_ai_result_immutable BEFORE UPDATE ON lab_ai_requests
 WHEN OLD.status != 'pending' BEGIN SELECT RAISE(ABORT, 'immutable AI result'); END;
CREATE TRIGGER IF NOT EXISTS lab_ai_result_no_delete BEFORE DELETE ON lab_ai_requests
 BEGIN SELECT RAISE(ABORT, 'append-only AI history'); END;
PRAGMA user_version=5;
COMMIT;
"""

RESEARCH_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS lab_bindings (
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES lab_assignments(id),
 grantor TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_preflights (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 assignment_id TEXT NOT NULL REFERENCES lab_assignments(id), binding_id TEXT NOT NULL REFERENCES lab_bindings(id),
 status TEXT NOT NULL, document TEXT, raw_document TEXT, sha256 TEXT,
 created_at TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_evidence (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 kind TEXT NOT NULL, document TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_notes (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 document TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS lab_bindings_no_update BEFORE UPDATE ON lab_bindings
 BEGIN SELECT RAISE(ABORT, 'immutable binding'); END;
CREATE TRIGGER IF NOT EXISTS lab_bindings_no_delete BEFORE DELETE ON lab_bindings
 BEGIN SELECT RAISE(ABORT, 'immutable binding'); END;
CREATE TRIGGER IF NOT EXISTS lab_evidence_no_update BEFORE UPDATE ON lab_evidence
 BEGIN SELECT RAISE(ABORT, 'immutable evidence'); END;
CREATE TRIGGER IF NOT EXISTS lab_evidence_no_delete BEFORE DELETE ON lab_evidence
 BEGIN SELECT RAISE(ABORT, 'immutable evidence'); END;
CREATE TRIGGER IF NOT EXISTS lab_notes_no_update BEFORE UPDATE ON lab_notes
 BEGIN SELECT RAISE(ABORT, 'append-only notebook'); END;
CREATE TRIGGER IF NOT EXISTS lab_notes_no_delete BEFORE DELETE ON lab_notes
 BEGIN SELECT RAISE(ABORT, 'append-only notebook'); END;
CREATE TRIGGER IF NOT EXISTS lab_preflight_no_update BEFORE UPDATE ON lab_preflights WHEN OLD.status != 'collecting'
 BEGIN SELECT RAISE(ABORT, 'immutable preflight'); END;
CREATE TRIGGER IF NOT EXISTS lab_preflight_no_delete BEFORE DELETE ON lab_preflights
 BEGIN SELECT RAISE(ABORT, 'immutable preflight'); END;
PRAGMA user_version=3;
COMMIT;
"""
