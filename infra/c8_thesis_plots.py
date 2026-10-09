"""Rebuild C8 tables and native vector figures from immutable measured records.

No SSH, network traffic, generated measurement samples or dashboard images.
Run with backend/.venv/Scripts/python.exe from any directory.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
COLORS=['#0072B2','#D55E00','#009E73','#CC79A7']
STYLES=[('-', 'o'),('--','s'),('-.','^'),(':','D')]


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def csv_write(path,rows):
    if not rows:return
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        w.writeheader();w.writerows(rows)


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def interval(values,seed=42017):
    a=np.asarray(values,dtype=float)
    if len(a)<2:return [None,None]
    rng=np.random.default_rng(seed)
    means=np.mean(a[rng.integers(0,len(a),size=(10000,len(a)))],axis=1)
    return np.quantile(means,[.025,.975],method='linear').tolist()


def quantile(values,p):
    return float(np.quantile(values,p,method='linear')) if values else None


def usage_delta(before,after,address):
    old=next((s for s in before['native']['sessions'] if s['ue_ipv4']==address),None)
    new=next((s for s in after['native']['sessions'] if s['ue_ipv4']==address),None)
    if old is None or new is None:return {'identity_stable':False}
    identity=['upf_seid','smf_seid','ue_ipv4','dnn']
    stable=all(old[k]==new[k] for k in identity) and before['native']['pid']==after['native']['pid']
    stable=stable and before['native']['generation']==after['native']['generation']
    result={'identity_stable':stable,'upf_seid':old['upf_seid'],'smf_seid':old['smf_seid'],
            'native_pid':before['native']['pid'],'native_generation':before['native']['generation']}
    for key in ['total_packets','total_octets','ul_octets','dl_octets']:
        result['urr_'+key]=sum(int(u[key]) for u in new['usage'])-sum(int(u[key]) for u in old['usage'])
    result['qer_unchanged']=old['rules']['qer']==new['rules']['qer']
    result['urr_directions']=sorted({p['source_interface'] for p in old['rules']['pdr'] if p['urr_ids']})
    return result


def summarize(raw,before,after):
    config=raw['configuration'];rows=[];bins=[]
    for stream in raw['streams']:
        spec=stream['spec'];packets=stream['packets'];duration=stream['duration_s']
        valid=[p for p in packets if p['ack']]
        rtts=[p['rtt_ms'] for p in valid]
        jitter=[abs(b['rtt_ms']-a['rtt_ms']) for a,b in zip(valid,valid[1:]) if b['sequence']==a['sequence']+1]
        nf={'urllc':'upf3','miot':'upf2','embb':'upf'}[spec['slice']]
        accounting=usage_delta(before[nf],after[nf],spec['source'])
        sent=sum(p['send_ok'] for p in packets)
        expected_packets=(sent if 0 in accounting.get('urr_directions',[]) else 0)+(len(valid) if 1 in accounting.get('urr_directions',[]) else 0)
        resource_a=before[nf]['resources'];resource_b=after[nf]['resources']
        ticks=np.asarray(resource_b['cpu'][:8])-np.asarray(resource_a['cpu'][:8])
        busy=int(ticks.sum()-ticks[3]-ticks[4]);hz=resource_a['clock_ticks']
        process=resource_b['process_ticks']-resource_a['process_ticks']
        urr=accounting.get('urr_total_packets',0)
        row={'run_id':config['run_id'],'git_sha':config['git_sha'],'experiment':config['experiment'],
             'mode':config['mode'],'block':config['block'],'level':config['level'],
             'slice':spec['slice'],'source':spec['source'],'target':spec['target'],'port':spec['port'],
             'sensors':spec['sensors'],'duration_s':duration,'offered_pps':spec['pps'],
             'attempted':len(packets),'sent':sent,'received':len(valid),'send_errors':len(packets)-sent,
             'delivery_pct':100*len(valid)/len(packets),'deadline_miss_pct':100*sum(not p['ack'] or p['rtt_ms']>=5 for p in packets)/len(packets),
             'rtt_mean_ms':float(np.mean(rtts)) if rtts else None,
             'rtt_p50_ms':quantile(rtts,.5),'rtt_p95_ms':quantile(rtts,.95),'rtt_p99_ms':quantile(rtts,.99),
             'jitter_mean_ms':float(np.mean(jitter)) if jitter else None,
             'jitter_max_ms':max(jitter) if jitter else None,
             'receiver_pps':len(valid)/duration,'receiver_mbps':len(valid)*spec['payload_bytes']*8/duration/1e6,
             'duplicates':len(stream['duplicates']),'foreign_count':stream['foreign_count'],
             **{k:v for k,v in accounting.items() if k!='urr_directions'},
             'urr_directions':','.join(map(str,accounting.get('urr_directions',[]))),
             'expected_urr_packets_from_endpoint':expected_packets,
             'urr_matches_endpoint':accounting.get('urr_total_packets')==expected_packets,
             'cpu_window_s':(resource_b['monotonic_ns']-resource_a['monotonic_ns'])/1e9,
             'vm_busy_ticks':busy,'process_ticks':process,'clock_ticks':hz,
             'vm_cpu_us_per_urr_packet':busy/hz*1e6/urr if urr>0 else None,
             'process_cpu_us_per_urr_packet':process/hz*1e6/urr if urr>0 else None}
        rows.append(row)
        for second in range(int(duration)):
            ps=[p for p in valid if second <= (p['received_ns']-stream['start_monotonic_ns'])/1e9 < second+1]
            js=[abs(b['rtt_ms']-a['rtt_ms']) for a,b in zip(ps,ps[1:]) if b['sequence']==a['sequence']+1]
            bins.append({'run_id':config['run_id'],'block':config['block'],'experiment':config['experiment'],
                         'mode':config['mode'],'level':config['level'],'slice':spec['slice'],'second':second,
                         'receiver_mbps':len(ps)*spec['payload_bytes']*8/1e6,
                         'jitter_mean_ms':float(np.mean(js)) if js else None})
    return rows,bins


def style():
    plt.rcParams.update({'font.family':'serif','font.serif':['Times New Roman','DejaVu Serif'],
        'font.size':9,'axes.labelsize':9,'axes.titlesize':9,'legend.fontsize':8,
        'xtick.labelsize':8,'ytick.labelsize':8,'mathtext.fontset':'cm',
        'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none',
        'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,
        'grid.alpha':.2,'grid.linewidth':.5,'lines.linewidth':1.25,'savefig.dpi':300})


def save(fig,path):
    # No bbox_inches='tight': retain the exact IEEE physical column width.
    fig.tight_layout(pad=.65)
    for ext in ['pdf','svg','png']:
        fig.savefig(path.with_suffix('.'+ext),dpi=300)
    plt.close(fig)


def urllc_figures(out,rows,raws):
    samples={mode:[] for mode in ['kernel','xdp']}
    for raw in raws:
        c=raw['configuration']
        if c['experiment']=='urllc':
            samples[c['mode']]+=[p['rtt_ms'] for p in raw['streams'][0]['packets'] if p['ack']]
    if not all(samples.values()):return
    fig,ax=plt.subplots(figsize=(7,3.1))
    annotations=[]
    for i,(mode,vals) in enumerate(samples.items()):
        x=np.sort(vals);y=np.arange(1,len(x)+1)/len(x)
        ls,marker=STYLES[i]
        ax.step(x,y,where='post',color=COLORS[i],linestyle=ls,marker=marker,
                markevery=max(1,len(x)//16),markersize=3,label=f'{mode.capitalize()} (n={len(x)} ACK)')
        for p in [.95,.99]:
            q=quantile(vals,p)
            ax.vlines(q,0,p,color=COLORS[i],linestyle=':',linewidth=.8)
            ax.hlines(p,0,q,color=COLORS[i],linestyle=':',linewidth=.7)
            annotations.append(f'{mode.capitalize()} $RTT_{{p{int(p*100)}}}$ = {q:.3f} ms')
    ax.axvline(5,color='0.25',linestyle='-.',linewidth=.8,label='Deadline = 5 ms')
    ax.text(.98,.12,'\n'.join(annotations),ha='right',va='bottom',transform=ax.transAxes,
            fontsize=8,bbox={'facecolor':'white','alpha':.85,'edgecolor':'none'})
    ax.set(xlabel=r'$RTT$ [ms]',ylabel=r'$F_{RTT}(r)$ [1]',ylim=(0,1.02),xlim=(0,None))
    ax.legend(loc='lower right',bbox_to_anchor=(1,.49))
    save(fig,out/'urllc_rtt_cdf')
    relevant=[r for r in rows if r['experiment']=='urllc']
    fig,axes=plt.subplots(1,2,figsize=(7,2.7))
    for ax,metric,title in zip(axes,['process_cpu_us_per_urr_packet','vm_cpu_us_per_urr_packet'],['UPF process','Shared VM (inclusive)']):
        for i,mode in enumerate(['kernel','xdp']):
            v=[r[metric] for r in relevant if r['mode']==mode and r[metric] is not None]
            if not v:continue
            mean=float(np.mean(v));lo,hi=interval(v)
            ax.errorbar(i,mean,yerr=[[mean-lo],[hi-mean]],color=COLORS[i],marker=STYLES[i][1],capsize=3)
            ax.scatter(np.full(len(v),i)+np.linspace(-.06,.06,len(v)),v,color=COLORS[i],marker=STYLES[i][1],s=12,alpha=.6)
        ax.set(xticks=[0,1],xticklabels=['Kernel','XDP'],ylabel=r'CPU / URR packet [$\mu$s]',title=title)
    save(fig,out/'urllc_cpu_per_packet')


def isolation_figure(out,bins,rows):
    data=[r for r in bins if r['experiment']=='isolation']
    if not data:return
    fig,axes=plt.subplots(1,2,figsize=(7,3.0))
    for ax,metric,label in zip(axes,['receiver_mbps','jitter_mean_ms'],['Received payload [Mbps]',r'Mean $|\Delta RTT|$ [ms]']):
        for s,slice_name in enumerate(['embb','miot','urllc']):
            for i,level in enumerate(['idle','loaded']):
                v=[r[metric] for r in data if r['slice']==slice_name and r['level']==level and r[metric] is not None]
                if not v:continue
                pos=s*3+i
                box=ax.boxplot([v],positions=[pos],widths=.6,patch_artist=True,showfliers=False,
                               medianprops={'color':'black'},whiskerprops={'linestyle':STYLES[i][0]})
                box['boxes'][0].set(facecolor=COLORS[i],alpha=.25,hatch='//' if i else '')
                ax.scatter(pos+np.linspace(-.20,.20,len(v)),v,s=7,marker=STYLES[i][1],color=COLORS[i],alpha=.55,
                           label=level.capitalize() if s==0 else None)
        ax.set(xticks=[.5,3.5,6.5],xticklabels=['eMBB','MIoT','URLLC'],ylabel=label)
        ax.legend(loc='best')
    save(fig,out/'slice_throughput_jitter')
    # Replication is the run, including losses; no packet/second pseudo-replication.
    data=[r for r in rows if r['experiment']=='isolation' and r['slice']=='urllc']
    fig,axes=plt.subplots(1,3,figsize=(7,2.9))
    for ax,metric,label in zip(axes,['delivery_pct','rtt_p99_ms','deadline_miss_pct'],
                              ['URLLC delivery [%]',r'$RTT_{p99}$ [ms]','Deadline misses [%]']):
        for i,level in enumerate(['idle','loaded']):
            v=[r[metric] for r in data if r['level']==level and r[metric] is not None]
            if not v:continue
            mean=float(np.mean(v));lo,hi=interval(v)
            ax.scatter(i+np.linspace(-.09,.09,len(v)),v,color=COLORS[i],marker=STYLES[i][1],s=16,
                       label='Runs (n=6)' if i==0 else None)
            ax.errorbar(i,mean,yerr=[[mean-lo],[hi-mean]],color='black',marker='_',markersize=12,capsize=4,
                        label='Mean; 95% CI' if i==0 else None)
        for block in sorted({r['block'] for r in data}):
            pair=[next((r[metric] for r in data if r['block']==block and r['level']==level),None) for level in ['idle','loaded']]
            if all(v is not None for v in pair):ax.plot([0,1],pair,color='0.65',linestyle=':',linewidth=.6,zorder=0)
        ax.set(xticks=[0,1],xticklabels=['Control','Concurrent'],ylabel=label,xlim=(-.35,1.35))
    axes[0].legend(fontsize=6,loc='lower left')
    save(fig,out/'slice_isolation')


def miot_figure(out,rows):
    data=[r for r in rows if r['experiment']=='miot']
    if not data:return
    sizes=[10,50,100,1000]
    fig,axes=plt.subplots(1,3,figsize=(7,2.7))
    metrics=['receiver_pps','rtt_p99_ms','urr_total_packets']
    for ax,metric,label in zip(axes,metrics,['Received [packet/s]',r'$RTT_{p99}$ [ms]','UPF URR [packet]']):
        x=[];means=[];lower=[];upper=[]
        for n in sizes:
            values=[r[metric] for r in data if r['sensors']==n and r[metric] is not None]
            if not values:continue
            lo,hi=interval(values);mean=float(np.mean(values))
            x.append(n);means.append(mean);lower.append(mean-lo);upper.append(hi-mean)
        ax.errorbar(x,means,yerr=[lower,upper],color=COLORS[0],marker='o',linestyle='-',capsize=2,label='Measured; 95% CI')
        if metric=='receiver_pps':ax.plot(sizes,np.asarray(sizes)*.2,'--s',color=COLORS[1],markersize=3,label='Offered')
        if metric=='urr_total_packets':ax.plot(sizes,np.asarray(sizes)*4,'--s',color=COLORS[1],markersize=3,label='UL + DL expected')
        ax.set(xscale='log',xticks=sizes,xticklabels=list(map(str,sizes)),xlabel='Logical sensors [1]',ylabel=label)
    axes[0].legend(fontsize=6,loc='upper left');axes[2].legend(fontsize=6,loc='upper left')
    save(fig,out/'miot_scaling')


def qoe_figure(root,out):
    source=ROOT/'.work/c6-qoe/evidence/20261007T072411Z'
    target=root/'raw/c6';target.mkdir(parents=True,exist_ok=True)
    # Consolidation only: keep C6 source/certification artifacts untouched.
    if source.exists():
        names=['campaign.json','analysis.json','executed-source.json','manifest.json',
               'ffprobe.json','transfers.json','core-samples.jsonl','core-path.pcap','upf-path.pcap','ue-path.pcap']
        names += [p.name for p in source.glob('*-p1203.json')]+[p.name for p in source.glob('*-player.json')]
        for name in names:
            if not (target/name).exists():shutil.copyfile(source/name,target/name)
        for directory in ['media','executed-source']:
            shutil.copytree(source/directory,target/directory,dirs_exist_ok=True)
    campaign=json.loads((target/'campaign.json').read_text())
    trials=campaign['trials'];origin=trials[0]['started'];rows=[]
    groups={key:[t['score']['mos'] for t in trials if t['phase'].startswith(key)] for key in ['baseline','congestion']}
    # t(.975, df=1) = tan(.475*pi); two recordings per condition.
    critical=float(np.tan(.475*np.pi))
    fig,ax=plt.subplots(figsize=(7,2.8))
    times=[];mos=[]
    for i,t in enumerate(trials):
        condition='baseline' if t['phase'].startswith('baseline') else 'congestion'
        color=COLORS[0 if condition=='baseline' else 1]
        values=groups[condition];mean=float(np.mean(values));half=critical*float(np.std(values,ddof=1))/np.sqrt(2)
        start,end=t['started']-origin,t['finished']-origin
        score=t['score']['mos'];times.append(end);mos.append(score)
        ax.fill_between([start,end],[mean-half]*2,[mean+half]*2,color=color,alpha=.18,hatch='//' if condition=='congestion' else None,
                        label='95% t CI by condition (n=2)' if i==0 else None)
        ax.plot([start,end],[mean,mean],linestyle='--',color=color,marker='s',markersize=3,label='Condition mean' if i==0 else None)
        ax.text((start+end)/2,5.28,['A1','B1','B2','A2'][i],ha='center',fontsize=8)
        rows.append({'run_id':campaign['run_id'],'phase':t['phase'],'start_elapsed_s':start,'end_elapsed_s':end,
                     'mos_p1203':score,'condition_mean':mean,'ci95_low':mean-half,'ci95_high':mean+half,
                     'replicates_per_condition':2,'ci_method':'Student t df=1, exploratory normal-model assumption'})
    ax.plot(times,mos,'-o',color='black',markersize=4,label='Observed session MOS')
    ax.set(xlabel='Campaign elapsed time [s]',ylabel=r'$\mathrm{MOS}_{P.1203}$ [1]',ylim=(1,5.5))
    ax.legend(loc='lower left',fontsize=7,ncols=2)
    save(fig,out/'qoe_abba_mos')
    csv_write(root/'datasets/qoe.csv',rows)
    return rows


def analyze(root, ieee=False):
    output=root/'figures';output.mkdir(parents=True,exist_ok=True)
    datasets=root/'datasets';datasets.mkdir(exist_ok=True)
    style();rows=[];bins=[];raws=[];campaigns=[];excluded_rows=[];historical_rows=[]
    from c8_oe4 import historical_runs
    historical = historical_runs(root)
    exclusions_path=root/'setup/exclusions.json'
    exclusions=json.loads(exclusions_path.read_text())['runs'] if exclusions_path.exists() else {}
    for campaign_file in sorted((root/'runs').glob('*/campaign.json')):
        campaign=json.loads(campaign_file.read_text());campaigns.append({'run_id':campaign['run_id'],'status':campaign['status'],
            'experiment':campaign['arguments']['experiment'],'error':campaign.get('error'),
            'exclusion_reason':exclusions.get(campaign['run_id'])})
        if campaign['arguments']['experiment']=='pilot':continue
        for trial in campaign['trials']:
            if trial['config']['warmup']:continue
            directory=campaign_file.parent;name=trial['name']
            raw=json.loads((directory/(name+'-raw.json')).read_text())
            before=json.loads((directory/(name+'-before.json')).read_text());after=json.loads((directory/(name+'-after.json')).read_text())
            r,b=summarize(raw,before,after)
            if campaign['run_id'] in historical:
                historical_rows += r
                continue
            if campaign['run_id'] in exclusions:
                excluded_rows += [dict(row,exclusion_reason=exclusions[campaign['run_id']]) for row in r]
                continue
            rows+=r;bins+=b;raws.append(raw)
    csv_write(datasets/'trials.csv',rows);csv_write(datasets/'intervals.csv',bins)
    csv_write(datasets/'excluded_trials.csv',excluded_rows)
    csv_write(datasets/'historical_isolation_trials.csv',historical_rows)
    # Lossless normalized packet table, including failures; raw JSON remains authoritative.
    with (datasets/'packets.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['run_id','git_sha','experiment','mode','block','level','slice','sequence','sensor_id','scheduled_ns','sent_ns','send_ok','ack','received_ns','rtt_ms','sensor_match','send_error']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for raw in raws:
            c=raw['configuration']
            for stream in raw['streams']:
                for packet in stream['packets']:
                    writer.writerow({**{k:c[k] for k in fields[:6]},'slice':stream['spec']['slice'],**packet})
    groups=[]
    keys=sorted({(r['experiment'],r['mode'],str(r['level']),r['slice']) for r in rows})
    for key in keys:
        relevant=[r for r in rows if (r['experiment'],r['mode'],str(r['level']),r['slice'])==key]
        g=dict(zip(['experiment','mode','level','slice'],key));g['n_runs']=len(relevant)
        for metric in ['rtt_mean_ms','rtt_p50_ms','rtt_p95_ms','rtt_p99_ms','deadline_miss_pct','delivery_pct','jitter_mean_ms','receiver_pps','receiver_mbps','urr_total_packets','vm_cpu_us_per_urr_packet','process_cpu_us_per_urr_packet']:
            values=[r[metric] for r in relevant if r.get(metric) is not None]
            g[metric]={'mean':float(np.mean(values)) if values else None,'ci95':interval(values),'n':len(values)}
        groups.append(g)
    paired={}
    for experiment,control,treatment in [('isolation','idle','loaded'),('urllc','kernel','xdp')]:
        relevant=[r for r in rows if r['experiment']==experiment and r['slice']=='urllc']
        effects={}
        for metric in ['rtt_p99_ms','jitter_mean_ms','deadline_miss_pct','delivery_pct']:
            deltas=[]
            for block in sorted({r['block'] for r in relevant}):
                a=[r for r in relevant if r['block']==block and str(r['level'])==control]
                b=[r for r in relevant if r['block']==block and str(r['level'])==treatment]
                if len(a)==len(b)==1 and a[0][metric] is not None and b[0][metric] is not None:deltas.append(b[0][metric]-a[0][metric])
            effects[metric]={'n_pairs':len(deltas),'difference_treatment_minus_control':float(np.mean(deltas)) if deltas else None,'ci95':interval(deltas),'block_differences':deltas}
        paired[experiment]=effects
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'campaigns':campaigns,'groups':groups,'paired_effects':paired,
            'trial_count':len(raws),'excluded_trial_count':len(excluded_rows),'packet_count':sum(len(s['packets']) for raw in raws for s in raw['streams']),
            'identity_stable_all':all(r['identity_stable'] for r in rows) if rows else False,
            'urr_endpoint_mismatches':[{'run_id':r['run_id'],'slice':r['slice'],'block':r['block'],'level':r['level'],
                'urr_packets':r.get('urr_total_packets'),'endpoint_expected':r['expected_urr_packets_from_endpoint']} for r in rows if not r['urr_matches_endpoint']],
            'limitations':['Repeated blocks on the same VMs; not independent physical deployments.',
                           'RTT distributions condition on ACK; losses separately included in deadline misses.',
                           'CPU VM cost includes other services and measurement overhead.',
                           'QoE has n=2 per condition; pointwise exploratory t intervals, no temporal replication.']}
    dump(datasets/'analysis.json',report)
    urllc_figures(output,rows,raws);isolation_figure(output,bins,rows);miot_figure(output,rows)
    if not ieee:qoe_figure(root,output)
    else:
        from c8_ieee_plots import extended_figures
        extended_figures(root,rows,raws)
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True)
    dump(root/'environment.json',{'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,
                                'matplotlib':matplotlib.__version__,'seed':42017,'bootstrap_resamples':10000,
                                'git_sha':git.stdout.strip() if git.returncode==0 else (rows[0]['git_sha'] if rows else None)})
    print(json.dumps({'trials':len(raws),'packets':report['packet_count'],'figures':len(list(output.glob('*.pdf'))),
                      'urr_endpoint_mismatches':len(report['urr_endpoint_mismatches'])}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'.work/c8-campaign')
    parser.add_argument('--ieee',action='store_true')
    args=parser.parse_args();analyze(args.root,args.ieee)
