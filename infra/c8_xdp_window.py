"""C8 temporary v7 window, same PDU/binary for kernel and XDP trials."""
import argparse
import json
import re
import time
import os
from c8_remote import ROOT, CAMPAIGN_ROOT, LoggedLab, get_settings

OUT=CAMPAIGN_ROOT/'setup'
BUNDLE='/opt/maestro-c4-v7-20261006'
DROP='/etc/systemd/system/open5gs-upfd-urllc.service.d/zzzzzzz-c8.conf'
CLI='/home/emsadmin/UERANSIM/build/nr-cli'
UNITS=['maestro-ue-vehicle','ueransim-ue-05']


def xdp_counters(upf):
    source="""import pathlib,subprocess
p=str(sorted(pathlib.Path('/usr/lib/linux-tools').glob('*/bpftool'))[-1])
print(subprocess.check_output([p,'-j','map','dump','pinned','/run/maestro-bpf/c4/xmaps/counters'],text=True))
"""
    raw=json.loads(upf.run(['python3','-c',source],sudo=True))
    return {'raw':raw, **{name:sum(int(v['value']['packets']) for v in next(
        r['formatted']['values'] for r in raw if r['formatted']['key']==key))
        for key,name in [(0,'UL_OK'),(1,'DL_OK')]}}


def wait_pfcp(upf, core):
    """Require association messages from both current processes, never old logs."""
    peers=[(upf,'open5gs-upfd-urllc','10.210.50.18'),
           (core,'open5gs-smfd3','10.210.50.22')]
    evidence={}
    for host,unit,peer in peers:
        pid=host.run(['systemctl','show',unit,'-p','MainPID','--value']).strip()
        for _ in range(30):
            log=host.run(['journalctl','-u',unit,'_PID='+pid,'-n','150','--no-pager'],sudo=True)
            if 'PFCP associated ['+peer+']' in log:
                evidence[unit]={'pid':int(pid),'journal':log};break
            time.sleep(1)
        else:raise RuntimeError('PFCP_association_timeout:'+unit)
    return evidence


def wait_pdu(ue, upf, require_registry=True):
    """Resolve the actual interface from NAS and verify the bound MEC route."""
    import yaml
    for attempt in range(60):
        sessions={}
        for suffix in ['002','005']:
            raw=ue.run([CLI,'imsi-999700000000'+suffix,'-e','ps-list'],sudo=True,check=False)
            data=yaml.safe_load(raw) or {}
            entries=list(data.values()) if isinstance(data,dict) else data
            for s in entries:
                if isinstance(s,dict) and s.get('apn')=='5g-plus' and s.get('state')=='PS-ACTIVE':sessions[suffix]=s
        if attempt in (10,30):
            # Retry cell selection only before readiness; never during a measured block.
            for suffix,unit in zip(['002','005'],UNITS):
                if suffix not in sessions:ue.run(['systemctl','restart',unit],sudo=True)
        addresses=json.loads(ue.run(['ip','-j','-4','address','show']))
        tunnels=[{'interface':i['ifname'],'address':a['local']} for i in addresses
                 for a in i['addr_info'] if a['local'].startswith('10.47.')]
        if len(sessions)==2 and len(tunnels)==2:
            if require_registry:
                raw=upf.run(['cat','/run/maestro-c4/registry-v1.json'],sudo=True,check=False)
                registry=json.loads(raw) if raw.startswith('{') else {}
                if len(registry.get('sessions',[]))!=2 or not all(s['bridge_ready'] and s['fast_eligible'] for s in registry['sessions']):
                    if attempt in (10,30):
                        registered={s['ue'] for s in registry.get('sessions',[])}
                        for suffix,unit in zip(['002','005'],UNITS):
                            if sessions[suffix]['address'] not in registered:
                                # NAS may retain PS-ACTIVE after a stale release removed PFCP.
                                # Recover only during preparation, before any measured block.
                                ue.run([CLI,'imsi-999700000000'+suffix,'-e','ps-release-all'],sudo=True,check=False)
                                ue.run(['systemctl','restart',unit],sudo=True)
                    time.sleep(1);continue
            routes=[json.loads(ue.run(['ip','-j','route','get','172.31.48.2','from',t['address'],'oif',t['interface']])) for t in tunnels]
            if not all(r and r[0]['dev']==t['interface'] for r,t in zip(routes,tunnels)):
                raise RuntimeError('MEC_route_mismatch')
            probe=r'''import socket,json,sys,time
tunnels=json.loads(sys.argv[1]);results=[]
for t in tunnels:
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(2)
 s.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,t['interface'].encode()+b'\0')
 s.bind((t['address'],0));payload=b'C8 readiness only'.ljust(64,b'.')
 start=time.monotonic_ns();s.sendto(payload,('172.31.48.2',8765));data,peer=s.recvfrom(1024)
 assert data==payload and peer==('172.31.48.2',8765)
 results.append(dict(t,rtt_ms=(time.monotonic_ns()-start)/1e6));s.close()
print(json.dumps(results))
'''
            echoes=json.loads(ue.run(['python3','-c',probe,json.dumps(tunnels)],sudo=True))
            return {'nas':sessions,'tunnels':tunnels,'routes':routes,'excluded_readiness_echo':echoes}
        time.sleep(1)
    raise RuntimeError('URLLC_PDU_readiness_timeout')

