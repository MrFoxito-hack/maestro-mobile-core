"""Preregister and acquire exactly 20 balanced URLLC pairs in an isolated C8 root."""
import argparse
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
from c8_remote import ROOT

DEST=Path(os.environ.get('C8_IEEE_ROOT',ROOT/'.work/c8-campaign/ieee-rr')).resolve()


def balanced_order():
    order=[['kernel','xdp'],['xdp','kernel']]*10
    random.Random(42017).shuffle(order)
    return order


def archive_previous():
    if not DEST.exists():return
    previous=json.loads((DEST/'setup/acquisition.json').read_text())
    window=json.loads((DEST/'setup/xdp-window.json').read_text())
    assert previous['status']=='completed' and window['restored'] and window['mode']=='restore'
    # Rename within the verified campaign directory, retaining every original byte.
    assert DEST.resolve().parent==(ROOT/'.work/c8-campaign').resolve()
    target=DEST.with_name('ieee-before-rr-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    assert not target.exists()
    files={p.relative_to(DEST).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in DEST.rglob('*') if p.is_file()}
    DEST.rename(target)
    record={'directory':target.name,'files':files,'status':'retained_without_pooling'}
    (target.parent/'setup/oe3-previous-campaign.json').write_text(json.dumps(record,indent=2)+'\n')


def preregister():
    from c8_ieee_stats import planning_power
    setup=DEST/'setup';setup.mkdir(parents=True,exist_ok=True)
    path=setup/'preregistered-design.json'
    if path.exists():raise ValueError('preregistration_already_exists')
    assert not list((DEST/'runs').glob('*/campaign.json'))
    archive=ROOT/'.work/c8-campaign-baseline-v1-certified.zip'
    baseline_hash=hashlib.sha256(archive.read_bytes()).hexdigest()
    assert baseline_hash=='ea40608970a94218875a880e5ed824c0b8e2d53286463a35f58f3e91d7bba3b7'
    frozen=json.loads((DEST.parent/'setup/frozen-source-start.json').read_text())
    assert len(frozen)==785 and all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in frozen.items())
    record={'created_at':datetime.now(timezone.utc).isoformat(),'blocks':20,'duration_s':15,
        'previous_n20_directory':'ieee','current_directory':DEST.name,
        'seed':42017,'order':balanced_order(),'pps':100,'payload_bytes':64,'target':'172.31.48.2:8765',
        'formal_packets':60000,'warmup_s':2,'baseline_archive_sha256':baseline_hash,
        'scheduler':{'policy':'SCHED_RR','priority':10,'application':'before prepare, after service restarts, and after restore',
                     'verification':'all critical threads before and after every run',
                     'affinity':'record current affinity; no claim of dedicated physical CPUs'},
        'requested_acceptance':{'delivery':60000,'fast_share_pct_min':99.8,
            'p50_bca95_upper_lt':0,'p50_one_sided_holm_alpha':0.05,
            'p95_and_p99_mean_paired_difference_lt':0,
            'strong_tail_claim_requires_p95_bca95_upper_lt':0},
        'primary_metrics':['rtt_mean_ms','rtt_p50_ms','rtt_p95_ms'],
        'secondary_metrics':['rtt_p99_ms','jitter_mean_ms','deadline_miss_pct'],
        'hypothesis':'Mean paired difference XDP minus Kernel < 0; no guaranteed rejection',
        'tests':{'t':'paired Student, one-sided less and two-sided',
                 'wilcoxon':'exact sign distribution of midranks; discard zeros, handle ties; symmetry assumption',
                 'multiplicity':'Holm correction across three primary endpoints, separately for each test family',
                 'cohen_d':'paired dz = mean(difference)/sample_SD(difference)',
                 'bootstrap':'BCa of mean paired difference; 10000 common block resamples, seed 42017; two-sided 95% and 99%',
                 'degeneracy':'constant differences: BCa undefined, report null; all-zero Wilcoxon p=1',
                 'success':'All three primary metrics: Holm-adjusted one-sided t and Wilcoxon p<0.01 and BCa95 upper<0'},
        'deadline':'ACK RTT >=5 ms or missing ACK; also report >5 ms and exact equality for user threshold comparison',
        'replication':'20 pairs, not 60000 independent replicates; repeated VMs, temporal dependence possible',
        'exclusions':'Only preregistered pilot/warmup or documented instrumentation/PDU failure; never RTT/p-value/outlier based. No automatic replacement of failed trials. No significance-based stopping.',
        'missing_data':'No imputation or pair dropping: missing metric invalidates corresponding contrast',
        'scope':'RTT is not internal service time; generic XDP on veth is not hardware offload. Percentiles do not certify TS 22.261.',
        'planning_power_from_preliminary':planning_power(DEST.parent),
        'rollback_seconds':3600,'frozen_source_files':len(frozen),
        'sources':['https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.ttest_rel.html',
                   'https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.bootstrap.html',
                   'https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.wilcoxon.html',
                   'https://www.etsi.org/deliver/etsi_TS/122200_122299/122261/17.16.00_60/ts_122261v171600p.pdf']}
    path.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (setup/'preregistered-design.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest()+'\n')
    (setup/'exclusions.json').write_text(json.dumps({'decision':'fixed N; no data-based exclusions','runs':{}},indent=2))
    shutil.copyfile(DEST.parent/'setup/frozen-source-start.json',setup/'frozen-source-start.json')
    print(json.dumps(record['planning_power_from_preliminary']))


def acquire():
    setup=DEST/'setup';path=setup/'preregistered-design.json'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==(setup/'preregistered-design.sha256').read_text().strip()
    protocol=json.loads(path.read_text());order=balanced_order();assert order==protocol['order']
    assert not list((DEST/'runs').glob('*/campaign.json')) and not (setup/'acquisition.json').exists(), 'no_silent_restart'
    env={**os.environ,'C8_CAMPAIGN_ROOT':str(DEST),'C8_ROLLBACK_SECONDS':'3600'}
    def call(script,*args):subprocess.run([sys.executable,str(ROOT/'infra'/script),*args],cwd=ROOT,env=env,check=True)
    (setup/'xdp-order.json').write_text(json.dumps({'seed':42017,'blocks':order},indent=2))
    source=setup/'executed-source';source.mkdir(exist_ok=True)
    for name in ['c8_run_xdp_ieee.py','c8_scheduler.py','c8_acquire.py','c8_traffic.py','c8_remote.py','c8_xdp_window.py','c8_xdp_controller.py','c8_registry.py','c8_ieee_stats.py']:
        shutil.copyfile(ROOT/'infra'/name,source/name)
    record={'started_at':datetime.now(timezone.utc).isoformat(),'status':'started','completed':[],
            'source_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in source.glob('*.py')}}
    def save():(setup/'acquisition.json').write_text(json.dumps(record,indent=2)+'\n')
    save();started=time.monotonic()
    try:
        call('c8_scheduler.py','apply','--label','before-prepare')
        call('c8_xdp_window.py','prepare');time.sleep(8)
        call('c8_scheduler.py','apply','--label','after-prepare')
        for block,modes in enumerate(order):
            for mode in modes:
                if time.monotonic()-started>3480:raise RuntimeError('rollback_deadline_near')
                call('c8_xdp_window.py',mode)
                call('c8_scheduler.py','status','--label',f'b{block}-{mode}-before')
                call('c8_acquire.py','--experiment','urllc','--mode',mode,'--blocks','1',
                     '--block-start',str(block),'--duration','15','--seed',str(42017+block))
                call('c8_scheduler.py','status','--label',f'b{block}-{mode}-after')
                call('c8_xdp_window.py','check')
                record['completed'].append({'block':block,'mode':mode,'at':datetime.now(timezone.utc).isoformat()});save()
        record['status']='completed'
    except BaseException as exc:
        record['status']='failed';record['error']=type(exc).__name__+': '+str(exc);raise
    finally:
        try:
            if (setup/'xdp-window.json').exists():
                try:call('c8_xdp_window.py','kernel')
                finally:
                    try:call('c8_xdp_window.py','restore')
                    finally:call('c8_scheduler.py','apply','--label','after-restore')
        finally:record['finished_at']=datetime.now(timezone.utc).isoformat();save()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--preregister',action='store_true')
    p.add_argument('--archive-previous',action='store_true');a=p.parse_args()
    if a.archive_previous:
        if not a.preregister:p.error('--archive-previous requires --preregister')
        archive_previous()
    preregister() if a.preregister else acquire()
