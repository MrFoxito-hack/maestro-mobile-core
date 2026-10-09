"""C0 evidence collector. Reads running NFs; writes only new backup directories.

Run from backend with its venv. Private archives are kept under ignored .work.
No service restart, policy mutation, Mongo restore or ledger rollback occurs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT/'infra/charging'))
from app.core.config import get_settings
from e2e_native import Lab

REMOTE = r'''
import base64,hashlib,json,os,pathlib,pwd,re,sqlite3,subprocess,sys,tarfile,time
root=pathlib.Path(sys.argv[1]); owner=pwd.getpwnam(sys.argv[2]); label=sys.argv[3]
assert root.parent==pathlib.Path('/home')/owner.pw_name and root.name.startswith('maestro-baseline-')
assert root.is_dir() and root.stat().st_uid==owner.pw_uid
os.nice(10)
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=35)
 return {'code':p.returncode,'output':p.stdout,'error':p.stderr[-300:] if p.returncode else ''}
state={'host':label,'started_at':time.time(),'units':{},'files':{},'errors':[]}
state['kernel']=run(['uname','-a'])
state['cpu']=run(['lscpu','-J']);state['memory']=run(['free','-b'])
state['packages']=run(['dpkg-query','-W','-f=${Package}\t${Version}\n'])
state['links']=run(['ip','-j','-d','address']);state['sockets']=run(['ss','-H','-lnut'])
state['namespaces']=run(['ip','netns','list'])
files={}; generated={}
def include(path):
 p=pathlib.Path(path)
 if not p.is_file():return
 p=p.resolve()
 if p.stat().st_size>150_000_000:raise ValueError('Oversized backup member')
 key=str(p)
 if key in files:return
 raw=p.read_bytes();s=p.stat()
 files[key]=raw
 state['files'][key]={'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),
  'mode':oct(s.st_mode&0o777),'uid':s.st_uid,'gid':s.st_gid}
units=run(['systemctl','list-units','--type=service','--state=running','--no-legend','--plain'])
names=[l.split()[0] for l in units['output'].splitlines() if l.split() and
       l.split()[0].startswith(('open5gs-','maestro-','ueransim-','mongod.'))]
for unit in names:
 props=run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','ExecMainStartTimestamp',
            '-p','FragmentPath','-p','DropInPaths'])
 values=dict(l.split('=',1) for l in props['output'].splitlines() if '=' in l)
 state['units'][unit]=values
 generated['units/'+unit+'.txt']=run(['systemctl','cat',unit])['output'].encode()
 include(values.get('FragmentPath',''))
 for p in values.get('DropInPaths','').split():include(p)
 pid=values.get('MainPID','0')
 if pid=='0':continue
 proc=pathlib.Path('/proc')/pid
 try:
  args=proc.joinpath('cmdline').read_bytes().decode().split('\0')
  if '-c' in args:include(args[args.index('-c')+1])
  exe=proc.joinpath('exe').resolve()
  if str(exe).startswith(('/opt/','/home/')):include(exe)
  for line in proc.joinpath('maps').read_text().splitlines():
   path=line.split()[-1]
   if path.startswith(('/opt/','/home/')) and '.so' in path:
    include(path)
    for alias in pathlib.Path(path).parent.glob('*.so*'):
     if alias.is_symlink():state.setdefault('symlinks',{})[str(alias)]=os.readlink(alias)
  # Environment is private evidence, never printed. Required for restore.
  generated['process/'+unit+'.environ']=proc.joinpath('environ').read_bytes()
  generated['process/'+unit+'.cmdline']=proc.joinpath('cmdline').read_bytes()
 except OSError:state['errors'].append('process_changed:'+unit)
for directory,pattern in [('/etc/open5gs','*.yaml'),('/etc/open5gs','*.env'),
                           ('/home/'+owner.pw_name+'/UERANSIM/config','*.yaml')]:
 for p in pathlib.Path(directory).glob(pattern):include(p)
for tree in ['/home/'+owner.pw_name+'/maestro-charging/chf',
             '/home/'+owner.pw_name+'/maestro-charging/nwdaf']:
 for p in pathlib.Path(tree).rglob('*'):
  if p.is_file() and not any(x.startswith('.') or x=='__pycache__' for x in p.relative_to(tree).parts):
   if p.suffix in ('.py','.env','.token') or p.name.startswith('requirements'):include(p)
source=pathlib.Path('/home')/owner.pw_name/'maestro-charging/open5gs'
if source.exists():
 state['open5gs_revision']=run(['git','-C',str(source),'rev-parse','HEAD'])
 generated['open5gs-working.patch']=run(['git','-C',str(source),'diff','--binary'])['output'].encode()
 listing=run(['git','-C',str(source),'ls-files','--others','--exclude-standard'])
 for relative in listing['output'].splitlines():
  p=source/relative
  if p.suffix in ('.c','.h','.inc','.py','.sh','.build'):include(p)
if label=='core':
 mongo=run(['mongosh','--quiet','--eval',
  "const d=db.getSiblingDB('open5gs');print(EJSON.stringify({collections:d.getCollectionNames().map(n=>({name:n,indexes:d.getCollection(n).getIndexes(),documents:d.getCollection(n).find().toArray()}))},{relaxed:false}))"])
 if mongo['code']==0:generated['mongo-open5gs.ejson']=mongo['output'].encode()
 else:state['errors'].append('mongo_export_failed')
 for unit in ['open5gs-chfd.service','maestro-nwdaf.service']:
  props=state['units'].get(unit,{})
  if props.get('MainPID','0')=='0':continue
  env=dict(x.split('=',1) for x in pathlib.Path('/proc',props['MainPID'],'environ').read_bytes().decode().split('\0') if '=' in x)
  paths=list({v for k,v in env.items() if
    (k.endswith('DATABASE_PATH') or k in ('NWDAF_DATABASE','NWDAF_PM_DATABASE')) and pathlib.Path(v).is_file()})
  if unit=='maestro-nwdaf.service':
   default=pathlib.Path(env.get('NWDAF_DATABASE','data/nwdaf.sqlite3'))
   if not default.is_absolute():default=pathlib.Path('/proc',props['MainPID'],'cwd').resolve()/default
   if default.is_file() and str(default) not in paths:paths.append(str(default))
  for number,path in enumerate(paths):
   destination=root/(unit+str(number)+'.sqlite3')
   begin=time.monotonic()
   def progress(*_):
    if time.monotonic()-begin>45:raise TimeoutError('sqlite snapshot deadline')
   with sqlite3.connect('file:'+path+'?mode=ro',uri=True) as a,sqlite3.connect(destination) as b:
    a.backup(b,pages=256,progress=progress)
    assert b.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
   generated['databases/'+unit+str(number)+'.sqlite3']=destination.read_bytes()
   state.setdefault('databases',[]).append({'source':path,'member':'databases/'+unit+str(number)+'.sqlite3','integrity':'ok'})
if label=='ue':
 cli='/home/'+owner.pw_name+'/UERANSIM/build/nr-cli'
 state['ues']={}
 for i in range(1,7):
  supi='imsi-99970000000000'+str(i)
  state['ues'][supi]=run([cli,supi,'--exec','ps-list'])
if label=='upf':
 state['urllc_links']=run(['ip','netns','exec','maestro-urllc','ip','-j','-d','link'])
 state['urllc_sockets']=run(['ip','netns','exec','maestro-urllc','ss','-H','-lnut'])
for unit,before in state['units'].items():
 after=run(['systemctl','show',unit,'-p','MainPID','--value'])['output'].strip()
 if after!=before['MainPID']:state['errors'].append('pid_changed:'+unit)
import io
archive=root/'private-backup.tar.gz'
with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
 for path,data in files.items():
  info=tarfile.TarInfo('files/'+path.lstrip('/'));info.size=len(data);info.mode=0o600
  tar.addfile(info,io.BytesIO(data))
 for name,data in generated.items():
  info=tarfile.TarInfo('evidence/'+name);info.size=len(data);info.mode=0o600
  tar.addfile(info,io.BytesIO(data))
  state.setdefault('generated',{})['evidence/'+name]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
os.chmod(archive,0o600);os.chown(archive,owner.pw_uid,owner.pw_gid)
state['archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
state['finished_at']=time.time();state['archive']=str(archive)
print(json.dumps(state))
'''


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def git(*args):
    return subprocess.check_output(['git','-c','core.safecrlf=false',*args],cwd=ROOT)


def verify_remote(archive, manifest):
    assert digest(archive)==manifest['archive_sha256']
    with tarfile.open(archive,'r:gz') as tar:
        for path,info in manifest['files'].items():
            assert hashlib.sha256(tar.extractfile('files/'+path.lstrip('/')).read()).hexdigest()==info['sha256']
        for path,info in manifest['generated'].items():
            assert hashlib.sha256(tar.extractfile(path).read()).hexdigest()==info['sha256']


def snapshot_database(source, destination, deadline_seconds=60):
    """Pin rollback-journal pages while copying; release before offline checks.

    A shared SQLite lock temporarily delays writer commits. In WAL mode a raw
    file copy is invalid, so use SQLite's backup API instead. Never overwrite a
    previous snapshot or perform recovery on the live source.
    """
    start = time.monotonic()

    def progress(*_):
        if time.monotonic() - start > deadline_seconds:
            raise TimeoutError('EMS backup deadline')

    if destination.exists():
        raise FileExistsError(destination)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5) as conn:
        conn.execute('BEGIN')
        conn.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
        journal = conn.execute('PRAGMA journal_mode').fetchone()[0]
        try:
            if journal in ('delete', 'truncate', 'persist'):
                with source.open('rb') as original, destination.open('xb') as copy:
                    while chunk := original.read(8 * 1024 * 1024):
                        progress()
                        copy.write(chunk)
            else:
                with sqlite3.connect(destination) as target:
                    conn.backup(target, pages=8192, progress=progress, sleep=.05)
        finally:
            conn.rollback()
    lock_seconds = time.monotonic() - start
    with sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True) as target:
        if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('Snapshot integrity check failed')
    return {'source': str(source), 'snapshot': str(destination),
            'sha256': digest(destination), 'integrity': 'ok',
            'journal_mode': journal, 'read_transaction_seconds': lock_seconds}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote',action='store_true')
    parser.add_argument('--ems-snapshot',type=Path,help='Reuse a completed offline SQLite snapshot; never the live DB')
    args=parser.parse_args()
    tag='c0-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=ROOT/'.work'/tag;out.mkdir(parents=True,exist_ok=False)
    assert subprocess.run(['git','check-ignore','-q',str(out)],cwd=ROOT).returncode==0
    manifest={'schema_version':1,'started_at':datetime.now(timezone.utc).isoformat(),
              'branch':git('branch','--show-current').decode().strip(),
              'git_head':git('rev-parse','HEAD').decode().strip(),'local_files':{},'hosts':{},
              'scope':'read-only network capture; not a globally atomic distributed snapshot',
              'restore_drill':'offline archive/database verification only'}
    if manifest['branch']!='codex/multi-upf-slicing':raise RuntimeError('Unexpected branch')
    (out/'git-status.txt').write_bytes(git('status','--short'))
    (out/'working.patch').write_bytes(git('diff','--binary','HEAD'))
    names=set(git('ls-files','-z','--cached','--others','--exclude-standard').decode().split('\0'))-{''}
    with zipfile.ZipFile(out/'workspace.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            path=ROOT/name
            if path.is_file():
                data=path.read_bytes();archive.writestr(name,data)
                manifest['local_files'][name]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
    with zipfile.ZipFile(out/'workspace.zip') as archive:
        for name,info in manifest['local_files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==info['sha256']
    settings=get_settings()
    manifest['python']=sys.version
    manifest['dependencies']=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True)
    database=settings.database_path.resolve()
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    if args.ems_snapshot:
        snapshot=args.ems_snapshot.resolve()
        if not snapshot.is_relative_to(ROOT/'.work') or snapshot==database:
            raise ValueError('Expected an existing private snapshot under .work')
        with sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True) as c:
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        manifest['ems_database']={'source':str(database),'snapshot':str(snapshot),
                                  'sha256':digest(snapshot),'integrity':'ok','external_snapshot':True}
    elif database.is_file():
        manifest['ems_database']=snapshot_database(database, out/'ems.sqlite3')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    if args.remote:
        if settings.execution_mode!='remote':raise RuntimeError('Remote mode required')
        for label,port in [('core',settings.ssh_port),('upf',settings.upf_ssh_port),
                           ('miot',settings.upf2_ssh_port),('gnb',settings.gnb_ssh_port),('ue',settings.ue_ssh_port)]:
            h=Lab(settings,port)
            try:
                remote=h.run(['mktemp','-d','/home/'+settings.ssh_user+'/maestro-baseline-'+tag+'-XXXXXX']).strip()
                result=json.loads(h.run(['python3','-c',REMOTE,remote,settings.ssh_user,label],sudo=True,timeout=180))
                with h.client.open_sftp() as sftp:sftp.get(result['archive'],str(out/(label+'.tar.gz')))
                verify_remote(out/(label+'.tar.gz'),result)
                result['verified_locally']=True;manifest['hosts'][label]=result
                print(label+': archive verified; errors='+str(len(result['errors'])),flush=True)
            finally:
                h.client.close()
                (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    manifest['finished_at']=datetime.now(timezone.utc).isoformat()
    manifest['status']='CAPTURED' if (not args.remote or len(manifest['hosts'])==5) and not any(
        h['errors'] for h in manifest['hosts'].values()) else 'PARTIAL'
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'status':manifest['status'],'evidence':str(out),'source_files':len(manifest['local_files'])}))


if __name__=='__main__':main()