PREPARE=r'''import hashlib,json,pathlib,pwd,os,subprocess
bundle=pathlib.Path('/opt/maestro-c4-v7-20261006')
pins=pathlib.Path('/sys/fs/bpf/maestro-c8')
target=pathlib.Path('/run/maestro-bpf/c4')
assert hashlib.sha256((bundle/'open5gs-upfd').read_bytes()).hexdigest()=='05b4db6052025648111e560ff8b901f8d218617325059b7ecb3be47096d2f8dd'
if pins.exists() and target.exists():
 print(json.dumps({'pins':str(pins),'reused_c8_pins':True}));__import__('sys').exit(0)
assert not pins.exists() and not target.exists()
pins.mkdir();(pins/'maps').mkdir();(pins/'xmaps').mkdir()
bpftool=str(sorted(pathlib.Path('/usr/lib/linux-tools').glob('*/bpftool'))[-1])
def run(a):return subprocess.check_output(a,text=True)
run([bpftool,'prog','load',str(bundle/'c4_policy_kern.o'),str(pins/'policy'),'type','xdp','pinmaps',str(pins/'maps')])
run([bpftool,'prog','load',str(bundle/'c4_xdp_kern.o'),str(pins/'xdp'),'type','xdp','map','name','c4_policy_v1','pinned',str(pins/'maps/c4_policy_v1'),'pinmaps',str(pins/'xmaps')])
u=pwd.getpwnam('open5gs')
for p in [pins,*pins.rglob('*')]:
 os.chown(p,u.pw_uid,u.pw_gid);p.chmod(0o750 if p.is_dir() else 0o660)
target.mkdir(parents=True);run(['mount','--bind',str(pins),str(target)])
print(json.dumps({'pins':str(pins),'programs':[json.loads(run([bpftool,'-j','prog','show','pinned',str(pins/n)])) for n in ['policy','xdp']]}))
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','kernel','xdp','check','restore']);a=p.parse_args()
    upf=LoggedLab(get_settings(),2223,OUT);ue=LoggedLab(get_settings(),2226,OUT);core=LoggedLab(get_settings(),2222,OUT)
    path=OUT/'xdp-window.json'
    try:
        if a.action in ('prepare','xdp'):
            guard=json.loads(upf.run(['python3','-c',
                "import yaml,json; c=yaml.safe_load(open('/etc/open5gs/upf-urllc.yaml'))['upf']; "
                "print(json.dumps({'charging_enforcement':c.get('charging_enforcement'), 'nchf':bool(c.get('nchf'))}))"],sudo=True))
            if guard['charging_enforcement'] is not False or guard['nchf']:
                raise ValueError('unsynchronized_XDP_charging_blocked')
        if a.action=='prepare':
            if path.exists():
                previous=json.loads(path.read_text())
                if previous['mode']!='restore':raise ValueError('window_already_active')
                (OUT/('xdp-window-attempt-'+str(time.time_ns())+'.json')).write_text(path.read_text())
            before=upf.run(['systemctl','show','open5gs-upfd-urllc','-p','ExecStart','-p','ActiveState'])
            remote=upf.run(['mktemp','-d','/home/emsadmin/c8-xdp-XXXXXX']).strip()
            record={'before_service':before,'remote':remote,'mode':'preparing','charging_guard':guard,
                    'execution_mode':'xdpgeneric on veth; no driver or hardware offload claim'}
            path.write_text(json.dumps(record,indent=2))
            upf.run(['test','!','-e',DROP],sudo=True)
            rollback=f'''import pathlib,subprocess
