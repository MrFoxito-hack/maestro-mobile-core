"""Resolve a dedicated UERANSIM unit from its configured SUPI, never its suffix."""
import json
import shlex

from fastapi import HTTPException
from app.core.config import get_settings
from app.services.terminal_access import normalize_imsi


# Executed on the UE host. No client-controlled paths, units or command strings.
# Inactive units remain discoverable, so airplane-off does not need an active TUN.
SCRIPT = r'''
import json,os,pathlib,re,shlex,shutil,subprocess,sys,yaml
supi,operation=sys.argv[1:3]
assert re.fullmatch(r'imsi-\d{14,15}',supi)
assert operation in ('inspect','start','stop','configure')
def run(args):
 return subprocess.check_output(args,text=True,timeout=15).strip()
units=run(['systemctl','list-unit-files','--type=service','--no-legend','--no-pager']).splitlines()
candidates=[]
for line in units:
 unit=line.split()[0]
 if not unit.startswith(('ueransim-','maestro-ue-')) or '@' in unit: continue
 raw=run(['systemctl','show',unit,'--property=ExecStart','--value'])
 entries=re.findall(r'argv\[\]=(.*?) ;',raw)
 if len(entries)!=1: continue
 args=shlex.split(entries[0])
 if not args or pathlib.Path(args[0]).name!='nr-ue': continue
 # Reject overrides/multi-UE invocations: a unit must own exactly one identity.
 if len(args)!=3 or args[1] not in ('-c','--config'): continue
 path=pathlib.Path(args[2])
 if not path.is_absolute() or not path.is_file(): continue
 data=yaml.safe_load(path.read_text())
 if isinstance(data,dict) and data.get('supi')==supi:
  candidates.append((unit,path,data))
if len(candidates)!=1: raise SystemExit('Dedicated UE unit absent or ambiguous')
unit,path,data=candidates[0]
if operation=='configure':
 apn,sst,sd=sys.argv[3],int(sys.argv[4]),int(sys.argv[5])
 if (apn,sst,sd) not in [('internet',1,1),('corporate',1,2),('corporate',3,3),('5g-plus',2,2)]:
  raise SystemExit('Invalid DNN/slice')
 # Refuse to alter a running instance. Stop/start is performed only for this SUPI.
 state=run(['systemctl','show',unit,'--property=ActiveState','--value'])
 if state not in ('inactive','failed'): raise SystemExit('UE must be stopped')
 backup=path.with_name(path.name+'.before-terminal-apn')
 if not backup.exists(): shutil.copy2(path,backup)
 data['sessions']=[{'type':'IPv4','apn':apn,'slice':{'sst':sst,'sd':sd}}]
 data['default-nssai']=[{'sst':sst,'sd':sd}]
 stat=path.stat(); tmp=path.with_name(path.name+'.maestro.tmp')
 tmp.write_text(yaml.safe_dump(data,sort_keys=False))
 os.chown(tmp,stat.st_uid,stat.st_gid); os.chmod(tmp,stat.st_mode)
 os.replace(tmp,path)
elif operation in ('start','stop'):
 run(['systemctl',operation,unit])
print(json.dumps({'supi':supi,'unit':unit,'operation':operation}))
'''


async def control(imsi, operation, *, apn=None, expected=None):
    from app.services import terminal
    supi = normalize_imsi(imsi)
    args = ['python3', '-c', SCRIPT, supi, operation]
    if operation == 'configure':
        args += [apn, str(expected['sst']), str(expected['sd'])]
    remote = terminal.adapter()
    try:
        result = json.loads(await remote._run(remote._sudo_cmd(shlex.join(args)),
                                              port=get_settings().ue_ssh_port))
        if result['supi'] != supi or result['operation'] != operation:
            raise ValueError('Wrong runtime identity')
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, 'Instancia UERANSIM no confirmada para el IMSI solicitado') from None
