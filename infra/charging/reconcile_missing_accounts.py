"""Prepare or explicitly apply an additive recovery of missing account parents.

Existing accounts, sessions, usage, grants and CDRs are never updated/deleted.
Missing accounts use their last durable ledger snapshot and are DISABLED.
Application requires the exact reviewed plan hash and an exclusive backup path.
This is a financial-data repair, NOT policy-authority checkpoint restoration.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def plan(conn):
    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise ValueError('database_integrity_failure')
    failures = conn.execute('PRAGMA foreign_key_check').fetchall()
    if any(row[0] != 'charging_sessions' or row[2] != 'charging_accounts' for row in failures):
        raise ValueError('unsupported_foreign_key_failure')
    parents = conn.execute('''SELECT DISTINCT supi FROM charging_sessions WHERE supi NOT IN
                              (SELECT supi FROM charging_accounts) ORDER BY supi''').fetchall()
    recoveries = []
    for (supi,) in parents:
        row = conn.execute('''SELECT id,snapshot_json FROM account_ledger
                              WHERE supi=? ORDER BY id DESC LIMIT 1''', (supi,)).fetchone()
        if not row:
            raise ValueError('missing_durable_account_snapshot')
        after = json.loads(row[1]).get('after', {})
        keys = ('supi', 'quota_bytes', 'consumed_bytes', 'enabled', 'created_at', 'updated_at')
        if any(key not in after for key in keys) or after['supi'] != supi:
            raise ValueError('invalid_durable_account_snapshot')
        reserved = conn.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM charging_sessions WHERE supi=? AND status='OPEN'", (supi,)).fetchone()[0]
        if (any(type(after[k]) is not int or after[k] < 0 for k in ('quota_bytes', 'consumed_bytes'))
                or after['consumed_bytes'] + reserved > after['quota_bytes']
                or after.get('reserved_bytes') != reserved):
            raise ValueError('snapshot_requires_manual_reconciliation')
        parent = {k: after[k] for k in keys}
        parent['enabled'] = 0
        recoveries.append({'source_ledger_id': row[0], 'account': parent, 'reserved_bytes': reserved})
    protected = {}
    for table in ('charging_accounts', 'charging_sessions', 'charging_events', 'charging_cdrs'):
        cursor = conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')
        protected[table] = digest({'columns': [c[0] for c in cursor.description], 'rows': cursor.fetchall()})
    body = {'operation': 'recover_missing_accounts_disabled', 'foreign_key_failures': len(failures),
            'recoveries': recoveries, 'protected_sha256': protected}
    return {**body, 'plan_sha256': digest(body)}


def apply(database, *, expected, backup):
    database, backup = Path(database), Path(backup)
    conn = sqlite3.connect(str(database), isolation_level=None, timeout=10)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA synchronous=FULL')
        proposed = plan(conn)
        if proposed['plan_sha256'] != expected:
            raise ValueError('reviewed_plan_changed')
        # SQLite backup includes WAL contents. Never overwrite an older backup.
        fd = os.open(str(backup), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(str(backup))
        try:
            conn.backup(target)
        finally:
            target.close()
        conn.execute('BEGIN IMMEDIATE')
        if plan(conn)['plan_sha256'] != expected:
            raise ValueError('reviewed_plan_changed')
        now = datetime.now(timezone.utc).isoformat()
        for recovery in proposed['recoveries']:
            account = recovery['account']
            conn.execute('''INSERT INTO charging_accounts
                (supi,quota_bytes,consumed_bytes,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?)''',
                         tuple(account[k] for k in ('supi','quota_bytes','consumed_bytes','enabled','created_at','updated_at')))
            conn.execute('''INSERT INTO account_ledger
                (supi,charging_data_ref,operation,actor,snapshot_json,created_at) VALUES(?,NULL,?,?,?,?)''',
                (account['supi'], 'ACCOUNT_RECOVERED_DISABLED', 'explicit-operator-reconciliation',
                 json.dumps({'before': None, 'after': account, 'source_ledger_id': recovery['source_ledger_id'],
                             'reviewed_plan_sha256': expected}, sort_keys=True), now))
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('foreign_key_repair_incomplete')
        # No changes to original accounts or to any session/event/CDR.
        recovered = [r['account']['supi'] for r in proposed['recoveries']]
        cursor = conn.execute('SELECT * FROM charging_accounts' +
            (' WHERE supi NOT IN (' + ','.join('?' for _ in recovered) + ')' if recovered else '') +
            ' ORDER BY rowid', recovered)
        current = digest({'columns': [c[0] for c in cursor.description], 'rows': cursor.fetchall()})
        if current != proposed['protected_sha256']['charging_accounts']:
            raise ValueError('existing_accounts_changed')
        for table in ('charging_sessions', 'charging_events', 'charging_cdrs'):
            cursor = conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')
            current = digest({'columns': [c[0] for c in cursor.description], 'rows': cursor.fetchall()})
            if current != proposed['protected_sha256'][table]:
                raise ValueError('protected_accounting_changed')
        conn.commit()
        return {'recovered_disabled': len(proposed['recoveries']), 'backup': str(backup), 'plan_sha256': expected}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--apply-plan-sha256')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply_plan_sha256:
        if not args.backup:
            parser.error('--backup is mandatory for application')
        result = apply(args.database, expected=args.apply_plan_sha256, backup=args.backup)
    else:
        with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
            result = plan(connection)
    print(json.dumps(result, indent=2))
