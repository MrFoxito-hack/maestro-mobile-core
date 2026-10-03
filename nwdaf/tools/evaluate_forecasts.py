"""Causal rolling-origin evaluation on measured PM; never interpolate gaps."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.ingestion.buckets import regularize
from app.engine.slice_load import forecast


def evaluate(path):
    uri=path.resolve().as_uri()+'?mode=ro'
    output={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'slices':[]}
    with sqlite3.connect(uri,uri=True) as db:
        db.row_factory=sqlite3.Row
        for obj in ('nf:upf-01','nf:upf-02'):
            samples=[dict(r) for r in db.execute("SELECT bucket_epoch,value FROM metric_samples WHERE object_id=? AND counter_id='nwdaf.slice.dl.bps' AND quality IN ('measured','computed') ORDER BY bucket_epoch",(obj,))]
            # Evaluate all continuous covered segments, not just the last suffix.
            ends=sorted({(math.floor(s['bucket_epoch']/300)+1)*300 for s in samples})
            segments=[];current=[]
            for end in ends:
                group=[s for s in samples if end-300<=s['bucket_epoch']<end]
                bucket=regularize(group,interval=300,now=end,max_gap=90)
                if not bucket or (current and end-current[-1]['bucket_epoch']!=300):
                    if current:segments.append(current)
                    current=[]
                if bucket:current.extend(bucket)
            if current:segments.append(current)
            evidence=[]
            evaluated_loads=[]
            for segment in segments:
                y=[min(100,s['value']/20000000*100) for s in segment]
                timestamps=[s['bucket_epoch'] for s in segment]
                if len(segment)>=56:evaluated_loads.extend(y)
                for origin in range(50,len(segment)-5):
                    predicted=forecast(y[:origin],timestamps[:origin])
                    for point in predicted['points']:
                        index=origin+point['horizon_seconds']//300-1
                        evidence.append({'origin':timestamps[origin-1],'target':timestamps[index],
                            'horizon_seconds':point['horizon_seconds'],'prediction':point['value'],
                            'observed':y[index],'error_percentage_points':point['value']-y[index]})
            scores={str(h):math.sqrt(sum(x['error_percentage_points']**2 for x in evidence if x['horizon_seconds']==h)/sum(x['horizon_seconds']==h for x in evidence))
                    for h in (900,1800) if any(x['horizon_seconds']==h for x in evidence)}
            output['slices'].append({'object_id':obj,'raw_samples':len(samples),
                'contiguous_bucket_counts':[len(s) for s in segments],
                'minimum_buckets_for_one_30min_holdout':56,
                'rmse_percentage_points':scores,'predictions':evidence,
                'evaluated_workload':{'bucket_count':len(evaluated_loads),
                    'min_load_percentage':min(evaluated_loads) if evaluated_loads else None,
                    'max_load_percentage':max(evaluated_loads) if evaluated_loads else None,
                    'buckets_above_85_percent':sum(y>85 for y in evaluated_loads),
                    'rolling_origins':len({e['origin'] for e in evidence}),
                    'scope':'Observed workload only; low RMSE on idle traffic does not establish congestion forecasting.'},
                'status':'MEASURED' if evidence else 'INSUFFICIENT_CONTIGUOUS_HISTORY'})
    output['gate4_rmse']='PASSED' if all(s['rmse_percentage_points'] and all(v<5 for v in s['rmse_percentage_points'].values()) for s in output['slices']) else 'NOT_PASSED'
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('snapshot',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=evaluate(args.snapshot)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
