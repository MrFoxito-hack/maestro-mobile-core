"""Bounded real-counter bridge for unattended PCF control; no traffic generation."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from nwdaf_closed_loop_campaign import api

COUNTERS="""import json,pathlib,time
p=pathlib.Path('/sys/class/net/ogstun')
print(json.dumps(dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
ifindex=int((p/'ifindex').read_text()),tx_bytes=int((p/'statistics/tx_bytes').read_text()),
uptime=float(pathlib.Path('/proc/uptime').read_text().split()[0]),timestamp=time.time())))
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seconds',type=int,default=50)
    args=parser.parse_args();assert 2<=args.seconds<=50
    settings=get_settings();hosts={};previous={};count=0;started=time.monotonic()
    status={'started':datetime.now(timezone.utc).isoformat(),'status':'starting'}
    path=ROOT/'.work/nwdaf-fast-pm-status.json';path.parent.mkdir(exist_ok=True)
    try:
        hosts['core']=Lab(settings,settings.ssh_port)
        token=hosts['core'].read(settings.nwdaf_token_file).decode().strip()
        for name,port,address in [('upf-01',settings.upf_ssh_port,'10.45.0.1'),('upf-02',settings.upf2_ssh_port,'10.46.0.1')]:
            hosts[name]=Lab(settings,port)
            interfaces=json.loads(hosts[name].run(['ip','-j','-4','addr','show','dev','ogstun']))
            assert any(a.get('local')==address for i in interfaces for a in i.get('addr_info',[])), 'UPF identity mismatch'
        while time.monotonic()-started<args.seconds:
            for name in ('upf-01','upf-02'):
                current=json.loads(hosts[name].run(['python3','-c',COUNTERS],timeout=5))
                if name in previous:
                    status['last_input']={'object_id':'nf:'+name,'before':previous[name],'after':current}
                    status['core_clock']=float(hosts['core'].run(['python3','-c','import time; print(time.time())']).strip())
                    ahead=current['timestamp']-status['core_clock']
                    if ahead>0.5:
                        raise ValueError('UPF wall clock is more than 0.5s ahead of core')
                    if ahead>=0:
                        time.sleep(ahead+0.05)  # preserve source timestamp, never rewrite clocks
                    evidence=api(hosts['core'],token,'POST','/management/v1/pm-observations',
                        {'object_id':'nf:'+name,'before':previous[name],'after':current})
                    count+=1;status[name]={'bps':evidence['observed_bps'],'timestamp':current['timestamp'],'input_hash':evidence['input_hash']}
                previous[name]=current
            status.update(status='active',published=count,updated=datetime.now(timezone.utc).isoformat())
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(status,indent=2));temporary.replace(path)
            time.sleep(1)
    except Exception as exc:
        status.update(status='failed',error=type(exc).__name__)
        raise
    finally:
        for host in hosts.values():host.client.close()
        status.update(finished=datetime.now(timezone.utc).isoformat(),published=count)
        if status['status']=='active':status['status']='completed'
        path.write_text(json.dumps(status,indent=2))
        print(json.dumps(status),flush=True)

if __name__=='__main__':main()
