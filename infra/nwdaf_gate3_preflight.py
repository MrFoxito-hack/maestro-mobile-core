"""Read-only live Gate 3 prerequisites and reproducible source evidence.

Never changes services, policies, subscriber data, or charging accounts.
Credentials are used only by the existing SSH/API adapters and not exported.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from app.services.nwdaf import get_health, get_analytics, nwdaf_request


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = ROOT / '.work' / ('nwdaf-gate3-preflight-' + stamp)
    target.mkdir(parents=True)
    settings = get_settings()
    result = {'utc': stamp, 'mode': 'read-only', 'errors': {}}
    for name, call in {
        'health': get_health,
        'internet_analytics': lambda: get_analytics('LOAD_LEVEL_INFORMATION', json.dumps({'snssais': [{'sst': 1, 'sd': '000001'}]})),
        'predictions': lambda: nwdaf_request('/management/v1/predictions'),
        'ledger': lambda: nwdaf_request('/management/v1/closed-loop/history'),
    }.items():
        try:
            result[name] = call()
        except Exception as exc:
            result['errors'][name] = type(exc).__name__
    core = Lab(settings, settings.ssh_port)
    try:
        result['services'] = core.run(['systemctl', 'show',
            'maestro-nwdaf', 'open5gs-pcfd', 'open5gs-smfd', 'open5gs-amfd',
            '--property=Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp'], check=False)
        pid = core.run(['systemctl', 'show', 'open5gs-pcfd', '--property=MainPID', '--value']).strip()
        if pid.isdigit() and int(pid):
            result['pcf_executable'] = core.run(['readlink', '-f', '/proc/' + pid + '/exe'], sudo=True).strip()
            result['pcf_running_hash'] = core.run(['sha256sum', '/proc/' + pid + '/exe'], sudo=True).split()[0]
            script = """import json, pathlib
p=pathlib.Path('/proc/""" + pid + """/environ').read_bytes().split(b'\\0')
env=dict(x.decode().split('=',1) for x in p if b'=' in x)
print(json.dumps({k: ('<configured>' if 'TOKEN' in k else v) for k,v in env.items() if k.startswith('MAESTRO_NWDAF_')}))
"""
            result['pcf_nwdaf_environment'] = json.loads(core.run(['python3', '-c', script], sudo=True))
        base = '/home/emsadmin/maestro-charging/open5gs'
        result['staged_pcf_hash'] = core.run(['sha256sum', base + '/build/src/pcf/open5gs-pcfd']).split()[0]
        for name in ('nwdaf-handler.c', 'npcf-handler.c', 'sbi-path.c', 'context.h', 'context.c'):
            try:
                data = core.read(base + '/src/pcf/' + name)
                (target / name).write_bytes(data)
            except FileNotFoundError:
                result['errors']['source:' + name] = 'not present in this source tree'
        result['observer_source_hash'] = hashlib.sha256((target / 'nwdaf-handler.c').read_bytes()).hexdigest()
        result['pcf_recent_log'] = core.run(['journalctl', '-u', 'open5gs-pcfd', '--since', '-10min', '--no-pager', '-n', '80'], sudo=True)
        result['capture_tools'] = core.run(['sh', '-c', 'command -v tcpdump; command -v tshark'], check=False)
        result['core_listeners'] = core.run(['ss', '-lntup'], sudo=True)
        # SELECT-only connection prevents accidental charging database updates.
        script = """import sqlite3,json
c=sqlite3.connect('file:/home/emsadmin/maestro-charging/charging.sqlite3?mode=ro',uri=True)
c.row_factory=sqlite3.Row
print(json.dumps({'accounts':[dict(r) for r in c.execute("SELECT a.supi,a.quota_bytes,a.consumed_bytes,COALESCE((SELECT SUM(s.reserved_bytes) FROM charging_sessions s WHERE s.supi=a.supi AND s.status='OPEN'),0) AS reserved_bytes FROM charging_accounts a")]}))
"""
        result['charging'] = json.loads(core.run(['python3', '-c', script]))
    finally:
        core.client.close()
        (target / 'preflight.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'evidence': str(target), **{k: v for k, v in result.items() if k not in ('predictions', 'ledger', 'core_listeners', 'pcf_recent_log')}}, indent=2))


if __name__ == '__main__':
    main()
