"""C6 boundary tests. Synthetic inputs here are never campaign evidence."""
import copy
import json
import pytest
from app.laboratory.c6_advisor import deterministic, validate
from app.laboratory.qoe_metrics import adaptive_p1203_document
from app.laboratory.live import LivePreflight, CORE_SCRIPT
from test_laboratory_commissioning import Transport, OBSERVED, COMPETING
from test_laboratory_qoe_metrics import inputs


def test_embb_only_critical_services_block_admission():
    class NoncriticalDown(Transport):
        def read(self, node, script, arguments=(), **kw):
            result=super().read(node,script,arguments,**kw)
            if script==CORE_SCRIPT:
                result['services']['open5gs-smfd3']['ActiveState']='inactive'
            return result
    bindings={k:{'alias':k,'supi':s,'dnn':'internet'} for k,s in [('observed',OBSERVED),('competing',COMPETING)]}
    result=LivePreflight(NoncriticalDown()).collect(bindings,1000)
    assert next(c for c in result['checks'] if c['id']=='core_services')['status']=='passed'
    assert result['noncritical_observations']['open5gs-smfd3']=='inactive'
    assert result['execution_ready'] is False


def test_c6_preset_is_authenticated_and_does_not_grant_execution(client, teacher_headers):
    assert client.get('/api/v1/laboratory/presets').status_code==401
    result=client.get('/api/v1/laboratory/presets',headers=teacher_headers)
    assert result.status_code==200
    preset=result.json()[0]
    assert preset['observed_supi']==OBSERVED and preset['competing_supi']==COMPETING
    assert preset['execution_ready'] is False and preset['policy_mutation'] is False


def adaptive_inputs():
    probe,trace=inputs();probe['streams'][1]['channels']=2
    trace.update(played_seconds=10,fragments=[{'start':0,'duration':5,'level':0},{'start':5,'duration':5,'level':1}])
    second=copy.deepcopy(probe)
    for p in second['packets']:
        if p['stream_index']==0:p['size']*=2
    return {'0':probe,'1':second},trace


def test_played_representation_preserves_actual_packet_bitrates():
    doc=adaptive_p1203_document(*adaptive_inputs())
    assert [s['bitrate'] for s in doc['I13']['segments']]==[160,320]
    assert doc['I23']['stalling']==[[0,1],[4,.25]]


def test_replaced_fragment_splits_only_at_measured_media_time():
    probes,trace=adaptive_inputs()
    # Whole-asset synthetic fragments with packet coverage on both sides.
    trace['fragments']=[{'start':0,'duration':10,'level':0},
                        {'start':0,'duration':10,'level':1,'media_time':5}]
    doc=adaptive_p1203_document(probes,trace)
    assert [(s['start'],s['duration'],s['bitrate']) for s in doc['I13']['segments']]==[(0,5,160),(5,5,320)]


@pytest.mark.parametrize('change',['gap','duplicate','incomplete','mono','stall'])
def test_adaptive_rejects_unmeasured_or_out_of_domain_input(change):
    probes,trace=adaptive_inputs()
    if change=='gap':trace['fragments'][1]['start']=6
    if change=='duplicate':trace['fragments'][1]=trace['fragments'][0]
    if change=='incomplete':trace['played_seconds']=9
    if change=='mono':probes['1']['streams'][1]['channels']=1
    if change=='stall':trace['stalls']=[[11,1]]
    with pytest.raises(ValueError):adaptive_p1203_document(probes,trace)


def test_aggregate_playback_never_publishes_fabricated_p1203(monkeypatch):
    from app.services import nwdaf
    from app.services.terminal_experience import PlaybackObservation, _publish_p1203
    from test_terminal_experience import payload
    def forbidden(*a,**kw):raise AssertionError('No measured segment/stall input')
    monkeypatch.setattr(nwdaf,'nwdaf_request',forbidden)
    assert _publish_p1203(PlaybackObservation(**payload())) is None


def proposal():
    return {'scope':'offline_read_only','diagnosis':'normal','evidence_ids':['e1'],'confidence':.5,
            'explanation':'Control sin fallo inducido','uncertainty':'No prueba causalidad','recommendations':['retain_baseline']}


@pytest.mark.parametrize('change',['citation','command','action','scope'])
def test_advisor_rejects_unknown_evidence_or_executable_output(change):
    value=proposal()
    if change=='citation':value['evidence_ids']=['unknown']
    if change=='command':value['explanation']='Ejecutar sudo tc ahora'
    if change=='action':value['recommendations']=['apply_policy']
    if change=='scope':value['scope']='live_control'
    with pytest.raises(ValueError):validate(json.dumps(value),{'observations':[{'id':'e1','values':{}}]})


def test_deterministic_missing_measurements_abstain_and_congestion_needs_queue():
    def context(v):return {'observations':[{'id':'e1','values':v}]}
    assert deterministic(context({'p1203_mos':1.2}))=='insufficient_evidence'
    values={'capacity_bps':1500000,'offered_bps':3000000,'queue_drops':20,'startup_seconds':4}
    assert deterministic(context(values))=='n6_congestion'
    assert deterministic(context({**values,'queue_drops':0}))=='normal'


def test_old_iperf_udp_sender_summary_is_never_receiver_goodput():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'infra/c6_qoe_campaign.py'
    spec=importlib.util.spec_from_file_location('c6_campaign_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    doc={'end':{'sum':{'bits_per_second':3000000,'bytes':3000000}},
         'intervals':[{'sum':{'sender':False,'bytes':180000,'seconds':1}},
                      {'sum':{'sender':False,'bytes':180000,'seconds':1}}]}
    result=module.udp_rates(doc)
    assert result['receiver_bps']==1440000 and result['sender_bps']==3000000
    doc['intervals'][0]['sum']['sender']=True
    with pytest.raises(ValueError,match='receiver_intervals_required'):module.udp_rates(doc)
