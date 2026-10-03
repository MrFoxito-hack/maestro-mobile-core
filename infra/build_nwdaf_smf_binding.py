"""Build the narrow full-Internet PCC to existing-QER binding fix."""
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
if __name__=='__main__':
    settings=get_settings();core=Lab(settings,settings.ssh_port)
    base='/home/emsadmin/maestro-charging/open5gs'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        path=base+'/src/smf/binding.c';old=core.read(path)
        code=old.decode()
        if '#include "nwdaf-smf-binding.inc"' not in code:
            start=code.index('void smf_qos_flow_binding(smf_sess_t *sess)')
            index=code.index('        if (pcc_rule->type == OGS_PCC_RULE_TYPE_INSTALL) {',start)
            code=code[:index]+'#include "nwdaf-smf-binding.inc"\n\n'+code[index:]
        core.write(path+'.pre-nwdaf-binding-'+stamp,old)
        with core.client.open_sftp() as sftp:
            sftp.put(str(ROOT/'nwdaf/native/nwdaf-smf-binding.inc'),base+'/src/smf/nwdaf-smf-binding.inc')
            with sftp.open(path,'w') as out:out.write(code)
        build=core.run(['ninja','-C',base+'/build'],timeout=300)
        (ROOT/'.work'/('nwdaf-smf-build-'+stamp+'.log')).write_text(build,encoding='utf-8')
        print(build,flush=True)
    finally:core.client.close()
