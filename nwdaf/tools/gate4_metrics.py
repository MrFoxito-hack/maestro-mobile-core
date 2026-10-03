"""Explicit control/acquisition boundaries and convergence coverage from real data."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.ingestion.buckets import regularize
from evaluate_forecasts import evaluate

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('history',type=Path);parser.add_argument('control_analysis',type=Path)
    args=parser.parse_args()
    forecast=evaluate(args.history/'pm-snapshot.sqlite3')
    fast=json.loads((args.history/'fast-pm-series.json').read_text(encoding='utf-8'))
    forecast['fast_history']=[]
    for sd in ('000001','000002'):
        points=[p for p in fast if p['snssai']=={'sst':1,'sd':sd}]
        points.sort(key=lambda x:x['timestamp'])
        samples=[{'bucket_epoch':p['timestamp'],'value':p['value']} for p in points]
        # Fast task has explicit gaps; report two coverage tolerances separately.
        strict=regularize(samples,interval=300,now=datetime.now(timezone.utc).timestamp(),max_gap=3) if samples else []
        duty=regularize(samples,interval=300,now=datetime.now(timezone.utc).timestamp(),max_gap=15) if samples else []
        gaps=[b['timestamp']-a['timestamp'] for a,b in zip(points,points[1:])]
        forecast['fast_history'].append({'snssai':{'sst':1,'sd':sd},'raw_measured_samples':len(points),
            'maximum_gap_seconds':max(gaps) if gaps else None,
            'continuous_5min_buckets_with_3s_gap_limit':len(strict),
            'last_segment_5min_buckets_with_15s_gap_limit':len(duty),
            'warmup_required_buckets':50,'first_30min_holdout_required_buckets':56,
            'model_fit':'NOT_ATTEMPTED_INSUFFICIENT_COVERAGE' if len(strict)<50 else 'AVAILABLE_FOR_SEPARATE_EVALUATION',
            'interpretation':'50-second scheduled runs leave gaps; no imputation, no concatenation across outages, no claim of model convergence.'})
    forecast['convergence']=(
        'MINUTE_PM_MODEL_EXECUTED: causal forecasts returned without optimizer failure; stationary subseries may use the exact constant solution. Fast-source coverage is assessed separately; this is not evidence of prediction under congestion.'
        if all(s['status']=='MEASURED' for s in forecast['slices']) else
        'INSUFFICIENT_CONTIGUOUS_HISTORY: warm-up coverage assessed; optimizer convergence is not inferred from successful engine unit tests.')
    (args.history/'forecast-evaluation.json').write_text(json.dumps(forecast,indent=2),encoding='utf-8')
    analysis=json.loads(args.control_analysis.read_text(encoding='utf-8'))
    rows=[]
    for t in analysis['transitions']:
        rows.append({'action':t['action'],'decision_id':t['decision_id'],
            'control_ms':t['detection_to_pfcp_ack_ms'],
            'publication_to_pcf_detection_ms':t['nwdaf_report_generation_to_pfcp_ack_ms']-t['detection_to_pfcp_ack_ms'],
            'publication_to_pfcp_ack_ms':t['nwdaf_report_generation_to_pfcp_ack_ms']})
    latency={'definitions':{'control':'native PCF DETECTED timestamp to accepted PFCP response, same Core clock',
        'delivery_polling':'NWDAF timeStampGen to native PCF DETECTED, same Core clock',
        'acquisition':'counter measurement interval before NWDAF publication; excluded from both previous metrics',
        'e2e':'acquisition + publication-to-PFCP; not equivalent to control'},
        'polling_seconds':1,'samples':rows,'sample_count':len(rows),
        'control_below_50ms_in_observed_samples':all(r['control_ms']<50 for r in rows),
        'control_mean_ms':statistics.mean(r['control_ms'] for r in rows),
        'scope':'Measured case series; no tail-latency or universal maximum guarantee; no timer optimization claimed.'}
    (args.history/'latency-decomposition.json').write_text(json.dumps(latency,indent=2),encoding='utf-8')
    print(json.dumps({'forecast':forecast,'latency':latency},indent=2))
