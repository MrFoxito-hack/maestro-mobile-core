"""Verify current six-terminal/native-PFCP/return-route mapping after OE4."""
import asyncio
from datetime import datetime, timezone
import json
from c8_acquire import snapshot, terminal, terminal_sessions
from c8_remote import CAMPAIGN_ROOT, ROOT, LoggedLab, get_settings
from c8_qos import snapshot as qos_snapshot, assert_ready


async def observed():
    rows=[]
    for i,kind,dnn,target in [(1,'embb','internet','10.45.0.1'),(2,'urllc','5g-plus','172.31.48.2'),
                               (3,'miot','corporate','10.46.0.1'),(4,'embb','internet','10.45.0.1'),
                               (5,'urllc','5g-plus','172.31.48.2'),(6,'miot','corporate','10.46.0.1')]:
        supi=f'imsi-999700000000{i:03d}'
        state=await terminal.read_terminal(supi)
        session,=[s for s in terminal_sessions.sessions(state) if s['apn']==dnn]
        rows.append({'supi':supi,'slice':kind,'target':target,**session})
    return rows


def main():
    out=CAMPAIGN_ROOT/'setup/oe4';out.mkdir(parents=True,exist_ok=True)
    hosts={key:LoggedLab(get_settings(),port,out) for key,port in [('ue',2226),('upf',2223),('upf2',2224)]}
    record={'at':datetime.now(timezone.utc).isoformat(),'status':'started'}
    try:
        rows=asyncio.run(observed());record['terminals']=rows
        native={'upf':snapshot(hosts['upf'],'upf'),'upf2':snapshot(hosts['upf2'],'upf2'),
                'upf3':snapshot(hosts['upf'],'upf3')};record['native']=native
        for row in rows:
            nf={'urllc':'upf3','miot':'upf2','embb':'upf'}[row['slice']]
            session,=[s for s in native[nf]['native']['sessions'] if s['ue_ipv4']==row['address']]
            assert session['dnn']==row['apn']
            assert row['snssai']['sst']=={'embb':1,'urllc':2,'miot':3}[row['slice']]
            route=json.loads(hosts['ue'].run(['ip','-j','route','get',row['target'],'from',row['address'],'oif',row['interface']]))
            assert route[0]['dev']==row['interface'];row['route']=route;row['upf']=nf
        state=qos_snapshot(hosts['ue']);assert_ready(state,{r['slice']:r for r in rows[:3]})
        record['qos']=state
        remote=hosts['ue'].run(['mktemp','-d','/home/emsadmin/c8-final-XXXXXX']).strip()
        config={'duration_s':5,'seed':42017,'streams':[{'slice':r['slice'],'source':r['address'],
                'interface':r['interface'],'target':r['target'],'port':8765,'payload_bytes':64,'pps':20,'sensors':1}
                for r in rows if r['slice']=='urllc']}
        hosts['ue'].write(remote+'/traffic.py',(ROOT/'infra/c8_traffic.py').read_bytes())
        hosts['ue'].write(remote+'/config.json',json.dumps(config))
        record['canary_summary']=json.loads(hosts['ue'].run(['python3',remote+'/traffic.py',remote+'/config.json',remote+'/raw.json'],sudo=True,timeout=20))
        raw=hosts['ue'].read(remote+'/raw.json');(out/'final-canary-raw.json').write_bytes(raw)
        assert all(r['sent']==r['received']==100 for r in record['canary_summary']['streams'])
        record['status']='verified'
    finally:
        (out/'runtime-final.json').write_text(json.dumps(record,indent=2)+'\n')
        for host in hosts.values():host.client.close()
    print(json.dumps({'status':record['status'],'terminals':len(rows),'canary':record['canary_summary']['streams']}))


if __name__=='__main__':main()
