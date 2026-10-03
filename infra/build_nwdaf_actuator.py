"""Back up, stage and build the native N7 controller; no service changes."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    settings=get_settings()
    host=Lab(settings,settings.ssh_port)
    base='/home/emsadmin/maestro-charging/open5gs'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    local=ROOT/'.work'/('nwdaf-build-'+stamp)
    local.mkdir()
    try:
        context=host.read(base+'/src/pcf/context.h').decode()
        path=host.read(base+'/src/pcf/sbi-path.c').decode()
        if 'bool nwdaf_throttled;' not in context:
            anchor='    /* Related Context */\n    ogs_pool_id_t pcf_ue_sm_id;'
            assert context.count(anchor)==1
            context=context.replace(anchor, '    /* NWDAF state belongs to this session generation only. */\n'
                '    bool nwdaf_throttled;\n    bool nwdaf_pending;\n    ogs_time_t nwdaf_changed_at;\n\n'+anchor)
        if '#include "nwdaf-handler.h"' not in path:
            path=path.replace('#include "sbi-path.h"','#include "sbi-path.h"\n#include "nwdaf-handler.h"')
        start=path.index('bool pcf_sbi_send_smpolicycontrol_update_notify(')
        end=path.index('bool pcf_sbi_send_smpolicycontrol_delete_notify(',start)
        section=path[start:end]
        if 'pcf_nwdaf_notify_context' not in section:
            old='    rc = ogs_sbi_send_request_to_client(\n            client, client_notify_cb, request, NULL);'
            assert section.count(old)==1
            section=section.replace(old, '''    if (pcf_nwdaf_notify_context(sess))
        rc = ogs_sbi_send_request_to_client(client, pcf_nwdaf_notify_response,
                request, pcf_nwdaf_notify_context(sess));
    else
        rc = ogs_sbi_send_request_to_client(
                client, client_notify_cb, request, NULL);''')
            path=path[:start]+section+path[end:]
        updates={'context.h':context.encode(),'sbi-path.c':path.encode()}
        for name in ('nwdaf-handler.c','nwdaf-handler.h','mml-control.inc'):
            updates[name]=(ROOT/'nwdaf/native'/name).read_bytes()
        for name,data in updates.items():
            remote=base+'/src/pcf/'+name
            old=host.read(remote) if name != 'mml-control.inc' else b''
            host.write(remote+'.pre-actuator-'+stamp,old)
            (local/(name+'.before')).write_bytes(old)
            (local/name).write_bytes(data)
            with host.client.open_sftp() as sftp, sftp.open(remote,'w') as out: out.write(data)
        result=host.run(['ninja','-C',base+'/build'],timeout=900,check=False)
        (local/'build.log').write_text(result,encoding='utf-8')
        print(result,flush=True)
        if 'FAILED:' in result or 'build stopped' in result: raise RuntimeError('Native build failed')
        (local/'source-sha256.json').write_text(json.dumps({n:hashlib.sha256(d).hexdigest() for n,d in updates.items()},indent=2))
        print('Build evidence: '+str(local),flush=True)
    finally: host.client.close()

if __name__=='__main__': main()
