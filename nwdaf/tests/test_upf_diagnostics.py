import pytest
from app.engine.upf_diagnostics import assess, IDENTITIES


def window(service, **metrics):
    sst,sd,dnn=IDENTITIES[service]
    return dict(service=service,snssai={'sst':sst,'sd':sd},dnn=dnn,upf_id='upf-03',
                timestamp=1000,complete=True,metrics=metrics)


def test_throughput_does_not_prove_urllc_health():
    result=assess(window('urllc',dl_bps=471000000),{},1001)
    assert result['status']=='insufficient_evidence'
    assert 'rtt_p99_ms' in result['missing']
    assert result['actuation_allowed'] is False


def test_miot_distinguishes_signalling_from_delivery():
    limits={'miot':{'registration_rate':5,'registration_failure_ratio':0.01,'packet_loss_ratio':0.02}}
    result=assess(window('miot',registration_rate=20,registration_failure_ratio=0,packet_loss_ratio=0),limits,1001)
    assert result['findings']==['signalling_pressure']
    result=assess(window('miot',registration_rate=1,registration_failure_ratio=0,packet_loss_ratio=0.05),limits,1001)
    assert result['findings']==['delivery_degradation']


def test_incomplete_and_stale_windows_never_authorize_actuation():
    body=window('embb',dl_bps=300)
    body['complete']=False
    result=assess(body,{'embb':{'dl_budget_bps':100}},1100)
    assert result['status']=='insufficient_evidence'
    assert not result['actuation_allowed']
    assert result['llm_context']['allowed_actions']==[]


def test_old_slice_id_cannot_be_relabelled():
    body=window('urllc')
    body['snssai']['sst']=1
    with pytest.raises(ValueError):assess(body,{},1000)


@pytest.mark.parametrize('value',[float('nan'),-1,True])
def test_bad_metrics_are_rejected(value):
    with pytest.raises(ValueError):assess(window('embb',dl_bps=value),{},1000)
