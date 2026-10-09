"""Persisted group ownership for terminal reads and actions."""
import re
from fastapi import HTTPException
from datetime import datetime, timezone
from contextlib import closing

from app.db import connection
from app.services.terminal_inventory import inventory, public_device


def normalize_imsi(imsi):
    if not isinstance(imsi, str) or not re.fullmatch(r'(?:imsi-)?\d{14,15}', imsi):
        raise HTTPException(422, 'Se requiere el IMSI explícito del terminal')
    return imsi if imsi.startswith('imsi-') else 'imsi-' + imsi


def authorize_device(imsi, user):
    supi = normalize_imsi(imsi)
    device = next((d for d in visible_devices(user) if d['supi'] == supi), None)
    if device is None:
        raise HTTPException(403, 'No autorizado para consultar u operar este terminal UE')
    return device


def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS terminal_groups '
                 '(id TEXT PRIMARY KEY, testbed TEXT NOT NULL, label TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS terminal_group_members '
                 '(username TEXT NOT NULL, group_id TEXT NOT NULL, granted_at TEXT NOT NULL, '
                 'granted_by TEXT NOT NULL, PRIMARY KEY(username,group_id), '
                 'FOREIGN KEY(username) REFERENCES users(username), FOREIGN KEY(group_id) REFERENCES terminal_groups(id))')
    conn.execute('CREATE TABLE IF NOT EXISTS terminal_device_owners '
                 '(device_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, '
                 'FOREIGN KEY(group_id) REFERENCES terminal_groups(id))')
    conn.execute('CREATE TABLE IF NOT EXISTS terminal_access_migrations '
                 '(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
    if conn.execute('SELECT 1 FROM terminal_access_migrations WHERE version=1').fetchone():return
    doc=inventory();stamp=datetime.now(timezone.utc).isoformat()
    for group in doc.groups:
        conn.execute('INSERT INTO terminal_groups VALUES(?,?,?) ON CONFLICT(id) DO NOTHING',
                     (group.id,doc.testbed,group.label))
    for device in doc.devices:
        conn.execute('INSERT INTO terminal_device_owners VALUES(?,?) ON CONFLICT(device_id) DO NOTHING',
                     (device.id,device.group_id))
    # Explicit one-time provisioning of the authorized existing lab account.
    # Authorization below never infers group membership from a username/SUPI.
    user=conn.execute('SELECT role,testbed,assigned_imsi FROM users WHERE username=?',('grupo1',)).fetchone()
    if user and tuple(user)==('student','local','imsi-999700000000001'):
        conn.execute('INSERT INTO terminal_group_members VALUES(?,?,?,?) ON CONFLICT DO NOTHING',
                     ('grupo1','grupo-1',stamp,'migration:terminal-triads-v1'))
    conn.execute('INSERT INTO terminal_access_migrations VALUES(1,?)',(stamp,))


def visible_devices(user):
    doc=inventory()
    with closing(connection()) as conn:
        row=conn.execute('SELECT role,testbed,enabled FROM users WHERE username=?',(user.username,)).fetchone()
        if not row or not row['enabled']:return []
        if row['role'] in ('admin','teacher'):
            allowed={d.id for d in doc.devices} if row['testbed'] in (None,doc.testbed) else set()
        elif row['role']=='student' and row['testbed']==doc.testbed:
            allowed={r[0] for r in conn.execute(
                'SELECT o.device_id FROM terminal_device_owners o '
                'JOIN terminal_groups g ON g.id=o.group_id '
                'JOIN terminal_group_members m ON m.group_id=g.id '
                'WHERE m.username=? AND g.testbed=?',(user.username,doc.testbed))}
        else:allowed=set()
    return [public_device(d) for d in doc.devices if d.id in allowed]
