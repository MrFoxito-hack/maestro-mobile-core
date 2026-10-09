"""Paired-block statistics; fixed hypotheses, BCa intervals, exact signed ranks."""
import json
from pathlib import Path
import warnings
import numpy as np
import scipy
from scipy import stats, optimize

METRICS=['rtt_mean_ms','rtt_p50_ms','rtt_p95_ms','rtt_p99_ms','jitter_mean_ms','deadline_miss_pct']
PRIMARY=METRICS[:3]


def load_trials(root):
    from c8_thesis_plots import summarize
    exclusions=json.loads((root/'setup/exclusions.json').read_text())['runs']
    rows=[]
    for p in sorted((root/'runs').glob('*/campaign.json')):
        c=json.loads(p.read_text())
        if c['arguments']['experiment']!='urllc' or c['run_id'] in exclusions:continue
        if c['status']!='completed':raise ValueError('incomplete_campaign')
        for t in c['trials']:
            if t['config']['warmup']:continue
            raw=json.loads((p.parent/(t['name']+'-raw.json')).read_text())
            before=json.loads((p.parent/(t['name']+'-before.json')).read_text())
            after=json.loads((p.parent/(t['name']+'-after.json')).read_text())
            row,=summarize(raw,before,after)[0]
            packets=raw['streams'][0]['packets'];rtt=[p['rtt_ms'] for p in packets if p['ack']]
            row['rtt_mean_ms']=float(np.mean(rtt)) if rtt else None
            row['rtt_sd_within_ms']=float(np.std(rtt,ddof=1)) if len(rtt)>1 else None
            row['deadline_gt5_pct']=100*sum(not p['ack'] or p['rtt_ms']>5 for p in packets)/len(packets)
            row['equal_5ms_count']=sum(p['ack'] and p['rtt_ms']==5 for p in packets)
            rows.append(row)
    return rows


def paired_arrays(rows,metric):
    blocks=sorted({r['block'] for r in rows});a=[];b=[]
    for block in blocks:
        pair={}
        for mode in ['kernel','xdp']:
            r,=[r for r in rows if r['block']==block and r['mode']==mode]
            if r[metric] is None or not np.isfinite(r[metric]):raise ValueError('missing_metric_no_pair_dropping')
            pair[mode]=r[metric]
        a.append(pair['kernel']);b.append(pair['xdp'])
    return np.asarray(a),np.asarray(b)


def exact_wilcoxon(differences):
    # Integer DP enumerates all 2^m sign assignments, including midrank ties.
    d=np.round(np.asarray(differences,dtype=float),12)
    d=d[d!=0]
    if not len(d):return {'statistic_wplus':0.,'p_less':1.,'p_two_sided':1.,'nonzero_pairs':0,'method':'exact sign distribution, zero differences discarded'}
    ranks=np.rint(2*stats.rankdata(np.abs(d),method='average')).astype(int)
    counts=[1]
    for rank in ranks:
        new=[0]*(len(counts)+int(rank))
        for i,c in enumerate(counts):new[i]+=c;new[i+rank]+=c
        counts=new
    observed=int(ranks[d>0].sum());total=2**len(d)
    lower=sum(counts[:observed+1])/total;upper=sum(counts[observed:])/total
    return {'statistic_wplus':observed/2,'p_less':lower,'p_two_sided':min(1.,2*min(lower,upper)),
            'nonzero_pairs':len(d),'method':'exact DP of sign assignments, midranks, wilcox zeros, differences rounded to 12 decimals'}


def holm(pvalues):
    order=sorted(pvalues,key=pvalues.get);out={};last=0.
    for i,key in enumerate(order):
        last=max(last,min(1.,(len(order)-i)*pvalues[key]));out[key]=last
    return out


def contrast(kernel,xdp):
    a,b=np.asarray(kernel),np.asarray(xdp);d=b-a;n=len(d)
    mean=float(np.mean(d));sd=float(np.std(d,ddof=1))
    intervals={};warning_text=[]
    if sd==0:
        t={'statistic':None,'df':n-1,'p_less':1. if mean==0 else (0. if mean<0 else 1.),'p_two_sided':1. if mean==0 else 0.,'status':'degenerate_constant_differences'}
        intervals={'95':None,'99':None};dz=None
    else:
        one=stats.ttest_rel(b,a,alternative='less');two=stats.ttest_rel(b,a,alternative='two-sided')
        t={'statistic':float(one.statistic),'df':n-1,'p_less':float(one.pvalue),'p_two_sided':float(two.pvalue),'status':'computed'}
        dz=mean/sd
        for level in [.95,.99]:
            with warnings.catch_warnings(record=True) as caught:
                result=stats.bootstrap((d,),np.mean,method='BCa',confidence_level=level,n_resamples=10000,
                                       rng=np.random.default_rng(42017),batch=1000)
            ci=[float(result.confidence_interval.low),float(result.confidence_interval.high)]
            intervals[str(round(level*100))]=ci if all(np.isfinite(ci)) else None
            warning_text.extend(str(w.message) for w in caught)
    return {'n_pairs':n,'kernel_mean':float(a.mean()),'kernel_sd':float(a.std(ddof=1)),
            'xdp_mean':float(b.mean()),'xdp_sd':float(b.std(ddof=1)),
            'difference':mean,'difference_sd':sd,'cohen_dz':dz,'differences':d.tolist(),
            'bca':intervals,'bca_warnings':warning_text,'t':t,'wilcoxon':exact_wilcoxon(d),
            'shapiro_p':float(stats.shapiro(d).pvalue) if sd>0 else None,
            'lag1_correlation':float(np.corrcoef(d[:-1],d[1:])[0,1]) if n>3 and np.std(d[:-1])>0 and np.std(d[1:])>0 else None}


def analyze_rows(rows):
    result={metric:contrast(*paired_arrays(rows,metric)) for metric in METRICS}
    for family in ['t','wilcoxon']:
        adjusted=holm({m:result[m][family]['p_less'] for m in PRIMARY})
        for m,p in adjusted.items():result[m][family]['p_less_holm_primary']=p
    success=all(result[m]['bca']['95'] is not None and result[m]['bca']['95'][1]<0 and
                all(result[m][f]['p_less_holm_primary']<.01 for f in ['t','wilcoxon']) for m in PRIMARY)
    return {'metrics':result,'primary_joint_criterion_met':success,'scipy':scipy.__version__,
            'numpy':np.__version__,'bootstrap_resamples':10000,'seed':42017}


def planning_power(root):
    rows=load_trials(root);out={}
    # Conditional planning estimate, NOT achieved/post-hoc power of the new data.
    for m in PRIMARY:
        a,b=paired_arrays(rows,m);d=b-a;dz=float(-d.mean()/d.std(ddof=1))
        out[m]={'preliminary_standardized_benefit':dz}
        for alpha in [.01,.01/3]:
            critical=stats.t.ppf(alpha,19)
            power=lambda effect:float(stats.nct.cdf(critical,19,-effect*np.sqrt(20)))
            out[m][str(alpha)]={'conditional_power_n20':power(dz),
                              'benefit_dz_for_80pct_power':float(optimize.brentq(lambda effect:power(effect)-.8,0,5))}
    return {'warning':'Pilot-effect plug-in estimates are uncertain; N=20 does not guarantee high power. Assumes independent normal paired differences.',
            'endpoints':out}
