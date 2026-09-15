import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


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

    def upsert_account(self, supi: str, quota_bytes: int, enabled: bool) -> dict:
        now = utc_now()
        with self.transaction(immediate=True) as conn:
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
            row = conn.execute(
                "SELECT * FROM charging_accounts WHERE supi=?", (supi,)
            ).fetchone()
            return dict(row)

    def get_account(self, supi: str) -> dict | None:
        with self.transaction() as conn:
            row = conn.execute(
                """SELECT a.*,
                          COALESCE((SELECT SUM(reserved_bytes) FROM charging_sessions s
                                    WHERE s.supi=a.supi AND s.status='OPEN'), 0)
                            AS reserved_bytes
                   FROM charging_accounts a WHERE a.supi=?""",
                (supi,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def stored_response(row: sqlite3.Row, request_hash: str) -> dict | None:
        if row["request_hash"] != request_hash:
            raise ValueError("SEQUENCE_CONFLICT")
        return json.loads(row["response_json"]) if row["response_json"] else None
