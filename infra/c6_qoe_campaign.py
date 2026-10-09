"""C6 isolated N6 acquisition. Run using backend/.venv Python, cwd backend.

No PCF/NWDAF control, quota changes, UE reconnects or historical certification.
An origin namespace owns the shaper; independent timers remove only this run's
routes, namespace and bounded auxiliary units after controller loss.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings
from app.laboratory.live import LivePreflight
from app.laboratory.qoe_transport import Lab
from app.laboratory.qoe_metrics import adaptive_p1203_document, score_p1203_locally

ORIGIN = '172.31.60.2'
GATEWAY = '172.31.60.1'
NS = 'maestro-c6'
VETH = 'c6-host'
PEER = 'c6-origin'
NETWORK = '172.31.60.0/30'


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def udp_rates(data):
    # iperf3 3.9 UDP combines sender bytes with receiver loss in end.sum.
    # Receiver goodput must come from receiver intervals, never that sum.
    intervals = [i['sum'] for i in data['intervals'] if not i['sum'].get('omitted')]
    if not intervals or any(i.get('sender') is not False for i in intervals):
        raise ValueError('receiver_intervals_required')
    seconds = sum(i['seconds'] for i in intervals)
    received = sum(i['bytes'] for i in intervals)
    sender = data['end'].get('sum_sent', data['end'].get('sum'))
    return {'sender_bps': sender['bits_per_second'], 'receiver_bps': received*8/seconds,
            'receiver_bytes': received, 'receiver_seconds': seconds, 'source': 'iperf3_receiver_intervals'}


def main():
    settings = get_settings()
    preset = json.loads((ROOT/'backend/app/laboratory/c6_preset.json').read_text())
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = ROOT/'.work/c6-qoe/evidence'/run_id
    directory.mkdir(parents=True)
    report = {'run_id': run_id, 'kind': 'live_c6_acquisition', 'preset': preset,
              'started_at': datetime.now(timezone.utc).isoformat(), 'trials': [],
              'policy_mutations': [], 'execution_status': 'preflight', 'accepted': False}
    def checkpoint(): write(directory/'campaign.json', report)
    checkpoint()
    print('Evidence: '+str(directory), flush=True)
    hosts = {}; cleanups = []; remote = {}; server = None
    sample_stop=threading.Event(); host_samples=[]
    def sample_windows():
        psutil.cpu_percent()
        while not sample_stop.wait(.5):
            host_samples.append({'time':time.time(),'cpu_percent':psutil.cpu_percent(),'ram_available_bytes':psutil.virtual_memory().available})
    threading.Thread(target=sample_windows,daemon=True).start()
    tag = 'c6-'+uuid.uuid4().hex[:10]
    try:
        bindings = {role: {'alias': role, 'supi': preset[field], 'dnn': 'internet'} for role, field in
                    [('observed', 'observed_supi'), ('competing', 'competing_supi')]}
        collector = LivePreflight()
        preflight = collector.collect(bindings, 60_000_000)
        write(directory/'preflight.json', preflight)
        write(directory/'preflight-raw.json', collector.raw)
        if preflight['errors'] or any(c['status'] != 'passed' for c in preflight['checks']):
            raise ValueError('preflight_failed')
        sessions = {k: v['session'] for k, v in preflight['subjects'].items()}
        report['sessions'] = sessions
        for name, port in [('core', settings.ssh_port), ('ue', settings.ue_ssh_port), ('upf', settings.upf_ssh_port)]:
            hosts[name] = Lab(settings, port)
        core, ue, upf = (hosts[n] for n in ('core', 'ue', 'upf'))
        # Reject collisions before arming cleanup; no deletion of preexisting objects.
        if NS in core.run(['ip', 'netns', 'list']): raise ValueError('namespace_in_use')
        if core.run(['ip', '-j', 'link', 'show', VETH], check=False).strip().startswith('['): raise ValueError('veth_in_use')
        for host in (core, upf):
            if host.run(['ip', 'route', 'show', 'exact', NETWORK]).strip(): raise ValueError('origin_route_in_use')
        if core.run(['sysctl', '-n', 'net.ipv4.ip_forward']).strip() != '1': raise ValueError('core_forwarding_disabled')
        for index, s in enumerate(sessions.values()):
            if core.run(['ip', 'route', 'show', 'exact', s['address']+'/32']).strip(): raise ValueError('ue_host_route_in_use')
            rules = json.loads(ue.run(['ip', '-j', 'rule']))
            if any(r.get('priority') == 106+index or str(r.get('table')) == str(60106+index) for r in rules): raise ValueError('routing_slot_in_use')
        for name, host in hosts.items():
            remote[name] = host.run(['mktemp', '-d', '/home/emsadmin/'+tag+'-XXXXXX']).strip()
        report['remote'] = remote
        # Arm bounded independent recovery BEFORE the first network mutation.
        scripts = {'core': ['#!/bin/sh', f'systemctl stop {tag}-origin {tag}-iperf {tag}-capture {tag}-sample 2>/dev/null',
                            f'ip netns del {NS} 2>/dev/null', f'ip link del {VETH} 2>/dev/null'],
                   'ue': ['#!/bin/sh', f'systemctl stop {tag}-load {tag}-capture 2>/dev/null'],
                   'upf': ['#!/bin/sh', f'systemctl stop {tag}-capture 2>/dev/null', f'ip route del {NETWORK} via 10.210.50.1 2>/dev/null']}
        for index, s in enumerate(sessions.values()):
            scripts['core'].append(f"ip route del {s['address']}/32 via 10.210.50.8 2>/dev/null")
            scripts['ue'] += [f"ip rule del priority {106+index} from {s['address']}/32 to {ORIGIN}/32 table {60106+index} 2>/dev/null",
                              f"ip route del {ORIGIN}/32 dev {s['interface']} table {60106+index} 2>/dev/null"]
        for name, host in hosts.items():
            cleanup = remote[name]+'/cleanup.sh'
            host.write(cleanup, '\n'.join(scripts[name])+ '\nexit 0\n', 0o700)
            host.run(['systemd-run', '--unit='+tag+'-rescue', '--on-active=900s', '--timer-property=AccuracySec=1s', '/bin/sh', cleanup], sudo=True)
            if host.run(['systemctl', 'is-active', tag+'-rescue.timer']).strip() != 'active': raise ValueError('rescue_not_armed')
            cleanups.append((host, cleanup))
        report['recovery_timers_armed'] = True
        checkpoint()
        core.run(['ip', 'netns', 'add', NS], sudo=True)
        core.run(['ip', 'link', 'add', VETH, 'type', 'veth', 'peer', 'name', PEER], sudo=True)
        core.run(['ip', 'link', 'set', PEER, 'netns', NS], sudo=True)
        core.run(['ip', 'addr', 'add', GATEWAY+'/30', 'dev', VETH], sudo=True)
        core.run(['ip', 'link', 'set', VETH, 'up'], sudo=True)
        def ns(args, **kw): return core.run(['ip', 'netns', 'exec', NS, *args], sudo=True, **kw)
        ns(['ip', 'addr', 'add', ORIGIN+'/30', 'dev', PEER])
        ns(['ip', 'link', 'set', PEER, 'up']); ns(['ip', 'link', 'set', 'lo', 'up'])
        ns(['ip', 'route', 'add', 'default', 'via', GATEWAY])
        upf.run(['ip', 'route', 'add', NETWORK, 'via', '10.210.50.1'], sudo=True)
        for index, s in enumerate(sessions.values()):
            core.run(['ip', 'route', 'add', s['address']+'/32', 'via', '10.210.50.8'], sudo=True)
            ue.run(['ip', 'route', 'add', ORIGIN+'/32', 'dev', s['interface'], 'src', s['address'], 'table', str(60106+index)], sudo=True)
            ue.run(['ip', 'rule', 'add', 'priority', str(106+index), 'from', s['address']+'/32', 'to', ORIGIN+'/32', 'table', str(60106+index)], sudo=True)
        report['routes'] = {'core': core.run(['ip', '-j', 'route']), 'upf': upf.run(['ip', '-j', 'route']),
                            'ue': {r: ue.run(['ip', '-j', 'route', 'get', ORIGIN, 'from', s['address']]) for r, s in sessions.items()}}
        report['ue_pdus'] = {r: ue.run(['/home/emsadmin/UERANSIM/build/nr-cli', bindings[r]['supi'], '--exec', 'ps-list']) for r in sessions}
        media = remote['core']+'/media'
        core.run(['mkdir', media])
        probes = {}; assets = {}
        for level, bitrate in enumerate((250, 1000)):
            folder = media+'/'+str(level); core.run(['mkdir', folder])
            core.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25',
                      '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '24', '-c:v', 'libx264', '-preset', 'veryfast',
                      '-threads', '2', '-pix_fmt', 'yuv420p', '-b:v', str(bitrate)+'k', '-minrate', str(bitrate)+'k', '-maxrate', str(bitrate)+'k',
                      '-bufsize', str(bitrate)+'k', '-x264-params', 'nal-hrd=cbr:force-cfr=1', '-g', '50', '-keyint_min', '50', '-sc_threshold', '0',
                      '-c:a', 'aac', '-ac', '2', '-b:a', '64k', '-f', 'hls', '-hls_time', '2', '-hls_playlist_type', 'vod',
                      '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4', '-hls_segment_filename', folder+'/seg%03d.m4s', folder+'/index.m3u8'], timeout=90)
            probes[str(level)] = json.loads(core.run(['ffprobe', '-v', 'error', '-show_streams', '-show_packets', '-of', 'json', folder+'/index.m3u8']))
            for asset in ['index.m3u8', 'init.mp4']+[f'seg{i:03d}.m4s' for i in range(12)]:
                assets[str(level)+'/'+asset] = core.read(folder+'/'+asset)
        master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=330000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2"\n0/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1100000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2"\n1/index.m3u8\n'
        core.write(media+'/master.m3u8', master); assets['master.m3u8'] = master.encode()
        write(directory/'ffprobe.json', probes)
        report['media_sha256'] = {n: hashlib.sha256(b).hexdigest() for n,b in assets.items()}
        localmedia=directory/'media'; localmedia.mkdir()
        for name,data in assets.items():
            target=localmedia/name;target.parent.mkdir(exist_ok=True);target.write_bytes(data)
        core.start(tag+'-origin', ['ip', 'netns', 'exec', NS, 'python3', '-m', 'http.server', '18096', '--bind', ORIGIN, '--directory', media], remote['core'])
        core.start(tag+'-iperf', ['ip', 'netns', 'exec', NS, 'iperf3', '-s', '-B', ORIGIN, '-p', '5216', '-J'], remote['core'])
        # Captures link origin, N6/UPF and the two actual UE TUNs.
        for name, interface in [('core', VETH), ('upf', 'any'), ('ue', 'any')]:
            hosts[name].start(tag+'-capture', ['tcpdump', '-i', interface, '-U', '-s', '128', '-w', remote[name]+'/path.pcap', 'host '+ORIGIN], remote[name], properties=('--property=KillSignal=SIGINT',))
        time.sleep(.5)
        for role, s in sessions.items():
            manifest=ue.run(['curl', '--fail', '--silent', '--noproxy', '*', '--interface', s['interface'], '--max-time', '8', f'http://{ORIGIN}:18096/master.m3u8'])
            if manifest != master: raise ValueError('unverified_origin_path_'+role)
        ns(['tc', 'qdisc', 'add', 'dev', PEER, 'root', 'handle', '6:', 'tbf', 'rate', '1500kbit', 'burst', '8kb', 'latency', '200ms'])
        ns(['tc', 'qdisc', 'add', 'dev', PEER, 'parent', '6:1', 'handle', '60:', 'fq_codel', 'limit', '128', 'target', '5ms', 'interval', '100ms'])
        # Kernel qdisc counters and host CPU sampled remotely, avoiding SSH polling load.
        sampler = '''import json,time,subprocess,pathlib
out=open(__import__('sys').argv[1],'w',buffering=1)
end=time.monotonic()+700
while time.monotonic()<end:
 q=json.loads(subprocess.check_output(['ip','netns','exec','maestro-c6','tc','-j','-s','qdisc','show','dev','c6-origin']))
 out.write(json.dumps({'time':time.time(),'monotonic':time.monotonic(),'qdisc':q,'cpu':pathlib.Path('/proc/stat').read_text().splitlines()[0],'loadavg':pathlib.Path('/proc/loadavg').read_text()})+'\\n')
 time.sleep(.2)
'''
        core.write(remote['core']+'/sample.py', sampler)
        core.start(tag+'-sample', ['python3', remote['core']+'/sample.py', remote['core']+'/samples.jsonl'], remote['core'])
        report['calibration'] = []
        for role, s in sessions.items():
            raw=ue.run(['iperf3','-c',ORIGIN,'-p','5216','-B',s['address'],'-R','-u','-b','3M','-l','1200','-t','6','-J','--get-server-output'],timeout=15)
            data=json.loads(raw);write(directory/('calibration-'+role+'.json'),data)
            if 'error' in data: raise ValueError('calibration_traffic_failed')
            entry={'role':role, **udp_rates(data)}
            report['calibration'].append(entry)
            if not 1_000_000 < entry['receiver_bps'] < 1_700_000: raise ValueError('capacity_calibration_failed')
        report['execution_status']='acquiring'; checkpoint()
        # Local browser receives only bytes fetched on the observed UE interface.
        transfers=[]; lock=threading.Lock(); current={'trial':''}
        class Relay(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                if self.path=='/':
                    body=b'<html><body><h1>C6 eMBB / UE 001</h1><video muted playsinline width="640" height="360"></video></body></html>'
                    self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body);return
                asset=self.path.removeprefix('/media/')
                if asset not in assets: self.send_error(404);return
                started=time.time()
                received=bytearray();delivered=0;aborted=False
                with lock:
                    command=['curl','--fail','--silent','--noproxy','*','--interface',sessions['observed']['interface'],'--max-time','45','--max-filesize','1000000',f'http://{ORIGIN}:18096/'+asset]
                    stdin,stdout,stderr=ue.client.exec_command(shlex.join(['timeout','--kill-after=2','47',*command]),timeout=50)
                    stdin.channel.shutdown_write()
                    headers=False
                    while True:
                        block=stdout.read(8192)
                        if not block:break
                        received.extend(block)
                        if len(received)>1_000_000:stdout.channel.close();raise ValueError('media_response_limit')
                        if not aborted:
                            try:
                                if not headers:
                                    self.send_response(200);self.send_header('Content-Length',str(len(assets[asset])));self.send_header('Content-Type','application/vnd.apple.mpegurl' if asset.endswith('.m3u8') else 'video/mp4');self.end_headers();headers=True
                                self.wfile.write(block);delivered+=len(block)
                            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):aborted=True
                    code=stdout.channel.recv_exit_status();stdout.channel.close()
                digest=hashlib.sha256(received).hexdigest();valid=code==0 and digest==report['media_sha256'][asset]
                transfers.append({'trial':current['trial'],'asset':asset,'started':started,'finished':time.time(),'bytes':len(received),'sha256':digest,'verified':valid,'delivered_bytes':delivered,'browser_aborted':aborted,'ue_interface':sessions['observed']['interface'],'relay_mode':'streaming_8192_bytes'})
                if not headers:
                    try:self.send_error(502)
                    except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Relay)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        for phase in ('baseline-1','congestion-1','congestion-2','baseline-2'):
            print('Playback '+phase,flush=True);current['trial']=phase
            trial={'phase':phase,'started':time.time()}
            # Same 1.5 Mb/s shared capacity for both conditions; only background changes.
            if phase.startswith('congestion'):
                ue.run(['systemd-run','--unit='+tag+'-load','--collect','--property=RuntimeMaxSec=35',
                        '--property=StandardOutput=file:'+remote['ue']+'/'+phase+'-load.json',
                        'python3','-c','import time,os,sys;time.sleep(5);os.execvp(sys.argv[1],sys.argv[1:])',
                        'iperf3','-c',ORIGIN,'-p','5216','-B',sessions['competing']['address'],'-R','-u','-b','3M','-l','1200','-t','16','-J','--get-server-output'],sudo=True)
                trial['load_schedule']={'delay_seconds':5,'duration_seconds':16}
            process=subprocess.run(['node','tools/measure-c6-qoe.mjs','http://127.0.0.1:'+str(server.server_port),str(directory/(phase+'-player.json'))],cwd=ROOT/'frontend',capture_output=True,text=True,timeout=125)
            (directory/(phase+'-browser.log')).write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
            trial['finished']=time.time()
            player=json.loads((directory/(phase+'-player.json')).read_text())
            trial['ended']=player['ended']
            if process.returncode: raise ValueError('player_failed_'+phase)
            doc=adaptive_p1203_document(probes,player);score=score_p1203_locally(doc)
            write(directory/(phase+'-p1203.json'),{'input':doc,'score':score})
            trial.update(startup_seconds=player['startup_seconds'],stalls=player['stalls'],switches=player['switches'],score=score)
            if phase.startswith('congestion'):
                load=json.loads(ue.read(remote['ue']+'/'+phase+'-load.json'))
                write(directory/(phase+'-load.json'),load)
                rates=udp_rates(load)
                trial['offered_bps']=rates['sender_bps']
                trial['received_bps']=rates['receiver_bps']
                trial['receiver_bytes']=rates['receiver_bytes']
            report['trials'].append(trial);write(directory/'transfers.json',transfers);checkpoint()
        report['all_transfers_verified']=all(t['verified'] for t in transfers)
        report['execution_status']='completed'
    except Exception as error:
        report['execution_status']='failed'
        report['error']=str(error)
        print(type(error).__name__+': '+str(error),flush=True)
    finally:
        sample_stop.set();write(directory/'windows-host-samples.json',host_samples)
        if server: server.shutdown();server.server_close()
        # Collect captures after flushing them; recovery is attempted on every node.
        for name,host in hosts.items():
            if name not in remote: continue
            host.run(['systemctl','stop',tag+'-capture'],sudo=True,check=False)
            if name=='core': host.run(['systemctl','stop',tag+'-sample'],sudo=True,check=False)
            for filename in (['path.pcap','samples.jsonl'] if name=='core' else ['path.pcap']):
                try:
                    host.run(['chown',settings.ssh_user,remote[name]+'/'+filename],sudo=True,check=False)
                    (directory/(name+'-'+filename)).write_bytes(host.read(remote[name]+'/'+filename))
                except Exception: pass
        recovery={}
        for host,cleanup in reversed(cleanups):
            try:
                host.run(['/bin/sh',cleanup],sudo=True)
                host.run(['systemctl','stop',tag+'-rescue.timer'],sudo=True)
                recovery[str(host.settings.ssh_user)+':'+cleanup]={'cleanup_completed':True}
            except Exception:
                recovery[cleanup]={'cleanup_completed':False,'independent_timer_armed':True}
        if len(hosts)==3 and cleanups:
            recovery['namespace_absent']=NS not in hosts['core'].run(['ip','netns','list'])
            recovery['origin_route_absent']=not hosts['upf'].run(['ip','route','show','exact',NETWORK]).strip()
            recovery['ue_rules_absent']=not any(r.get('priority') in (106,107) for r in json.loads(hosts['ue'].run(['ip','-j','rule'])))
            recovery['core_host_routes_absent']=all(not hosts['core'].run(['ip','route','show','exact',s['address']+'/32']).strip() for s in report.get('sessions',{}).values())
        report['recovery']=recovery
        report['finished_at']=datetime.now(timezone.utc).isoformat();checkpoint()
        for host in hosts.values(): host.client.close()
        write(directory/'manifest.json',{'files':[{'path':p.relative_to(directory).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json']})
        print(json.dumps({'path':str(directory),'status':report['execution_status'],'error':report.get('error'),'trials':report['trials'],'recovery':recovery}),flush=True)
    return 0 if report['execution_status']=='completed' else 1

if __name__=='__main__': raise SystemExit(main())
