"""C8-only statistical/data-integrity checks; no C0-C7 regressions."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from copy import deepcopy

SPEC=importlib.util.spec_from_file_location('c8_plots',Path(__file__).parents[1]/'c8_thesis_plots.py')
c8=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c8)


def snapshot(packets,octets=0):
    return {'native':{'pid':123,'generation':'g1','sessions':[
        {'ue_ipv4':'10.47.0.2','upf_seid':'1','smf_seid':'2','dnn':'5g-plus',
         'rules':{'qer':[{'id':1}], 'pdr':[{'urr_ids':[1],'source_interface':0},{'urr_ids':[1],'source_interface':1}]},
         'usage':[{'total_packets':packets,'total_octets':octets,'ul_octets':0,'dl_octets':octets}]}]},
         'resources':{'cpu':[100,0,100,1000,0,0,0,0],'process_ticks':30,'clock_ticks':100,'monotonic_ns':1_000_000_000}}


def test_losses_and_boundary_are_deadline_misses_not_dropped_samples():
    packets=[{'sequence':i,'sensor_id':1,'send_ok':True,'ack':rtt is not None,
              'rtt_ms':rtt,'received_ns':int(1e9+i*1e8) if rtt else None}
             for i,rtt in enumerate([4.9,5.0,None,4.0])]
    raw={'configuration':{'run_id':'test','git_sha':'abc','experiment':'urllc','mode':'kernel','block':0,'level':'kernel'},
         'streams':[{'spec':{'slice':'urllc','source':'10.47.0.2','target':'172.31.48.2','port':8765,'sensors':1,'pps':4,'payload_bytes':64},
                     'packets':packets,'duration_s':1,'start_monotonic_ns':1_000_000_000,'duplicates':[],'foreign_count':0}]}
    rows,_=c8.summarize(raw,{'upf3':snapshot(0)},{'upf3':snapshot(7)})
    row=rows[0]
    assert row['deadline_miss_pct']==50
    assert row['delivery_pct']==75
    assert abs(row['jitter_mean_ms']-.1)<1e-12  # Do not bridge missing sequence 2.
    assert row['urr_matches_endpoint']


def test_session_change_invalidates_identity():
    a,b=snapshot(0),snapshot(2)
    b['native']['sessions'][0]['upf_seid']='different'
    assert not c8.usage_delta(a,b,'10.47.0.2')['identity_stable']


def test_quantiles_keep_outliers_and_bootstrap_is_reproducible():
    assert c8.quantile([1,2,3,100],.99)>97
    a=c8.interval([1,2,3,4,5,6]);b=c8.interval([1,2,3,4,5,6])
    assert a==b and a[0]<3.5<a[1]
    assert c8.interval([1])==[None,None]


def test_t_critical_for_two_qoe_recordings():
    assert abs(float(np.tan(.475*np.pi))-12.7062047362)<1e-9


def renewal_guard():
    spec=importlib.util.spec_from_file_location('c8_registry',Path(__file__).parents[1]/'c8_registry.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.validate_renewal


def registry():
    return {'pid':123,'instance':'1','revoked':False,'sessions':[
        {'ue':'10.47.0.2','seid':'4','dnn':'5g-plus','generation':'2','bridge_ready':True,
         'fast_eligible':True,'qers':[{'id':1,'mbr':[1000,1000]}],'pdrs':[{'id':1}],
         'urrs':[{'id':1,'quota_active':False,'native_bytes':0,'native_packets':0}]}]}


def test_c8_renewal_accepts_only_same_policy_and_session():
    old=registry();new=deepcopy(old)
    new['sessions'][0]['generation']='3';new['sessions'][0]['urrs'][0]['native_packets']=20
    renewal_guard()(old,new)


@pytest.mark.parametrize('field,value',[('seid','5'),('ue','10.47.0.9'),('qers',[{'id':1,'mbr':[500,500]}]),
                                       ('fast_eligible',False),('generation','1')])
def test_c8_renewal_rejects_identity_policy_or_eligibility_changes(field,value):
    old=registry();new=deepcopy(old);new['sessions'][0][field]=value
    with pytest.raises(ValueError):renewal_guard()(old,new)


def test_readiness_recovers_nas_active_session_missing_from_pfcp(monkeypatch):
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2]/'backend'))
    import c8_xdp_window as window
    monkeypatch.setattr(window.time,'sleep',lambda _:None)
    restarts=[]
    class UE:
        def run(self,args,**kwargs):
            if args[0]==window.CLI:
                if args[-1]=='ps-release-all':return ''
                address='10.47.0.2' if args[1].endswith('002') else '10.47.0.3'
                return json.dumps({'PDU Session1':{'apn':'5g-plus','state':'PS-ACTIVE','address':address}})
            if args[:2]==['systemctl','restart']:
                restarts.append(args[2]);return ''
            if args[:4]==['ip','-j','-4','address']:
                return json.dumps([{'ifname':f'tun{i}','addr_info':[{'local':f'10.47.0.{i}'}]} for i in [2,3]])
            if args[:3]==['ip','-j','route']:return json.dumps([{'dev':args[-1]}])
            if args[0]=='python3':return '[]'
            raise AssertionError(args)
    class UPF:
        def run(self,args,**kwargs):
            addresses=['10.47.0.3'] if not restarts else ['10.47.0.2','10.47.0.3']
            return json.dumps({'sessions':[{'ue':a,'bridge_ready':True,'fast_eligible':True} for a in addresses]})
    result=window.wait_pdu(UE(),UPF())
    assert restarts==['maestro-ue-vehicle']
    assert len(result['nas'])==len(result['tunnels'])==2


def test_xdp_counters_sum_all_cpus(monkeypatch):
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2]/'backend'))
    import c8_xdp_window as window
    class UPF:
        def run(self,args,**kwargs):
            return json.dumps([{'formatted':{'key':key,'values':[
                {'cpu':0,'value':{'packets':10+key}}, {'cpu':1,'value':{'packets':20+key}}]}}
                for key in [0,1]])
    result=window.xdp_counters(UPF())
    assert (result['UL_OK'],result['DL_OK'])==(30,32)
