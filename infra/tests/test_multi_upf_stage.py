import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multi_upf_stage import candidates


def test_candidates_keep_charging_and_do_not_mutate_existing_config():
    smf = {'logger':{'file':{'path':'old.log'}}, 'smf':{
        'sbi':{'client':{'scp':[{'uri':'http://127.0.0.200:7777'}]}, 'server':[]},
        'chf':{'enabled':True,'nf_instance_id':'old','requested_units':500000},
        'metrics':{'server':[{'address':'127.0.0.15','port':9091}]},
        'freeDiameter':'old.conf'}}
    upf = {'logger':{'file':{'path':'old.log'}}, 'upf':{'charging_enforcement':True}}
    new_smf, new_upf = candidates(smf, upf)
    assert new_upf['upf']['charging_enforcement'] is True
    assert new_smf['smf']['chf']['requested_units'] == 500000
    assert new_smf['smf']['chf']['nf_instance_id'] != 'old'
    assert new_smf['smf']['sbi']['client'] == smf['smf']['sbi']['client']
    assert new_smf['smf']['pfcp']['client']['upf'] == [{'address':'10.210.50.22','dnn':'5g-plus'}]
    assert new_smf['smf']['pfcp']['server'] == [{'address': '10.210.50.18'}]
    assert new_smf['smf']['metrics']['server'] == [{'address': '10.210.50.18', 'port': 9092}]
    assert new_upf['upf']['session'][0]['subnet'] == '10.47.0.0/16'
    assert 'pfcp' not in smf['smf'] and 'session' not in upf['upf']


def test_native_session_number_is_not_the_yaml_label():
    import pytest
    from multi_upf_cutover import session_number
    assert session_number('PDU Session2') == 2
    assert session_number(3) == 3
    for invalid in ('PDU Session0', 'PDU Session16', 'PDU Session2; reboot'):
        with pytest.raises(ValueError):
            session_number(invalid)
