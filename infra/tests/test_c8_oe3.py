"""Reject false real-time and inference claims independently of acquisition."""
import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from c8_oe3 import scheduler_identity, empirical_criteria


def scheduler_record():
    hosts={}
    for port,units in {'2226':['maestro-ue-vehicle','ueransim-ue-05'],
                       '2225':['ueransim-gnb'],'2223':['open5gs-upfd-urllc','maestro-terminal-echo-mec']}.items():
        hosts[port]=[]
        for unit in units:
            state={'unit':unit,'pid':100,'threads':[{'tid':100,'policy':2,'priority':10,'affinity':[0,1]}]}
            hosts[port].append({'before':copy.deepcopy(state),'after':copy.deepcopy(state)})
    return {'success':True,'hosts':hosts}


def test_all_critical_threads_must_be_real_time():
    record=scheduler_record()
    assert len(scheduler_identity(record))==5
    record['hosts']['2226'][1]['after']['threads'].append({'tid':101,'policy':0,'priority':0,'affinity':[0,1]})
    with pytest.raises(AssertionError,match='not_SCHED_RR_10'):scheduler_identity(record)


def test_scheduler_cannot_omit_critical_service():
    record=scheduler_record();record['hosts']['2223'].pop()
    with pytest.raises(AssertionError):scheduler_identity(record)


def test_criteria_do_not_confuse_acquisition_with_superiority():
    metric={'bca':{'95':[-1,-.1]},'difference':-.5,
            't':{'p_less_holm_primary':.01},'wilcoxon':{'p_less_holm_primary':.02}}
    stats={'extended':{'metrics':{k:copy.deepcopy(metric) for k in ['rtt_p50_ms','rtt_p95_ms','rtt_p99_ms']}},
           'pooled_descriptive_only':[{'ack':30000,'attempted':30000},{'ack':30000,'attempted':30000}]}
    certificate={'realtime_evidence':{'formal_bpf_totals':{'xdp':{'UL_OK':30000,'DL_OK':30000}}}}
    assert all(empirical_criteria(stats,certificate).values())
    stats['extended']['metrics']['rtt_p95_ms']['bca']['95'][1]=0
    stats['pooled_descriptive_only'][0]['ack']-=1
    certificate['realtime_evidence']['formal_bpf_totals']['xdp']['DL_OK']=29939
    result=empirical_criteria(stats,certificate)
    assert not result['p95_bca95_strictly_negative']
    assert not result['all_60000_packets_delivered']
    assert not result['generic_xdp_formal_forwarding_ge_99_8_pct']
