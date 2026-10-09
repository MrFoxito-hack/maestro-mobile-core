"""Bounded SMF2 requested-unit calibration, restoring the exact original file."""
import argparse
import json
from c8_remote import ROOT, LoggedLab, get_settings

OUT=ROOT/'.work/c8-campaign/setup'
CONFIG='/etc/open5gs/smf2.yaml'


def main():
    global OUT
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','restore'])
    p.add_argument('--evidence',type=type(OUT),default=OUT);a=p.parse_args()
    OUT=a.evidence;OUT.mkdir(parents=True,exist_ok=True)
    core=LoggedLab(get_settings(),2222,OUT);ue=LoggedLab(get_settings(),2226,OUT)
    record=OUT/'smf-window.json'
    try:
        if a.action=='prepare':
            if record.exists(): raise ValueError('already_prepared')
            raw=core.run(['cat',CONFIG],sudo=True).encode()
            if raw.count(b'requested_units: 100\n')!=1:raise ValueError('unexpected_requested_units')
            (OUT/'smf2-before.yaml').write_bytes(raw)
            remote=core.run(['mktemp','-d','/home/emsadmin/c8-smf-XXXXXX']).strip()
            core.write(remote+'/before.yaml',raw)
            core.write(remote+'/after.yaml',raw.replace(b'requested_units: 100\n',b'requested_units: 100000\n'))
            code=f"import subprocess,pathlib\npathlib.Path({CONFIG!r}).write_bytes(pathlib.Path({remote+'/before.yaml'!r}).read_bytes())\nsubprocess.run(['systemctl','restart','open5gs-smfd2'],check=True)\n"
            core.write(remote+'/restore.py',code)
            record.write_text(json.dumps({'remote':remote}))
            core.run(['systemd-run','--unit=c8-smf-restore','--on-active=3500s','python3',remote+'/restore.py'],sudo=True)
            for supi in ['imsi-999700000000003','imsi-999700000000006']:
                ue.run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'-e','ps-release-all'],sudo=True,check=False)
            core.run(['python3','-c','import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())',remote+'/after.yaml',CONFIG],sudo=True)
            core.run(['systemctl','restart','open5gs-smfd2'],sudo=True)
            ue.run(['systemctl','restart','maestro-ue-sensor','ueransim-ue-06'],sudo=True)
        else:
            data=json.loads(record.read_text())
            for supi in ['imsi-999700000000003','imsi-999700000000006']:
                ue.run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'-e','ps-release-all'],sudo=True,check=False)
            core.run(['python3',data['remote']+'/restore.py'],sudo=True)
            if core.run(['cat',CONFIG],sudo=True).encode()!=(OUT/'smf2-before.yaml').read_bytes(): raise ValueError('restore_mismatch')
            core.run(['systemctl','stop','c8-smf-restore.timer'],sudo=True,check=False)
            ue.run(['systemctl','restart','maestro-ue-sensor','ueransim-ue-06'],sudo=True)
            data['restored_exact']=True;record.write_text(json.dumps(data))
        print(a.action)
    finally:core.client.close();ue.client.close()


if __name__=='__main__':main()
