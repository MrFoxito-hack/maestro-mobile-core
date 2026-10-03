#!/usr/bin/env python3
"""Explicit, leased UE session registration from a bidirectional GTP capture.

No PFCP/QER/URR policy synchronization is claimed. Use only in a bounded lab run.
"""
import argparse
import contextlib
import io
import json
import socket
import struct
import subprocess
import time
from pathlib import Path

ROOT = Path('/sys/fs/bpf/upf_xdp')
STATE = Path(__file__).resolve().parent / 'current-session.json'
NAMES = ['ul_redirect_requested', 'dl_redirect_requested', 'pass', 'unknown_session',
         'invalid', 'adjust_failed', 'expired', 'unsupported']


def run(*args):
    return subprocess.check_output(list(args), text=True)


def update(name, key, value):
    run('bpftool', 'map', 'update', 'pinned', str(ROOT / 'maps' / name),
        'key', 'hex', *[f'{b:02x}' for b in key], 'value', 'hex',
        *[f'{b:02x}' for b in value])


def toggle(active):
    update('enabled', bytes(4), struct.pack('<I', active))


def decode_capture(path, ue):
    from scapy.all import rdpcap, IP, UDP, Ether
    found = {}
    for p in rdpcap(path):
        if IP not in p or UDP not in p or p[UDP].dport != 2152:
            continue
        g = bytes(p[UDP].payload)
        if len(g) < 8 or g[1] != 255:
            continue
        off, qfi = 8, 0
        if g[0] & 7:
            if len(g) < 12:
                continue
            off, nxt = 12, g[11]
            while nxt and off + 4 <= len(g):
                size = g[off] * 4
                if size < 4 or off + size > len(g):
                    raise ValueError('Invalid GTP extension')
                if nxt == 0x85:
                    qfi = g[off + 2] & 63
                nxt = g[off + size - 1]
                off += size
        inner = IP(g[off:])
        direction = 'ul' if inner.src == ue else 'dl' if inner.dst == ue else None
        if direction:
            found[direction] = dict(teid=int.from_bytes(g[4:8], 'big'),
                src=p[IP].src, dst=p[IP].dst, mac_src=p[Ether].src,
                mac_dst=p[Ether].dst, qfi=qfi)
    if set(found) != {'ul', 'dl'}:
        raise ValueError('Need current packets in BOTH directions for the selected UE')
    return found


def register(args):
    f = decode_capture(args.pcap, args.ue)
    ul, dl = f['ul'], f['dl']
    if ul['src'] != dl['dst'] or ul['dst'] != dl['src']:
        raise ValueError('Asymmetric tunnel endpoints')
    mac = lambda x: bytes.fromhex(x.replace(':', ''))
    ip = socket.inet_aton
    n3, n6 = socket.if_nametoindex(args.n3), socket.if_nametoindex(args.n6)
    route = json.loads(run('ip', '-j', 'route', 'get', '1.1.1.1'))[0]
    if route['dev'] != args.n6:
        raise ValueError('N6 is not the default egress')
    gateway = route['gateway']
    subprocess.run(['ping', '-c', '1', '-W', '1', gateway], stdout=subprocess.DEVNULL)
    neigh = json.loads(run('ip', '-j', 'neigh', 'show', gateway, 'dev', args.n6))
    gateway_mac = next(n['lladdr'] for n in neigh if n.get('lladdr'))
    n6_mac = Path('/sys/class/net/' + args.n6 + '/address').read_text().strip()
    n3_mtu = int(Path('/sys/class/net/' + args.n3 + '/mtu').read_text())
    value = (ip(args.ue) + ip(args.nat) + struct.pack('!II', ul['teid'], dl['teid']) +
        ip(ul['src']) + ip(ul['dst']) + struct.pack('<III', n3, n6, n3_mtu) +
        mac(ul['mac_dst']) + mac(ul['mac_src']) + mac(n6_mac) + mac(gateway_mac) +
        bytes([dl['qfi'], 0, 0, 0]) + struct.pack('<Q', time.monotonic_ns() + args.lease * 10**9))
    assert len(value) == 72
    toggle(0)
    # This controller owns a single-UE experiment. Retire old TEIDs/IPs first.
    for name in ['sessions_uplink_map', 'sessions_downlink_map', 'nat_downlink_map']:
        rows = json.loads(run('bpftool', '-j', 'map', 'dump', 'pinned', str(ROOT / 'maps' / name)))
        for row in rows:
            key = row['key']
            key = bytes(int(x, 16) for x in key) if isinstance(key, list) else struct.pack('<I', key)
            run('bpftool', 'map', 'delete', 'pinned', str(ROOT / 'maps' / name),
                'key', 'hex', *[f'{b:02x}' for b in key])
    update('sessions_uplink_map', struct.pack('!I', ul['teid']), value)
    update('sessions_downlink_map', ip(args.ue), value)
    if args.nat != '0.0.0.0':
        update('nat_downlink_map', ip(args.nat), value)
    evidence = dict(ue=args.ue, nat=args.nat, n3=args.n3, n6=args.n6,
                    lease_seconds=args.lease, expires_ns=struct.unpack('<Q', value[64:])[0],
                    n3_mtu=n3_mtu, observed=f, gateway=gateway, gateway_mac=gateway_mac)
    Path(args.output).write_text(json.dumps(evidence, indent=2) + '\n')
    if ROOT.name == 'upf_xdp':
        STATE.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


