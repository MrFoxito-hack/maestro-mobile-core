"""BPF_PROG_TEST_RUN only; never attaches a hook or changes a live map."""
import json
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import time
import uuid
from scapy.all import Ether, IP, UDP, ICMP, Raw
from scapy.utils import checksum
from scapy.layers.inet import in4_chksum
import urllc_xdp_agent as agent


def main():
    root = Path('/run/maestro-bpf/test_' + uuid.uuid4().hex[:8])
    (root/'maps').mkdir(parents=True)
    agent.ROOT = root
    results = []
    try:
        agent.command('bpftool','prog','load',str(Path(__file__).with_name('urllc_xdp_kern.o')),
                      str(root/'prog'),'type','xdp','pinmaps',str(root/'maps'))
        ip = socket.inet_aton
        n3,n6 = socket.if_nametoindex(agent.N3),socket.if_nametoindex(agent.N6)
        def install(expiry):
            value=(ip('10.47.0.3')+bytes(4)+struct.pack('!II',1234,5678)+ip('10.210.50.10')+ip('10.210.50.22')+
                   struct.pack('<III',n3,n6,1400)+bytes.fromhex('020000000001020000000002020000000003020000000004')+
                   bytes([1,0,0,0])+struct.pack('<Q',expiry))
            agent.update('sessions_uplink_map',struct.pack('!I',1234),value)
            agent.update('sessions_downlink_map',ip('10.47.0.3'),value)
        install(time.monotonic_ns()+60_000_000_000);agent.flag(1)
        with tempfile.TemporaryDirectory(prefix='urllc-packets-') as directory:
            work=Path(directory)
            def test(name,packet,ingress,expected,verify=None):
                raw=bytes(packet);(work/'in').write_bytes(raw)
                (work/'ctx').write_bytes(struct.pack('<6I',0,len(raw),0,ingress,0,0))
                r=json.loads(agent.command('bpftool','-j','prog','run','pinned',str(root/'prog'),
                    'data_in',str(work/'in'),'data_out',str(work/'out'),'ctx_in',str(work/'ctx'),'repeat','1'))
                assert r['retval']==expected,(name,r)
                out=(work/'out').read_bytes()
                if expected==2: assert out==raw,(name,'PASS mutated the packet')
                if verify:verify(Ether(out))
                results.append(name)
            def ul(inner,teid=1234,qfi=1,extension=0x85,source='10.210.50.10'):
                ext=bytes([0,0,0,extension,1,0x10,qfi,0])
                gtp=struct.pack('!BBHI',0x34,255,len(ext)+len(bytes(inner)),teid)
                return Ether()/IP(src=source,dst='10.210.50.22')/UDP(sport=2152,dport=2152)/Raw(gtp+ext+bytes(inner))
            def valid_ip(p):
                assert checksum(bytes(p)[:20])==0
                if p.proto==17 and p[UDP].chksum: assert in4_chksum(17,p,bytes(p.payload))==0
            def check_ul(p):
                assert p[IP].src=='10.47.0.3' and p[IP].dst=='172.31.48.2' and p[IP].ttl==63
                valid_ip(p[IP])
            def check_dl(p):
                assert p[IP].src=='10.210.50.22' and p[IP].dst=='10.210.50.10'
                valid_ip(p[IP]);g=bytes(p[UDP].payload)
                assert int.from_bytes(g[4:8],'big')==5678 and g[14]==1
                inner=IP(g[16:]);assert inner.dst=='10.47.0.3' and inner.ttl==63
                valid_ip(inner)
            packet=ul(IP(src='10.47.0.3',dst='172.31.48.2')/UDP(sport=40001,dport=8765)/Raw(b'x'*64))
            test('UL UDP MEC',packet,n3,4,check_ul)
            test('DL UDP MEC',Ether()/IP(src='172.31.48.2',dst='10.47.0.3')/UDP(sport=8765,dport=40001)/Raw(b'x'*64),n6,4,check_dl)
            test('UL echo MEC',ul(IP(src='10.47.0.3',dst='172.31.48.2')/ICMP()),n3,4,check_ul)
            test('DL echo MEC',Ether()/IP(src='172.31.48.2',dst='10.47.0.3')/ICMP(type=0),n6,4,check_dl)
            for name,inner,kwargs in [
                ('eMBB source',IP(src='10.45.0.3',dst='172.31.48.2')/ICMP(),{}),
                ('MIoT destination',IP(src='10.47.0.3',dst='10.46.0.1')/ICMP(),{}),
                ('gateway control',IP(src='10.47.0.3',dst='10.47.0.1')/ICMP(),{}),
                ('unapproved port',IP(src='10.47.0.3',dst='172.31.48.2')/UDP(dport=53),{}),
                ('wrong QFI',IP(src='10.47.0.3',dst='172.31.48.2')/ICMP(),{'qfi':2}),
                ('wrong TEID',IP(src='10.47.0.3',dst='172.31.48.2')/ICMP(),{'teid':999}),
                ('unknown extension',IP(src='10.47.0.3',dst='172.31.48.2')/ICMP(),{'extension':0x84}),
                ('fragment',IP(src='10.47.0.3',dst='172.31.48.2',flags='MF')/ICMP(),{}),
                ('expired TTL',IP(src='10.47.0.3',dst='172.31.48.2',ttl=1)/ICMP(),{}),
                ('oversize',IP(src='10.47.0.3',dst='172.31.48.2')/ICMP()/Raw(b'x'*1400),{}),
            ]:test(name,ul(inner,**kwargs),n3,2)
            test('wrong ingress',packet,n6,2)
            test('spoofed MEC',Ether()/IP(src='10.46.0.1',dst='10.47.0.3')/ICMP(type=0),n6,2)
            # TEST_RUN itself requires an Ethernet header (14 bytes).
            for size in range(14,len(bytes(packet))):test('truncated '+str(size),bytes(packet)[:size],n3,2)
            install(time.monotonic_ns()-1);test('expired lease',packet,n3,2)
            agent.flag(0);test('disabled',packet,n3,2)
            assert agent.flag()==0
            counts=agent.counters()
            assert counts['ul_redirect_requested']['packets']==2
            assert counts['dl_redirect_requested']['packets']==2
            agent.clear_sessions();agent.flag(1)
            test('revoked maps stay empty on re-enable',packet,n3,2)
        print(json.dumps({'passed':len(results),'cases':results}))
    finally:
        shutil.rmtree(root)


if __name__=='__main__':main()