for dev in ['murllc-n3','murllc-mec']:
 subprocess.run(['ip','netns','exec','maestro-urllc','ip','link','set','dev',dev,'xdpgeneric','off'],check=True)
subprocess.run(['systemctl','stop','c8-xdp-controller'],check=False)
p=pathlib.Path({DROP!r})
if p.exists():
 assert '# C8 temporary window' in p.read_text();p.unlink()
 subprocess.run(['systemctl','daemon-reload'],check=True)
 subprocess.run(['systemctl','restart','open5gs-upfd-urllc'],check=True)
'''
            upf.write(remote+'/restore.py',rollback)
            deadline=int(os.environ.get('C8_ROLLBACK_SECONDS','1800'))
            if not 1800<=deadline<=3600:raise ValueError('rollback_bound')
            record['rollback_seconds']=deadline
            upf.run(['systemd-run','--unit=c8-xdp-restore',f'--on-active={deadline}s','python3',remote+'/restore.py'],sudo=True)
            core.run(['systemd-run','--unit=c8-smf3-restore',f'--on-active={deadline+10}s','systemctl','restart','open5gs-smfd3'],sudo=True)
            ue.run(['systemd-run','--unit=c8-vehicle-restore',f'--on-active={deadline+20}s','systemctl','restart',*UNITS,'ueransim-watchdog'],sudo=True)
            record['pins']=json.loads(upf.run(['python3','-c',PREPARE],sudo=True))
            path.write_text(json.dumps(record,indent=2))
            ue.run(['systemctl','stop','ueransim-watchdog'],sudo=True)
            for supi in ['imsi-999700000000002','imsi-999700000000005']:
                ue.run([CLI,supi,'-e','ps-release-all'],sudo=True,check=False)
            ue.run(['systemctl','stop',*UNITS],sudo=True)
            # Stop the peer first: no stale PFCP transaction can cross UPF replacement.
            core.run(['systemctl','stop','open5gs-smfd3'],sudo=True)
            conf=f'''# C8 temporary window
