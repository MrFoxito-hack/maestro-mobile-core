import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.security import hash_password


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction():
    conn = connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize() -> None:
    with transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
              role TEXT NOT NULL, testbed TEXT, assigned_imsi TEXT, enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
              role TEXT NOT NULL, testbed TEXT, action TEXT NOT NULL,
              parameters TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenario_states (
              scenario_id TEXT PRIMARY KEY, state TEXT NOT NULL,
              parameters TEXT NOT NULL, message TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal_preferences (
              terminal_key TEXT PRIMARY KEY, apn TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal_playback (
              terminal_key TEXT NOT NULL, session_id TEXT NOT NULL,
              sequence INTEGER NOT NULL, created_at REAL NOT NULL,
              received_at REAL NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(terminal_key, session_id)
            );
            CREATE TABLE IF NOT EXISTS trace_tasks (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              trace_type TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              testbed_id TEXT NOT NULL,
              owner TEXT NOT NULL,
              owner_role TEXT NOT NULL,
              capture_agent_id TEXT NOT NULL,
              capture_host TEXT,
              node_id TEXT,
              component_id TEXT,
              capture_point TEXT,
              selector_kind TEXT,
              selector_hash TEXT,
              selector_masked TEXT,
              procedures TEXT NOT NULL DEFAULT '[]',
              interfaces_3gpp TEXT NOT NULL DEFAULT '[]',
              protocols TEXT NOT NULL DEFAULT '[]',
              include_user_plane INTEGER NOT NULL DEFAULT 0,
              include_sbi INTEGER NOT NULL DEFAULT 0,
              auto_trigger INTEGER NOT NULL DEFAULT 0,
              duration_seconds INTEGER NOT NULL,
              max_megabytes INTEGER NOT NULL,
              status TEXT NOT NULL,
              result TEXT,
              source TEXT,
              device TEXT,
              filter_profile TEXT,
              pcap_file TEXT NOT NULL,
              filtered_file TEXT,
              analysis_file TEXT,
              pid INTEGER,
              remote_path TEXT,
              remote_log TEXT,
              packet_count INTEGER NOT NULL DEFAULT 0,
              size_bytes INTEGER NOT NULL DEFAULT 0,
              protocol_summary TEXT NOT NULL DEFAULT '[]',
              message TEXT,
              created_at TEXT NOT NULL,
              started_at TEXT,
              completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trace_tasks_created
              ON trace_tasks(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_trace_tasks_owner
              ON trace_tasks(owner, testbed_id);
            CREATE TABLE IF NOT EXISTS trace_events (
              task_id TEXT NOT NULL,
              event_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL,
              timestamp TEXT NOT NULL,
              relative_ms REAL NOT NULL,
              source_nf TEXT NOT NULL,
              target_nf TEXT NOT NULL,
              interface_3gpp TEXT NOT NULL,
              protocol TEXT NOT NULL,
              message TEXT NOT NULL,
              procedure TEXT NOT NULL,
              status TEXT NOT NULL,
              packet_number INTEGER,
              evidence_type TEXT NOT NULL,
              identifiers TEXT NOT NULL DEFAULT '[]',
              PRIMARY KEY(task_id, event_id),
              FOREIGN KEY(task_id) REFERENCES trace_tasks(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS metric_samples (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              collected_at TEXT NOT NULL,
              bucket_epoch INTEGER NOT NULL,
              testbed_id TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              object_id TEXT NOT NULL,
              counter_id TEXT NOT NULL,
              value REAL NOT NULL,
              unit TEXT NOT NULL,
              source TEXT NOT NULL,
              quality TEXT NOT NULL DEFAULT 'measured',
              UNIQUE(bucket_epoch, testbed_id, scenario_id, object_id, counter_id)
            );
            CREATE INDEX IF NOT EXISTS idx_metric_samples_query
              ON metric_samples(testbed_id, scenario_id, counter_id, object_id, bucket_epoch);
            CREATE TABLE IF NOT EXISTS kpi_folders (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              owner TEXT NOT NULL,
              testbed_id TEXT NOT NULL,
              scope TEXT NOT NULL DEFAULT 'personal',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kpi_folders_owner
              ON kpi_folders(owner, testbed_id, scope);
            CREATE TABLE IF NOT EXISTS kpi_queries (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              folder_id TEXT,
              owner TEXT NOT NULL,
              testbed_id TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              scope TEXT NOT NULL DEFAULT 'personal',
              object_ids TEXT NOT NULL,
              counter_ids TEXT NOT NULL,
              range_key TEXT NOT NULL,
              granularity_seconds INTEGER NOT NULL,
              aggregation TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(folder_id) REFERENCES kpi_folders(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kpi_queries_owner
              ON kpi_queries(owner, testbed_id, scope);
            CREATE TABLE IF NOT EXISTS nf_metric_definitions (
              testbed_id TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              counter_id TEXT NOT NULL,
              definition TEXT NOT NULL,
              last_seen TEXT NOT NULL,
              PRIMARY KEY(testbed_id,scenario_id,counter_id)
            );
            CREATE TABLE IF NOT EXISTS collector_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              completed_at TEXT,
              testbed_id TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              status TEXT NOT NULL,
              sample_count INTEGER NOT NULL DEFAULT 0,
              error TEXT
            );
            CREATE TABLE IF NOT EXISTS telco_events (
              event_key TEXT PRIMARY KEY,
              observed_at TEXT NOT NULL,
              observed_epoch REAL NOT NULL,
              testbed_id TEXT NOT NULL,
              scenario_id TEXT NOT NULL,
              network_function TEXT NOT NULL,
              procedure TEXT NOT NULL,
              event_type TEXT NOT NULL,
              cause TEXT,
              duration_ms REAL,
              source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_telco_events_summary
              ON telco_events(testbed_id, scenario_id, procedure, event_type, observed_epoch);
            CREATE TABLE IF NOT EXISTS operation_runs (
              id TEXT PRIMARY KEY,
              scenario_id TEXT NOT NULL,
              testbed_id TEXT NOT NULL,
              component_id TEXT NOT NULL,
              component_label TEXT NOT NULL,
              operation_id TEXT NOT NULL,
              operation_label TEXT NOT NULL,
              category TEXT NOT NULL,
              mutating INTEGER NOT NULL DEFAULT 0,
              username TEXT NOT NULL,
              role TEXT NOT NULL,
              parameters TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL,
              source TEXT NOT NULL,
              output TEXT NOT NULL DEFAULT '',
              data TEXT,
              error TEXT,
              started_at TEXT NOT NULL,
              completed_at TEXT NOT NULL,
              duration_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_operation_runs_history
              ON operation_runs(testbed_id, scenario_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_operation_runs_owner
              ON operation_runs(username, started_at DESC);
            """
        )
        try:
            conn.execute("ALTER TABLE users ADD COLUMN assigned_imsi TEXT")
        except sqlite3.OperationalError:
            pass

        users = [
            ("docente", "teacher-change-me", "teacher", None, None),
            ("grupo1", "grupo1-pass-2026", "student", "local", "imsi-999700000000001"),
            ("grupo2", "grupo2-pass-2026", "student", "local", "imsi-999700000000002"),
            ("grupo3", "grupo3-pass-2026", "student", "local", "imsi-999700000000003"),
            ("grupo4", "grupo4-pass-2026", "student", "local", "imsi-999700000000004"),
            ("grupo5", "grupo5-pass-2026", "student", "local", "imsi-999700000000005"),
        ]
        for username, password, role, testbed, assigned_imsi in users:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,testbed,assigned_imsi) VALUES(?,?,?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET role=excluded.role, testbed=excluded.testbed, assigned_imsi=excluded.assigned_imsi",
                (username, hash_password(password), role, testbed, assigned_imsi),
            )
        conn.execute("DELETE FROM users WHERE username IN ('admin', 'alumno')")
        # Remove measurements produced by an obsolete correlation rule. A
        # control-plane procedure taking over 60 seconds is treated as
        # uncorrelated, never as a valid latency sample.
        conn.execute(
            """DELETE FROM metric_samples
            WHERE counter_id LIKE '%.latency_ms' AND (value < 0 OR value > 60000)"""
        )


def add_audit(username: str, role: str, testbed: str | None, action: str, parameters: dict, result: str) -> None:
    safe = {key: value for key, value in parameters.items() if key.lower() not in {"key", "opc", "op", "password", "secret"}}
    with transaction() as conn:
        conn.execute(
            "INSERT INTO audit_events(username,role,testbed,action,parameters,result,created_at) VALUES(?,?,?,?,?,?,?)",
            (username, role, testbed, action, json.dumps(safe), result, datetime.now(timezone.utc).isoformat()),
        )
