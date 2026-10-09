"""Control for generator-induced latency: same streams, local UDP echo only."""
import json
from datetime import datetime, timezone
from c8_remote import ROOT, CAMPAIGN_ROOT, LoggedLab, get_settings


def main():
    out = CAMPAIGN_ROOT/'setup/oe4'
    h = LoggedLab(get_settings(), 2226, out)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    unit = 'c8-loop-'+stamp.lower()
    echo = '''import socket,selectors
s=selectors.DefaultSelector()
for port in [38765,38766,38767]:
 u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);u.bind(('127.0.0.1',port));s.register(u,selectors.EVENT_READ)
while True:
 for k,_ in s.select():
  d,a=k.fileobj.recvfrom(65535);k.fileobj.sendto(d,a)
'''
    try:
        remote = h.run(['mktemp','-d','/home/emsadmin/c8-loop-XXXXXX']).strip()
        h.write(remote+'/traffic.py',(ROOT/'infra/c8_traffic.py').read_bytes())
        config = {'duration_s':10,'seed':42017,'streams':[
            {'slice':kind,'interface':'lo','source':'127.0.0.1','target':'127.0.0.1',
             'port':port,'payload_bytes':size,'pps':pps,'sensors':1}
            for kind,port,size,pps in [('urllc',38765,64,100),('embb',38766,1200,500),('miot',38767,64,500)]]}
        h.write(remote+'/config.json',json.dumps(config))
        h.run(['systemd-run','--unit='+unit,'--property=RuntimeMaxSec=60','python3','-c',echo],sudo=True)
        print(h.run(['python3',remote+'/traffic.py',remote+'/config.json',remote+'/raw.json'],sudo=True,timeout=30))
        raw=h.read(remote+'/raw.json');(out/f'loopback-{stamp}.json').write_bytes(raw)
        for stream in json.loads(raw)['streams']:
            values=[p['rtt_ms'] for p in stream['packets'] if p['ack']]
            print(stream['spec']['slice'],len(values),sum(values)/len(values))
    finally:
        h.run(['systemctl','stop',unit],sudo=True,check=False)
        h.client.close()


if __name__=='__main__':main()
