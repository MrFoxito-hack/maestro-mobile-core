"""Enable the existing Nchf path in SMF3. Never restart or modify UPF3."""
import json
import time
import yaml
from e2e_native import Lab, ROOT
from lab_command import get_settings
from verify_c5_live import api, CLI


def main():
    settings=get_settings()
    core,ue,upf=Lab(settings,2222),Lab(settings,2226),Lab(settings,2223)
    result={}
    backup=None
    try:
        result['upf3Before']=upf.run(['systemctl','show','open5gs-upfd-urllc','-p','MainPID','-p','ActiveEnterTimestamp'])
        directory=core.run(['mktemp','-d','/home/emsadmin/c5-urllc-XXXXXX']).strip()
        result['directory']=directory
        config=core.run(['cat','/etc/open5gs/smf3.yaml'],sudo=True)
        core.write(directory+'/smf3.before.yaml',config)
        backup=directory+'/smf3.before.yaml'
        parsed=yaml.safe_load(config)
        assert parsed['smf']['chf']['nf_instance_id']=='ea40a6fe-b275-5200-8f2a-92b81406bd14'
        # Existing configured bearer must match the actual NF's CHF credential.
        check='''import pathlib,json,shlex
def env(path):
 return {k:v for token in shlex.split(pathlib.Path(path).read_text(),comments=True) if '=' in token for k,v in [token.split('=',1)]}
s=env('/etc/open5gs/smf3.env'); c=env('/etc/open5gs/chf-triad.env')
assert s.get('SMF_CHF_TOKEN') and json.loads(c['CHF_SBI_TOKENS'])['ea40a6fe-b275-5200-8f2a-92b81406bd14']==s['SMF_CHF_TOKEN']
print('SMF3_NF_BOUND_CREDENTIAL_MATCH')
'''
        result['credentialCheck']=core.run(['python3','-c',check],sudo=True)
        policy=dict(dnn='5g-plus',sst=2,sd='000002',ratingGroup=1,mode='ZERO_RATED')
        result['policy']=api(core,'/admin/v1/service-policies',policy,'PUT')
        for i in (2,5):
            result['release'+str(i)]=ue.run([CLI,'imsi-99970000000000'+str(i),'-e','deregister switch-off'],check=False)
        ue.run(['systemctl','stop','maestro-ue-vehicle','ueransim-ue-05'],sudo=True)
        time.sleep(2)
        parsed['smf']['chf']['enabled']=True
        core.write(directory+'/smf3.yaml',yaml.safe_dump(parsed,sort_keys=False))
        core.run(['install','-o','root','-g','open5gs','-m','640',directory+'/smf3.yaml','/etc/open5gs/smf3.yaml'],sudo=True)
        core.run(['systemctl','restart','open5gs-smfd3'],sudo=True)
        result['smf3']=core.run(['systemctl','is-active','open5gs-smfd3'])
        ue.run(['systemctl','start','maestro-ue-vehicle','ueransim-ue-05'],sudo=True)
        time.sleep(2)
        for i in (2,5):
            supi='imsi-99970000000000'+str(i)
            raw=ue.run([CLI,supi,'-e','ps-list'],check=False)
            if 'PS-ACTIVE' not in raw:
                ue.run([CLI,supi,'-e','ps-establish IPv4 --sst 2 --sd 2 --dnn 5g-plus'],check=False)
        time.sleep(3)
        result['pdus']={str(i):ue.run([CLI,'imsi-99970000000000'+str(i),'-e','ps-list'],check=False) for i in (2,5)}
        result['upf3After']=upf.run(['systemctl','show','open5gs-upfd-urllc','-p','MainPID','-p','ActiveEnterTimestamp'])
        assert result['upf3Before']==result['upf3After']
        from verify_c5_services import pdu
        result['confirmedPdus']={str(i):pdu(ue,i,'5g-plus',timeout=30) for i in (2,5)}
        result['status']='ACTIVE'
    except Exception as exc:
        result['status']='FAILED'
        result['error']=str(exc)
        if backup:
            core.run(['install','-o','root','-g','open5gs','-m','640',backup,'/etc/open5gs/smf3.yaml'],sudo=True)
            core.run(['systemctl','restart','open5gs-smfd3'],sudo=True,check=False)
            ue.run(['systemctl','start','maestro-ue-vehicle','ueransim-ue-05'],sudo=True,check=False)
        raise
    finally:
        (ROOT/'.work/c5-charging/evidence/urllc-activation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        for host in (core,ue,upf):host.client.close()
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
