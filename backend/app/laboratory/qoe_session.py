"""Single-treatment session extracted from infra/nwdaf_qoe_campaign.py.

Generator checkpoints separate preparation, playback and recovery. No legacy main
is invoked. NWDAF returns to the accepted operational regime after each trial.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
import uuid
import yaml
from app.core.security import create_token
from app.db import connection
from app.laboratory.qoe_transport import Lab, api, COUNTERS
ROOT = Path(__file__).resolve().parents[3]


def receiver_snapshot(ue, interface):
    if not re.fullmatch(r'uesimtun\d+', interface):
        raise ValueError('invalid_receiver_interface')
    return json.loads(ue.run(['python3', '-c', "import pathlib,json,sys; p=pathlib.Path('/sys/class/net')/sys.argv[1]; print(json.dumps({'rx_bytes':int((p/'statistics/rx_bytes').read_text()),'ifindex':int((p/'ifindex').read_text()),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))", interface]))

def accounts(core):
    return json.loads(core.run(['python3', '-c', """import sqlite3,json
c=sqlite3.connect('file:/home/emsadmin/maestro-charging/charging.sqlite3?mode=ro',uri=True)
c.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in c.execute("SELECT a.supi,a.quota_bytes,a.consumed_bytes,COALESCE((SELECT SUM(s.reserved_bytes) FROM charging_sessions s WHERE s.supi=a.supi AND s.status='OPEN'),0) AS reserved_bytes FROM charging_accounts a")]))
"""]))

def session(settings, tag, local, case, checkpoint, check_ownership, owner, *, admission=None, remote_owner=None):
    from app.laboratory.acceptance import require_real_actuator
    require_real_actuator(admission)
    if not remote_owner: raise ValueError('remote_guard_required')
    hosts={};units=[];cleanup=[];server=None;sampler=None;stop=threading.Event()
    result={'run':tag,'utc':datetime.now(timezone.utc).isoformat(),'cases':[],'observations':[],'transfers':[]}
    phase={'name':'preparation'};executor=ThreadPoolExecutor(max_workers=2);traffic=[]
    try:
        for name,port in [('core',settings.ssh_port),('upf',settings.upf_ssh_port),('ue',settings.ue_ssh_port)]:
            hosts[name]=Lab(settings,port,check_ownership)
            hosts[name].bind_owner(remote_owner)
        core,upf,ue=(hosts[n] for n in ('core','upf','ue'))
        token=core.read(settings.nwdaf_token_file).decode().strip()
        result['accounts_before']=accounts(core)
        result['ledger_before']=api(core,token,'GET','/management/v1/closed-loop/history')
        sessions={}
        links=json.loads(ue.run(['ip','-j','-4','addr','show']))
        for role,supi in [('video','imsi-999700000000001'),('load','imsi-999700000000004')]:
            pdus=yaml.safe_load(ue.run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'--exec','ps-list']))
            active=[p for p in pdus.values() if p.get('apn')=='internet' and p.get('state')=='PS-ACTIVE']
            assert len(active)==1
            address=active[0]['address']
            interfaces=[i['ifname'] for i in links if any(a['local']==address for a in i.get('addr_info',[]))]
            assert len(interfaces)==1,'Ambiguous active UE interface'
            account=next(a for a in result['accounts_before'] if a['supi']==supi)
            sessions[role]={'supi':supi,'address':address,'interface':interfaces[0],
                            'free':account['quota_bytes']-account['consumed_bytes']-account['reserved_bytes']}
        assert sessions['load']['free']>=37000000,'Insufficient background UE quota'
        result['sessions']=sessions; checkpoint(result)
        remote=core.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        assert re.fullmatch('/home/emsadmin/'+tag+'-[A-Za-z0-9]{6}',remote)
        result['remote']=remote; checkpoint(result)
        media=remote+'/media';core.run(['mkdir',media])
        cache=local.parent/'media-assets'
        if cache.exists():
            from app.laboratory.qoe_media import read_media
            for asset,payload in read_media(cache).items():
                core.write(media+'/'+asset,payload)
        else:
            core.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin',
            '-f','lavfi','-i','testsrc2=size=640x360:rate=25','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
            '-t','30','-c:v','libx264','-preset','veryfast','-threads','2','-pix_fmt','yuv420p',
            '-b:v','500k','-minrate','500k','-maxrate','500k','-bufsize','500k','-x264-params','nal-hrd=cbr:force-cfr=1',
            '-g','50','-keyint_min','50','-sc_threshold','0','-c:a','aac','-b:a','64k',
            '-f','hls','-hls_time','2','-hls_playlist_type','vod','-hls_segment_type','fmp4',
            '-hls_fmp4_init_filename','init.mp4','-hls_segment_filename',media+'/seg%03d.m4s',media+'/index.m3u8'],timeout=90)
        probe=json.loads(core.run(['ffprobe','-v','error','-show_streams','-show_packets','-show_format','-of','json',media+'/index.m3u8'],timeout=30))
        (local/'ffprobe.json').write_text(json.dumps(probe,indent=2),encoding='utf-8')
        manifest=core.read(media+'/index.m3u8');assets=['index.m3u8','init.mp4']+re.findall(r'^seg\d{3}\.m4s$',manifest.decode(),re.M)
        originals={asset:core.read(media+'/'+asset) for asset in assets}
        if not cache.exists():
            from app.laboratory.qoe_media import save_media
            save_media(cache,originals)
        total=sum(map(len,originals.values()));result['media_bytes']=total
        result['media_sha256']={name:hashlib.sha256(data).hexdigest() for name,data in originals.items()}
        assert 2*total*1.15+500000<sessions['video']['free'],'Insufficient video UE quota; no top-up performed'
        (local/'index.m3u8').write_bytes(manifest)
        # Independent core cleanup owns exactly these routes and auxiliary units.
        for s in sessions.values():assert not core.run(['ip','route','show','exact',s['address']+'/32']).strip()
        script='#!/bin/sh\nsystemctl start maestro-nwdaf\n'
        for suffix in ('origin','iperf','capture'):script+='systemctl stop '+tag+'-'+suffix+' 2>/dev/null\n'
        for s in sessions.values():script+='ip route del '+s['address']+'/32 via 10.210.50.8 2>/dev/null\n'
        core.write(remote+'/cleanup.sh',script,0o700)
        core.run(['systemd-run','--unit='+tag+'-cleanup','--on-active=300s','--timer-property=AccuracySec=1s','/bin/sh',remote+'/cleanup.sh'],sudo=True)
        if core.run(['systemctl','is-active',tag+'-cleanup.timer']).strip()!='active':raise RuntimeError('core_rescue_not_armed')
        cleanup.append(('core',['/bin/sh',remote+'/cleanup.sh'],tag+'-cleanup.timer'))
        for s in sessions.values():core.run(['ip','route','add',s['address']+'/32','via','10.210.50.8'],sudo=True)
        # Only the background UDP client requires a source routing rule.
        s=sessions['load'];table=str(59000+int(uuid.uuid4().hex[:3],16))
        routes=json.loads(ue.run(['ip','-j','route','show','table','all']))
        rules=json.loads(ue.run(['ip','-j','rule','show']))
        assert not any(str(r.get('table'))==table for r in routes)
        assert not any(r.get('priority')==105 or str(r.get('table'))==table for r in rules)
        uremote=ue.run(['mktemp','-d','/home/emsadmin/'+tag+'-XXXXXX']).strip()
        assert re.fullmatch('/home/emsadmin/'+tag+'-[A-Za-z0-9]{6}',uremote)
        result['uremote']=uremote; result['table']=table; checkpoint(result)
        uscript='#!/bin/sh\nsystemctl stop '+tag+'-load 2>/dev/null\nip rule del priority 105 from '+s['address']+'/32 to 10.210.50.1/32 table '+table+' 2>/dev/null\nip route del 10.210.50.1/32 dev '+s['interface']+' table '+table+' 2>/dev/null\n'
        ue.write(uremote+'/cleanup.sh',uscript,0o700)
        ue.run(['systemd-run','--unit='+tag+'-route','--on-active=300s','--timer-property=AccuracySec=1s','/bin/sh',uremote+'/cleanup.sh'],sudo=True)
        if ue.run(['systemctl','is-active',tag+'-route.timer']).strip()!='active':raise RuntimeError('ue_rescue_not_armed')
        result['rescue_timers_verified']={'core':tag+'-cleanup.timer','ue':tag+'-route.timer','deadline_seconds':300}
        checkpoint(result)
        cleanup.append(('ue',['/bin/sh',uremote+'/cleanup.sh'],tag+'-route.timer'))
        ue.run(['ip','route','add','10.210.50.1/32','dev',s['interface'],'src',s['address'],'table',table],sudo=True)
        ue.run(['ip','rule','add','priority','105','from',s['address']+'/32','to','10.210.50.1/32','table',table],sudo=True)
        for port in ('18091','5210'):assert not core.run(['ss','-H','-lnt','sport = :'+port]).strip()
        for suffix,command in [('origin',['python3','-m','http.server','18091','--bind','10.210.50.1','--directory',media]),
            ('iperf',['iperf3','-s','-B','10.210.50.1','-p','5210']),
            ('capture',['tcpdump','-i','any','-U','-s','256','-c','4000','-w',remote+'/control.pcap','tcp port 8085 or tcp port 7777 or udp port 8805'])]:
            unit=tag+'-'+suffix
            core.run(['systemd-run','--unit='+unit,'--collect','--property=RuntimeMaxSec=290','--property=KillSignal=SIGINT',*command],sudo=True)
            units.append(unit)
        # Prove N6 before disabling analytics or spending the traffic budget.
        time.sleep(.5)
        probe_manifest=ue.run(['curl','--silent','--fail','--noproxy','*','--interface',sessions['video']['interface'],
            '--connect-timeout','3','--max-time','5','http://10.210.50.1:18091/index.m3u8'])
        assert probe_manifest==manifest.decode(),'Video UE has no verified N6 path'
        print('Verified live N6 manifest on '+sessions['video']['address'],flush=True)

        def sample():
            previous=None
            while not stop.wait(.8):
                try:
                    current=json.loads(upf.run(['python3','-c',COUNTERS]))
                    if previous:
                        record={'phase':phase['name'],'before':previous,'after':current}
                        record['observed_bps']=(current['tx_bytes']-previous['tx_bytes'])*8/(current['uptime']-previous['uptime'])
                        if phase['name']=='closed-loop':
                            time.sleep(.12)
                            try:record['published']=api(core,token,'POST','/management/v1/pm-observations',{'object_id':'nf:upf-01','before':previous,'after':current})
                            except RuntimeError as exc:record['publication_error']=str(exc)  # another fresh publisher may win
                        result['observations'].append(record)
                    previous=current
                except Exception as exc:result['observations'].append({'phase':phase['name'],'error':type(exc).__name__})
        sampler=threading.Thread(target=sample,daemon=True);sampler.start()
        media_lock=threading.Lock();used={'bytes':0}
        class Relay(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.path!='/start':self.send_error(404);return
                if any(item[0]==phase['name'] for item in traffic):self.send_error(409);return
                future=executor.submit(ue.run,['systemd-run','--unit='+tag+'-load','--collect','--property=RuntimeMaxSec=16','--property=StandardOutput=file:'+uremote+'/load.json','timeout','14','iperf3','--connect-timeout','3000','-c','10.210.50.1','-p','5210',
                    '-B',sessions['load']['address'],'-R','-u','-b','19M','-l','1200','-t','10','-J'],sudo=True,check=False,timeout=22)
                traffic.append((phase['name'],future))
                time.sleep(3)
                self.send_response(204);self.end_headers()
            def do_GET(self):
                check_ownership()
                asset=self.path.removeprefix('/media/')
                if asset not in originals:self.send_error(404);return
                with media_lock:
                    if used['bytes']+len(originals[asset])>sessions['video']['free']-500000:self.send_error(429);return
                    started=time.time()
                    import base64
                    command=['curl','--silent','--fail','--noproxy','*','--interface',sessions['video']['interface'],
                        '--connect-timeout','3','--max-time','15','--max-filesize','3000000','http://10.210.50.1:18091/'+asset]
                    try:
                        raw=ue.run(['python3','-c',"import subprocess,base64,sys,json; r=subprocess.run(sys.argv[1:],capture_output=True,timeout=17); print(json.dumps({'body':base64.b64encode(r.stdout).decode(),'exit_code':r.returncode}))",*command],timeout=20)
                        download=json.loads(raw)
                        payload=base64.b64decode(download['body']);code=download['exit_code']
                    except Exception as error:
                        result['transfers'].append({'phase':phase['name'],'asset':asset,'started':started,
                            'finished':time.time(),'bytes':0,'verified':False,'error':str(error),
                            'ue_interface':sessions['video']['interface']})
                        self.send_error(502);return
                    used['bytes']+=len(payload)
                    digest=hashlib.sha256(payload).hexdigest()
                    valid=code==0 and digest==result['media_sha256'][asset]
                    result['transfers'].append({'phase':phase['name'],'asset':asset,'started':started,'finished':time.time(),
                        'bytes':len(payload),'sha256':digest,'verified':valid,'exit_code':code,'ue_interface':sessions['video']['interface']})
                if not valid:self.send_error(502);return
                try:
                    self.send_response(200);self.send_header('Content-Type','application/vnd.apple.mpegurl' if asset.endswith('.m3u8') else 'video/mp4')
                    self.send_header('Content-Length',str(len(payload)));self.send_header('X-MAEstro-Path','DN-UPF-UE-SSH-relay');self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Relay);threading.Thread(target=server.serve_forever,daemon=True).start()
        with connection() as db:user=db.execute("SELECT username,role FROM users WHERE enabled=1 AND username=?", (owner,)).fetchone()
        assert user
        env=os.environ|{'MAESTRO_CHECK_TOKEN':create_token(user['username'],user['role']),'MAESTRO_CHECK_USER':user['username'],
            'NWDAF_QOE_ROLE':user['role'],
            'NWDAF_QOE_OUTPUT':str(local),'NWDAF_QOE_RELAY':'http://127.0.0.1:'+str(server.server_port)}
        checkpoint(result)
        yield {'phase': 'prepared', 'result': result}
        for case in (case,):
            phase['name']=case
            if case=='baseline':
                core.run(['systemd-run','--unit='+tag+'-nwdaf-restore','--on-active=300s','/usr/bin/systemctl','start','maestro-nwdaf'],sudo=True)
                core.run(['systemctl','stop','maestro-nwdaf'],sudo=True)
                # The native PCF has a 30-second stale-analytics fallback. This
                # wait is recorded, but is not itself effective policy proof.
                result['baseline_stabilization_seconds']=35
                for _ in range(35):
                    check_ownership();time.sleep(1)
            else:
                core.run(['systemctl','start','maestro-nwdaf'],sudo=True);time.sleep(3)
                core.run(['systemctl','stop',tag+'-nwdaf-restore.timer'],sudo=True,check=False)
            print('Executing real Stream5G '+case,flush=True)
            result['treatment_before'] = core.run(['systemctl','is-active','maestro-nwdaf'],check=False).strip()
            result['receiver_before'] = receiver_snapshot(ue, sessions['video']['interface'])
            process=subprocess.run(['node','tools/measure-stream5g-qoe.mjs'],cwd=ROOT/'frontend',env=env|{'NWDAF_QOE_PHASE':case},
                timeout=100,capture_output=True,text=True,encoding='utf-8')
            (local/(case+'-browser.log')).write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
            check_ownership()
            player=json.loads((local/(case+'-player.json')).read_text(encoding='utf-8'))
            result['cases'].append({'phase':case,'player_status':player['status']})
            if process.returncode:raise RuntimeError('Stream5G '+case+' failed; see browser evidence')
            result['receiver_after'] = receiver_snapshot(ue, sessions['video']['interface'])
            result['treatment_after'] = core.run(['systemctl','is-active','maestro-nwdaf'],check=False).strip()
        checkpoint(result)
        yield {'phase': 'played', 'result': result}
        phase['name']='recovery';core.run(['systemctl','start','maestro-nwdaf'],sudo=True)
        time.sleep(12)
        result['ledger_after']=api(core,token,'GET','/management/v1/closed-loop/history')
        result['status']='MEASURED'
    except Exception as exc:
        result['status']='FAILED';result['error']=type(exc).__name__;raise
    finally:
        result.setdefault('status','INTERRUPTED')
        stop.set()
        if sampler:sampler.join(timeout=10)
        if server:server.shutdown();server.server_close()
        for case,future in traffic:
            try:
                future.result(timeout=20)
                raw=ue.read(uremote+'/load.json').decode()
                (local/(case+'-iperf.json')).write_text(raw,encoding='utf-8')
            except Exception as exc:result[case+'_traffic_error']=type(exc).__name__
        executor.shutdown(wait=True)
        result['cleanup']={}
        for name,command,timer in reversed(cleanup):
            try:
                hosts[name].run(command,sudo=True,check=False)
                # Timers remain armed until the adapter verifies actual cleanup.
                result['cleanup'][name]='executed'
            except Exception:result['cleanup'][name]='independent timer remains'
        if 'core' in hosts:
            core=hosts['core'];core.run(['systemctl','start','maestro-nwdaf'],sudo=True)
            # The independent rescue remains armed until recovery verification.
            result['accounts_after']=accounts(core)
            result['quota_unchanged']={x['supi']:x['quota_bytes'] for x in result.get('accounts_before',[])}=={x['supi']:x['quota_bytes'] for x in result['accounts_after']}
            try:
                core.run(['chown',settings.ssh_user,remote+'/control.pcap'],sudo=True)
                core.run(['chmod','600',remote+'/control.pcap'],sudo=True)
                data=core.read(remote+'/control.pcap');(local/'control.pcap').write_bytes(data)
                result['pcap_sha256']=hashlib.sha256(data).hexdigest()
            except Exception as exc:result['pcap_export_error']=type(exc).__name__
        checkpoint(result)
        for host in hosts.values():host.client.close()
        (local/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({'run':tag,'status':result['status'],'path':str(local)}),flush=True)
