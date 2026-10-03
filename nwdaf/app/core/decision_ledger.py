"""Append-only control evidence. Acknowledgement is never enforcement proof."""
import hashlib
import json
import time

TRANSITIONS = {
    'DETECTED': {'N7_SENT','CANCELLED'},
    'N7_SENT': {'N7_ACK','FAILED'},
    'N7_ACK': {'ENFORCEMENT_VERIFIED','FAILED'},
    'ENFORCEMENT_VERIFIED': set(), 'FAILED':set(), 'CANCELLED':set(),
}


class DecisionLedger:
    def __init__(self, database):
        self.db = database
        with self.db.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS decision_events (
                    id INTEGER PRIMARY KEY, decision_id TEXT NOT NULL,
                    event_id TEXT UNIQUE NOT NULL, stage TEXT NOT NULL,
                    received_at REAL NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS decision_lookup ON decision_events(decision_id,id);
                CREATE TRIGGER IF NOT EXISTS decision_no_update BEFORE UPDATE ON decision_events
                BEGIN SELECT RAISE(ABORT,'append-only decision evidence'); END;
                CREATE TRIGGER IF NOT EXISTS decision_no_delete BEFORE DELETE ON decision_events
                BEGIN SELECT RAISE(ABORT,'append-only decision evidence'); END;
            ''')

    def append(self, event):
        body = event.model_dump(mode='json',exclude_none=True)
        encoded = json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id,sha256 FROM decision_events WHERE event_id=?',(str(event.event_id),)).fetchone()
            if existing:
                if existing['sha256'] != digest: raise ValueError('Event identity conflict')
                return existing['id']
            previous = db.execute('SELECT stage,body FROM decision_events WHERE decision_id=? ORDER BY id DESC LIMIT 1',
                                  (str(event.decision_id),)).fetchone()
            allowed = TRANSITIONS[previous['stage']] if previous else {'DETECTED'}
            if event.stage not in allowed: raise ValueError('Invalid decision transition')
            if previous:
                prior = json.loads(previous['body'])
                for key in ('supi','snssai','policy_id','action','nominal_mbr_bps','target_mbr_bps'):
                    if prior.get(key) != body.get(key): raise ValueError('Decision target cannot change')
            cursor = db.execute('INSERT INTO decision_events(decision_id,event_id,stage,received_at,body,sha256) VALUES(?,?,?,?,?,?)',
                (str(event.decision_id),str(event.event_id),event.stage,time.time(),encoded,digest))
            return cursor.lastrowid

    def list(self, limit=50, before=None):
        with self.db.connect() as db:
            rows = db.execute('SELECT * FROM decision_events WHERE id<? ORDER BY id DESC LIMIT ?',
                              (before if before is not None else 9223372036854775807,limit)).fetchall()
        return {'items':[{'id':row['id'],'received_at':row['received_at'],'sha256':row['sha256'],
                          **json.loads(row['body'])} for row in rows],
                'next_before':rows[-1]['id'] if len(rows)==limit else None}
