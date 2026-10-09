"""Temporary MIoT grant calibration for C8; never erase consumed units."""
import argparse
import json
import sys
from c8_remote import ROOT, LoggedLab, get_settings
sys.path.insert(0, str(ROOT/'infra/charging'))
from verify_c5_live import api, API, ENV, CLI

OUT = ROOT/'.work/c8-campaign/setup'


def main():
    global OUT
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['prepare','restore'])
    p.add_argument('--evidence', type=type(OUT), default=OUT)
    a=p.parse_args()
    OUT=a.evidence; OUT.mkdir(parents=True,exist_ok=True)
    core=LoggedLab(get_settings(),2222,OUT)
    ue=LoggedLab(get_settings(),2226,OUT)
    path=OUT/'charging-window.json'
    try:
        if a.action=='prepare':
            if path.exists(): raise ValueError('window_already_recorded')
            policy=next(x for x in api(core,'/admin/v1/service-policies')['items'] if x['dnn']=='corporate')
            accounts={s:api(core,'/admin/v1/accounts/'+s) for s in ['imsi-999700000000003','imsi-999700000000006']}
            record={'before_policy':policy,'before_accounts':accounts}
            path.write_text(json.dumps(record,indent=2))
            remote=core.run(['mktemp','-d','/home/emsadmin/c8-charging-XXXXXX']).strip()
            restore=f'ENV={ENV!r}\nPATH="/admin/v1/service-policies"\nBODY={policy!r}\nMETHOD="PUT"\n'+API
            core.write(remote+'/restore.py',restore)
            core.run(['systemd-run','--unit=c8-charging-restore','--on-active=3600s','python3',remote+'/restore.py'],sudo=True)
            record['policy']=api(core,'/admin/v1/service-policies',{**policy,'grantBlockSize':100000},'PUT')
            record['accounts']={s:api(core,'/admin/v1/accounts/'+s+'/messages',{'quotaMessages':1000000},'PUT') for s in accounts}
            # Grants are a per-session snapshot. Release normally before reconnecting.
            for s in accounts: ue.run([CLI,s,'-e','ps-release-all'],sudo=True,check=False)
            ue.run(['systemctl','restart','maestro-ue-sensor','ueransim-ue-06'],sudo=True)
            path.write_text(json.dumps(record,indent=2))
        else:
            record=json.loads(path.read_text())
            record['restored_policy']=api(core,'/admin/v1/service-policies',record['before_policy'],'PUT')
            for s in record['before_accounts']: ue.run([CLI,s,'-e','ps-release-all'],sudo=True,check=False)
            # Keep the explicit experiment top-up and all real consumption; no ledger edits.
            ue.run(['systemctl','restart','maestro-ue-sensor','ueransim-ue-06'],sudo=True)
            core.run(['systemctl','stop','c8-charging-restore.timer'],sudo=True,check=False)
            record['after_accounts']={s:api(core,'/admin/v1/accounts/'+s) for s in record['before_accounts']}
            path.write_text(json.dumps(record,indent=2))
        print(json.dumps({'action':a.action,'evidence':str(path)}))
    finally:core.client.close();ue.client.close()


if __name__=='__main__':main()
