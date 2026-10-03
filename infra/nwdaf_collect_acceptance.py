"""Capture post-deployment state and publish independently verified decisions."""
import hashlib
import json
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from nwdaf_closed_loop_campaign import api
from nwdaf_gate3_live_probe import accounts


def main():
    settings=get_settings();core=Lab(settings,settings.ssh_port)
    directory=ROOT/'.work'/'nwdaf-loop-aef171083b'
    analysis=json.loads((directory/'analysis.json').read_text())
    result=json.loads((directory/'result.json').read_text())
    state={'utc':datetime.now(timezone.utc).isoformat()}
    try:
        token=core.read(settings.nwdaf_token_file).decode().strip()
        for path,name in [('/health','health'),('/management/v1/predictions','predictions'),('/management/v1/closed-loop/history','ledger_before_verification')]:
            state[name]=api(core,token,'GET',path)
        journal=core.run(['cat','/var/lib/open5gs/nwdaf/audit.jsonl'],sudo=True)
        (directory/'native-audit.jsonl').write_text(journal,encoding='utf-8')
        # Replaying identical event IDs is safe and also repairs interrupted delivery.
        state['journal_replay_count']=0
        for line in journal.splitlines():
            api(core,token,'POST','/management/v1/closed-loop/events',json.loads(line))
            state['journal_replay_count']+=1
        if analysis['gate3']=='PASSED':
            for transition in analysis['transitions'][:2]:
                assert transition['control_verified']
                source=next(x for x in result['ledger_after']['items'] if x['decision_id']==transition['decision_id'] and x['stage']=='N7_ACK')
                event={k:source[k] for k in ('decision_id','supi','snssai','policy_id','action','nominal_mbr_bps','target_mbr_bps')}
                event.update(event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'nwdaf-loop-aef171083b:'+source['decision_id']+':verified')),
                             stage='ENFORCEMENT_VERIFIED',elapsed_ms=transition['detection_to_pfcp_ack_ms'],
                             evidence_ref='pcap:sha256:'+result['pcap_sha256']+':frames='+str(transition['pfcp_request_frame'])+','+str(transition['pfcp_response_frame'])+';UE-counters:nwdaf-loop-aef171083b/result.json')
                api(core,token,'POST','/management/v1/closed-loop/events',event)
        state['ledger_after_verification']=api(core,token,'GET','/management/v1/closed-loop/history')
        state['accounts']=accounts(core)
        state['services']=core.run(['systemctl','show','open5gs-pcfd','open5gs-smfd','maestro-nwdaf','--property=Id,ActiveState,ExecMainStartTimestamp'])
        state['routes']=core.run(['ip','-j','route','show'])
        state['auxiliary_units']=core.run(['systemctl','list-units','--all','--no-pager','nwdaf-loop-*.service','nwdaf-loop-*.timer'])
        state['pcf_tail']=core.run(['tail','-n','20','/var/log/open5gs/pcf.log'],sudo=True)
        data=core.read('/home/emsadmin/maestro-charging/nwdaf/data/pm-source.sqlite3')
        (directory/'pm-snapshot.sqlite3').write_bytes(data)
        state['pm_sha256']=hashlib.sha256(data).hexdigest()
        (directory/'post-verification.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in state.items() if k in ('health','services','journal_replay_count')},indent=2))
        print(json.dumps([{'slice':x['snssai'],'status':x['evidence'].get('status'),'samples':len(x['evidence'].get('history',[])),'points':x['evidence'].get('points')} for x in state['predictions']['items']],indent=2))
    finally:core.client.close()

if __name__=='__main__':main()
