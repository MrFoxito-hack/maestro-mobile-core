"""Additive schema v2: v1 data is retained; legacy owners are NOT invented."""

SESSION_COLUMNS = {
    "owner_nf": "TEXT", "charging_id": "INTEGER", "notify_uri": "TEXT",
    "context_json": "TEXT", "upf_id": "TEXT NOT NULL DEFAULT ''",
    "observed_bytes": "INTEGER NOT NULL DEFAULT 0",
    "uplink_bytes": "INTEGER NOT NULL DEFAULT 0",
    "downlink_bytes": "INTEGER NOT NULL DEFAULT 0",
    "unclassified_bytes": "INTEGER NOT NULL DEFAULT 0",
    "overrun_bytes": "INTEGER NOT NULL DEFAULT 0",
    "granted_bytes": "INTEGER NOT NULL DEFAULT 0",
    "last_usage_sequence": "INTEGER NOT NULL DEFAULT -1",
    "valid_until": "TEXT", "closure_reason": "TEXT",
}


def migrate_v2(conn):
    conn.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(charging_sessions)")}
    for name, definition in SESSION_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE charging_sessions ADD COLUMN {name} {definition}")
    if 'observed_bytes' not in columns:
        conn.execute("""UPDATE charging_sessions SET observed_bytes=consumed_bytes,
            unclassified_bytes=consumed_bytes,granted_bytes=COALESCE((SELECT SUM(granted_bytes)
            FROM charging_events WHERE charging_data_ref=charging_sessions.charging_data_ref),0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_events (
        charging_data_ref TEXT NOT NULL REFERENCES charging_sessions(charging_data_ref),
        local_sequence INTEGER NOT NULL, payload_hash TEXT NOT NULL,
        total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
        uplink_bytes INTEGER NOT NULL CHECK(uplink_bytes >= 0),
        downlink_bytes INTEGER NOT NULL CHECK(downlink_bytes >= 0),
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(charging_data_ref, local_sequence))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, supi TEXT NOT NULL,
        charging_data_ref TEXT, operation TEXT NOT NULL, actor TEXT NOT NULL,
        snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS charging_cdrs (
        charging_data_ref TEXT PRIMARY KEY REFERENCES charging_sessions(charging_data_ref),
        supi TEXT NOT NULL, closed_at TEXT NOT NULL, record_json TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS charging_replays (
        id INTEGER PRIMARY KEY AUTOINCREMENT, charging_data_ref TEXT NOT NULL,
        operation TEXT NOT NULL, invocation_sequence INTEGER NOT NULL, created_at TEXT NOT NULL)""")
    for table in ("usage_events", "account_ledger", "charging_cdrs", "charging_events", "charging_replays"):
        for verb in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{verb}
                BEFORE {verb} ON {table} BEGIN
                SELECT RAISE(ABORT, 'immutable charging evidence'); END""")
    # Enforce committed + reserved <= quota even for accidental direct SQL.
    for verb in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS account_budget_{verb}
            BEFORE {verb} ON charging_accounts WHEN
            NEW.consumed_bytes + COALESCE((SELECT SUM(reserved_bytes)
                FROM charging_sessions WHERE supi=NEW.supi AND status='OPEN'),0) > NEW.quota_bytes
            BEGIN SELECT RAISE(ABORT, 'account budget exceeded'); END""")
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS reservation_budget_{verb}
            BEFORE {verb} ON charging_sessions WHEN NEW.status='OPEN' AND
            NEW.reserved_bytes + COALESCE((SELECT SUM(reserved_bytes) FROM charging_sessions
                WHERE supi=NEW.supi AND status='OPEN' AND charging_data_ref<>NEW.charging_data_ref),0)
            + (SELECT consumed_bytes FROM charging_accounts WHERE supi=NEW.supi)
            > (SELECT quota_bytes FROM charging_accounts WHERE supi=NEW.supi)
            BEGIN SELECT RAISE(ABORT, 'reservation budget exceeded'); END""")
    conn.execute("PRAGMA user_version=2")


def migrate_v3(conn):
    """Policy snapshots preserve the meaning of existing sessions and replays."""
    columns = {row[1] for row in conn.execute('PRAGMA table_info(charging_sessions)')}
    additions = {
        'policy_json': "TEXT NOT NULL DEFAULT '{\"mode\":\"BYTE_QUOTA\",\"source\":\"legacy\"}'",
        'authorized_bytes': 'INTEGER NOT NULL DEFAULT 0 CHECK(authorized_bytes >= 0)',
        'reserved_messages': 'INTEGER NOT NULL DEFAULT 0 CHECK(reserved_messages >= 0)',
        'consumed_messages': 'INTEGER NOT NULL DEFAULT 0 CHECK(consumed_messages >= 0)',
        'observed_messages': 'INTEGER NOT NULL DEFAULT 0 CHECK(observed_messages >= 0)',
        'overrun_messages': 'INTEGER NOT NULL DEFAULT 0 CHECK(overrun_messages >= 0)',
        'granted_messages': 'INTEGER NOT NULL DEFAULT 0 CHECK(granted_messages >= 0)',
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE charging_sessions ADD COLUMN {name} {definition}')
    if 'authorized_bytes' not in columns:
        conn.execute('UPDATE charging_sessions SET authorized_bytes=reserved_bytes')
    conn.execute('''CREATE TABLE IF NOT EXISTS service_policies (
        dnn TEXT NOT NULL, sst INTEGER NOT NULL, sd TEXT NOT NULL,
        rating_group INTEGER NOT NULL, policy_json TEXT NOT NULL,
        PRIMARY KEY(dnn,sst,sd,rating_group))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS message_accounts (
        supi TEXT PRIMARY KEY REFERENCES charging_accounts(supi),
        quota_messages INTEGER NOT NULL CHECK(quota_messages >= 0),
        consumed_messages INTEGER NOT NULL DEFAULT 0 CHECK(consumed_messages >= 0))''')
    # Separate wallets: bytes must never be relabelled as packets or messages.
    for verb in ('INSERT', 'UPDATE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS message_budget_{verb}
            BEFORE {verb} ON message_accounts WHEN NEW.consumed_messages +
            COALESCE((SELECT SUM(reserved_messages) FROM charging_sessions
                WHERE supi=NEW.supi AND status='OPEN'),0) > NEW.quota_messages
            BEGIN SELECT RAISE(ABORT, 'message budget exceeded'); END''')
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS message_reservation_{verb}
            BEFORE {verb} ON charging_sessions WHEN NEW.status='OPEN' AND
            (NEW.reserved_messages + COALESCE((SELECT SUM(reserved_messages)
                FROM charging_sessions WHERE supi=NEW.supi AND status='OPEN'
                AND charging_data_ref<>NEW.charging_data_ref),0)
                + COALESCE((SELECT consumed_messages FROM message_accounts WHERE supi=NEW.supi),0)
                > COALESCE((SELECT quota_messages FROM message_accounts WHERE supi=NEW.supi),0))
            BEGIN SELECT RAISE(ABORT, 'message reservation budget exceeded'); END''')
    conn.execute('PRAGMA user_version=3')
