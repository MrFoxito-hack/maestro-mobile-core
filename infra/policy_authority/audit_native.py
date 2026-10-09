"""Read-only audit of loaded Open5GS binaries and existing observation APIs.

Does not restart/signal/attach to NFs, modify rules, or open charging databases.
Source exports are private evidence, not proof that a binary was built from them.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from infra.charging.e2e_native import Lab, get_settings
from app.services.upf_inventory import inventory
from app.services.execution import _info_address


INSPECT = r'''
import hashlib,json,pathlib,socket,subprocess,sys,uuid,urllib.request
units=json.loads(sys.argv[1]);core=sys.argv[2]=='core';urls=json.loads(sys.argv[3])
r={'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'units':{}}
for unit in units:
 raw=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','ExecMainStartTimestampMonotonic'],text=True)
 fields=dict(l.split('=',1) for l in raw.splitlines() if '=' in l)
 pid=int(fields['MainPID']);entry={'systemd':fields};r['units'][unit]=entry
 if pid:
  exe=pathlib.Path('/proc')/str(pid)/'exe'
  entry['executable']=str(exe.resolve());binary=exe.read_bytes()
  entry['sha256']=hashlib.sha256(binary).hexdigest()
  entry['markers']={m:m.encode() in binary for m in ['fencing_token','expected_version','effective_policy','laboratory_snapshot','maestro-policy-native','mml_poll','upf_quota_allow']}
  maps=(pathlib.Path('/proc')/str(pid)/'maps').read_text().splitlines()
  entry['loaded_policy_modules']=sorted({l.split()[-1] for l in maps if any(s in l.lower() for s in ('maestro','policy','nwdaf')) and '/' in l.split()[-1]})
r['unix_sockets']=[l for l in pathlib.Path('/proc/net/unix').read_text().splitlines() if any(s in l for s in ('maestro','open5gs'))]
if core:
 r['native_socket_exists']=pathlib.Path('/run/maestro-policy-native/control.sock').exists()
 r['pcf_queries']={}
 for op in ('status','laboratory_snapshot'):
  with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as s:
   s.settimeout(3);s.bind('\0c3-audit-'+uuid.uuid4().hex)
   try:
    s.sendto(json.dumps({'operation':op}).encode(),'/var/lib/open5gs/nwdaf/mml.sock')
    d=json.loads(s.recv(60001))
    r['pcf_queries'][op]={k:d[k] for k in ('status','detail','mode','schema_version','scope','effective_policy_available','all_policy_writers_observed') if k in d}
   except (OSError,ValueError) as exc:r['pcf_queries'][op]={'error':type(exc).__name__}
 r['session_apis']={}
 for nf,url in urls.items():
  try:
   with urllib.request.urlopen(url,timeout=3) as response:d=json.load(response)
   r['session_apis'][nf]=d
  except Exception as exc:r['session_apis'][nf]={'error':type(exc).__name__}
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(6)
  try:
   s.connect('/run/maestro-policy-authority/control.sock');s.sendall(b'{"operation":"status"}\n')
   with s.makefile('rb') as stream:r['authority']=json.loads(stream.readline(65537))
  except (OSError,ValueError) as exc:r['authority']={'error':type(exc).__name__}
print(json.dumps(r))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    groups = {'core': (settings.ssh_port, ['open5gs-pcfd', 'open5gs-smfd', 'open5gs-smfd2', 'open5gs-smfd3'])}
    for target in inventory()['targets']:
        key = target['ssh_port_setting']
        if key not in groups:
            groups[key] = (getattr(settings, key), [])
        groups[key][1].append(target['unit'])
    evidence = {'observed_at': datetime.now(timezone.utc).isoformat(), 'hosts': {}}
    urls = {}
    for nf in ('smf', 'smf2', 'smf3'):
        address, port = _info_address(nf)
        urls[nf] = f'http://{address}:{port}/pdu-info'
    for name, (port, units) in groups.items():
        host = Lab(settings, port)
        try:
            result = json.loads(host.run(['python3', '-c', INSPECT, json.dumps(units), name,
                                          json.dumps(urls if name == 'core' else {})], sudo=True, timeout=45))
            evidence['hosts'][name] = result
            if name == 'core':
                source_root = '/home/emsadmin/maestro-charging/open5gs'
                for relative in ['src/pcf/mml-control.inc', 'src/pcf/nwdaf-handler.c', 'src/pcf/sbi-path.c',
                                 'src/pcf/npcf-handler.c', 'src/smf/npcf-handler.c', 'src/smf/pfcp-path.c',
                                 'src/upf/n4-handler.c', 'src/smf/info.c']:
                    dest = args.evidence / 'source' / relative
                    try:
                        data = host.read(source_root + '/' + relative)
                    except FileNotFoundError:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            after = {}
            for unit in units:
                raw = host.run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState', '-p', 'ExecMainStartTimestampMonotonic'])
                after[unit] = dict(l.split('=', 1) for l in raw.splitlines() if '=' in l)
            result['processes_unchanged'] = all(after[u] == result['units'][u]['systemd'] for u in units)
            assert result['processes_unchanged'], 'NF identity changed during read-only audit'
        finally:
            host.client.close()
            (args.evidence / 'audit.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps({'evidence': str(args.evidence.resolve()), 'hosts': {
        n: {'pids': {u: d['systemd']['MainPID'] for u, d in h['units'].items()},
            'processes_unchanged': h['processes_unchanged'],
            **({k: h[k] for k in ('native_socket_exists', 'pcf_queries', 'authority')} if n == 'core' else {})}
        for n, h in evidence['hosts'].items()}}, indent=2))


if __name__ == '__main__':
    main()
