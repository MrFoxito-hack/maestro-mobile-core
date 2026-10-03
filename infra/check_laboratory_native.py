"""Compile the local controller in an isolated remote temp directory; no deployment.

Uses the configured testbed compiler/header set. Never writes the existing source,
objects, installed binaries, service definitions or runtime policy state.
"""
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.laboratory.live import LabSSH
from app.laboratory.repository import canonical, stamp

CHECK = r'''
import json,pathlib,shlex,subprocess,sys,tempfile
base=pathlib.Path('/home/emsadmin/maestro-charging/open5gs')
entries=json.loads((base/'build/compile_commands.json').read_text())
entry=next(e for e in entries if e['file'].endswith('/nwdaf-handler.c'))
incoming=json.loads(sys.argv[1])
if set(incoming)!={'nwdaf-handler.c','nwdaf-handler.h','mml-control.inc'}:
 raise ValueError('unexpected_sources')
with tempfile.TemporaryDirectory(prefix='maestro-native-check-') as temp:
 root=pathlib.Path(temp)
 for name,text in incoming.items(): (root/name).write_text(text)
 original=shlex.split(entry['command']); flags=[]; skip=False
 for arg in original:
  if skip: skip=False; continue
  if arg in ('-o','-MF','-MQ','-MT'): skip=True; continue
  if arg in ('-MD','-MMD','-c') or arg==entry['file']: continue
  flags.append(arg)
 flags+=['-fsyntax-only',str(root/'nwdaf-handler.c')]
 result=subprocess.run(flags,cwd=entry['directory'],capture_output=True,text=True,timeout=12)
 print(json.dumps({'exit_code':result.returncode,'compiler_output':(result.stdout+result.stderr)[-12000:],
                   'deployment':False,'check':'native_controller_syntax_existing_headers'}))
'''


def main():
    sources = {name: (ROOT / 'nwdaf/native' / name).read_text(encoding='utf-8')
               for name in ('nwdaf-handler.c', 'nwdaf-handler.h', 'mml-control.inc')}
    result = LabSSH().read('core', CHECK, (canonical(sources),))
    result['created_at'] = stamp()
    result['sources_sha256'] = {n: hashlib.sha256(s.encode()).hexdigest() for n, s in sources.items()}
    directory = ROOT / '.work' / ('lab-native-check-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    (directory / 'result.json').write_text(canonical(result), encoding='utf-8')
    print(json.dumps({'path': str(directory), **result}))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