def stats():
    raw = json.loads(run('bpftool', '-j', 'map', 'dump', 'pinned', str(ROOT / 'maps/counters')))
    result = {}
    for i, row in enumerate(raw):
        key = row['key']
        idx = int.from_bytes(bytes(int(x, 16) for x in key), 'little') if isinstance(key, list) else key
        packets = size = 0
        for v in row['values']:
            val = v['value']
            if isinstance(val, dict):
                p, b = val['packets'], val['bytes']
            else:
                p, b = struct.unpack('<QQ', bytes(int(x, 16) for x in val))
            packets += p; size += b
        result[NAMES[idx]] = dict(packets=packets, bytes=size)
    print(json.dumps(result, indent=2))


def status():
    if not (ROOT / 'prog').exists():
        return dict(available=False, mode='legacy', reason='Programa XDP no cargado')
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    row = json.loads(run('bpftool', '-j', 'map', 'lookup', 'pinned', str(ROOT / 'maps/enabled'), 'key', '0', '0', '0', '0'))
    flag = row['value']
    if isinstance(flag, list): flag = int.from_bytes(bytes(int(x,16) for x in flag),'little')
    remaining = max(0, (state.get('expires_ns', 0) - time.monotonic_ns()) // 10**9)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): stats()
    return dict(available=True, mode='xdp' if flag and remaining else 'legacy',
        ue=state.get('ue'), lease_seconds=remaining, n3_mtu=state.get('n3_mtu'),
        driver_mode='generic', counters=json.loads(buf.getvalue()),
        reason=None if remaining else 'Registrar nuevamente una sesión observada')


def activate():
    current = status()
    if not current.get('lease_seconds'):
        raise ValueError('Session missing or expired: capture and register the current UE first')
    state = json.loads(STATE.read_text())
    if int(Path('/sys/class/net/' + state['n3'] + '/mtu').read_text()) != state['n3_mtu']:
        raise ValueError('N3 MTU changed: revalidate and register first')
    toggle(1)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['register', 'on', 'off', 'stats', 'status'])
    p.add_argument('--pcap')
    p.add_argument('--ue')
    p.add_argument('--nat', default='10.0.2.16')
    p.add_argument('--n3', default='enp0s8')
    p.add_argument('--n6', default='enp0s3')
    p.add_argument('--lease', type=int, default=3600)
    p.add_argument('--output', default='session.json')
    a = p.parse_args()
    if a.action == 'register':
        if not a.pcap or not a.ue: p.error('register requires --pcap and --ue')
        register(a)
    elif a.action == 'stats': stats()
    elif a.action == 'status': print(json.dumps(status()))
    elif a.action == 'on': activate()
    else: toggle(0)
