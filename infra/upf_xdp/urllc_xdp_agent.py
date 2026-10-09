#!/usr/bin/env python3
"""Namespace-scoped, leased, native-PFCP-authorized URLLC fast path.

Only this profile may bypass the UPF; it must be unmetered and have no URRs,
SDF rules, closed gates or bitrate QERs. Native UPF mutations clear the gate.
"""
import argparse
import contextlib
import fcntl
import grp
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time
import yaml

ROOT = Path('/run/maestro-bpf/urllc')
RUNTIME = Path('/run/maestro-urllc-xdp')
NAMESPACE = Path('/run/netns/maestro-urllc')
CONFIG = Path('/etc/open5gs/upf-urllc.yaml')
N3, N6, MEC = 'murllc-n3', 'murllc-mec', '172.31.48.2'
LEASE_NS = 3_000_000_000
REASONS = ['ul_redirect_requested', 'dl_redirect_requested', 'pass', 'unknown_session',
           'invalid', 'adjust_failed', 'expired', 'unsupported']


def command(*args):
    if args[0] == 'bpftool':
        candidates = sorted(Path('/usr/lib/linux-tools').glob('*/bpftool'))
        args = (str(candidates[-1]) if candidates else '/usr/sbin/bpftool', *args[1:])
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=8)


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    tmp = path.with_suffix('.agent-tmp')
    tmp.write_text(json.dumps(value))
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def namespace_check():
    if os.stat('/proc/self/ns/net').st_ino != NAMESPACE.stat().st_ino:
        raise ValueError('Wrong network namespace; refusing to access XDP')


