"""Collect native journals and validate/replay exact closed Releases once.

No new usage is injected: payloads come verbatim from the native SMF journals.
NF credentials are read only on Core and never written into evidence.
"""
import json
from pathlib import Path
from e2e_native import Lab, ROOT, CHF
from lab_command import get_settings


def main():
    evidence=ROOT/'.work/c5-charging/evidence'
    pointer=json.loads((evidence/'services-latest.json').read_text())
    acceptance=json.loads((Path(pointer['directory'])/'result.json').read_text())
    assert acceptance['status']=='PASS'
    refs={v['cdr']['chargingSessionId']:(3 if k in ('2','5') else 2) for k,v in acceptance['cases'].items()}
    core=Lab(get_settings(),2222)
    try:
        directory=core.run(['mktemp','-d','/home/emsadmin/c5-journals-XXXXXX']).strip()
        core.write(directory+'/recover_release.py',(ROOT/'chf/tools/recover_release.py').read_bytes())
        code='''import pathlib,json,shlex,sys,sqlite3,fcntl,os,stat,pwd
sys.path[:0]=[STAGING,CHF]
from recover_release import parse_journal, replay_http2
results={}
def env(path):
 return {k:v for token in shlex.split(pathlib.Path(path).read_text(),comments=True) if '=' in token for k,v in [token.split('=',1)]}
management=env('/home/emsadmin/maestro-charging/management/management.env')
conn=sqlite3.connect(management['CHF_DATABASE_PATH']);conn.row_factory=sqlite3.Row
for ref,smf in REFS.items():
 candidates=sorted(pathlib.Path('/var/lib/open5gs/chf-journal-smf'+str(smf)).glob('*'),key=lambda p:p.stat().st_mtime,reverse=True)
 for path in candidates[:300]:
  if not path.is_file() or len(path.name)!=36:continue
  data=path.read_bytes()
  if ref.encode() not in data:continue
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  metadata=os.fstat(fd)
  assert stat.S_ISREG(metadata.st_mode) and metadata.st_uid==pwd.getpwnam('open5gs').pw_uid and not metadata.st_mode&0o077
  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  data=os.read(fd,4*1024*1024+1)
  state,_=parse_journal(data)
  assert state['state']=='closed',state
  entries=[json.loads(line) for line in data.splitlines()]
  entry=next(x for x in reversed(entries) if x['event']=='request' and x['uri'].endswith('/release'))
  supi=json.loads(entry['body'])['subscriberIdentifier']
  before=dict(conn.execute('SELECT * FROM charging_accounts WHERE supi=?',(supi,)).fetchone())
  beforemsg=conn.execute('SELECT consumed_messages FROM message_accounts WHERE supi=?',(supi,)).fetchone()
  credential=env('/etc/open5gs/smf'+str(smf)+'.env')['SMF_CHF_TOKEN']
  replay_http2(entry,'http://127.0.0.1:8081',credential)
  after=dict(conn.execute('SELECT * FROM charging_accounts WHERE supi=?',(supi,)).fetchone())
  aftermsg=conn.execute('SELECT consumed_messages FROM message_accounts WHERE supi=?',(supi,)).fetchone()
  assert before['consumed_bytes']==after['consumed_bytes']
  assert (beforemsg[0] if beforemsg else None)==(aftermsg[0] if aftermsg else None)
  replays=conn.execute('SELECT count(*) FROM charging_replays WHERE charging_data_ref=? AND operation=?',(ref,'RELEASE')).fetchone()[0]
  assert replays>=1
  results[ref]={'path':str(path),'state':state,'exactReleaseReplay':'HTTP_204_NO_ADDITIONAL_DEBIT','replays':replays,'journal':data.decode()}
  os.close(fd)
  break
 assert ref in results,'Journal not found: '+ref
print(json.dumps(results))
'''
        code=f'REFS={refs!r}\nCHF={CHF!r}\nSTAGING={directory!r}\n'+code
        core.write(directory+'/collect.py',code)
        raw=core.run([CHF+'/.venv/bin/python',directory+'/collect.py'],sudo=True,timeout=60)
        results=json.loads(raw)
        (evidence/'journals').mkdir(exist_ok=True)
        for ref,item in results.items():
            (evidence/'journals'/(ref+'.jsonl')).write_text(item.pop('journal'),encoding='utf-8')
        (evidence/'native-journal-replays.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        print(json.dumps(results,indent=2))
    finally:core.client.close()


if __name__=='__main__':main()
