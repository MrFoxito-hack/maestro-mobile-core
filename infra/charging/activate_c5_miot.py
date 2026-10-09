"""Activate the C5 packet profile only in SMF2, retaining observer ABI/hooks."""
import json
import time
import yaml
from e2e_native import Lab, ROOT
from lab_command import get_settings
from verify_c5_live import api, CLI


def main():
    settings=get_settings()
    core,ue=Lab(settings,2222),Lab(settings,2226)
    result={}
    original=None
    previous_override=None
    target='/etc/systemd/system/open5gs-smfd2.service.d/zzzzzz-c5-charging.conf'
    try:
        build=json.loads((ROOT/'.work/c5-charging/evidence/native-build.json').read_text())
        binary=build['directory']+'/out/open5gs-smfd'
        bundle='/opt/maestro-c5-'+build['manifest']['binarySha256'][:12]
        result['bundle']=bundle
        directory=core.run(['mktemp','-d','/home/emsadmin/c5-miot-XXXXXX']).strip()
        result['directory']=directory
        original=core.run(['cat','/etc/open5gs/smf2.yaml'],sudo=True)
        core.write(directory+'/smf2.before.yaml',original)
        try:
            previous_override=core.read(target)
            assert b'/opt/maestro-c5-' in previous_override and b'MAESTRO_CHF_PACKET_QUOTA=1' in previous_override
            core.write(directory+'/override.before.conf',previous_override)
        except FileNotFoundError:pass
        for i in (3,6):
            supi='imsi-99970000000000'+str(i)
            account=api(core,'/admin/v1/accounts/'+supi)
            quota=max(10000,(account.get('messages') or {}).get('quota_messages',0))
            result['account'+str(i)]=api(core,'/admin/v1/accounts/'+supi+'/messages',{'quotaMessages':quota},'PUT')
        result['policy']=api(core,'/admin/v1/service-policies',dict(dnn='corporate',sst=3,sd='000003',ratingGroup=1,mode='MESSAGE_QUOTA',unitKind='IP_PACKET',grantBlockSize=100),'PUT')
        for i in (3,6):
            ue.run([CLI,'imsi-99970000000000'+str(i),'-e','deregister switch-off'],check=False)
        ue.run(['systemctl','stop','maestro-ue-sensor','ueransim-ue-06'],sudo=True)
        time.sleep(2)
        config=yaml.safe_load(original)
        config['smf']['chf']['requested_units']=100
        core.write(directory+'/smf2.yaml',yaml.safe_dump(config,sort_keys=False))
        core.run(['install','-d','-m','755',bundle],sudo=True)
        core.run(['install','-m','755',binary,bundle+'/open5gs-smfd'],sudo=True)
        dropin='[Service]\nExecStart=\nExecStart='+bundle+'/open5gs-smfd -c /etc/open5gs/smf2.yaml\nEnvironment=MAESTRO_CHF_PACKET_QUOTA=1\n'
        core.write(directory+'/c5.conf',dropin)
        core.run(['install','-o','root','-g','open5gs','-m','640',directory+'/smf2.yaml','/etc/open5gs/smf2.yaml'],sudo=True)
        core.run(['install','-m','644',directory+'/c5.conf',target],sudo=True)
        core.run(['systemctl','daemon-reload'],sudo=True)
        core.run(['systemctl','restart','open5gs-smfd2'],sudo=True)
        result['service']=core.run(['systemctl','is-active','open5gs-smfd2'])
        ue.run(['systemctl','start','maestro-ue-sensor','ueransim-ue-06'],sudo=True)
        time.sleep(6)
        for i in (3,6):
            supi='imsi-99970000000000'+str(i)
            raw=ue.run([CLI,supi,'-e','ps-list'],check=False)
            if 'PS-ACTIVE' not in raw:
                ue.run([CLI,supi,'-e','ps-establish IPv4 --sst 3 --sd 3 --dnn corporate'],check=False)
        time.sleep(3)
        result['pdus']={str(i):ue.run([CLI,'imsi-99970000000000'+str(i),'-e','ps-list'],check=False) for i in (3,6)}
        from verify_c5_services import pdu
        result['confirmedPdus']={str(i):pdu(ue,i,'corporate',timeout=30) for i in (3,6)}
        result['status']='ACTIVE'
    except Exception as exc:
        result['status']='FAILED'
        result['error']=str(exc)
        if original is not None:
            core.run(['install','-o','root','-g','open5gs','-m','640',directory+'/smf2.before.yaml','/etc/open5gs/smf2.yaml'],sudo=True)
            if previous_override:
                core.run(['install','-m','644',directory+'/override.before.conf',target],sudo=True)
            else:
                core.run(['rm','-f',target],sudo=True,check=False)
            core.run(['systemctl','daemon-reload'],sudo=True)
            if not previous_override:
                api(core,'/admin/v1/service-policies',dict(dnn='corporate',sst=3,sd='000003',ratingGroup=1,mode='BYTE_QUOTA'),'PUT')
            core.run(['systemctl','restart','open5gs-smfd2'],sudo=True,check=False)
            ue.run(['systemctl','start','maestro-ue-sensor','ueransim-ue-06'],sudo=True,check=False)
        raise
    finally:
        (ROOT/'.work/c5-charging/evidence/miot-activation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        for host in (core,ue):host.client.close()
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
