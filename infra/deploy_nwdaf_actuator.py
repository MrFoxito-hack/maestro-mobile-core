"""Deploy compiled PCF with a scoped drop-in and independent rollback timer."""
from datetime import datetime, timezone
from pathlib import Path
import json
import shlex
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend')); sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

def main():
    settings=get_settings(); host=Lab(settings,settings.ssh_port)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    tag='nwdaf-pcf-'+stamp.lower()
    dropin='/etc/systemd/system/open5gs-pcfd.service.d/80-maestro-nwdaf.conf'
    base='/home/emsadmin/maestro-charging/open5gs'
    local=ROOT/'.work'/tag; local.mkdir()
    changed=False; healthy=False
    try:
        remote=host.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        try: host.read(dropin)
        except FileNotFoundError: pass
        else: raise RuntimeError('Deployment drop-in already exists; review existing state')
        # Narrow ACLs grant this service access without exposing the bearer token.
        print(host.run(['env','DEBIAN_FRONTEND=noninteractive','apt-get','install','-y','acl'],sudo=True,timeout=180),flush=True)
        acl=host.run(['getfacl','-p','/home/emsadmin',settings.nwdaf_token_file])
        host.write(remote+'/original.acl',acl)
        rollback='#!/bin/sh\nset -eu\nrm -f '+shlex.quote(dropin)+'\nsetfacl --restore='+shlex.quote(remote+'/original.acl')+'\nsystemctl daemon-reload\nsystemctl restart open5gs-pcfd\n'
        host.write(remote+'/rollback.sh',rollback,0o700)
        host.run(['systemd-run','--unit='+tag+'-rollback','--on-active=120s','/bin/sh',remote+'/rollback.sh'],sudo=True)
        content='''[Service]
ExecStart=
ExecStart=/home/emsadmin/maestro-charging/open5gs/build/src/pcf/open5gs-pcfd -c /etc/open5gs/pcf.yaml
Environment=MAESTRO_NWDAF_TOKEN_FILE=/home/emsadmin/maestro-charging/nwdaf/nwdaf.token
Environment=MAESTRO_NWDAF_SST=1
Environment=MAESTRO_NWDAF_SD=000001
Environment=MAESTRO_NWDAF_ACTUATE=1
Environment=MAESTRO_NWDAF_AUDIT_FILE=/var/lib/open5gs/nwdaf/audit.jsonl
StateDirectory=open5gs/nwdaf
StateDirectoryMode=0700
UMask=0077
'''
        host.write(remote+'/override.conf',content)
        changed=True
        host.run(['setfacl','-m','u:open5gs:--x','/home/emsadmin'],sudo=True)
        host.run(['setfacl','-m','u:open5gs:r--',settings.nwdaf_token_file],sudo=True)
        host.run(['mkdir','-p','/etc/systemd/system/open5gs-pcfd.service.d'],sudo=True)
        host.run(['install','-m','0644',remote+'/override.conf',dropin],sudo=True)
        host.run(['systemctl','daemon-reload'],sudo=True)
        host.run(['systemctl','restart','open5gs-pcfd'],sudo=True)
        time.sleep(3)
        assert host.run(['systemctl','is-active','open5gs-pcfd']).strip()=='active'
        pid=host.run(['systemctl','show','open5gs-pcfd','--property=MainPID','--value']).strip()
        loaded=host.run(['readlink','-f','/proc/'+pid+'/exe'],sudo=True).strip()
        assert loaded==base+'/build/src/pcf/open5gs-pcfd'
        log=host.run(['tail','-n','100','/var/log/open5gs/pcf.log'],sudo=True)
        journal=host.run(['journalctl','-u','open5gs-pcfd','--since','-2min','--no-pager','-n','100'],sudo=True)
        (local/'pcf.log').write_text(log,encoding='utf-8')
        (local/'journal.log').write_text(journal,encoding='utf-8')
        assert 'native N7 actuation=enabled' in log+journal
        healthy=True
        host.run(['systemctl','stop',tag+'-rollback.timer'],sudo=True)
        info={'status':'DEPLOYED','binary':loaded,'pid':pid,'backup':remote,
              'rollback_command':'sudo /bin/sh '+remote+'/rollback.sh',
              'sha256':host.run(['sha256sum','/proc/'+pid+'/exe'],sudo=True).split()[0]}
        (local/'deployment.json').write_text(json.dumps(info,indent=2))
        print(json.dumps(info,indent=2),flush=True)
        print(log[-4500:],flush=True)
    finally:
        if changed and not healthy:
            print(host.run(['/bin/sh',remote+'/rollback.sh'],sudo=True,check=False),flush=True)
            host.run(['systemctl','stop',tag+'-rollback.timer'],sudo=True,check=False)
        host.client.close()

if __name__=='__main__': main()
