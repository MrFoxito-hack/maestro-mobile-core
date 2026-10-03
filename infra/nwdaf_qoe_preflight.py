"""Discover the real streaming client, UE addresses and available experiment quota."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from nwdaf_gate3_live_probe import accounts

if __name__=='__main__':
    settings=get_settings();result={'utc':datetime.now(timezone.utc).isoformat()}
    for name,port in [('core',settings.ssh_port),('ue',settings.ue_ssh_port)]:
        host=Lab(settings,port)
        try:
            info={}
            info['client_search']=host.run(['python3','-c',"import os,shutil,json; out={'executable':shutil.which('stream5g'),'ffmpeg':shutil.which('ffmpeg'),'files':[]};\nfor base in ['/home/emsadmin','/opt','/usr/local/bin']:\n for root,dirs,files in os.walk(base):\n  dirs[:]=[d for d in dirs if d not in ['.git','node_modules','.venv','build','__pycache__']]\n  out['files'] += [os.path.join(root,f) for f in files if 'stream5g' in f.lower()]\nprint(json.dumps(out))"],timeout=30)
            if name=='core':
                info['accounts']=accounts(host)
                info['media_probe']=host.run(['ffprobe','-v','error','-show_streams','-of','json','/opt/maestro-terminal-media/720p/index.m3u8'],check=False)
                info['pm_path']='/home/emsadmin/maestro-charging/nwdaf/data/pm-source.sqlite3'
            else:
                info['interfaces']=json.loads(host.run(['ip','-j','-4','addr','show']))
                info['ues']=host.run(['/home/emsadmin/UERANSIM/build/nr-cli','--dump'])
                info['primary_session']=host.run(['/home/emsadmin/UERANSIM/build/nr-cli','imsi-999700000000001','--exec','ps-list'])
            result[name]=info
        finally:host.client.close()
    directory=ROOT/'.work'/('nwdaf-qoe-preflight-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    directory.mkdir();(directory/'preflight.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'path':str(directory),'core_client':result['core']['client_search'],'ue_client':result['ue']['client_search'],
        'session':result['ue']['primary_session'],'accounts':result['core']['accounts'][:5],
        'media_probe':result['core']['media_probe'][:2000]},indent=2))
