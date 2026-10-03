"""Read-only deployment health and accounting totals, without subscriber secrets."""
import json
from e2e_native import Lab, CHF
from lab_command import get_settings

s = get_settings()
result = {}
for name, port, service in [('core', s.ssh_port, 'open5gs-smfd'), ('upf1', s.upf_ssh_port, 'open5gs-upfd'), ('upf2', s.upf2_ssh_port, 'open5gs-upfd')]:
    host = Lab(s, port)
    try:
        result[name] = {key: host.run(['systemctl', 'show', service, '-p', key, '--value']).strip()
                        for key in ('ActiveState', 'UnitFileState', 'NRestarts', 'ExecMainStatus')}
        if name == 'core':
            code = '''import sqlite3,json
c=sqlite3.connect('file:/home/emsadmin/maestro-charging/charging.sqlite3?mode=ro',uri=True)
c.row_factory=sqlite3.Row
print(json.dumps({'usage':dict(c.execute('SELECT SUM(observed_bytes) observed, SUM(consumed_bytes) debited, SUM(overrun_bytes) overrun, SUM(reserved_bytes) reserved FROM charging_sessions').fetchone()),'cdr_count':c.execute('SELECT COUNT(*) FROM charging_cdrs').fetchone()[0]}))
'''
            result['accounting'] = json.loads(host.run([CHF + '/.venv/bin/python', '-c', code]))
    finally:
        host.client.close()
print(json.dumps(result, indent=2))
