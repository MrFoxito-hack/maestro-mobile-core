"""Install the already-tested QER policer with matching private libraries.

Keep UPF-01's exact current configuration path. No PFCP peer, network setting,
or charging parameter is edited. An independent timer restores the old unit.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import shlex
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend')); sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

def main():
    settings=get_settings(); core=Lab(settings,settings.ssh_port); upf=Lab(settings,settings.upf_ssh_port)
    base='/home/emsadmin/maestro-charging/open5gs/build'
    tag='nwdaf-upf-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ').lower()
    local=ROOT/'.work'/tag; local.mkdir()
    target='/opt/'+tag
    override='/etc/systemd/system/open5gs-upfd.service.d/zz-maestro-nwdaf-policer.conf'
    changed=False; ready=False
    try:
        try: upf.read(override)
        except FileNotFoundError: pass
        else: raise RuntimeError('Policer override already exists; review first')
        exec_start=upf.run(['systemctl','show','open5gs-upfd','--property=ExecStart','--value'])
        match=re.search(r'argv\[\]=([^;]+)',exec_start)
        argv=shlex.split(match[1].strip())
        assert len(argv)==3 and argv[1]=='-c' and argv[2].startswith('/opt/maestro-charging/')
        config_path=argv[2]
        config_hash=upf.run(['sha256sum',config_path],sudo=True).split()[0]
        old_pid=upf.run(['systemctl','show','open5gs-upfd','--property=MainPID','--value']).strip()
        old_binary=upf.run(['sha256sum','/proc/'+old_pid+'/exe'],sudo=True).split()[0]
        test=core.run(['meson','test','-C',base,'--suite','nwdaf','--print-errorlogs'],timeout=120)
        (local/'native-tests.log').write_text(test,encoding='utf-8')
        print(test,flush=True)
        staging=upf.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        upf.run(['mkdir',staging+'/lib'])
        binary=base+'/src/upf/open5gs-upfd'
        dependencies=core.run(['ldd',binary])
        hashes={}
        for source in [binary]+sorted(set(re.findall(r'=> (\S+/maestro-charging/open5gs/build/\S+)',dependencies))):
            name=source.rsplit('/',1)[-1]
            data=core.read(source)
            destination=staging+'/open5gs-upfd' if source==binary else staging+'/lib/'+name
            upf.write(destination,data,0o755 if source==binary else 0o644)
            hashes[name]=hashlib.sha256(data).hexdigest()
        linked=upf.run(['env','LD_LIBRARY_PATH='+staging+'/lib','ldd',staging+'/open5gs-upfd'])
        assert 'not found' not in linked
        assert all(staging+'/lib' in line for line in linked.splitlines() if 'libogs' in line or 'libprom.so' in line)
        upf.run(['install','-d','-m','0755',target],sudo=True)
        upf.run(['cp','-a',staging+'/lib',target+'/lib'],sudo=True)
        upf.run(['install','-m','0755',staging+'/open5gs-upfd',target+'/open5gs-upfd'],sudo=True)
        rollback='#!/bin/sh\nset -eu\nrm -f '+shlex.quote(override)+'\nsystemctl daemon-reload\nsystemctl restart open5gs-upfd\n'
        upf.write(staging+'/rollback.sh',rollback,0o700)
        upf.run(['systemd-run','--unit='+tag+'-rollback','--on-active=120s','/bin/sh',staging+'/rollback.sh'],sudo=True)
        override_text='[Service]\nExecStart=\nExecStart='+target+'/open5gs-upfd -c '+config_path+'\nEnvironment=LD_LIBRARY_PATH='+target+'/lib\n'
        upf.write(staging+'/override.conf',override_text)
        changed=True
        upf.run(['install','-m','0644',staging+'/override.conf',override],sudo=True)
        upf.run(['systemctl','daemon-reload'],sudo=True)
        upf.run(['systemctl','restart','open5gs-upfd'],sudo=True)
        time.sleep(5)
        assert upf.run(['systemctl','is-active','open5gs-upfd']).strip()=='active'
        assert upf.run(['sha256sum',config_path],sudo=True).split()[0]==config_hash
        log=core.run(['tail','-n','100','/var/log/open5gs/smf.log'],sudo=True)
        (local/'smf-after.log').write_text(log,encoding='utf-8')
        (local/'upf-after.log').write_text(upf.run(['journalctl','-u','open5gs-upfd','--since','-2min','--no-pager','-n','100'],sudo=True),encoding='utf-8')
        ready=True
        upf.run(['systemctl','stop',tag+'-rollback.timer'],sudo=True)
        result={'status':'DEPLOYED','config_path':config_path,'config_sha256_unchanged':config_hash,
                'prior_binary_sha256':old_binary,'artifacts':hashes,
                'rollback_command':'sudo /bin/sh '+staging+'/rollback.sh'}
        (local/'deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
        print(log[-3000:],flush=True)
    finally:
        if changed and not ready:
            upf.run(['/bin/sh',staging+'/rollback.sh'],sudo=True,check=False)
            upf.run(['systemctl','stop',tag+'-rollback.timer'],sudo=True,check=False)
        core.client.close(); upf.client.close()

if __name__=='__main__': main()
