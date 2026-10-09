"""Actual generic XDP redirects in three disposable namespaces, never the lab NFs.

Run as root on Linux, with the compiled urllc_xdp_kern.o alongside this file.
Proves delivery separately from BPF_PROG_TEST_RUN's redirect return value.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time
import uuid

import urllc_xdp_agent as agent


CLIENT = r'''
import json,socket,struct
from scapy.all import Ether,IP,UDP,Raw,sendp
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.bind(('10.210.50.10',2152));s.settimeout(2)
inner=bytes(IP(src='10.47.0.3',dst='172.31.48.2')/UDP(sport=40001,dport=8765)/Raw(b'maestro-xdp-canary'))
ext=bytes([0,0,0,0x85,1,0x10,1,0])
gtp=struct.pack('!BBHI',0x34,255,len(ext)+len(inner),1234)+ext+inner
import sys
sendp(Ether(dst=sys.argv[1])/IP(src='10.210.50.10',dst='10.210.50.22')/UDP(sport=2152,dport=2152)/Raw(gtp),iface='gnb0',verbose=False)
reply=s.recv(2048)
assert int.from_bytes(reply[4:8],'big')==5678
p=IP(reply[16:]);assert p.src=='172.31.48.2' and p.dst=='10.47.0.3'
assert bytes(p[UDP].payload)==b'maestro-xdp-canary'
print(json.dumps({'delivery':'PASS','ul_teid':1234,'dl_teid':5678,'payload':len(bytes(p[UDP].payload))}))
'''


def main():
    assert os.geteuid()==0
    token='mxt'+uuid.uuid4().hex[:6]
    names=[token+x for x in ('u','g','m')]
    upf,gnb,mec=names
    root=Path('/run/maestro-bpf')/token
    processes=[];created=[]
    def run(*args):return agent.command(*args)
    def inside(ns,*args):return run('ip','netns','exec',ns,*args)
    try:
        for ns in names:
            run('ip','netns','add',ns);created.append(ns)
            inside(ns,'ip','link','set','lo','up')
        for a,b,peer,ip1,ip2 in [('n3','gnb0',gnb,'10.210.50.22/24','10.210.50.10/24'),
                                 ('n6','mec0',mec,'172.31.48.1/30','172.31.48.2/30')]:
            inside(upf,'ip','link','add',a,'type','veth','peer','name',b)
            inside(upf,'ip','link','set',b,'netns',peer)
            for ns,dev,address in [(upf,a,ip1),(peer,b,ip2)]:
                inside(ns,'ip','address','add',address,'dev',dev)
                inside(ns,'ip','link','set',dev,'up')
        inside(mec,'ip','route','add','10.47.0.0/16','via','172.31.48.1')
        def link(ns,dev):return json.loads(inside(ns,'ip','-j','link','show',dev))[0]
        n3,n6,g,m=link(upf,'n3'),link(upf,'n6'),link(gnb,'gnb0'),link(mec,'mec0')
        (root/'maps').mkdir(parents=True)
        agent.ROOT=root
        bpftools=sorted(Path('/usr/lib/linux-tools').glob('*/bpftool'))
        inside(upf,str(bpftools[-1]) if bpftools else '/usr/sbin/bpftool',
               'prog','load',str(Path(__file__).with_name('urllc_xdp_kern.o')),str(root/'prog'),
               'type','xdp','pinmaps',str(root/'maps'))
        ip=socket.inet_aton
        mac=lambda x:bytes.fromhex(x['address'].replace(':',''))
        value=(ip('10.47.0.3')+bytes(4)+struct.pack('!II',1234,5678)+ip('10.210.50.10')+ip('10.210.50.22')+
               struct.pack('<III',n3['ifindex'],n6['ifindex'],1400)+mac(n3)+mac(g)+mac(n6)+mac(m)+
               bytes([1,0,0,0])+struct.pack('<Q',time.monotonic_ns()+10_000_000_000))
        agent.update('sessions_uplink_map',struct.pack('!I',1234),value)
        agent.update('sessions_downlink_map',ip('10.47.0.3'),value)
        for dev in ('n3','n6'):inside(upf,'ip','link','set',dev,'xdpgeneric','pinned',str(root/'prog'))
        echo="import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('172.31.48.2',8765));data,peer=s.recvfrom(2048);s.sendto(data,peer)"
        p=subprocess.Popen(['ip','netns','exec',mec,'python3','-c',echo]);processes.append(p)
        time.sleep(.2);agent.flag(1)
        result=json.loads(inside(gnb,'python3','-c',CLIENT,n3['address']))
        p.wait(timeout=2)
        result['counters']=agent.counters()
        assert all(result['counters'][d+'_redirect_requested']['packets']==1 for d in ('ul','dl'))
        agent.flag(0)
        print(json.dumps(result))
    finally:
        if (root/'maps/enabled').exists():agent.flag(0)
        for p in processes:
            if p.poll() is None:p.terminate();p.wait(timeout=2)
        for ns in reversed(created):run('ip','netns','delete',ns)
        if root.exists():shutil.rmtree(root)


if __name__=='__main__':main()
