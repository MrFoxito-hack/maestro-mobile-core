"""Install only the independent authority supervisor; never restart any NF.

Missing native fencing/checkpoint protocol results in admission_available=false.
It does not trigger a migration to an unfenced legacy control socket.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from infra.charging.e2e_native import Lab, get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    args.evidence.mkdir(parents=True, exist_ok=True)
    sources = {
        '/opt/maestro-policy-authority/policy_authority.py': ROOT / 'backend/app/services/policy_authority.py',
        '/opt/maestro-policy-authority/server.py': ROOT / 'infra/policy_authority/server.py',
        '/etc/systemd/system/maestro-policy-authority.service': ROOT / 'infra/policy_authority/maestro-policy-authority.service',
    }
    summary = {'applied': False, 'files': {remote: hashlib.sha256(local.read_bytes()).hexdigest()
                                        for remote, local in sources.items()}}
    try:
        units = ['open5gs-pcfd', 'open5gs-smfd', 'open5gs-smfd2', 'open5gs-smfd3', 'maestro-nwdaf']
        summary['core_before'] = {unit: core.run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState']) for unit in units}
        inspect = """import json,pathlib,socket,uuid
r={'native_socket_exists':pathlib.Path('/run/maestro-policy-native/control.sock').exists()}
with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as s:
 s.settimeout(4);s.bind('\\0maestro-c3-inspect-'+uuid.uuid4().hex)
 s.sendto(b'{"operation":"laboratory_snapshot"}','/var/lib/open5gs/nwdaf/mml.sock')
 d=json.loads(s.recv(60001))
 r['legacy_snapshot']={k:d.get(k) for k in ('schema_version','scope','effective_policy_available','all_policy_writers_observed','controller_pending_count')}
print(json.dumps(r))
"""
        summary['native_contract'] = json.loads(core.run(['python3', '-c', inspect], sudo=True))
        if args.apply:
            stage = core.run(['mktemp', '-d', '/home/emsadmin/maestro-c3-XXXXXX']).strip()
            for remote, local in sources.items():
                candidate = stage + '/' + local.name
                core.write(candidate, local.read_bytes())
                backup = remote + '.before-c3-' + tag
                script = """import pathlib,shutil,sys
p=pathlib.Path(sys.argv[1]);b=pathlib.Path(sys.argv[2])
if p.exists():
 if b.exists(): raise RuntimeError('backup_exists')
 shutil.copy2(p,b)
"""
                core.run(['python3', '-c', script, remote, backup], sudo=True)
                core.run(['install', '-D', '-o', 'root', '-g', 'root', '-m', '0644', candidate, remote], sudo=True)
            core.run(['systemd-analyze', 'verify', '/etc/systemd/system/maestro-policy-authority.service'], sudo=True)
            core.run(['systemctl', 'daemon-reload'], sudo=True)
            core.run(['systemctl', 'enable', 'maestro-policy-authority'], sudo=True)
            core.run(['systemctl', 'restart', 'maestro-policy-authority'], sudo=True)
            summary['applied'] = True
            probe = """import json,socket,time
for i in range(20):
 try:
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
   s.settimeout(4);s.connect('/run/maestro-policy-authority/control.sock')
   s.sendall(b'{"operation":"status"}\\n');print(s.recv(65536).decode());break
 except OSError:
  time.sleep(0.1)
else: raise RuntimeError('supervisor_unavailable')
"""
            summary['supervisor'] = json.loads(core.run(['python3', '-c', probe], sudo=True))
        summary['core_after'] = {unit: core.run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState']) for unit in units}
        summary['core_pids_unchanged'] = summary['core_before'] == summary['core_after']
        assert summary['core_pids_unchanged'], 'Unexpected Core process change'
    finally:
        (args.evidence / 'authority-deployment.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        core.client.close()
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
