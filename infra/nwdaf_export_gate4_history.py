"""Export immutable real fast-PM evidence and the independent minute snapshot."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__=='__main__':
    settings=get_settings();core=Lab(settings,settings.ssh_port)
    directory=ROOT/'.work'/('nwdaf-gate4-history-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));directory.mkdir()
    try:
        base='/home/emsadmin/maestro-charging/nwdaf/data/'
        data=core.read(base+'pm-source.sqlite3');(directory/'pm-snapshot.sqlite3').write_bytes(data)
        script="""import sqlite3,json,sys
c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute("SELECT id,created,target,input_hash,evidence FROM analyses WHERE event='LOAD_LEVEL_INFORMATION' ORDER BY id")]
print(json.dumps(rows))
"""
        rows=json.loads(core.run(['python3','-c',script,base+'nwdaf.sqlite3'],timeout=30))
        (directory/'analyses-load.json').write_text(json.dumps(rows),encoding='utf-8')
        fast=[]
        for row in rows:
            evidence=json.loads(row['evidence'])
            if evidence.get('model')!='upf-counter-delta-v1':continue
            point=evidence['history'][-1]
            fast.append({'timestamp':point['timestamp'],'value':point['value'],
                'bps':evidence['observed_bps'],'sampling_seconds':evidence['sampling_seconds'],
                'capacity_bps':evidence['capacity_units'],'snssai':json.loads(row['target']),
                'input_hash':row['input_hash'],'analysis_id':row['id'],'received_at':row['created']})
        fast.sort(key=lambda x:(x['timestamp'],x['analysis_id']))
        (directory/'fast-pm-series.json').write_text(json.dumps(fast,indent=2),encoding='utf-8')
        summary={'source_pm_sha256':hashlib.sha256(data).hexdigest(),'fast_samples':len(fast),
            'first_timestamp':fast[0]['timestamp'] if fast else None,'last_timestamp':fast[-1]['timestamp'] if fast else None,
            'method':'Minute and fast sources are exported separately; no gap filling or duplicated weighting.'}
        (directory/'export.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps({'path':str(directory),**summary},indent=2))
    finally:core.client.close()
