"""Deploy the PCC binding implementation; retain SMF network/CHF configuration."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shlex
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
if __name__=='__main__':
    settings=get_settings();core=Lab(settings,settings.ssh_port);ue=Lab(settings,settings.ue_ssh_port)
    base='/home/emsadmin/maestro-charging/open5gs'
    tag='nwdaf-smf-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ').lower()
    local=ROOT/'.work'/tag;local.mkdir()
    dropin='/etc/systemd/system/open5gs-smfd.service.d/zz-maestro-nwdaf-binding.conf'
    changed=False;ready=False
    try:
        try:core.read(dropin)
        except FileNotFoundError:pass
        else:raise RuntimeError('SMF binding override exists')
        testlist=core.run(['meson','test','-C',base+'/build','--list'])
        print(testlist,flush=True)
        tests=[line.strip().split('/')[-1].strip() for line in testlist.splitlines() if 'chf' in line.lower() or 'nwdaf' in line.lower()]
        if tests:
            tested=core.run(['meson','test','-C',base+'/build','--print-errorlogs',*tests],timeout=180)
            (local/'native-tests.log').write_text(tested,encoding='utf-8');print(tested,flush=True)
        argv=shlex.split(re.search(r'argv\[\]=([^;]+)',core.run(['systemctl','show','open5gs-smfd','--property=ExecStart','--value']))[1].strip())
        assert len(argv)==3 and argv[1]=='-c'
        config=argv[2];config_hash=core.run(['sha256sum',config],sudo=True).split()[0]
        libraries=core.run(['ldd',base+'/build/src/smf/open5gs-smfd'])
        libdirs=sorted(set(p.rsplit('/',1)[0] for p in re.findall(r'=> (\S+/maestro-charging/open5gs/build/\S+)',libraries)))
        assert libdirs
        remote=core.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        restore='#!/bin/sh\nset -eu\nrm -f '+shlex.quote(dropin)+'\nsystemctl daemon-reload\nsystemctl restart open5gs-smfd open5gs-pcfd\n'
        core.write(remote+'/rollback.sh',restore,0o700)
        core.run(['systemd-run','--unit='+tag+'-rollback','--on-active=150s','/bin/sh',remote+'/rollback.sh'],sudo=True)
        ue.run(['systemd-run','--unit='+tag+'-ue-recover','--on-active=150s','/bin/systemctl','start','ueransim-ue'],sudo=True)
        # Graceful NAS release avoids intentionally abandoning a CHF reservation.
        ue.run(['/home/emsadmin/UERANSIM/build/nr-cli','imsi-999700000000001','--exec','deregister switch-off'],check=False)
        time.sleep(3)
        ue.run(['systemctl','stop','ueransim-ue'],sudo=True)
        text='[Service]\nExecStart=\nExecStart='+base+'/build/src/smf/open5gs-smfd -c '+config+'\nEnvironment=LD_LIBRARY_PATH='+':'.join(libdirs)+'\n'
        core.write(remote+'/override.conf',text)
        changed=True
        core.run(['install','-m','0644',remote+'/override.conf',dropin],sudo=True)
        core.run(['systemctl','daemon-reload'],sudo=True)
        core.run(['systemctl','restart','open5gs-smfd','open5gs-pcfd'],sudo=True)
        time.sleep(3)
        ue.run(['systemctl','start','ueransim-ue'],sudo=True)
        for _ in range(30):
            time.sleep(1)
            state=ue.run(['/home/emsadmin/UERANSIM/build/nr-cli','imsi-999700000000001','--exec','ps-list'],check=False)
            if 'PS-ACTIVE\n' in state and 'address: 10.45.' in state:break
        else:raise RuntimeError('Primary UE did not recover')
        assert core.run(['systemctl','is-active','open5gs-smfd']).strip()=='active'
        assert core.run(['sha256sum',config],sudo=True).split()[0]==config_hash
        ready=True
        core.run(['systemctl','stop',tag+'-rollback.timer'],sudo=True)
        ue.run(['systemctl','stop',tag+'-ue-recover.timer'],sudo=True)
        result={'status':'DEPLOYED','configuration_sha256_unchanged':config_hash,
                'configuration':config,'rollback_command':'sudo /bin/sh '+remote+'/rollback.sh','ue':state}
        (local/'deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
    finally:
        if changed and not ready:core.run(['/bin/sh',remote+'/rollback.sh'],sudo=True,check=False)
        ue.run(['systemctl','start','ueransim-ue'],sudo=True,check=False)
        core.client.close();ue.client.close()
