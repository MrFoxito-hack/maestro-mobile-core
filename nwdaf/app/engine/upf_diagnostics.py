"""Evidence-only service diagnostics; no invented QoE or implicit actuation."""
import math

IDENTITIES = {
    'embb': (1, '000001', 'internet'),
    'urllc': (2, '000002', '5g-plus'),
    'miot': (3, '000003', 'corporate'),
}
METRICS = {'ul_bps','dl_bps','ul_pps','dl_pps','rtt_p99_ms','jitter_ms',
           'packet_loss_ratio','deadline_miss_ratio','registration_rate',
           'registration_failure_ratio','message_age_p99_ms'}
RULES = {
    'embb': [('dl_bps','dl_budget_bps','bandwidth_pressure')],
    'urllc': [('rtt_p99_ms','rtt_p99_ms','latency_degradation'),
              ('jitter_ms','jitter_ms','latency_degradation'),
              ('deadline_miss_ratio','deadline_miss_ratio','deadline_violations')],
    'miot': [('registration_rate','registration_rate','signalling_pressure'),
             ('registration_failure_ratio','registration_failure_ratio','signalling_failures'),
             ('packet_loss_ratio','packet_loss_ratio','delivery_degradation')],
}


def assess(body, slos, now):
    if not isinstance(body,dict) or set(body) != {'service','snssai','dnn','upf_id','timestamp','complete','metrics'}:
        raise ValueError('Expected a versioned per-UPF window')
    service = body['service']
    if service not in IDENTITIES:
        raise ValueError('Unknown service')
    sst, sd, dnn = IDENTITIES[service]
    if body['snssai'] != {'sst':sst,'sd':sd} or body['dnn'] != dnn:
        raise ValueError('Slice and DNN do not match the service')
    if not isinstance(body['upf_id'],str) or not 1 <= len(body['upf_id']) <= 64:
        raise ValueError('UPF identity required')
    if type(body['complete']) is not bool or type(body['timestamp']) not in (int,float) or not math.isfinite(body['timestamp']):
        raise ValueError('Invalid provenance')
    metrics = body['metrics']
    if not isinstance(metrics,dict) or not set(metrics) <= METRICS:
        raise ValueError('Unsupported metric')
    for name,value in metrics.items():
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0 or (name.endswith('_ratio') and value > 1):
            raise ValueError('Invalid metric value')
    limits = slos.get(service,{})
    for value in limits.values():
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid operator SLO')
    missing, findings, evidence = [], [], []
    if not body['complete']: missing.append('complete_measurement')
    if not 0 <= now-body['timestamp'] <= 20: missing.append('fresh_measurement')
    for metric,limit,label in RULES[service]:
        if metric not in metrics: missing.append(metric)
        if limit not in limits: missing.append('operator_slo:'+limit)
        if metric in metrics and limit in limits:
            item = {'metric':metric,'observed':metrics[metric],'threshold':limits[limit],
                    'exceeded':metrics[metric]>limits[limit]}
            evidence.append(item)
            if item['exceeded']:findings.append(label)
    return {'schema_version':1,'status':'insufficient_evidence' if missing else
            'degraded' if findings else 'within_configured_thresholds',
            'service':service,'snssai':body['snssai'],'dnn':dnn,'upf_id':body['upf_id'],
            'timestamp':body['timestamp'],'findings':sorted(set(findings)),
            'missing':missing,'evidence':evidence,'actuation_allowed':False,
            'llm_context':{'role':'diagnosis_only','observations':evidence,
                           'missing_evidence':missing,'allowed_actions':[],
                           'instruction':'Distinguir hipótesis de hechos; no inventar métricas ni ejecutar acciones.'}}
