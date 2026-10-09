import sqlite3
from app.db import migrate_terminal_profiles


def test_migration_preserves_legacy_preferences_and_is_idempotent():
    with sqlite3.connect(':memory:') as conn:
        conn.execute("CREATE TABLE terminal_preferences(terminal_key TEXT PRIMARY KEY, "
                     "apn TEXT CHECK(apn IN ('internet','corporate')), updated_at TEXT)")
        conn.execute("INSERT INTO terminal_preferences VALUES('ue','corporate','before')")
        migrate_terminal_profiles(conn)
        migrate_terminal_profiles(conn)
        assert conn.execute('SELECT * FROM terminal_preferences').fetchall() == [('ue','corporate','before')]
        conn.execute("UPDATE terminal_preferences SET apn='5g-plus'")
        assert conn.execute('SELECT apn FROM terminal_preferences_before_triad').fetchone()[0] == 'corporate'
