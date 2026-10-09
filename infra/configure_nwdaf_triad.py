"""C2: migrate only NWDAF slice mappings; preserve databases and service policy."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'infra/charging')]
from app.core.config import get_settings
from app.services.nwdaf import slice_catalog, nwdaf_request
from e2e_native import Lab


def candidate(raw):
    lines = raw.decode().splitlines()
    definitions = [line.partition('=')[2].strip().strip("'\"") for line in lines if line.startswith('NWDAF_SLICE_MAPS=')]
    if len(definitions) != 1:
        raise ValueError('Expected one explicit existing slice map')
    previous = json.loads(definitions[0])
    # Carry forward the already declared experimental normalization budget.
    fields = ('capacity_units', 'interval_seconds', 'period', 'source_max_gap_seconds')
    policies = {tuple(entry.get(key) for key in fields) for entry in previous}
    if len(policies) != 1:
        raise ValueError('Different existing budgets need an explicit per-service mapping')
    policy = dict(zip(fields, policies.pop()))
    maps = [{**policy, 'snssai': s['snssai'], 'testbed_id': 'local',
             'object_id': s['object_id'], 'counter_id': 'nwdaf.upf.dl.bps',
             'label': s['label'] + ' / experimental DL budget (not physical capacity)'} for s in slice_catalog()]
    new = "NWDAF_SLICE_MAPS='" + json.dumps(maps, separators=(',', ':'), ensure_ascii=True) + "'"
    return ('\n'.join(new if line.startswith('NWDAF_SLICE_MAPS=') else line for line in lines)+'\n').encode(), maps


APPLY = r'''
import hashlib,os,pathlib,shutil,sys
path=pathlib.Path('/home/emsadmin/maestro-charging/nwdaf/service.env')
source=pathlib.Path(sys.argv[1]); backup=pathlib.Path(sys.argv[2]); expected=sys.argv[3]
assert source.parent==path.parent and source.name.startswith('service.env.c2-')
assert backup.parent==path.parent and backup.name.startswith('service.env.before-c2-')
assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
assert not backup.exists()
stat=path.stat();shutil.copy2(path,backup);os.chown(backup,stat.st_uid,stat.st_gid)
os.chmod(source,stat.st_mode);os.chown(source,stat.st_uid,stat.st_gid)
os.replace(source,path)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    settings = get_settings(); host = Lab(settings, settings.ssh_port)
    path = '/home/emsadmin/maestro-charging/nwdaf/service.env'
    try:
        before = host.read(path)
        proposed, maps = candidate(before)
        (args.evidence/'nwdaf-service.env.before').write_bytes(before)
        (args.evidence/'nwdaf-service.env.candidate').write_bytes(proposed)
        report = {'maps': maps, 'before_sha256': hashlib.sha256(before).hexdigest(),
                  'candidate_sha256': hashlib.sha256(proposed).hexdigest(), 'applied': False}
        if args.apply:
            tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            stage, backup = path+'.c2-'+tag, path+'.before-c2-'+tag
            host.write(stage, proposed)
            host.run(['python3', '-c', APPLY, stage, backup, report['before_sha256']], sudo=True)
            host.run(['systemctl', 'restart', 'maestro-nwdaf'], sudo=True)
            report.update(applied=True, remote_backup=backup,
                          service=host.run(['systemctl', 'is-active', 'maestro-nwdaf']).strip())
        (args.evidence/'nwdaf-migration.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True))
    finally:
        host.client.close()


if __name__ == '__main__':
    main()
