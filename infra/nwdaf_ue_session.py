"""Inspect or re-establish only the selected UE's Internet PDU session."""
import argparse
import json
import re
from pathlib import Path
import sys
import time
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--supi',default='imsi-999700000000004')
    parser.add_argument('--reestablish',action='store_true')
    args=parser.parse_args()
    if args.supi not in ['imsi-999700000000001','imsi-999700000000004','imsi-999700000000005']:
        parser.error('Choose an existing lab UE with available quota')
    settings=get_settings(); ue=Lab(settings,settings.ue_ssh_port)
    cli=['/home/emsadmin/UERANSIM/build/nr-cli',args.supi,'--exec']
    try:
        before=yaml.safe_load(ue.run(cli+['ps-list']))
        print('before',json.dumps(before),flush=True)
        if args.reestablish:
            for key,pdu in before.items():
                if isinstance(pdu,dict) and pdu.get('apn')=='internet':
                    identity=re.fullmatch(r'PDU Session(\d+)',str(key))
                    if not identity: raise RuntimeError('Unrecognized PDU identity')
                    print(ue.run(cli+['ps-release '+identity[1]]),flush=True)
            time.sleep(2)
            print(ue.run(cli+['ps-establish IPv4 --dnn internet --sst 1 --sd 1']),flush=True)
            time.sleep(4)
            print('after',ue.run(cli+['ps-list']),flush=True)
        else:
            print(ue.run(cli+['ps-establish --help'],check=False),flush=True)
            print(ue.run(cli+['ps-release --help'],check=False),flush=True)
    finally:ue.client.close()
