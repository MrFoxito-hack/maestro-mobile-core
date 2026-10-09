"""Read-only C6 acquisition validation and offline Qwen comparison."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import struct
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.laboratory.c6_advisor import deterministic, diagnose
from app.laboratory.investigation.benchmark import gpu, model_processes
import psutil


def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')


def pcap_summary(path):
    """Decode bounded IPv4 headers from Ethernet and Linux cooked captures."""
    raw=path.read_bytes();magic=raw[:4]
    endian='<' if magic in (b'\xd4\xc3\xb2\xa1',b'\x4d\x3c\xb2\xa1') else '>'
    link=struct.unpack(endian+'I',raw[20:24])[0];offset=24;flows={};count=0
    while offset+16<=len(raw):
        sec,usec,size,original=struct.unpack(endian+'4I',raw[offset:offset+16]);offset+=16
        packet=raw[offset:offset+size];offset+=size
        if len(packet)!=size:raise ValueError('truncated_capture')
        count+=1
        if link==1: skip=14;protocol=int.from_bytes(packet[12:14],'big');interface=None
        elif link==113:skip=16;protocol=int.from_bytes(packet[14:16],'big');interface=None
        elif link==276:skip=20;protocol=int.from_bytes(packet[:2],'big');interface=int.from_bytes(packet[4:8],'big')
        else:raise ValueError('unsupported_linktype_'+str(link))
        ip=packet[skip:]
        if protocol!=0x800 or len(ip)<20:continue
        src='.'.join(map(str,ip[12:16]));dst='.'.join(map(str,ip[16:20]));proto=ip[9];ihl=(ip[0]&15)*4
        sport,dport=struct.unpack('!HH',ip[ihl:ihl+4]) if proto in (6,17) and len(ip)>=ihl+4 else (0,0)
        key=(src,dst,proto,sport,dport,interface)
        item=flows.setdefault(key,{'src':src,'dst':dst,'protocol':proto,'source_port':sport,'destination_port':dport,'ifindex':interface,'packets':0,'ip_bytes':0})
        item['packets']+=1;item['ip_bytes']+=int.from_bytes(ip[2:4],'big')
    return {'linktype':link,'captured_packets':count,'flows':list(flows.values()),'sha256':hashlib.sha256(raw).hexdigest()}


def cpu_percent(samples):
    values=[]
    for a,b in zip(samples,samples[1:]):
        first=list(map(int,a['cpu'].split()[1:9]));last=list(map(int,b['cpu'].split()[1:9]))
        delta=[y-x for x,y in zip(first,last)];total=sum(delta)
        if total>0:values.append(100*(total-delta[3]-delta[4])/total)
    return {'mean':statistics.mean(values) if values else None,'peak':max(values,default=None)}


def analyze(directory):
    campaign=json.loads((directory/'campaign.json').read_text())
    samples=[json.loads(s) for s in (directory/'core-samples.jsonl').read_text().splitlines()]
    transfers=json.loads((directory/'transfers.json').read_text())
    captures={n:pcap_summary(directory/(n+'-path.pcap')) for n in ('core','upf','ue')}
    windows=json.loads((directory/'windows-host-samples.json').read_text()) if (directory/'windows-host-samples.json').exists() else []
    metrics=[]; cases=[]
    for index,trial in enumerate(campaign['trials']):
        sample=[s for s in samples if trial['started']<=s['time']<=trial['finished']]
        queues=[s['qdisc'][0] for s in sample]
        transfer=[t for t in transfers if t['trial']==trial['phase'] and t['asset'].endswith('.m4s')]
        unique_payload=sum(t['bytes'] for t in {t['asset']:t for t in transfer if t['verified']}.values())
        player=json.loads((directory/(trial['phase']+'-player.json')).read_text())
        duration=trial['finished']-trial['started']
        metric={'phase':trial['phase'],'startup_seconds':trial['startup_seconds'],
                'stall_seconds':sum(s[1] for s in trial['stalls']),'stall_count':len(trial['stalls']),
                'mos':trial['score']['mos'],'queue_peak_bytes':max(q['backlog'] for q in queues),
                'queue_drops':queues[-1]['drops']-queues[0]['drops'],
                'queue_overlimits':queues[-1]['overlimits']-queues[0]['overlimits'],
                'capacity_bps':queues[0]['options']['rate']*8,
                'shaper_tx_bps':(queues[-1]['bytes']-queues[0]['bytes'])*8/(sample[-1]['time']-sample[0]['time']),
                'core_cpu':cpu_percent(sample),'windows_cpu_mean':statistics.mean([s['cpu_percent'] for s in windows if trial['started']<=s['time']<=trial['finished']]) if windows else None,
                'video_payload_bytes':sum(t['bytes'] for t in transfer),
                'video_unique_payload_bytes':unique_payload,
                'video_session_goodput_bps':unique_payload*8/duration,
                'competitor_offered_bps':trial.get('offered_bps',0),'competitor_received_bps':trial.get('received_bps',0),
                'representation_levels_played':list(dict.fromkeys(f['level'] for f in player['fragments'])),
                'all_media_verified':all(t['verified'] for t in transfer)}
        metrics.append(metric)
        # Hide case identity, labels, treatment names and filenames from the model.
        context={'observations':[
            {'id':'player','values':{'startup_seconds':metric['startup_seconds'],'stall_seconds':metric['stall_seconds'],'p1203_mos':metric['mos']}},
            {'id':'network','values':{'capacity_bps':metric['capacity_bps'],'offered_bps':metric['competitor_offered_bps'],
                                     'queue_peak_bytes':metric['queue_peak_bytes'],'queue_drops':metric['queue_drops'],'received_bps':metric['competitor_received_bps']}},
            {'id':'host','values':{'host_cpu_percent':metric['core_cpu']['mean']}},
            {'id':'transport','values':{'media_verified':metric['all_media_verified'],'http_errors':0}}]}
        cases.append({'id':'live-'+str(index+1),'source':'measured_campaign','label':'n6_congestion' if trial['phase'].startswith('congestion') else 'normal','context':context})
    paths={}
    for role,session in campaign['sessions'].items():
        addr=session['address']
        paths[role]={n:any(f['src']=='172.31.60.2' and f['dst']==addr and f['packets']>0 for f in captures[n]['flows']) for n in ('upf','ue')}
    # Origin may see NAT addresses. UE interfaces and UPF capture anchor identity.
    recovery=campaign['recovery']
    checks={'four_completed_trials':len(metrics)==4 and all(t['ended'] for t in campaign['trials']),
            'both_ue_paths_captured':all(all(p.values()) for p in paths.values()),
            'all_media_sha256_verified':all(t['verified'] for t in transfers),
            'capacity_calibrated_both_ues':len(campaign['calibration'])==2 and all(1e6<c['receiver_bps']<1.7e6 for c in campaign['calibration']),
            'congestion_measured':all(m['queue_drops']>0 and m['queue_peak_bytes']>0 and m['competitor_offered_bps']>m['capacity_bps'] and m['competitor_received_bps']>0 for m in metrics if m['phase'].startswith('congestion')),
            'real_representation_switch':any(len(m['representation_levels_played'])>1 for m in metrics),
            'real_stalls_observed':any(m['stall_count']>0 for m in metrics),
            'p1203_available':all(t['score']['status']=='estimated' for t in campaign['trials']),
            'recovery_verified':all(v.get('cleanup_completed',False) if isinstance(v,dict) else v is True for v in recovery.values()),
            'core_cpu_bounded':all(m['core_cpu']['mean']<50 for m in metrics),
            'windows_cpu_bounded':bool(windows) and all(m['windows_cpu_mean']<70 for m in metrics),
            'no_policy_mutations':campaign['policy_mutations']==[]}
    if len(metrics)==4:
        checks['core_cpu_condition_difference_below_10pp']=abs(statistics.mean(m['core_cpu']['mean'] for m in metrics if m['phase'].startswith('baseline'))-statistics.mean(m['core_cpu']['mean'] for m in metrics if m['phase'].startswith('congestion')))<10
        if windows:
            checks['windows_cpu_condition_difference_below_15pp']=abs(statistics.mean(m['windows_cpu_mean'] for m in metrics if m['phase'].startswith('baseline'))-statistics.mean(m['windows_cpu_mean'] for m in metrics if m['phase'].startswith('congestion')))<15
    report={'scope':'c6_operational_qoe_campaign','checks':checks,'accepted':all(checks.values()),'metrics':metrics,'paths':paths,
            'limitations':['Two trials per condition; no population-level causal or significance claim.',
                           'SSH relay contributes to observed startup; video content is a synthetic test pattern decoded in a real player.',
                           'Offered load field for the advisor is measured UDP competitor load; TCP video also shares the shaper.',
                           'P.1203 is an objective estimate, not a subjective viewer study.']}
    write(directory/'capture-analysis.json',captures);write(directory/'analysis.json',report)
    return cases,report


async def benchmark(directory,cases):
    # Additional boundary cases explicitly synthetic, separate from live accuracy.
    def ctx(v):return {'observations':[{'id':'case','values':v}]}
    cases += [
        {'id':'synthetic-origin','source':'synthetic_labeled','label':'media_origin','context':ctx({'http_errors':5,'queue_drops':0,'capacity_bps':1500000,'offered_bps':0,'startup_seconds':15,'host_cpu_percent':15})},
        {'id':'synthetic-host','source':'synthetic_labeled','label':'host_overload','context':ctx({'http_errors':0,'queue_drops':0,'capacity_bps':1500000,'offered_bps':0,'startup_seconds':10,'stall_seconds':8,'host_cpu_percent':98})},
        {'id':'synthetic-missing','source':'synthetic_labeled','label':'insufficient_evidence','context':ctx({'p1203_mos':1.5})},
        {'id':'synthetic-normal','source':'synthetic_labeled','label':'normal','context':ctx({'http_errors':0,'queue_drops':0,'capacity_bps':1500000,'offered_bps':0,'startup_seconds':1,'stall_seconds':0,'host_cpu_percent':12})}]
    runs=[];samples=[];stop=threading.Event()
    def sample():
        psutil.cpu_percent()
        while not stop.is_set():
            try:g=gpu()
            except Exception:g=None
            samples.append({'time':time.time(),'gpu':g,'cpu_percent':psutil.cpu_percent(),'processes':model_processes()})
            stop.wait(.5)
    worker=threading.Thread(target=sample,daemon=True);worker.start()
    try:
        for case in cases:
            before=time.perf_counter();rule=deterministic(case['context']);rule_time=time.perf_counter()-before
            try:result=await diagnose(case['context'])
            except Exception as error:result={'valid':False,'error':type(error).__name__}
            runs.append({**case,'rule':rule,'rule_seconds':rule_time,'llm':result})
            write(directory/'advisor-runs.json',runs)
            print(json.dumps({'case':case['id'],'rule':rule,'llm':result.get('result',{}).get('diagnosis'),'valid':result['valid'],'seconds':result.get('metadata',{}).get('wall_seconds')}),flush=True)
    finally:
        stop.set();worker.join(5)
    groups={}
    for source in ('measured_campaign','synthetic_labeled'):
        selected=[r for r in runs if r['source']==source];normal=[r for r in selected if r['label']=='normal']
        correct=lambda r:r['llm'].get('valid') and r['llm']['result']['diagnosis']==r['label']
        groups[source]={'n':len(selected),'rule_accuracy':sum(r['rule']==r['label'] for r in selected)/len(selected),
            'llm_accuracy':sum(bool(correct(r)) for r in selected)/len(selected),
            'false_positive_rate':sum(r['llm'].get('valid') and r['llm']['result']['diagnosis'] not in ('normal','insufficient_evidence') for r in normal)/len(normal),
            'invalid_proposal_rate':sum(not r['llm']['valid'] for r in selected)/len(selected),
            'abstention_rate':sum(r['llm'].get('result',{}).get('diagnosis')=='insufficient_evidence' for r in selected)/len(selected),
            'cited_evidence_valid_rate':sum(r['llm']['valid'] for r in selected)/len(selected)}
        groups[source]['incremental_accuracy']=groups[source]['llm_accuracy']-groups[source]['rule_accuracy']
    times=[r['llm']['metadata']['wall_seconds'] for r in runs if 'metadata' in r['llm']]
    summary={'groups':groups,'latency_seconds':{'mean':statistics.mean(times) if times else None,'max':max(times,default=None)},
             'host_cpu_mean_percent':statistics.mean(s['cpu_percent'] for s in samples),
             'gpu_peak_used_mib':max((s['gpu']['used_mib'] for s in samples if s['gpu']),default=None),
             'gpu_peak_percent':max((s['gpu']['gpu_percent'] for s in samples if s['gpu']),default=None),
             'scope':'offline_read_only_after_acquisition','network_actions':[],
             'interpretation':'Small labeled diagnostic evaluation; no evidence of generalization or network control efficacy.'}
    write(directory/'advisor-resource-samples.json',samples);write(directory/'advisor-summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);parser.add_argument('--llm',action='store_true');args=parser.parse_args()
    cases,report=analyze(args.directory)
    print(json.dumps({'accepted':report['accepted'],'checks':report['checks'],'metrics':report['metrics']}),flush=True)
    if args.llm:asyncio.run(benchmark(args.directory,cases))
    return 0 if report['accepted'] else 2

if __name__=='__main__':raise SystemExit(main())
