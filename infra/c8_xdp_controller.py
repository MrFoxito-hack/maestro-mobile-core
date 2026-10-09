"""Bounded live canary; never updates policy budgets or claims C3 readiness."""
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time
from c8_registry import validate_renewal

root = Path('/run/maestro-bpf/c4')
runtime = Path('/run/maestro-c4')
out = Path(sys.argv[1])
expected = json.loads(Path(sys.argv[2]).read_text())
libc = ctypes.CDLL(None, use_errno=True)
fds = []
stopping = False
def stop(*_):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
def bpf(cmd, attr):
    rc = libc.syscall(321, cmd, ctypes.byref(attr), len(attr))
    if rc < 0: raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return rc
def obj(path):
    p = ctypes.create_string_buffer(os.fsencode(path))
    a = ctypes.create_string_buffer(144)
    struct.pack_into('<Q', a, 0, ctypes.addressof(p))
    fd = bpf(7, a); fds.append(fd)
    return fd
def update(fd, key, value):
    k, v = ctypes.create_string_buffer(key), ctypes.create_string_buffer(value)
    a = ctypes.create_string_buffer(144)
    struct.pack_into('<I4xQQQ', a, 0, fd, ctypes.addressof(k), ctypes.addressof(v), 0)
    bpf(2, a)
def invoke(key, op):
    data = ctypes.create_string_buffer(struct.pack('<II',0x43345031,1)+key+
        struct.pack('<IIII',op,0,1,0)+bytes(64)+struct.pack('<Q',time.monotonic_ns()+2_000_000_000),128)
    result = ctypes.create_string_buffer(128)
    a = ctypes.create_string_buffer(144)
    struct.pack_into('<IIIIQQII',a,0,program,0,128,128,ctypes.addressof(data),ctypes.addressof(result),1,0)
    bpf(10,a)
    assert struct.unpack_from('<I',a,4)[0] == 2, 'policy invocation rejected'
    return list(struct.unpack_from('<8Q',result,56))
def net(*args):
    return json.loads(subprocess.check_output(['ip','netns','exec','maestro-urllc','ip','-j',*args],text=True))
def save(data):
    tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(out)

result={'scope':'c8_experiment','admission_ready':False,'ready':False,'epochs':[]}
entries=[]; gate=None


def close_epoch():
    for e in entries:invoke(e['key'],1)
    after={e['ue']:invoke(e['key'],2) for e in entries}
    result['epochs'].append({'identities':[dict(e['identity']) for e in entries],
                             'before':epoch_before,'after':after,
                             'closed_monotonic':time.monotonic()})
    return after
try:
    registry_raw=(runtime/'registry-v1.json').read_bytes()
    registry=json.loads(registry_raw)
    result.update(pid=registry['pid'],instance=registry['instance'])
    assert not registry['revoked'] and len(registry['sessions'])==2
    assert registry['pid']==expected['pid'] and registry['instance']==expected['instance']
    os.kill(registry['pid'],0)
    assert os.readlink('/proc/'+str(registry['pid'])+'/exe')=='/opt/maestro-c4-v7-20261006/open5gs-upfd'
    program=obj(root/'policy');gate=obj(root/'xmaps/enabled')
    update(gate,bytes(4),bytes(4))
    ulmap=obj(root/'xmaps/sessions_uplink_map');dlmap=obj(root/'xmaps/sessions_downlink_map')
    links={x['ifname']:x for x in net('link','show')}
    neighbors={(x['dev'],x['dst']):x['lladdr'] for x in net('neigh','show') if 'lladdr' in x}
    mac=lambda s:bytes.fromhex(s.replace(':',''))
    for s in registry['sessions']:
        assert s['bridge_ready'] and s['fast_eligible']
        identity=next(x for x in expected['sessions'] if x['ue']==s['ue'])
        assert identity['seid']==s['seid'] and identity['generation']==s['generation']
        q,=s['qers'];u,=s['urrs']
        pdrs=[p for p in s['pdrs'] if p['qer_id']==q['id']]
        assert len(pdrs)==2 and all(not p['sdf'] and p['far_action']==512 for p in pdrs)
        ul,dl=sorted(pdrs,key=lambda p:p['source'])
        assert [ul['source'],dl['source']]==[0,1]
        assert ul['upf']=='10.210.50.22' and dl['gnb']=='10.210.50.10'
        n3,n6=links['murllc-n3'],links['murllc-mec']
        route=net('route','get',dl['gnb'])[0]
        assert route['dev']=='murllc-n3'
        key=struct.pack('<QQQII',int(registry['instance']),int(s['seid']),int(s['generation']),q['id'],u['id'])
        prefix=(socket.inet_aton(s['ue'])+bytes(4)+struct.pack('!II',ul['teid'],dl['dl_teid'])+
            socket.inet_aton(dl['gnb'])+socket.inet_aton(ul['upf'])+
            struct.pack('<III',n3['ifindex'],n6['ifindex'],min(n3['mtu'],n6['mtu']))+
            mac(n3['address'])+mac(neighbors[('murllc-n3',route.get('gateway',dl['gnb']))])+
            mac(n6['address'])+mac(neighbors[('murllc-mec','172.31.48.2')])+bytes([q['qfi'],0,0,0]))
        assert len(prefix)==64
        entries.append({'ue':s['ue'],'key':key,'prefix':prefix,'ul':struct.pack('!I',ul['teid']),
                        'dl':socket.inet_aton(s['ue']),'identity':identity})
    assert not (runtime/'emergency-stop').exists(), 'explicit canary latch clearance required'
    result['before']={e['ue']:invoke(e['key'],2) for e in entries}
    epoch_before=result['before']
    result['identities']=[e['identity'] for e in entries]
    started=time.monotonic();result['started_monotonic']=started
    while not stopping and time.monotonic()-started<1500:
        assert not (runtime/'emergency-stop').exists(), 'emergency stop'
        current_raw=(runtime/'registry-v1.json').read_bytes()
        if current_raw!=registry_raw:
            update(gate,bytes(4),bytes(4))
            current=json.loads(current_raw)
            validate_renewal(registry,current)
            close_epoch()
            for e in entries:
                s=next(s for s in current['sessions'] if s['ue']==e['ue'])
                q,=s['qers'];u,=s['urrs']
                e['key']=struct.pack('<QQQII',int(current['instance']),int(s['seid']),int(s['generation']),q['id'],u['id'])
                e['identity']={k:s[k] for k in ['ue','seid','generation']}
            epoch_before={e['ue']:invoke(e['key'],2) for e in entries}
            registry,registry_raw=current,current_raw
            save(result)
        os.kill(registry['pid'],0)
        for e in entries:
            value=e['prefix']+struct.pack('<Q',time.monotonic_ns()+2_000_000_000)+e['key']
            assert len(value)==104
            update(ulmap,e['ul'],value);update(dlmap,e['dl'],value)
            invoke(e['key'],3)
        update(gate,bytes(4),struct.pack('<I',1))
        if not result['ready']:
            result['ready']=True;save(result)
        time.sleep(.1)
    result['end_reason']='stopped' if stopping else 'bounded deadline'
except BaseException as exc:
    result['error']=type(exc).__name__+': '+str(exc)
    raise
finally:
    if gate is not None: update(gate,bytes(4),bytes(4))
    if entries:
        result['after']=close_epoch()
    result['gate_closed']=True
    result['finished']=True
    save(result)
    for fd in fds: os.close(fd)