[Service]
ExecStart=
ExecStart={BUNDLE}/open5gs-upfd -c /etc/open5gs/upf-urllc.yaml
Environment=LD_LIBRARY_PATH={BUNDLE}/lib
Environment=MAESTRO_C4_RUNTIME=/run/maestro-c4
Environment=MAESTRO_C4_BPFFS=/run/maestro-bpf/c4
RuntimeDirectory=maestro-c4
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW CAP_BPF
LimitMEMLOCK=infinity
'''
            upf.write(remote+'/c8.conf',conf)
            upf.run(['install','-m','644',remote+'/c8.conf',DROP],sudo=True)
            upf.run(['systemctl','daemon-reload'],sudo=True)
            upf.run(['systemctl','restart','open5gs-upfd-urllc'],sudo=True)
            core.run(['systemctl','start','open5gs-smfd3'],sudo=True)
            record['pfcp']=wait_pfcp(upf,core)
            for unit in UNITS:
                ue.run(['systemctl','start',unit],sudo=True)
                time.sleep(1)
            record['pdu']=wait_pdu(ue,upf)
            record['counters_before']=xdp_counters(upf)
            controller=(ROOT/'infra/c8_xdp_controller.py').read_text(encoding='utf-8')
            (OUT/'c8_xdp_controller.py').write_text(controller,encoding='utf-8')
            upf.write(remote+'/controller.py',controller)
            upf.write(remote+'/c8_registry.py',(ROOT/'infra/c8_registry.py').read_bytes())
            record['mode']='kernel';path.write_text(json.dumps(record,indent=2))
        else:
            record=json.loads(path.read_text());remote=record['remote']
            if a.action=='kernel':
                upf.run(['systemctl','stop','c8-xdp-controller'],sudo=True,check=False)
                for dev in ['murllc-n3','murllc-mec']:
                    upf.run(['ip','netns','exec','maestro-urllc','ip','link','set','dev',dev,'xdpgeneric','off'],sudo=True)
                raw=upf.run(['cat',record.get('controller_output',remote+'/controller.json')],sudo=True,check=False)
                if raw.startswith('{'):
                    (OUT/('controller-'+str(time.time_ns())+'.json')).write_text(raw)
            elif a.action=='xdp':
                if record['mode']=='xdp':
                    control=json.loads(upf.run(['cat',record['controller_output']],sudo=True))
                    if not control.get('ready') or control.get('finished'):raise ValueError('controller_not_ready')
                    print(json.dumps({'action':'xdp','continued_window':True}));return
                registry=json.loads(upf.run(['cat','/run/maestro-c4/registry-v1.json'],sudo=True))
                if len(registry['sessions'])!=2 or not all(s['bridge_ready'] and s['fast_eligible'] for s in registry['sessions']):
                    raise ValueError('c8_xdp_not_eligible')
                expected={'pid':registry['pid'],'instance':registry['instance'], 'sessions':[
                    {k:s[k] for k in ['ue','seid','generation']} for s in registry['sessions']]}
                suffix=str(time.time_ns())
                record['controller_output']=remote+'/controller-'+suffix+'.json'
                upf.write(remote+'/expected-'+suffix+'.json',json.dumps(expected))
                for dev in ['murllc-n3','murllc-mec']:
                    upf.run(['ip','netns','exec','maestro-urllc','ip','link','set','dev',dev,'xdpgeneric','pinned','/run/maestro-bpf/c4/xdp'],sudo=True)
                upf.run(['systemctl','reset-failed','c8-xdp-controller'],sudo=True,check=False)
                upf.run(['systemd-run','--unit=c8-xdp-controller','--collect','--property=RuntimeMaxSec=1510',
                         'python3',remote+'/controller.py',record['controller_output'],remote+'/expected-'+suffix+'.json'],sudo=True)
                time.sleep(1)
                control=json.loads(upf.run(['cat',record['controller_output']],sudo=True))
                if not control.get('ready') or control.get('finished'):raise ValueError('controller_not_ready')
                record.setdefault('identities',[]).append(expected)
            elif a.action=='check':
                if record['mode']=='xdp':
                    control=json.loads(upf.run(['cat',record['controller_output']],sudo=True))
                    active=upf.run(['systemctl','is-active','c8-xdp-controller']).strip()
                    if active!='active' or not control.get('ready') or control.get('finished') or control.get('error'):
                        raise ValueError('controller_failed_during_measurement')
                import yaml
                nas={}
                for suffix in ['002','005']:
                    data=yaml.safe_load(ue.run([CLI,'imsi-999700000000'+suffix,'-e','ps-list'],sudo=True)) or {}
                    entries=list(data.values()) if isinstance(data,dict) else data
                    current=[s for s in entries if isinstance(s,dict) and s.get('apn')=='5g-plus' and s.get('state')=='PS-ACTIVE']
                    if len(current)!=1 or current[0]['address']!=record['pdu']['nas'][suffix]['address']:
                        raise ValueError('PDU_changed_during_measurement:'+suffix)
                    nas[suffix]=current[0]
                record.setdefault('pdu_checks',[]).append({'mode':record['mode'],'at_epoch':time.time(),'nas':nas})
                path.write_text(json.dumps(record,indent=2))
                print(json.dumps({'action':'check','mode':record['mode'],'accepted':True}));return
            else:
                if 'counters_before' in record:
                    record['counters_after']=xdp_counters(upf)
                for supi in ['imsi-999700000000002','imsi-999700000000005']:
                    ue.run([CLI,supi,'-e','ps-release-all'],sudo=True,check=False)
                ue.run(['systemctl','stop',*UNITS],sudo=True)
                core.run(['systemctl','stop','open5gs-smfd3'],sudo=True)
                upf.run(['python3',remote+'/restore.py'],sudo=True)
                core.run(['systemctl','start','open5gs-smfd3'],sudo=True)
                record['restored_pfcp']=wait_pfcp(upf,core)
                ue.run(['systemctl','restart',*UNITS,'ueransim-watchdog'],sudo=True)
                record['restored_pdu']=wait_pdu(ue,upf,require_registry=False)
                upf.run(['systemctl','stop','c8-xdp-restore.timer'],sudo=True,check=False)
                ue.run(['systemctl','stop','c8-vehicle-restore.timer'],sudo=True,check=False)
                core.run(['systemctl','stop','c8-smf3-restore.timer'],sudo=True,check=False)
                record['after_service']=upf.run(['systemctl','show','open5gs-upfd-urllc','-p','ExecStart','-p','ActiveState'])
                argv=lambda text: re.search(r'argv\[\]=(.*?) ;',text).group(1)
                record['restored']=argv(record['after_service'])==argv(record['before_service']) and 'ActiveState=active' in record['after_service']
            record['mode']=a.action;path.write_text(json.dumps(record,indent=2))
        print(json.dumps({'action':a.action,'evidence':str(path)}))
    finally:upf.client.close();ue.client.close();core.client.close()


if __name__=='__main__':main()
