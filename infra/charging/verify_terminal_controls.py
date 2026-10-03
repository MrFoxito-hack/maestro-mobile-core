"""Explicit destructive-to-connectivity lab smoke test; restores UE on exit.

Adds 50 MB of experimental quota once (idempotent replay checked), toggles UE
service via EMS and runs bounded ICMP. Preserves debit, CDR and ledger.
"""
import sys
import time
from pathlib import Path
from uuid import uuid4
import httpx
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.security import create_token
from app.db import connection

if '--execute' not in sys.argv:
    raise SystemExit('Requires --execute; restarts UE and adds 50 MB of lab quota')
with connection() as conn:
    user = conn.execute("SELECT username,role FROM users WHERE enabled=1 AND role='teacher' ORDER BY username LIMIT 1").fetchone()
client = httpx.Client(base_url='http://127.0.0.1:8000/api/v1/terminal', timeout=120,
                      headers={'Authorization': 'Bearer ' + create_token(user['username'], user['role'])})
def call(method, path, **kwargs):
    result = client.request(method, path, **kwargs)
    result.raise_for_status()
    return result.json()

initial = call('GET', '/status')
assert initial['service'] == 'active', 'Do not change an intentionally stopped UE'
request = {'request_id': str(uuid4())}
first = call('POST', '/topup', json=request)
second = call('POST', '/topup', json=request)
assert first['quota_bytes'] == second['quota_bytes'] == initial['balance']['quota_bytes'] + 50_000_000
print('PASS: live administrative topup replay credited exactly once', flush=True)
try:
    call('POST', '/airplane-mode', json={'enabled': True})
    off = call('GET', '/status')
    assert off['service'] == 'inactive'
    call('POST', '/airplane-mode', json={'enabled': False})
    for _ in range(15):
        on = call('GET', '/status')
        if on['registered'] and on['interfaces']:
            break
        time.sleep(2)
    assert on['registered'] and on['interfaces']
    print('PASS: UE disconnected then registered with real PDU interface', flush=True)
    call('POST', '/traffic/start')
    time.sleep(3)
    call('POST', '/traffic/stop')
    print('PASS: bounded traffic start/stop accepted', flush=True)
finally:
    call('POST', '/airplane-mode', json={'enabled': False})
    client.close()
