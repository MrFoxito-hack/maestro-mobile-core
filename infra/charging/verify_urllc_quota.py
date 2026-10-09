"""Bounded real URLLC/Nchf acceptance using a new private CHF database.

Production charging databases/configs are never changed. Independent timers
restore SMF3 and the initial UE service even if this controller disconnects.
This validates the traditional charging path, not XDP acceleration.
"""
import argparse
import json
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
from uuid import uuid4
import yaml

from e2e_native import Lab, ROOT, CHF
from lab_command import get_settings
sys.path.insert(0, str(ROOT/'infra'))
from multi_upf_stage import active_config
from multi_upf_cutover import CLI, SUPI, UE_CONFIG, wait_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',required=True)
    parser.parse_args()
    settings=get_settings()
    hosts={n:Lab(settings,p) for n,p in [('core',2222),('ue',2226),('upf',2223)]}
    core,ue,upf=(hosts[n] for n in ('core','ue','upf'))
    tag='urllc-quota-'+uuid4().hex[:10]
    local=ROOT/'.work'/tag;local.mkdir()
    paths={};timers=[];result={'status':'PREPARING','xdp_acceleration':'BLOCKED_UNSYNCHRONIZED_POLICY'}
    started=False
    try:
        for name in ('core','ue'):
            paths[name]=hosts[name].run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        cdir,edir=paths['core'],paths['ue']
        binary,_,_,config=active_config(core,'open5gs-smfd3')
        env=core.run(['systemctl','show','open5gs-smfd3','-p','Environment','--value'])
        lib=re.search(r'(?:^|\s)LD_LIBRARY_PATH=([^\s"\']+)',env)[1]
        ueconfig=yaml.safe_load(ue.run(['cat',UE_CONFIG]))
        assert ueconfig['supi']==SUPI
        ueconfig['sessions']=[{'type':'IPv4','apn':'5g-plus','slice':{'sst':2,'sd':2}}]
        ueconfig['default-nssai']=[{'sst':2,'sd':2}]
        ue.write(edir+'/ue.yaml',yaml.safe_dump(ueconfig,sort_keys=False))
        token=secrets.token_hex(32);owner=str(uuid4());quota=20000
        config['logger']={'file':{'path':cdir+'/smf.log'},'level':'info'}
        config['smf']['chf'].update(sbi=[{'addr':'127.0.0.1','port':18081}],
            requested_units=10000,journal_dir=cdir+'/journal',nf_instance_id=owner)
        core.write(cdir+'/smf.yaml',yaml.safe_dump(config,sort_keys=False))
        core.write(cdir+'/runtime.env','SMF_CHF_TOKEN='+token+'\nCHF_SBI_TOKENS=\''+json.dumps({owner:token})+"'\nCHF_SBI_LAB_NO_AUTH=false\nCHF_DATABASE_PATH="+cdir+'/charging.db\n')
        init='from pathlib import Path\nfrom app.repository import ChargingRepository\nr=ChargingRepository(Path('+repr(cdir+'/charging.db')+'));r.initialize()\nr.upsert_account('+repr(SUPI)+',20000,True,"urllc-isolated-acceptance")\n'
        core.write(cdir+'/init.py',init)
        core.run(['env','PYTHONPATH='+CHF,CHF+'/.venv/bin/python',cdir+'/init.py'])
        for name in ('core','ue'):
            units=[tag+'-smf',tag+'-chf',tag+'-capture'] if name=='core' else [tag+'-ue']
            restore='#!/bin/sh\n'+'systemctl stop '+' '.join(units)+' 2>/dev/null\n'
            restore+='systemctl start '+('open5gs-smfd3' if name=='core' else 'ueransim-ue')+'\n'
            hosts[name].write(paths[name]+'/restore.sh',restore,0o700)
            timer=tag+'-restore-'+name
            hosts[name].run(['systemd-run','--unit='+timer,'--on-active='+('240s' if name=='core' else '260s'),'/bin/sh',paths[name]+'/restore.sh'],sudo=True)
            timers.append((name,timer))
        # Record both namespaces: no legacy XDP program may bypass this test.
        result['xdp_links']={}
        for ns in (None,'maestro-urllc'):
            args=['ip','-j','-details','link']
            if ns:args=['ip','netns','exec',ns,*args]
            links=json.loads(upf.run(args,sudo=True))
            assert not any(i.get('xdp',{}).get('attached') or i.get('xdp',{}).get('prog') for i in links)
            result['xdp_links'][ns or 'root']=links
        started=True
        ue.run([CLI,SUPI,'-e','deregister normal'],check=False)
        time.sleep(2)
        ue.run(['systemctl','stop','ueransim-ue'],sudo=True)
        core.run(['systemctl','stop','open5gs-smfd3'],sudo=True)
        core.start(tag+'-chf',[CHF+'/.venv/bin/hypercorn','app.main:app','--bind','127.0.0.1:18081','--access-logfile','-'],cdir,
                   properties=['--property=EnvironmentFile='+cdir+'/runtime.env','--property=Environment=PYTHONPATH='+CHF])
        core.start(tag+'-capture',['/usr/bin/tcpdump','-Z','root','-i','any','-U','-s','0','-w',cdir+'/n4-nchf.pcap','udp port 8805 or tcp port 18081'],cdir)
        core.start(tag+'-smf',[binary,'-c',cdir+'/smf.yaml'],cdir,
                   properties=['--property=EnvironmentFile='+cdir+'/runtime.env','--property=Environment=LD_LIBRARY_PATH='+lib])
        wait_for(lambda:'PFCP associated [10.210.50.22]' in core.run(['cat',cdir+'/smf.log'],sudo=True,check=False),'Private SMF PFCP association failed')
        ue.start(tag+'-ue',['/home/emsadmin/UERANSIM/build/nr-ue','-c',edir+'/ue.yaml'],edir)
        def session():
            raw=yaml.safe_load(ue.run([CLI,SUPI,'-e','ps-list'],check=False))
            if isinstance(raw,dict):
                return next((v for v in raw.values() if isinstance(v,dict) and v.get('state')=='PS-ACTIVE' and v.get('apn')=='5g-plus'),None)
        pdu=wait_for(session,'Private URLLC session failed',50)
        assert pdu['address'].startswith('10.47.') and pdu['s-nssai']=={'sst':2,'sd':2}
        links=json.loads(ue.run(['ip','-j','-4','address']))
        interface=next(i['ifname'] for i in links if any(a['local']==pdu['address'] for a in i['addr_info']))
        result['pdu']=pdu
        print('Private URLLC session ready; consuming a 20 KB test quota',flush=True)
        ping=ue.run(['ping','-I',interface,'-s','1000','-c','30','-i','0.2','-W','1','172.31.48.2'],check=False,timeout=30)
        (local/'ping.txt').write_text(ping)
        received=re.search(r'(\d+) packets transmitted, (\d+) received',ping)
        assert received and 0<int(received[2])<30,'Expected successful traffic followed by quota cut'
        ue.run(['systemctl','stop',tag+'-ue'],sudo=True)
        time.sleep(3)
        query='import sqlite3,json\nc=sqlite3.connect('+repr(cdir+'/charging.db')+');c.row_factory=sqlite3.Row\nprint(json.dumps({t:[dict(r) for r in c.execute("SELECT * FROM "+t)] for t in ["charging_accounts","charging_sessions","charging_events","charging_cdrs"]}))'
        data=json.loads(core.run(['python3','-c',query]))
        (local/'accounting.json').write_text(json.dumps(data,indent=2))
        used=[s for s in data['charging_sessions'] if s['observed_bytes']>0]
        assert len(used)==1
        row=used[0]
        assert row['consumed_bytes']==quota and row['observed_bytes']==row['uplink_bytes']+row['downlink_bytes']
        assert all(s['reserved_bytes']==0 and s['status']=='RELEASED' for s in data['charging_sessions'])
        assert len(data['charging_cdrs'])==len(data['charging_sessions'])
        assert {'CREATE','UPDATE','RELEASE'}<={e['operation'] for e in data['charging_events']}
        log=ue.read(edir+'/'+tag+'-ue.log').decode(errors='replace')
        assert 'PDU Session Release Command received' in log
        result.update(status='PASS_TRADITIONAL_PATH',received=int(received[2]),quota_bytes=quota,
                      consumed_bytes=row['consumed_bytes'],observed_bytes=row['observed_bytes'],
                      reserved_bytes=row['reserved_bytes'],cdr_count=len(data['charging_cdrs']),nas_release=True)
    finally:
        if started:
            ue.run(['systemctl','stop',tag+'-ue'],sudo=True,check=False)
            core.run(['/bin/sh',paths['core']+'/restore.sh'],sudo=True)
            time.sleep(3)
            ue.run(['/bin/sh',paths['ue']+'/restore.sh'],sudo=True)
        for name,timer in timers:
            hosts[name].run(['systemctl','stop',timer+'.timer'],sudo=True,check=False)
        result['remote_evidence']=paths
        (local/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({'status':result['status'],'evidence':str(local)}),flush=True)
        for host in hosts.values():host.client.close()


if __name__=='__main__':main()
