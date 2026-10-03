"""Real bounded closed-loop campaign. Verdict is assigned by packet analysis.

No synthetic load reports, quota grants, or direct PCC/PFCP injection. The PCF
alone issues policies from authenticated NWDAF observations of UPF counters.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
import time
import uuid
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from nwdaf_gate3_live_probe import accounts


def api(core, token, method, path, body=None):
    connection=http.client.HTTPConnection('127.0.0.1',8085,timeout=8)
    channel=core.client.get_transport().open_channel('direct-tcpip',('127.0.0.1',8085),('127.0.0.1',0),timeout=8)
    channel.settimeout(8);connection.sock=channel
    try:
        connection.request(method,path,body=json.dumps(body) if body is not None else None,
                           headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read()
        if response.status not in (200,201,204): raise RuntimeError('NWDAF API status '+str(response.status)+' '+raw.decode()[:300])
        return json.loads(raw) if raw else None
    finally:connection.close()


def counters(upf, ue, interface):
    script="""import json,pathlib,time
p=pathlib.Path('/sys/class/net/ogstun')
print(json.dumps(dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
ifindex=int((p/'ifindex').read_text()),tx_bytes=int((p/'statistics/tx_bytes').read_text()),
uptime=float(pathlib.Path('/proc/uptime').read_text().split()[0]),timestamp=time.time())))
"""
    reading=json.loads(upf.run(['python3','-c',script]))
    rx=int(ue.run(['cat','/sys/class/net/'+interface+'/statistics/rx_bytes']).strip())
    return reading,{'rx_bytes':rx,'local_monotonic':time.monotonic(),'utc':time.time()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--external-pm',action='store_true',help='Use the scheduled real PM publisher; retain independent counter measurements without competing writes')
    args=parser.parse_args()
    if not args.execute:parser.error('--execute required for live bounded traffic')
    sys.stdout.reconfigure(encoding='utf-8')
    settings=get_settings();tag='nwdaf-loop-'+uuid.uuid4().hex[:10]
    local=ROOT/'.work'/tag;local.mkdir()
    hosts={};units=[];routes=[];remote=None
    result={'run':tag,'utc':datetime.now(timezone.utc).isoformat(),'gate3':'PENDING_ANALYSIS','observations':[],'traffic':[]}
    try:
        for name,port in [('core',settings.ssh_port),('upf',settings.upf_ssh_port),('ue',settings.ue_ssh_port)]: hosts[name]=Lab(settings,port)
        core,upf,ue=(hosts[x] for x in ('core','upf','ue'))
        token=core.read(settings.nwdaf_token_file).decode().strip()
        supi='imsi-999700000000001'
        pdu=yaml.safe_load(ue.run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'--exec','ps-list']))
        internet=[p for p in pdu.values() if p.get('apn')=='internet' and p.get('state')=='PS-ACTIVE']
        assert len(internet)==1
        address=internet[0]['address']
        log=ue.run(['journalctl','-u','ueransim-ue','--no-pager','-n','250'],sudo=True)
        matches=re.findall(r'TUN interface\[(uesimtun\d+), '+re.escape(address)+r'\] is up',log)
        assert matches,'No primary-service TUN identity evidence'
        interface=matches[-1]
        links=json.loads(ue.run(['ip','-j','-4','addr','show','dev',interface]))
        assert any(a['local']==address for x in links for a in x.get('addr_info',[]))
        result['session']={'supi':supi,'interface':interface,'address':address,'pdu':internet[0]}
        result['accounts_before']=accounts(core)
        account=next(a for a in result['accounts_before'] if a['supi']==supi)
        available=account['quota_bytes']-account['consumed_bytes']-account['reserved_bytes']
        assert available>=22000000, 'Insufficient unreserved quota for bounded campaign'
        result['health_before']=api(core,token,'GET','/health')
        result['ledger_before']=api(core,token,'GET','/management/v1/closed-loop/history')
        assert result['health_before']['closed_loop_enabled']
        remote=core.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        result['remote']=remote
        upath=ue.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        # Core route is narrowly scoped to this UE and independently expires.
        assert not core.run(['ip','route','show','exact',address+'/32']).strip()
        core.run(['systemd-run','--unit='+tag+'-route','--on-active=110s','/usr/sbin/ip','route','del',address+'/32','via','10.210.50.8'],sudo=True)
        core.run(['ip','route','add',address+'/32','via','10.210.50.8'],sudo=True)
        routes.append(('core',['ip','route','del',address+'/32','via','10.210.50.8'],tag+'-route.timer'))
        # The VM contains stale TUNs with duplicate IPs. An owned table/rule
        # pins only this test destination/source to the primary service's TUN.
        table=str(50000+int(uuid.uuid4().hex[:4],16)%9000)
        all_routes=json.loads(ue.run(['ip','-j','route','show','table','all']))
        assert not any(str(r.get('table'))==table for r in all_routes)
        rules=json.loads(ue.run(['ip','-j','rule','show']))
        assert not any(str(r.get('table'))==table or r.get('priority')==105 for r in rules)
        cleanup='#!/bin/sh\nip rule del priority 105 from '+address+'/32 to 10.210.50.1/32 table '+table+' 2>/dev/null\nip route del 10.210.50.1/32 dev '+interface+' table '+table+' 2>/dev/null\n'
        ue.write(upath+'/cleanup.sh',cleanup,0o700)
        ue.run(['systemd-run','--unit='+tag+'-ue-route','--on-active=110s','/bin/sh',upath+'/cleanup.sh'],sudo=True)
        ue.run(['ip','route','add','10.210.50.1/32','dev',interface,'src',address,'table',table],sudo=True)
        routes.append(('ue',['/bin/sh',upath+'/cleanup.sh'],tag+'-ue-route.timer'))
        ue.run(['ip','rule','add','priority','105','from',address+'/32','to','10.210.50.1/32','table',table],sudo=True)
        assert not core.run(['ss','-H','-lnt','sport = :5209']).strip()
        for suffix,command in [
            ('capture',['tcpdump','-i','any','-U','-s','0','-w',remote+'/control.pcap','tcp port 8085 or tcp port 7777 or udp port 8805']),
            ('server',['iperf3','-s','-B','10.210.50.1','-p','5209'])]:
            unit=tag+'-'+suffix
            core.run(['systemd-run','--unit='+unit,'--collect','--property=RuntimeMaxSec=100','--property=KillSignal=SIGINT','--property=TimeoutStopSec=5',*command],sudo=True)
            units.append(('core',unit))
        # Capture the native client's new HTTP/2 connection, including preface
        # and HPACK state, without restarting PCF or losing its session state.
        core.run(['systemctl','restart','maestro-nwdaf'],sudo=True)
        time.sleep(2)
        pid=core.run(['systemctl','show','open5gs-pcfd','--property=MainPID','--value']).strip()
        result['pcf_before']=core.run(['cat','/proc/'+pid+'/stat','/proc/'+pid+'/status'])
        result['pcf_cpu_started']=time.monotonic()
        result['clock_ticks']=int(core.run(['getconf','CLK_TCK']).strip())
        previous,previous_rx=counters(upf,ue,interface)

        def sample(phase):
            nonlocal previous,previous_rx
            current,rx=counters(upf,ue,interface)
            if current['uptime']-previous['uptime']<0.5:
                time.sleep(0.5);current,rx=counters(upf,ue,interface)
            body={'object_id':'nf:upf-01','before':previous,'after':current}
            if args.external_pm:
                duration=current['uptime']-previous['uptime']
                assert current['boot_id']==previous['boot_id'] and current['ifindex']==previous['ifindex'] and duration>0
                assert current['tx_bytes']>=previous['tx_bytes']
                bps=(current['tx_bytes']-previous['tx_bytes'])*8/duration
                published={'observed_bps':bps,'observed_percentage_unclipped':bps/20000000*100,
                           'status':'independent_measurement_external_publisher','capacity_units':20000000}
            else:
                published=api(core,token,'POST','/management/v1/pm-observations',body)
            received=(rx['rx_bytes']-previous_rx['rx_bytes'])*8/(rx['local_monotonic']-previous_rx['local_monotonic'])
            result['observations'].append({'phase':phase,'input':body,'published':published,'ue_bps':received,'ue_counter':rx})
            previous,previous_rx=current,rx
            print(json.dumps({'phase':phase,'offered_observed_bps':round(published['observed_bps']),'ue_bps':round(received)}),flush=True)

        def transfer(seconds,phase):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(ue.run,['timeout',str(seconds+5),'iperf3','--connect-timeout','3000','-c','10.210.50.1','-p','5209','-B',address,'-R','-u','-b','19M','-l','1200','-t',str(seconds),'-J'],timeout=seconds+10,check=False)
                while not future.done():
                    time.sleep(0.7);sample(phase)
                raw=future.result()
            (local/(phase+'-iperf.json')).write_text(raw,encoding='utf-8')
            parsed=json.loads(raw)
            result['traffic'].append({'phase':phase,'result':parsed})
            assert not parsed.get('error'),parsed.get('error')

        time.sleep(1);sample('idle')
        transfer(10,'overload')
        for _ in range(13):
            time.sleep(0.7);sample('recovery')
        result['ledger_recovery']=api(core,token,'GET','/management/v1/closed-loop/history')
        transfer(2,'restored-probe')
        # A restored-rate probe can legitimately retrigger the controller.
        # Keep real idle observations and capture beyond the 10s residence time.
        for _ in range(18):
            time.sleep(0.7);sample('final-idle')
        result['pcf_after']=core.run(['cat','/proc/'+pid+'/stat','/proc/'+pid+'/status'])
        result['pcf_cpu_elapsed']=time.monotonic()-result['pcf_cpu_started']
        result['ledger_after']=api(core,token,'GET','/management/v1/closed-loop/history')
        result['accounts_after']=accounts(core)
        result['quota_unchanged']={a['supi']:a['quota_bytes'] for a in result['accounts_before']}=={a['supi']:a['quota_bytes'] for a in result['accounts_after']}
        (local/'pcf.log').write_text(core.run(['tail','-n','180','/var/log/open5gs/pcf.log'],sudo=True),encoding='utf-8')
        result['status']='MEASURED'
    except Exception as exc:
        result['status']='FAILED';result['error']=str(exc)
        raise
    finally:
        result['cleanup']={}
        for name,unit in reversed(units):
            try:hosts[name].run(['systemctl','stop',unit],sudo=True);result['cleanup'][unit]='stopped'
            except Exception:result['cleanup'][unit]='independent runtime limit remains'
        for name,command,timer in reversed(routes):
            try:
                hosts[name].run(command,sudo=True)
                hosts[name].run(['systemctl','stop',timer],sudo=True)
                result['cleanup'][timer]='route removed'
            except Exception:result['cleanup'][timer]='independent timer remains'
        if remote:
            try:
                hosts['core'].run(['chown',settings.ssh_user,remote+'/control.pcap'],sudo=True)
                hosts['core'].run(['chmod','600',remote+'/control.pcap'],sudo=True)
                data=hosts['core'].read(remote+'/control.pcap')
                (local/'control.pcap').write_bytes(data)
                result['pcap_sha256']=hashlib.sha256(data).hexdigest()
            except Exception as exc:result['cleanup']['pcap_export_error']=type(exc).__name__
        for host in hosts.values():host.client.close()
        (local/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({'run':tag,'evidence':str(local),'status':result.get('status'),'cleanup':result['cleanup']}),flush=True)

if __name__=='__main__':main()
