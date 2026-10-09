"""Fixed 20-pair campaign, with same-process/socket warmup and boundary handshake."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'.work/c8-campaign/ieee-2vcpu'
os.environ['C8_CAMPAIGN_ROOT']=str(DEST)
os.environ['C8_ROLLBACK_SECONDS']='3600'
from c8_remote import LoggedLab, get_settings
from c8_acquire import sessions, snapshot
from c8_xdp_window import xdp_counters
from c8_2vcpu_scheduler import verify
from c8_qos import snapshot as qos_snapshot, assert_ready


def dump(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def call(script,*args):
    subprocess.run([sys.executable,str(ROOT/'infra'/script),*args],cwd=ROOT,check=True)


def order():
    rows=[['kernel','xdp'],['xdp','kernel']]*10
    random.Random(42017).shuffle(rows)
    return rows


def host_gate(label='campaign-host-gate'):
    subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT/'infra/c8_2vcpu_host_noise.ps1'),'-Action','Apply'],check=True,cwd=ROOT)
    subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT/'infra/c8_2vcpu_pin.ps1'),
                    '-VerifyOnly','-Label',label,'-ExpectedPriority','Normal'],check=True,cwd=ROOT)


def preregister():
    host_gate()
    p=DEST/'setup/preregistered-design.json'
    assert not p.exists() and not list((DEST/'runs').glob('*'))
    record={'created_at':datetime.now(timezone.utc).isoformat(),'blocks':20,'duration_s':15,
            'pps':100,'formal_packets':60000,'seed':42017,'order':order(),'warmup_s':5,
            'warmup':'500 excluded packets on the same process and UDP socket; boundary handshake before formal acquisition',
            'primary_metrics':['rtt_mean_ms','rtt_p50_ms','rtt_p95_ms'],
            'secondary_metrics':['rtt_p99_ms','jitter_mean_ms','deadline_miss_pct'],
            'tests':{'t':'paired Student less and two-sided','wilcoxon':'exact conditional sign distribution with Pratt zeros and midrank ties',
                     'bootstrap':'BCa mean paired differences, 10000 common resamples, seed 42017, 95% and 99%',
                     'multiplicity':'Holm separately across 3 primary endpoints for t and Wilcoxon',
                     'success':'all primary BCa95 upper <0 and both one-sided Holm p<0.01'},
            'acquisition_acceptance':'all 40 fixed runs retained; 60000 ACK; stable PDU/QER and scheduling; formal XDP UL/DL >=99.8%; kernel XDP deltas zero',
            'pilot':{'xdp_p95_ms_lt':5,'xdp_p99_ms_lt':10,'ack_share_ge':.998,'xdp_ul_dl_share_ge':.998,
                     'scope':'User-authorized experimental application RTT criteria, not a universal 3GPP requirement; kernel RTT is descriptive'},
            'amendment':{'authorization':'User request 2026-10-08: optimize sender and replace p100 gate before new pilot and N=20',
                         'sender_revision':'single-thread timerfd absolute periodic pacing and nonblocking receive; avoids sender/receiver GIL contention; all timer overruns retained',
                         'failed_optimized_pilot':'history/optimized-sleep-pilot: p95=5.34808305 ms, p99=11.21944904 ms; no formal runs; not included in inference',
                         'failed_timerfd_pilot':'history/timerfd-shared-host-pilot: p95=8.1414538 ms, p99=13.9862789 ms; no formal runs',
                         'host_intervention':'User separately authorized temporary E-core affinity for Wallpaper Engine, Chrome and Roblox; originals restored at teardown; guest and VM allocations unchanged',
                         'previous_design_sha256':hashlib.sha256((DEST/'history/original-pilot/setup/preregistered-design.json').read_bytes()).hexdigest(),
                         'prior_attempt':'history/original-pilot; all prior observations retained outside inference',
                         'normative_reference':'https://www.etsi.org/deliver/etsi_TS/122200_122299/122261/16.12.00_60/ts_122261v161200p.pdf',
                         'normative_caveat':'Section 7.2 defines scenario-specific requirements; RTT percentiles and XDP share do not prove 3GPP URLLC reliability'},
            'scheduler':{'policy':'SCHED_RR','priority':10,'critical_guest_affinity':[1]},
            'host_priority_deviation':{'requested':'High','observed_and_user_authorized':'Normal',
                'reason':'Windows denies changing hardened executors; user explicitly authorized Normal before acquisition',
                'verification':'five suplib-3rdchild executor processes, not launchers, before/after each run'},
            'guest_irq_plan':'Stop irqbalance temporarily, movable hardware IRQs and network effective IRQ affinity on vCPU0; enp0s8/murllc-host RPS mask 2 (vCPU1). This does not make all OS work exclusive to vCPU0.',
            'host_allocation':json.loads((DEST/'setup/pending-hardware-plan.json').read_text())['proposed_allocation'],
            'exclusions':'No outlier exclusion, pair dropping, imputation, replacement or significance-based stopping; pilot/warmup separately retained',
            'deadline':'RTT >=5ms or missing ACK; also report >5ms',
            'scope':'generic XDP on veth; application RTT; CPU time not PMU cycles; Windows affinity not exclusive physical reservation'}
    dump(p,record)
    (p.with_suffix('.sha256')).write_text(hashlib.sha256(p.read_bytes()).hexdigest()+'\n')
    dump(DEST/'setup/exclusions.json',{'runs':{},'decision':'fixed N; pilot outside formal runs'})
    for dirname in ['ieee','ieee-rr']:
        prior=DEST.parent/dirname
        dump(DEST/'setup'/('preserved-'+dirname+'.json'),{p.relative_to(prior).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in prior.rglob('*') if p.is_file()})
    print(json.dumps({'preregistered':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}),flush=True)


def acquire_one(block,mode,pilot=False):
    prefix='pilot-' if pilot else ''
    host_gate(f'{prefix}b{block}-{mode}-host-before')
    verify('status',f'{prefix}b{block}-{mode}-before')
    run=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-urllc-'+mode
    out=DEST/('pilot/runs' if pilot else 'runs')/run
    out.mkdir(parents=True)
    source=out/'source';source.mkdir()
    names=['c8_2vcpu_campaign.py','c8_2vcpu_traffic.py','c8_2vcpu_scheduler.py','c8_2vcpu_irq.py','c8_2vcpu_governor.py','c8_2vcpu_pin.ps1','c8_2vcpu_host_noise.ps1','c8_acquire.py','c8_remote.py','c8_xdp_window.py','c8_xdp_controller.py','c8_registry.py']
    for name in names:shutil.copyfile(ROOT/'infra'/name,source/name)
    git_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    report={'run_id':run,'git_sha':git_sha,'arguments':{'experiment':'urllc','mode':mode,'seed':42017+block,'block_start':block,'duration':15},
            'source_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in names},'status':'started','trials':[],'pilot':pilot}
    dump(out/'campaign.json',report)
    hs={k:LoggedLab(get_settings(),port,out) for k,port in [('ue',2226),('control',2226),('upf',2223),('upf2',2224)]}
    try:
        ss=asyncio.run(sessions());report['sessions']=ss;s=ss['urllc']
        qos=qos_snapshot(hs['ue']);assert_ready(qos,ss);dump(out/'qos-before.json',qos)
        spec={'slice':'urllc','source':s['address'],'interface':s['interface'],'supi':s['supi'],'target':'172.31.48.2','port':8765,'payload_bytes':64,'pps':100,'sensors':1}
        config={'run_id':run,'git_sha':git_sha,'experiment':'urllc','mode':mode,'block':block,'level':mode,'warmup':False,'seed':42017+block,'duration_s':15,'streams':[spec]}
        remote=hs['ue'].run(['mktemp','-d','/home/emsadmin/c8-2vcpu-XXXXXX']).strip()
        hs['ue'].write(remote+'/traffic.py',(source/'c8_2vcpu_traffic.py').read_bytes())
        hs['ue'].write(remote+'/config.json',json.dumps(config))
        name='001-b'+str(block)+'-'+mode
        def counters():
            return {'upf3':snapshot(hs['upf'],'upf3'),'upf2':snapshot(hs['upf2'],'upf2'),
                    'upf':snapshot(hs['upf'],'upf'),'bpf_counters':xdp_counters(hs['upf'])}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(hs['ue'].run,['python3',remote+'/traffic.py',remote+'/config.json',remote+'/raw.json'],sudo=True,timeout=60)
            deadline=time.monotonic()+20
            while True:
                try:
                    ready=json.loads(hs['control'].read(remote+'/raw.ready'));break
                except FileNotFoundError:
                    if future.done():future.result();raise RuntimeError('missing_ready')
                    if time.monotonic()>deadline:raise TimeoutError('warmup_ready_timeout')
                    time.sleep(.1)
            dump(out/'traffic-ready.json',ready)
            before=counters();dump(out/(name+'-before.json'),before)
            hs['control'].write(remote+'/raw.go','go')
            summary=json.loads(future.result())
        after=counters();dump(out/(name+'-after.json'),after)
        raw=json.loads(hs['control'].read(remote+'/raw.json'))
        dump(out/(name+'-raw.json'),raw)
        (out/'excluded-warmup-raw.json').write_bytes(hs['control'].read(remote+'/raw.warmup.json'))
        dump(out/(name+'-config.json'),config)
        report['trials'].append({'name':name,'config':config,'summary':summary})
        report['status']='completed'
        packets=raw['streams'][0]['packets']
        metrics={'run_id':run,'block':block,'mode':mode,'pilot':pilot,'sent':sum(p['send_ok'] for p in packets),'ack':sum(p['ack'] for p in packets),
                 'max_rtt_ms':max(p['rtt_ms'] for p in packets if p['ack']),
                 'bpf_delta':{k:after['bpf_counters'][k]-before['bpf_counters'][k] for k in ['UL_OK','DL_OK']}}
        metrics.update({f'p{q}_rtt_ms':float(np.quantile([p['rtt_ms'] for p in packets if p['ack']],q/100)) for q in [50,95,99]})
        metrics['sender_lateness_p99_ms']=float(np.quantile([p['sender_lateness_ms'] for p in packets],.99))
        verify('status',f'{prefix}b{block}-{mode}-after')
        host_gate(f'{prefix}b{block}-{mode}-host-after')
        qos=qos_snapshot(hs['ue']);assert_ready(qos,ss);dump(out/'qos-after.json',qos)
        print(json.dumps(metrics),flush=True)
        return metrics
    except BaseException as exc:
        report['status']='failed';report['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();dump(out/'campaign.json',report)
        for h in hs.values():h.client.close()


def assess_pilot(pilot):
    kernel,xdp=pilot
    checks={'all_delivered':all(r['ack']==r['sent']==1500 for r in pilot),
            'xdp_p95_lt5':xdp['p95_rtt_ms']<5,'xdp_p99_lt10':xdp['p99_rtt_ms']<10,
            'kernel_bpf_zero':kernel['bpf_delta']=={'UL_OK':0,'DL_OK':0},
            'xdp_share_ge99_8':all(1497<=v<=1500 for v in xdp['bpf_delta'].values())}
    return {'runs':pilot,'checks':checks,'passed':all(checks.values()),'scope':'experimental application RTT; not normative 3GPP certification'}


def run():
    host_gate()
    protocol=DEST/'setup/preregistered-design.json'
    assert hashlib.sha256(protocol.read_bytes()).hexdigest()==protocol.with_suffix('.sha256').read_text().strip()
    path=DEST/'setup/acquisition.json';assert not path.exists(),'no_silent_restart'
    record={'status':'started','started_at':datetime.now(timezone.utc).isoformat(),'completed':[]}
    dump(path,record)
    try:
        call('c8_2vcpu_governor.py')
        call('c8_2vcpu_irq.py','apply')
        call('c8_xdp_window.py','prepare')
        verify('apply','after-prepare')
        call('c8_qos.py','apply')
        call('c8_oe4_runtime.py')
        pilot=[]
        for mode in ['kernel','xdp']:
            call('c8_xdp_window.py',mode);pilot.append(acquire_one(0,mode,pilot=True));call('c8_xdp_window.py','check')
        assessment=assess_pilot(pilot)
        dump(DEST/'pilot/assessment.json',assessment)
        assert assessment['passed'],'pilot_gate_failed'
        for block,modes in enumerate(order()):
            for mode in modes:
                call('c8_xdp_window.py',mode)
                result=acquire_one(block,mode)
                call('c8_xdp_window.py','check')
                record['completed'].append(result);dump(path,record)
        record['status']='completed'
    except BaseException as exc:
        record['status']='failed';record['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        try:
            if (DEST/'setup/xdp-window.json').exists():
                try:call('c8_xdp_window.py','kernel')
                finally:call('c8_xdp_window.py','restore')
            verify('apply','after-restore')
            call('c8_qos.py','apply')
            call('c8_oe4_runtime.py')
        finally:
            try:
                if (DEST/'setup/guest-irq-isolation.json').exists():call('c8_2vcpu_irq.py','restore')
            finally:
                try:
                    subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT/'infra/c8_2vcpu_host_noise.ps1'),'-Action','Restore'],check=True,cwd=ROOT)
                finally:
                    record['finished_at']=datetime.now(timezone.utc).isoformat();dump(path,record)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--preregister',action='store_true');a=p.parse_args()
    preregister() if a.preregister else run()