@contextlib.contextmanager
def locked():
    namespace_check()
    with (RUNTIME / 'control.lock').open('a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def update(name, key, value):
    command('bpftool', 'map', 'update', 'pinned', str(ROOT/'maps'/name),
            'key', 'hex', *key.hex(' ').split(), 'value', 'hex', *value.hex(' ').split())


def flag(value=None):
    if not (ROOT/'maps/enabled').exists(): return 0
    if value is not None:
        update('enabled', bytes(4), struct.pack('<I', value))
        return value
    row = json.loads(command('bpftool','-j','map','lookup','pinned',str(ROOT/'maps/enabled'),
                             'key','hex','00','00','00','00'))['value']
    return int.from_bytes(bytes(int(x,16) for x in row),'little') if isinstance(row,list) else int(row)


def native():
    if yaml.safe_load(CONFIG.read_text())['upf'].get('charging_enforcement') is not False:
        raise ValueError('URLLC charging enforcement is not explicitly disabled')
    n = read(RUNTIME/'native.json')
    pid = int(n['pid']); proc = Path('/proc')/str(pid)
    if os.stat(proc/'ns/net').st_ino != NAMESPACE.stat().st_ino:
        raise ValueError('Native UPF namespace changed')
    if proc.joinpath('comm').read_text().strip() != 'open5gs-upfd':
        raise ValueError('Native publisher is not a UPF')
    args = proc.joinpath('cmdline').read_bytes().decode().split('\0')
    if '-c' not in args or args[args.index('-c')+1] != str(CONFIG):
        raise ValueError('Unknown native UPF configuration')
    cgroup = proc.joinpath('cgroup').read_text()
    if 'open5gs-upfd-urllc.service' not in cgroup:
        raise ValueError('Unexpected native UPF unit')
    if not n.get('eligible') or n.get('dnn') != '5g-plus' or n.get('urr_count') != 0:
        raise ValueError('Native PFCP policy is not eligible (URR/QER/SDF/session)')
    if ipaddress.ip_address(n['ue']) not in ipaddress.ip_network('10.47.0.0/16') or n['upf'] != '10.210.50.22':
        raise ValueError('Native UE/UPF outside URLLC profile')
    if not 1 <= n['qfi'] <= 63 or not n['ul_teid'] or not n['dl_teid']:
        raise ValueError('Incomplete native tunnel')
    return n


def interfaces():
    return [json.loads(command('ip','-j','-d','link','show','dev',d))[0] for d in (N3,N6)]


def own_hooks():
    if not (ROOT/'prog').exists(): return False
    prog = json.loads(command('bpftool','-j','prog','show','pinned',str(ROOT/'prog')))
    identity = (prog[0] if isinstance(prog,list) else prog)['id']
    return all(i.get('xdp',{}).get('prog',{}).get('id') == identity for i in interfaces())


def route_mac(target, dev):
    route = json.loads(command('ip','-j','route','get',target))[0]
    if route['dev'] != dev: raise ValueError('Unexpected route for '+target)
    gateway = route.get('gateway',target)
    rows = json.loads(command('ip','-j','neigh','show',gateway,'dev',dev))
    valid = [r for r in rows if r.get('lladdr') and not set(r.get('state',[])) & {'FAILED','INCOMPLETE'}]
    if len(valid) != 1: raise ValueError('Neighbor unresolved: '+gateway)
    return bytes.fromhex(valid[0]['lladdr'].replace(':',''))


def session_value(n, expiry):
    links = interfaces()
    if not all('UP' in x['flags'] and x['mtu'] >= 1444 for x in links):
        raise ValueError('URLLC links down or MTU too small')
    ip = socket.inet_aton
    mac = lambda dev: bytes.fromhex(next(x['address'] for x in links if x['ifname']==dev).replace(':',''))
    return (ip(n['ue'])+ip('0.0.0.0')+struct.pack('!II',n['ul_teid'],n['dl_teid'])+
            ip(n['gnb'])+ip(n['upf'])+struct.pack('<III',socket.if_nametoindex(N3),socket.if_nametoindex(N6),1400)+
            mac(N3)+route_mac(n['gnb'],N3)+mac(N6)+route_mac(MEC,N6)+bytes([n['qfi'],0,0,0])+struct.pack('<Q',expiry))


def install(n, expiry):
    value = session_value(n,expiry)
    assert len(value) == 72
    update('sessions_uplink_map',struct.pack('!I',n['ul_teid']),value)
    update('sessions_downlink_map',socket.inet_aton(n['ue']),value)


def clear_sessions():
    # Never re-enable a recently revoked session during its remaining lease.
    for name in ('sessions_uplink_map','sessions_downlink_map','nat_downlink_map'):
        path = str(ROOT/'maps'/name)
        rows = json.loads(command('bpftool','-j','map','dump','pinned',path))
        for row in rows:
            key = row['key']
            if not isinstance(key,list):
                raise ValueError('Expected raw BPF key encoding')
            command('bpftool','map','delete','pinned',path,'key','hex',*key)


def off(reason='operator'):
    flag(0)
    write(RUNTIME/'agent.json',{'mode':'kernel','reason':reason,'expires_ns':0})


def prepare():
    namespace_check()
    native()
    if ROOT.exists():
        if own_hooks(): return
        raise ValueError('Existing or incomplete deployment; inspect owned pins')
    if any(i.get('xdp',{}).get('attached') for i in interfaces()):
        raise ValueError('An interface already has XDP')
    (ROOT/'maps').mkdir(parents=True)
    try:
        command('bpftool','prog','load',str(Path(__file__).with_name('urllc_xdp_kern.o')),
                str(ROOT/'prog'),'type','xdp','pinmaps',str(ROOT/'maps'))
        os.chown(ROOT/'maps/enabled',0,grp.getgrnam('open5gs').gr_gid)
        os.chmod(ROOT/'maps/enabled',0o660)
        for dev in (N3,N6):
            command('ip','link','set','dev',dev,'xdpgeneric','pinned',str(ROOT/'prog'))
    except Exception:
        # Partial attaches remain in PASS (initial zero gate), never overwrite another hook.
        if (ROOT/'maps/enabled').exists(): flag(0)
        raise


def detach():
    """Remove our hooks only. Foreign programs and mounts are never touched."""
    with locked():
        off('operator detach')
        if not (ROOT/'prog').exists(): return
        data = json.loads(command('bpftool','-j','prog','show','pinned',str(ROOT/'prog')))
        identity = (data[0] if isinstance(data,list) else data)['id']
        for link in interfaces():
            if link.get('xdp',{}).get('prog',{}).get('id') == identity:
                command('ip','link','set','dev',link['ifname'],'xdpgeneric','off')
        for pin in (ROOT/'maps').iterdir(): pin.unlink()
        (ROOT/'maps').rmdir(); (ROOT/'prog').unlink(); ROOT.rmdir()


def activate(ue):
    with locked():
        n = native()
        if n['ue'] != ue: raise ValueError('UE changed since backend observation')
        heartbeat = read(RUNTIME/'watchdog.json')
        if time.monotonic_ns()-heartbeat['timestamp_ns'] > 2_000_000_000:
            raise ValueError('Watchdog unavailable')
        if not own_hooks(): raise ValueError('Both owned XDP hooks are required')
        flag(0)
        clear_sessions()
        expiry = time.monotonic_ns()+LEASE_NS
        install(n,expiry)
        # Only this generation has entries; all other traffic stays in the stack.
        write(RUNTIME/'agent.json',{'mode':'xdp','generation':n['generation'],
                                  'ue':n['ue'],'pid':n['pid'],'expires_ns':expiry,'reason':None,
                                  'confirmed':False,'confirmation_deadline_ns':time.monotonic_ns()+15_000_000_000})
        flag(1)


def confirm(ue, generation):
    with locked():
        n = native(); state = read(RUNTIME/'agent.json')
        if (n['ue'] != ue or n['generation'] != generation or state.get('generation') != generation or
            state.get('mode') != 'xdp' or not flag() or not own_hooks() or
            state['expires_ns'] <= time.monotonic_ns() or
            state['confirmation_deadline_ns'] <= time.monotonic_ns()):
            raise ValueError('Activation expired or native session changed')
        state['confirmed'] = True
        write(RUNTIME/'agent.json',state)


def counters():
    rows = json.loads(command('bpftool','-j','map','dump','pinned',str(ROOT/'maps/counters')))
    result = {}
    for row in rows:
        key = row['key']
        idx = int.from_bytes(bytes(int(x,16) for x in key),'little') if isinstance(key,list) else key
        packets = size = 0
        for cpu in row['values']:
            v = cpu['value']
            p,b = (v['packets'],v['bytes']) if isinstance(v,dict) else struct.unpack('<QQ',bytes(int(x,16) for x in v))
            packets += p; size += b
        result[REASONS[idx]] = {'packets':packets,'bytes':size}
    return result


def status():
    namespace_check()
    result = {'available':False,'slice':'urllc','mode':'unknown','effective_mode':'unknown',
              'driver_mode':None,'namespace':'maestro-urllc','interfaces':[N3,N6],
              'required_charging_policy':'unmetered_lab','charging_policy':'unknown',
              'counter_semantics':'redirect requests; UL/DL inner IPv4 bytes',
              'counters':{},'lease_seconds':0}
    if not (ROOT/'prog').exists(): result.update(mode='kernel',effective_mode='kernel')
    try:
        with locked():
            n = native()
            attached = own_hooks()
            heartbeat = read(RUNTIME/'watchdog.json')
            fresh = 0 <= time.monotonic_ns()-heartbeat['timestamp_ns'] <= 2_000_000_000
            result.update(available=attached and fresh,ue=n['ue'],session_generation=n['generation'],
                          charging_policy='unmetered_lab',mode='kernel',effective_mode='kernel',
                          driver_mode='generic' if attached else None)
            result['reason'] = None if result['available'] else 'Hooks/watchdog no disponibles'
            state = read(RUNTIME/'agent.json') if (RUNTIME/'agent.json').exists() else {}
            result['confirmed'] = bool(state.get('confirmed'))
            remaining = max(0,(state.get('expires_ns',0)-time.monotonic_ns())/1e9)
            if attached and fresh and flag() and remaining and state.get('generation')==n['generation']:
                result.update(mode='xdp',effective_mode='xdp',lease_seconds=remaining)
            elif state.get('reason'): result['reason']=state['reason']
            if (ROOT/'maps/counters').exists(): result['counters']=counters()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        result['reason'] = str(e) if isinstance(e,ValueError) else 'Estado URLLC no verificable'
    return result


def watch():
    namespace_check()
    alive = True
    def stop(*_):
        nonlocal alive
        alive = False
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with locked(): off('watchdog started; explicit activation required')
    try:
        while alive:
            with locked():
                write(RUNTIME/'watchdog.json',{'timestamp_ns':time.monotonic_ns(),'pid':os.getpid()})
                state = read(RUNTIME/'agent.json')
                if state.get('mode') == 'xdp':
                    try:
                        n = native()
                        if n['generation'] != state['generation'] or not flag() or not own_hooks():
                            raise ValueError('PFCP generation or XDP ownership changed')
                        if state['expires_ns'] <= time.monotonic_ns():
                            raise ValueError('Lease expired; explicit reactivation required')
                        if not state.get('confirmed') and state['confirmation_deadline_ns'] <= time.monotonic_ns():
                            raise ValueError('MEC canary confirmation expired')
                        expiry = time.monotonic_ns()+LEASE_NS
                        install(n,expiry)
                        state['expires_ns']=expiry
                        write(RUNTIME/'agent.json',state)
                    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
                        off('native session/policy changed; kernel fallback')
            time.sleep(.5)
    finally:
        with locked(): off('watchdog stopped')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','detach','status','on','confirm','off','watch'])
    parser.add_argument('--ue')
    parser.add_argument('--generation')
    args=parser.parse_args()
    try:
        if args.action=='prepare': prepare()
        elif args.action=='detach': detach()
        elif args.action=='watch': watch()
        elif args.action=='on': activate(args.ue)
        elif args.action=='confirm': confirm(args.ue,args.generation)
        elif args.action=='off':
            with locked(): off()
        if args.action!='watch': print(json.dumps(status()))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(json.dumps({'error':'URLLC_PRECONDITION_FAILED','reason':str(exc) if isinstance(exc,ValueError) else 'Agent operation failed'}))
        raise SystemExit(1)
