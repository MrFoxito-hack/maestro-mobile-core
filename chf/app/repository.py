import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.errors import ChargingError
from app.migrations import migrate_v2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChargingRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def transaction(self, *, immediate: bool = False):
        conn = self.connect()
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.transaction() as conn:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise RuntimeError('Charging database is newer than this binary; refusing downgrade')
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='charging_accounts'").fetchone()
            if exists and version < 2:
                backup_path = self.database_path.with_suffix('.v1-backup.sqlite3')
                if not backup_path.exists():
                    backup = sqlite3.connect(backup_path)
                    try:
                        conn.backup(backup)
                    finally:
                        backup.close()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS charging_accounts (
                  supi TEXT PRIMARY KEY,
                  quota_bytes INTEGER NOT NULL CHECK(quota_bytes > 0),
                  consumed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(consumed_bytes >= 0),
                  enabled INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS charging_sessions (
                  charging_data_ref TEXT PRIMARY KEY,
                  create_key TEXT NOT NULL UNIQUE,
                  supi TEXT NOT NULL,
                  rating_group INTEGER NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('OPEN','RELEASED')),
                  reserved_bytes INTEGER NOT NULL DEFAULT 0 CHECK(reserved_bytes >= 0),
                  consumed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(consumed_bytes >= 0),
                  last_invocation_sequence INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  released_at TEXT,
                  FOREIGN KEY(supi) REFERENCES charging_accounts(supi)
                );
                CREATE INDEX IF NOT EXISTS idx_charging_sessions_account
                  ON charging_sessions(supi, status);
                CREATE TABLE IF NOT EXISTS charging_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  charging_data_ref TEXT NOT NULL,
                  operation TEXT NOT NULL,
                  invocation_sequence INTEGER NOT NULL,
                  request_hash TEXT NOT NULL,
                  used_bytes INTEGER NOT NULL DEFAULT 0,
                  granted_bytes INTEGER NOT NULL DEFAULT 0,
                  result_code TEXT NOT NULL,
                  response_json TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(charging_data_ref, operation, invocation_sequence),
                  FOREIGN KEY(charging_data_ref) REFERENCES charging_sessions(charging_data_ref)
                );
                """
            )
            migrate_v2(conn)

    def upsert_account(self, supi: str, quota_bytes: int | None, enabled: bool, actor: str = "admin") -> dict:
        from app.models import AccountUpsert
        AccountUpsert(supi=supi, quotaBytes=quota_bytes, enabled=enabled)
        now = utc_now()
        with self.transaction(immediate=True) as conn:
            previous = self.account_snapshot(conn, supi)
            if quota_bytes is None:
                if not previous:
                    raise ChargingError(404, "USER_UNKNOWN", "state update requires an existing account")
                quota_bytes = previous['quota_bytes']
            if previous and quota_bytes < previous['consumed_bytes'] + previous['reserved_bytes']:
                raise ChargingError(409, "QUOTA_COMMITTED", "quota is below already debited and reserved units")
            conn.execute(
                """INSERT INTO charging_accounts(
                       supi,quota_bytes,consumed_bytes,enabled,created_at,updated_at
                   ) VALUES(?,?,0,?,?,?)
                   ON CONFLICT(supi) DO UPDATE SET
                     quota_bytes=excluded.quota_bytes,
                     enabled=excluded.enabled,
                     updated_at=excluded.updated_at""",
                (supi, quota_bytes, int(enabled), now, now),
            )
            current = self.account_snapshot(conn, supi)
            self.append_ledger(conn, supi, None, "ACCOUNT_UPSERT", actor,
                               {"before": previous, "after": current})
            return current

    def get_account(self, supi: str) -> dict | None:
        with self.transaction() as conn:
            return self.account_snapshot(conn, supi)

    def topup(self, supi, amount, request_id, actor):
        from app.models import TopupRequest, MAX_BYTES
        TopupRequest(requestId=request_id, amountBytes=amount)
        with self.transaction(immediate=True) as conn:
            previous = conn.execute("SELECT snapshot_json FROM account_ledger WHERE supi=? AND operation='TOPUP' AND json_extract(snapshot_json,'$.request_id')=?", (supi, request_id)).fetchone()
            if previous:
                saved = json.loads(previous[0])
                if saved['amount_bytes'] != amount:
                    raise ChargingError(409, 'IDEMPOTENCY_CONFLICT', 'request ID already used with a different amount')
                return saved['after']
            before = self.account_snapshot(conn, supi)
            if not before:
                raise ChargingError(404, 'USER_UNKNOWN', 'account not found')
            quota = before['quota_bytes'] + amount
            if quota > MAX_BYTES:
                raise ChargingError(409, 'QUOTA_LIMIT', 'account quota limit exceeded')
            conn.execute('UPDATE charging_accounts SET quota_bytes=?,updated_at=? WHERE supi=?', (quota, utc_now(), supi))
            after = self.account_snapshot(conn, supi)
            self.append_ledger(conn, supi, None, 'TOPUP', actor,
                               {'request_id': request_id, 'amount_bytes': amount, 'before': before, 'after': after})
            return after

    @staticmethod
    def account_snapshot(conn, supi):
        row = conn.execute("""SELECT a.*, COALESCE((SELECT SUM(reserved_bytes)
            FROM charging_sessions s WHERE s.supi=a.supi AND s.status='OPEN'),0)
            AS reserved_bytes FROM charging_accounts a WHERE supi=?""", (supi,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['available_bytes'] = result['quota_bytes'] - result['consumed_bytes'] - result['reserved_bytes']
        return result

    @staticmethod
    def append_ledger(conn, supi, ref, operation, actor, snapshot):
        conn.execute("""INSERT INTO account_ledger(supi,charging_data_ref,operation,actor,
            snapshot_json,created_at) VALUES(?,?,?,?,?,?)""",
            (supi, ref, operation, actor, json.dumps(snapshot, sort_keys=True), utc_now()))

    def readiness(self):
        with self.transaction() as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != 'ok':
                raise ChargingError(503, "SYSTEM_FAILURE", "database integrity check failed")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ChargingError(503, "SYSTEM_FAILURE", "database relationship check failed")
            invalid = conn.execute("""SELECT supi FROM charging_accounts a WHERE consumed_bytes < 0
                OR consumed_bytes + COALESCE((SELECT SUM(reserved_bytes) FROM charging_sessions s
                    WHERE s.supi=a.supi AND status='OPEN'),0) > quota_bytes LIMIT 1""").fetchone()
            if invalid:
                raise ChargingError(503, "SYSTEM_FAILURE", "account invariants require reconciliation")
        return {"status": "ready", "schemaVersion": 2}

    def list_records(self, kind, *, supi=None, limit=100, offset=0):
        tables = {"accounts": ("charging_accounts", "supi"),
                  "sessions": ("charging_sessions", "created_at"),
                  "cdrs": ("charging_cdrs", "closed_at"),
                  "ledger": ("account_ledger", "id")}
        table, order = tables[kind]
        where, params = (" WHERE supi=?", [supi]) if supi else ("", [])
        with self.transaction() as conn:
            rows = conn.execute(f"SELECT * FROM {table}{where} ORDER BY {order} DESC LIMIT ? OFFSET ?",
                                [*params, limit, offset]).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM {table}{where}", params).fetchone()[0]
            items = [self.account_snapshot(conn, row['supi']) if kind == 'accounts' else dict(row) for row in rows]
            for item in items:
                for key in ("context_json", "record_json", "snapshot_json"):
                    if item.get(key):
                        item[key.removesuffix('_json')] = json.loads(item.pop(key))
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def stored_response(row: sqlite3.Row, request_hash: str) -> dict | None:
        if row["request_hash"] != request_hash:
            raise ValueError("SEQUENCE_CONFLICT")
        return json.loads(row["response_json"]) if row["response_json"] else None
