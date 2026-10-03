"""Read-only compact post-deployment operational evidence, without credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from nwdaf_closed_loop_campaign import api

if __name__=='__main__':
    settings=get_settings();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    state={'utc':stamp}
    for name,port,services in [('core',settings.ssh_port,['open5gs-pcfd','open5gs-smfd','maestro-nwdaf']),
                               ('upf-01',settings.upf_ssh_port,['open5gs-upfd']),
                               ('upf-02',settings.upf2_ssh_port,['open5gs-upfd'])]:
        host=Lab(settings,port)
        try:
            state[name]={'services':host.run(['systemctl','show',*services,'--property=Id,ActiveState,MainPID'])}
            if name=='core':
                token=host.read(settings.nwdaf_token_file).decode().strip()
                state[name]['health']=api(host,token,'GET','/health')
                state[name]['pcf_tail']=host.run(['tail','-n','3','/var/log/open5gs/pcf.log'],sudo=True)
                state[name]['replay']=host.run(['systemctl','show','maestro-nwdaf-audit-replay.service','--property=Result,ExecMainStatus'])
                state[name]['temporary_routes']=host.run(['ip','route','show','exact','10.45.0.2/32'])
            elif name=='upf-01':
                state[name]['config_sha256']=host.run(['sha256sum','/opt/maestro-charging/maestro-charging-0b62ab5297fc/upf.yaml'],sudo=True).split()[0]
        finally:host.client.close()
    state['fast_pm']=json.loads((ROOT/'.work/nwdaf-fast-pm-status.json').read_text())
    path=ROOT/'.work'/('nwdaf-operational-'+stamp+'.json')
    path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps(state,indent=2));print(str(path))
