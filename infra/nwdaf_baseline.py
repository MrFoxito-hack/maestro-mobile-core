"""Bounded real downlink baseline through an observed internet PDU tunnel.

Measures UPF-to-UE tunnel throughput, NOT physical N6 link capacity. Does not
change QoS, quota, APN, or firewall. Temporary iperf server auto-expires.
"""
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    settings = get_settings()
    n6 = '--n6' in sys.argv
    ue, upf = Lab(settings,settings.ue_ssh_port), Lab(settings,settings.ssh_port if n6 else settings.upf_ssh_port)
    server_address = '10.210.50.1' if n6 else '10.45.0.1'
    tag = 'nwdaf-baseline-'+uuid.uuid4().hex[:8]
    result = {'scope':'N6 application-to-UE path; achieved throughput, not link capacity' if n6 else 'UPF-to-UE tunnel, not N6 capacity', 'trials':[]}
    started = False
    temporary_route = False
    address = None
    try:
        interfaces = json.loads(ue.run(['ip','-j','addr','show']))
        candidates = [(x['ifname'],a['local']) for x in interfaces if x['ifname'].startswith('uesimtun')
                      for a in x.get('addr_info',[]) if a['family']=='inet' and a['local'].startswith('10.45.')]
        if not candidates: raise RuntimeError('No observed internet PDU IP')
        interface, address = sorted(candidates)[0]
        result.update(interface=interface,address=address)
        if n6:
            existing = upf.run(['ip','route','show','exact',address+'/32']).strip()
            if existing: raise RuntimeError('Existing host route: inspect before benchmark')
            # Specific route only; independent cleanup survives SSH/controller loss.
            upf.run(['systemd-run','--unit='+tag+'-route-cleanup','--on-active=90s',
                     '/usr/sbin/ip','route','del',address+'/32','via','10.210.50.8'],sudo=True)
            upf.run(['ip','route','add',address+'/32','via','10.210.50.8'],sudo=True)
            temporary_route = True
            result['temporary_route'] = address+'/32 via 10.210.50.8'
        for host in (ue,upf):
            host.run(['env','DEBIAN_FRONTEND=noninteractive','apt-get','install','-y','iperf3'],sudo=True,timeout=180)
        upf.run(['systemd-run','--unit='+tag,'--collect','--property=RuntimeMaxSec=120',
                 '/usr/bin/iperf3','-s','-B',server_address,'-p','5209'],sudo=True)
        started = True
        # First attempt may race the transient server start; no traffic/QoS change
        # on retry, and each stream is independently bounded by iperf's duration.
        for _ in range(3):
            raw = ue.run(['timeout','15','iperf3','--connect-timeout','3000','-c',server_address,'-p','5209','-B',address,'-R','-t','8','-J'],timeout=20,check=False)
            trial = json.loads(raw)
            result['trials'].append(trial)
            if trial.get('error'): raise RuntimeError(trial['error'])
        result['status'] = 'measured'
    finally:
        if started: upf.run(['systemctl','stop',tag],sudo=True,check=False)
        if temporary_route:
            upf.run(['ip','route','del',address+'/32','via','10.210.50.8'],sudo=True,check=False)
            upf.run(['systemctl','stop',tag+'-route-cleanup.timer'],sudo=True,check=False)
        ue.client.close(); upf.client.close()
        target = ROOT/'.work'/f'{tag}.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({'evidence':str(target), 'scope':result['scope'],
                         'rates_bps':[t.get('end',{}).get('sum_received',{}).get('bits_per_second') for t in result['trials']]}))


if __name__ == '__main__': main()
