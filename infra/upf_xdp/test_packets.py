#!/usr/bin/env python3
"""Kernel BPF_PROG_TEST_RUN tests, using separate maps and no live attachment."""
import argparse
import json
import shutil
import socket
import struct
import subprocess
from pathlib import Path
from scapy.all import Ether, IP, UDP, TCP, ICMP, Raw
from scapy.utils import checksum
from scapy.layers.inet import in4_chksum
import upf_xdp_agent as agent

ROOT = Path('/sys/fs/bpf/upf_xdp_test')
WORK = Path('evidence/packet-tests')
assert not ROOT.exists(), 'Test pins already exist; inspect before retrying'
ROOT.mkdir(); (ROOT / 'maps').mkdir(); WORK.mkdir(exist_ok=True)
subprocess.run(['bpftool', 'prog', 'load', 'upf_xdp_kern.o', str(ROOT / 'prog'), 'type', 'xdp', 'pinmaps', str(ROOT / 'maps')], check=True)
agent.ROOT = ROOT
results = []


def test(name, packet, ingress, expected, verify=None):
    raw = bytes(packet)
    (WORK / 'input.bin').write_bytes(raw)
    (WORK / 'ctx.bin').write_bytes(struct.pack('<6I', 0, len(raw), 0, socket.if_nametoindex(ingress), 0, 0))
    cmd = ['bpftool', '-j', 'prog', 'run', 'pinned', str(ROOT / 'prog'),
        'data_in', str(WORK / 'input.bin'), 'data_out', str(WORK / 'output.bin'),
        'ctx_in', str(WORK / 'ctx.bin'), 'repeat', '1']
    result = json.loads(subprocess.check_output(cmd, text=True))
    assert result['retval'] == expected, (name, result)
    output = (WORK / 'output.bin').read_bytes()
    if verify: verify(Ether(output))
    if expected == 2: assert output == raw, name + ': fallback modified packet'
    results.append(dict(name=name, verdict=result['retval']))


def check_ip(ip):
    assert checksum(bytes(ip)[:20]) == 0
    if ip.proto in (6, 17):
        assert in4_chksum(ip.proto, ip, bytes(ip.payload)) == 0


try:
    args = argparse.Namespace(pcap='evidence/session.pcap', ue='10.45.0.83', nat='10.0.2.16',
        n3='enp0s8', n6='enp0s3', lease=60, output='evidence/test-session.json')
    agent.register(args); agent.toggle(1)
    s = agent.decode_capture(args.pcap, args.ue)
    ul = s['ul']; dl = s['dl']

    def uplink(inner, teid=ul['teid'], extension=True, source=ul['src']):
        body = bytes(inner)
        extra = bytes.fromhex('0000008501100100') if extension else b''
        gtp = struct.pack('!BBHI', 0x34 if extension else 0x30, 255, len(extra)+len(body), teid)
        return Ether(src=ul['mac_src'], dst=ul['mac_dst'])/IP(src=source,dst=ul['dst'])/UDP(sport=2152,dport=2152)/Raw(gtp+extra+body)

    for protocol, l4 in [('icmp', ICMP()), ('tcp', TCP(sport=12345, dport=443)), ('udp', UDP(sport=12345,dport=53))]:
        inner = IP(src=args.ue,dst='1.1.1.1')/l4/Raw(b'test-payload')
        def check_ul(p):
            assert p[IP].src == args.nat and p[IP].dst == '1.1.1.1' and p[IP].ttl == 63
            check_ip(p[IP])
        test('ul-'+protocol, uplink(inner), 'enp0s8', 4, check_ul)
        response = Ether()/IP(src='1.1.1.1',dst=args.nat)/l4/Raw(b'test-payload')
        def check_dl(p):
            assert p[IP].src == ul['dst'] and p[IP].dst == ul['src']
            check_ip(p[IP]) if p[UDP].chksum else None
            g = bytes(p[UDP].payload)
            assert int.from_bytes(g[4:8], 'big') == dl['teid'] and g[14] == dl['qfi']
            inner = IP(g[16:])
            assert inner.dst == args.ue and inner.ttl == 63
            check_ip(inner)
        test('dl-'+protocol,response,'enp0s3',4,check_dl)
    valid = uplink(IP(src=args.ue,dst='1.1.1.1')/ICMP()/Raw(b'abcd'))
    test('base-gtp-no-extension',uplink(IP(src=args.ue,dst='1.1.1.1')/ICMP(),extension=False),'enp0s8',4)
    test('unknown-teid',uplink(IP(src=args.ue,dst='1.1.1.1')/ICMP(),teid=0xffffffff),'enp0s8',2)
    test('wrong-gnb',uplink(IP(src=args.ue,dst='1.1.1.1')/ICMP(),source='10.210.50.99'),'enp0s8',2)
    test('spoofed-ue',uplink(IP(src='10.45.0.99',dst='1.1.1.1')/ICMP()),'enp0s8',2)
    test('wrong-ingress',valid,'enp0s3',2)
    test('ttl-expired',uplink(IP(src=args.ue,dst='1.1.1.1',ttl=1)/ICMP()),'enp0s8',2)
    test('fragment',uplink(IP(src=args.ue,dst='1.1.1.1',flags='MF')/ICMP()),'enp0s8',2)
    test('private-destination',uplink(IP(src=args.ue,dst='10.45.0.1')/ICMP()),'enp0s8',2)
    raw = bytes(valid)
    for length in range(14, len(raw)):
        test('truncated-'+str(length),raw[:length],'enp0s8',2)
    bad = bytearray(raw); bad[54] = 0
    test('zero-extension-length',bad,'enp0s8',2)
    agent.toggle(0)
    test('disabled',valid,'enp0s8',2)
    print(json.dumps(dict(passed=len(results),cases=results), indent=2))
    Path('evidence/packet-tests.json').write_text(json.dumps(dict(passed=len(results),cases=results),indent=2))
finally:
    shutil.rmtree(ROOT)
