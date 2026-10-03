"""Re-register only the two experiment UEs after proven stale AMF contexts."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
if __name__=='__main__':
    if '--execute' not in sys.argv:raise SystemExit('--execute required')
    settings=get_settings();ue=Lab(settings,settings.ue_ssh_port);core=Lab(settings,settings.ssh_port)
    result={'utc':datetime.now(timezone.utc).isoformat()}
    try:
        result['amf_before']=core.run(['journalctl','-u','open5gs-amfd','--no-pager','-n','55'],sudo=True)
        assert 'Number of AMF-UEs is now 0' in result['amf_before'],'Do not reset functioning UEs without evaluating live associations'
        for unit in ['ueransim-ue','ueransim-ue-04']:
            ue.run(['systemctl','restart',unit],sudo=True)
            time.sleep(4)
            result[unit]=ue.run(['journalctl','-u',unit,'--since','-15sec','--no-pager'],sudo=True)
        result['amf_after']=core.run(['journalctl','-u','open5gs-amfd','--since','-20sec','--no-pager'],sudo=True)
        result['smf_after']=core.run(['journalctl','-u','open5gs-smfd','--since','-20sec','--no-pager'],sudo=True)
        (ROOT/'.work/nwdaf-qoe-ue-recovery.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        for unit in ['ueransim-ue','ueransim-ue-04']:print(result[unit])
    finally:ue.client.close();core.client.close()
