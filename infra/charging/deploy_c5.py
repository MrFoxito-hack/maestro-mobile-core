"""Deploy only CHF Python modules, backing up source and SQLite first."""
import hashlib
import json
from datetime import datetime, timezone

from e2e_native import Lab, ROOT, CHF
from lab_command import get_settings


def main():
    evidence = ROOT / '.work/c5-charging/evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    core = Lab(get_settings(), 2222)
    result = {'startedAt': datetime.now(timezone.utc).isoformat(), 'files': {}}
    backup = core.run(['mktemp', '-d', '/home/emsadmin/c5-chf-backup-XXXXXX']).strip()
    result['backup'] = backup
    stopped = False
    try:
        # Validate remote syntax in staging before the brief CHF-only stop.
        for source in sorted((ROOT / 'chf/app').glob('*.py')):
            data = source.read_bytes().replace(b'\r\n', b'\n')
            target = CHF + '/app/' + source.name
            previous = core.read(target)
            core.write(backup + '/' + source.name, previous)
            core.write(backup + '/new-' + source.name, data)
            result['files'][source.name] = {'before': hashlib.sha256(previous).hexdigest(), 'after': hashlib.sha256(data).hexdigest()}
        core.run([CHF + '/.venv/bin/python', '-m', 'compileall', '-q', backup])
        # Capture a consistent database backup; do not replace or reset history.
        core.write(backup + '/backup.py', '''import sqlite3
from pathlib import Path
env=dict(x.split('=',1) for x in Path('/home/emsadmin/maestro-charging/management/management.env').read_text().splitlines() if '=' in x and not x.startswith('#'))
src=sqlite3.connect(env['CHF_DATABASE_PATH'].strip("'\\\""))
dst=sqlite3.connect(str(Path(__file__).parent/'charging-before.sqlite3'))
src.backup(dst)
dst.close()
src.close()
''')
        core.run(['python3', backup + '/backup.py'])
        core.run(['systemctl', 'stop', 'open5gs-chfd.service', 'maestro-chf-management.service'], sudo=True)
        stopped = True
        with core.client.open_sftp() as sftp:
            for source in sorted((ROOT / 'chf/app').glob('*.py')):
                data = source.read_bytes().replace(b'\r\n', b'\n')
                with sftp.open(CHF + '/app/' + source.name, 'wb') as remote:
                    remote.write(data)
        core.run(['systemctl', 'start', 'open5gs-chfd.service', 'maestro-chf-management.service'], sudo=True)
        stopped = False
        result['services'] = core.run(['systemctl', 'is-active', 'open5gs-chfd.service', 'maestro-chf-management.service'])
        result['status'] = 'DEPLOYED'
    except Exception as exc:
        result['status'] = 'FAILED'
        result['errorType'] = type(exc).__name__
        raise
    finally:
        if stopped:
            # v3 readers are retained if migration has run; never run a v2 binary on v3.
            core.run(['systemctl', 'start', 'open5gs-chfd.service', 'maestro-chf-management.service'], sudo=True, check=False)
        (evidence / 'deployment.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        core.client.close()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
