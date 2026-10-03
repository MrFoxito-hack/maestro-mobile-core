"""Calculate P.1203 from probed AV bytes and actual Stream5G player events.

Publish both results to the existing immutable SERVICE_EXPERIENCE analysis
ledger. QoE records are not fabricated N7 decision transitions.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from nwdaf_closed_loop_campaign import api

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run');args=parser.parse_args()
    assert re.fullmatch('nwdaf-qoe-[a-f0-9]{10}',args.run)
    local=ROOT/'.work'/args.run;source=json.loads((local/'result.json').read_text(encoding='utf-8'))
    assert source['status']=='MEASURED' and source['quota_unchanged']
    probe=json.loads((local/'ffprobe.json').read_text(encoding='utf-8'))
    document={'I11':{'segments':[],'streamId':1},'I13':{'segments':[],'streamId':1},
        'I23':{'stalling':[],'streamId':1},'IGen':{'device':'mobile','displaySize':'640x360','viewingDistance':'30cm'}}
    for stream in probe['streams']:
        packets=[p for p in probe['packets'] if p['stream_index']==stream['index']]
        ordered=sorted(packets,key=lambda p:int(p['pts']))
        ticks=[int(b['pts'])-int(a['pts']) for a,b in zip(ordered,ordered[1:])]
        last_duration=ordered[-1].get('duration')
        if last_duration is None:
            # MP4 demuxing may omit packet duration. Derive the final frame
            # duration only when every observed PTS interval is identical.
            assert ticks and len(set(ticks))==1 and ticks[0]>0, 'Variable frame timing needs explicit duration metadata'
            last_duration=ticks[0]
        duration=float((int(ordered[-1]['pts'])+int(last_duration)-int(ordered[0]['pts']))*Fraction(stream['time_base']))
        segment={'start':0,'duration':duration,'bitrate':sum(int(p['size']) for p in packets)*8/duration/1000}
        if stream['codec_type']=='video':
            assert stream['codec_name']=='h264'
            segment.update(codec='h264',fps=float(Fraction(stream['avg_frame_rate'])),resolution=f"{stream['width']}x{stream['height']}")
            document['I13']['segments'].append(segment)
        elif stream['codec_type']=='audio':
            assert stream['codec_name']=='aac' and stream['profile']=='LC'
            segment.update(codec='aaclc');document['I11']['segments'].append(segment)
    assert len(document['I11']['segments'])==len(document['I13']['segments'])==1
    settings=get_settings();core=Lab(settings,settings.ssh_port)
    comparison={'run':args.run,'cases':[],'display_context':'declared mobile-model context; not a subjective human study',
        'media_metadata':'ffprobe packet bytes and PTS durations; same encoded clip for both cases'}
    try:
        token=core.read(settings.nwdaf_token_file).decode().strip()
        for phase in ('baseline','closed-loop'):
            player=json.loads((local/(phase+'-player.json')).read_text(encoding='utf-8'))
            assert player['status']=='PLAYED_TO_END' and player['player']['ended']
            trace=player['player'];assert trace['startup_seconds'] is not None and trace['startup_seconds']>=0
            assert not any(e['type'] in ('error','seeking') and e['current_time']>0 for e in trace['events'])
            assert all(t['verified'] for t in source['transfers'] if t['phase']==phase)
            doc=json.loads(json.dumps(document))
            doc['I23']['stalling']=[[0,trace['startup_seconds']],*trace['stalls']]
            stamp=max(e['utc_ms'] for e in trace['events'] if e['type']=='ended')/1000
            body={'supi':source['sessions']['video']['supi'],'app_id':'stream5g','timestamp':stamp,'document':doc}
            (local/(phase+'-p1203-input.json')).write_text(json.dumps(body,indent=2),encoding='utf-8')
            digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            score=api(core,token,'POST','/management/v1/publish/service-experience',body)
            record={'phase':phase,'mos':score['mos'],'score':score,'input_hash':digest,
                    'startup_seconds':trace['startup_seconds'],'rebuffer_count':len(trace['stalls']),
                    'rebuffer_seconds':sum(s[1] for s in trace['stalls']),'played_seconds':trace['played_seconds']}
            (local/(phase+'-p1203-result.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
            comparison['cases'].append(record)
        comparison['mos_delta']=comparison['cases'][1]['mos']-comparison['cases'][0]['mos']
        comparison['qoe_preserved_in_this_pair']=comparison['mos_delta']>=0
        hashes=[x['input_hash'] for x in comparison['cases']]
        script="""import sqlite3,json,sys
c=sqlite3.connect('file:/home/emsadmin/maestro-charging/nwdaf/data/nwdaf.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
print(json.dumps([dict(r) for r in c.execute('SELECT id,event,target,created,input_hash,evidence FROM analyses WHERE input_hash IN (?,?)',sys.argv[1:])]))
"""
        rows=json.loads(core.run(['python3','-c',script,*hashes]))
        assert len(rows)==2 and all(r['event']=='SERVICE_EXPERIENCE' for r in rows)
        comparison['immutable_ledger_rows']=rows
        (local/'qoe-comparison.json').write_text(json.dumps(comparison,indent=2),encoding='utf-8')
        print(json.dumps(comparison,indent=2))
    finally:core.client.close()
