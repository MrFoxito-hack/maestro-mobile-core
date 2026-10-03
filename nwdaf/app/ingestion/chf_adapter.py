"""CHF evidence reader: reservations are not consumed bytes; CDRs not live rates."""
import json
from app.ingestion.pm_adapter import readonly


def read_account(path, supi):
    with readonly(path) as db:
        account = db.execute('SELECT quota_bytes,consumed_bytes,enabled FROM charging_accounts WHERE supi=?', (supi,)).fetchone()
        if account is None:
            return None
        reserved = db.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM charging_sessions WHERE supi=? AND status='OPEN'", (supi,)).fetchone()[0]
        return {**dict(account), 'reserved_bytes': reserved,
                'available_bytes': account['quota_bytes']-account['consumed_bytes']-reserved}


def read_cdrs(path, *, after, through, limit=1000):
    if not 1 <= limit <= 10000 or after >= through:
        raise ValueError('Invalid CDR window')
    with readonly(path) as db:
        rows = db.execute('SELECT charging_data_ref,closed_at,record_json FROM charging_cdrs WHERE closed_at>? AND closed_at<=? ORDER BY closed_at,charging_data_ref LIMIT ?',
                          (after, through, limit+1)).fetchall()
    if len(rows) > limit:
        raise ValueError('CDR window too large; shorten it to avoid silently dropping data')
    return [{'charging_data_ref': r[0], 'closed_at': r[1], 'record': json.loads(r[2])} for r in rows]
