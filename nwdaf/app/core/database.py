from contextlib import contextmanager
from pathlib import Path
import sqlite3
import json
import hashlib
import time


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS analyses (
                  id INTEGER PRIMARY KEY, event TEXT NOT NULL, target TEXT NOT NULL,
                  created REAL NOT NULL, input_hash TEXT NOT NULL,
                  evidence TEXT NOT NULL, sbi_payload TEXT,
                  UNIQUE(event,target,input_hash));
                CREATE INDEX IF NOT EXISTS analyses_lookup ON analyses(event,target,created);
                CREATE TRIGGER IF NOT EXISTS analyses_no_update BEFORE UPDATE ON analyses
                  BEGIN SELECT RAISE(ABORT, 'immutable analysis'); END;
                CREATE TRIGGER IF NOT EXISTS analyses_no_delete BEFORE DELETE ON analyses
                  BEGIN SELECT RAISE(ABORT, 'immutable analysis'); END;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, event, target, inputs, evidence, payload=None):
        encoded = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO analyses(event,target,created,input_hash,evidence,sbi_payload) VALUES(?,?,?,?,?,?)',
                       (event, target, time.time(), digest, json.dumps(evidence, allow_nan=False),
                        json.dumps(payload, allow_nan=False) if payload else None))
        return digest

    def latest(self, event, target, max_age=300):
        with self.connect() as db:
            row = db.execute('SELECT sbi_payload FROM analyses WHERE event=? AND target=? AND created>=? ORDER BY created DESC,id DESC LIMIT 1',
                             (event, target, time.time()-max_age)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
