"""Disconnected PFCP/GTP test of the actual C4 UPF, never a live namespace.

Run under unshare --net --mount. Requires only loopback before setup.
The XDP accounting injection uses BPF_PROG_TEST_RUN; it is not MEC delivery.
"""
import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import time


def tlv(kind, value): return struct.pack('!HH', kind, len(value)) + value
def u32(value): return struct.pack('!I', value)
def fields(data):
    while data:
        kind, size = struct.unpack('!HH', data[:4])
        assert len(data) >= 4 + size
        yield kind, data[4:4+size]
        data = data[4+size:]
def packet(kind, sequence, payload, seid=None):
    rest = (struct.pack('!Q', seid) if seid is not None else b'') + sequence.to_bytes(3,'big') + b'\0' + payload
    return bytes([0x20 | (seid is not None), kind]) + struct.pack('!H',len(rest)) + rest


def run(binary, libraries, directory):
    links = json.loads(subprocess.check_output(['ip','-j','link'],text=True))
    assert {x['ifname'] for x in links} == {'lo'}, 'Requires a disconnected network namespace'
    subprocess.run(['mount','--make-rprivate','/'],check=True)
    subprocess.run(['ip','link','set','lo','up'],check=True)
    directory.mkdir(mode=0o700)
    pins = directory/'bpf'; pins.mkdir()
    subprocess.run(['mount','-t','bpf','bpf',str(pins)],check=True)
    (pins/'maps').mkdir()
    bpftool = str(sorted(Path('/usr/lib/linux-tools').glob('*/bpftool'))[-1])
    subprocess.run([bpftool,'prog','load',str(Path(__file__).with_name('c4_policy_kern.o')),
        str(pins/'policy'),'type','xdp','pinmaps',str(pins/'maps')],check=True)
    runtime=directory/'runtime';runtime.mkdir(mode=0o700)
    state=directory/'state';state.mkdir(mode=0o700)
    observer=str(directory/'observe.sock')
    config=directory/'upf.yaml'
    config.write_text(f'''logger:
  file:
    path: {directory}/upf.log
  level: info
global:
  max:
    ue: 16
upf:
  charging_enforcement: true
  pfcp:
    server:
      - address: 127.0.0.7
  gtpu:
    server:
      - address: 127.0.0.7
  session:
    - subnet: 10.99.0.0/24
      gateway: 10.99.0.1
      dnn: 5g-plus
      dev: c4testtun
''')
    env=os.environ | {'LD_LIBRARY_PATH':str(libraries),'MAESTRO_POLICY_OBSERVER_SOCKET':observer,
        'MAESTRO_POLICY_STATE_DIR':str(state),'MAESTRO_POLICY_PFCP_ENTERPRISE':'32473',
        'MAESTRO_C4_RUNTIME':str(runtime),'MAESTRO_C4_BPFFS':str(pins)}
    libc=ctypes.CDLL(None,use_errno=True)
    assert os.uname().machine=='x86_64'
    def bpf(cmd, attr):
        value=libc.syscall(321,cmd,ctypes.byref(attr),144)
        if value<0: raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
        return value
    path=ctypes.create_string_buffer(os.fsencode(pins/'policy'))
    attr=ctypes.create_string_buffer(144);struct.pack_into('Q',attr,0,ctypes.addressof(path))
    fd=bpf(7,attr)
    def invoke(key,op,bytes_=0):
        data=ctypes.create_string_buffer(struct.pack('<IIQQQIIIIII',0x43345031,1,*key,op,0,1,bytes_)+bytes(64)+
             struct.pack('<Q',time.monotonic_ns()+2_000_000_000),128)
        output=ctypes.create_string_buffer(128)
        attr=ctypes.create_string_buffer(144)
        struct.pack_into('<IIIIQQII',attr,0,fd,0,128,128,ctypes.addressof(data),ctypes.addressof(output),1,0)
        bpf(10,attr)
        return struct.unpack_from('<I',attr,4)[0]
    def observe():
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as c:
            c.settimeout(2);c.bind('\0c4-observe-'+secrets.token_hex(6));c.sendto(b'observe\n',observer)
            r=json.loads(c.recv(196608));assert r['status']=='success';return r['data']
    evidence={'scope':'isolated_actual_upf_pfcp_gtp','passed':False,'sessions':[]}
    with (directory/'process.log').open('wb') as log, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as n4:
        n4.bind(('127.0.0.1',8805));seq=0;reports=[];peer_seids={}
        process=subprocess.Popen([str(binary),'-c',str(config)],env=env,stdout=log,stderr=log)
        def receive(timeout=2):
            n4.settimeout(timeout);msg,peer=n4.recvfrom(65535)
            offset=16 if msg[0]&1 else 8
            sequence=int.from_bytes(msg[offset-4:offset-1],'big')
            if msg[1]==1:
                n4.sendto(packet(2,sequence,tlv(96,u32(12345))),peer);return None
            if msg[1]==56:
                reports.append(msg)
                local_seid=int.from_bytes(msg[4:12],'big')
                n4.sendto(packet(57,sequence,tlv(19,b'\1'),peer_seids[local_seid]),peer)
                return None
            return msg
        def request(kind,payload,seid=None):
            nonlocal seq
            seq+=1;n4.sendto(packet(kind,seq,payload,seid),('127.0.0.7',8805))
            until=time.monotonic()+3
            while time.monotonic()<until:
                msg=receive(until-time.monotonic())
                if msg and msg[1]==kind+1:
                    offset=16 if msg[0]&1 else 8
                    assert dict(fields(msg[offset:]))[19]==b'\1',msg.hex()
                    return msg
            raise TimeoutError('PFCP response missing')
        def settle(seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                try: receive(max(.001,end-time.monotonic()))
                except socket.timeout: break
        try:
            until=time.monotonic()+8
            while not Path(observer).exists():
                if process.poll() is not None or time.monotonic()>until:
                    raise RuntimeError('Candidate startup failed: '+(directory/'process.log').read_text()[-1800:])
                time.sleep(.05)
            node=tlv(60,b'\0'+socket.inet_aton('127.0.0.1'))
            request(5,node+tlv(96,u32(12345)))
            for index in (2,3):
                ue=f'10.99.0.{index}'
                # Source interface type N3 (11) enables native IPv4 subnet lookup.
                pdi=tlv(2,tlv(20,b'\0')+tlv(160,b'\x0b')+tlv(21,b'\1'+u32(1000+index)+socket.inet_aton('127.0.0.7'))+
                        tlv(22,b'\x075g-plus')+tlv(93,b'\2'+socket.inet_aton(ue)))
                pdr=tlv(1,tlv(56,b'\0\1')+tlv(29,u32(65535))+pdi+tlv(95,b'\0')+
                        tlv(108,u32(1))+tlv(109,u32(1))+tlv(81,u32(1)))
                # FORW is bit 2 of the first Apply Action octet, not the second.
                far=tlv(3,tlv(108,u32(1))+tlv(44,b'\2\0')+tlv(4,tlv(42,b'\1')+tlv(160,b'\x11')))
                qer=tlv(7,tlv(109,u32(1))+tlv(25,b'\0')+tlv(26,(10000).to_bytes(5,'big')*2)+tlv(124,b'\1'))
                urr=tlv(6,tlv(81,u32(1))+tlv(62,b'\2')+tlv(37,b'\2\0\0')+
                        tlv(31,b'\1'+struct.pack('!Q',64))+tlv(73,b'\1'+struct.pack('!Q',1000000)))
                request(50,node+tlv(57,b'\2'+struct.pack('!Q',40+index)+socket.inet_aton('127.0.0.1'))+
                        tlv(159,b'\x075g-plus')+tlv(113,b'\1')+pdr+far+qer+urr,0)
                # PFCP response transmission precedes the atomic registry publish
                # on the UPF event thread. Wait for this session's observation.
                deadline=time.monotonic()+2
                while True:
                    registry=json.loads((runtime/'registry-v1.json').read_text())
                    matches=[s for s in registry['sessions'] if s['ue']==ue]
                    if not registry['revoked'] and len(matches)==1:
                        entry=matches[0]
                        break
                    assert time.monotonic()<deadline, registry
                    time.sleep(.01)
                assert entry['bridge_ready']
                peer_seids[40+index]=int(entry['seid'])
                subprocess.run(['ip','link','set','c4testtun','up'],check=True)
                key=(int(registry['instance']),int(entry['seid']),int(entry['generation']),1,1)
                assert invoke(key,3)==2
                for _ in range(3):assert invoke(key,0,32)==2
                settle(.15)
                observed=next(s for s in observe()['sessions'] if s['ue_ipv4']==ue)
                assert int(observed['usage'][0]['total_octets'])==96,observed
                assert int(observed['usage'][0]['total_packets'])==3
                header=struct.pack('!BBHHHBBH4s4s',0x45,0,32,0,0,64,17,0,
                    socket.inet_aton(ue),socket.inet_aton('172.31.48.2'))
                checksum=sum(struct.unpack('!10H',header))
                while checksum>>16:checksum=(checksum&65535)+(checksum>>16)
                header=header[:10]+struct.pack('!H',(~checksum)&65535)+header[12:]
                inner=header+struct.pack('!HHHHI',5000,8765,12,0,index)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as gtp:
                    gtp.sendto(struct.pack('!BBHI',0x30,255,len(inner),1000+index)+inner,('127.0.0.7',2152))
                settle(.15)
                observed=next(s for s in observe()['sessions'] if s['ue_ipv4']==ue)
                policies=json.loads(subprocess.check_output(
                    [bpftool,'-j','map','dump','pinned',str(pins/'maps/c4_policy_v1')],text=True))
                policy=next(row['formatted'] for row in policies
                            if row['formatted']['key']['seid']==int(entry['seid']))
                assert policy['value']['usage'][0][0]=={'packets':1,'bytes':32},policy
                assert policy['value']['usage'][1][0]=={'packets':3,'bytes':96},policy
                assert int(observed['usage'][0]['total_octets'])==128,observed
                assert int(observed['usage'][0]['total_packets'])==4
                settle(.1)
                again=next(s for s in observe()['sessions'] if s['ue_ipv4']==ue)
                assert observed['usage']==again['usage'],'Importer replayed a delta'
                response=request(54,b'',int(entry['seid']))
                reports.append(response)
                evidence['sessions'].append({'ue':ue,'xdp_bytes':96,'native_bytes':32,
                    'urr_bytes':128,'urr_packets':4,'shared_policy':policy})
            totals=[]
            for report in reports:
                for kind,group in fields(report[16:]):
                    if kind not in (79,80):continue
                    data=dict(fields(group));v=data[66];flags=v[0];pos=1;values={}
                    for bit,label in enumerate(('bytes','ul_bytes','dl_bytes','packets','ul_packets','dl_packets')):
                        if flags&(1<<bit):values[label]=int.from_bytes(v[pos:pos+8],'big');pos+=8
                    totals.append(values)
            assert sum(v.get('bytes',0) for v in totals)==256,totals
            assert sum(v.get('packets',0) for v in totals)==8,totals
            evidence.update(passed=True,n4_reports=totals,periodic_import_idempotent=True)
        finally:
            if (runtime/'registry-v1.json').exists():
                evidence['registry_before_shutdown']=json.loads((runtime/'registry-v1.json').read_text())
            process.terminate()
            try:process.wait(5)
            except subprocess.TimeoutExpired:process.kill();process.wait(5)
            evidence['process_returncode']=process.returncode
            if process.returncode!=0:evidence['passed']=False
            evidence['n4_wire_hex']=[message.hex() for message in reports]
            os.close(fd)
            (directory/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    assert evidence['passed'],evidence
    return evidence


if __name__=='__main__':
    print(json.dumps(run(*(Path(x) for x in sys.argv[1:4]))))
