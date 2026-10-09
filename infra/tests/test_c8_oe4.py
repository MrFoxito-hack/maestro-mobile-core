"""Reject silent drift in the QoS mechanism used for OE4 acquisition."""
from copy import deepcopy
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from c8_qos import assert_ready
from c8_oe4 import historical_runs


def state():
    sessions={kind:{'interface':'tun'+str(i)} for i,kind in enumerate(['urllc','miot','embb'])}
    value={'qdisc':[{'root':True,'kind':'prio','handle':'20:'}],
           'bindings':{},'filters':[],'all_qdiscs':[]}
    for band,kind in enumerate(['urllc','miot','embb'],1):
        value['bindings'][kind]=[{'port':50000+band}]
        value['filters'].append({'options':{'classid':f'20:{band}',
            'keys':{'src_port':50000+band,'dst_port':4997,'dst_ip':'10.210.50.10'}}})
        if kind!='urllc':
            value['all_qdiscs'].append({'dev':sessions[kind]['interface'],'root':True,
                'kind':'tbf','handle':'30:','options':{'rate':{'embb':62500,'miot':5000}[kind]}})
    return value,sessions


def test_current_ports_and_admission_profile_are_accepted():
    assert_ready(*state())


@pytest.mark.parametrize('change',['port','destination','priority','rate','interface'])
def test_restart_route_priority_or_rate_drift_fails_closed(change):
    value,sessions=state();value=deepcopy(value)
    if change=='port':value['bindings']['urllc'][0]['port']+=10
    if change=='destination':value['filters'][0]['options']['keys']['dst_ip']='10.210.50.99'
    if change=='priority':value['filters'][0]['options']['classid']='20:3'
    if change=='rate':value['all_qdiscs'][0]['options']['rate']*=2
    if change=='interface':sessions['embb']['interface']='tun-recreated'
    with pytest.raises(ValueError):assert_ready(value,sessions)


def test_unrelated_baseline_has_no_implicit_exclusions(tmp_path):
    assert historical_runs(tmp_path)==set()
