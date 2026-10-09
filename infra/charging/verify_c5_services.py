"""Native URLLC zero-credit and MIoT packet-quota acceptance on actual UEs."""
import json
import time
from datetime import datetime, timezone
from uuid import uuid4
import yaml
from e2e_native import Lab, ROOT
from lab_command import get_settings
from verify_c5_live import api, CLI


def pdu(ue, number, dnn, *, timeout=15):
    supi='imsi-99970000000000'+str(number)
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        data=yaml.safe_load(ue.run([CLI,supi,'-e','ps-list'],check=False)) or {}
        for session in data.values():
            if isinstance(session,dict) and session.get('state')=='PS-ACTIVE' and session.get('apn')==dnn:
                return session
        time.sleep(.5)
    raise RuntimeError('PDU not active: '+supi)


def recreate(ue, number, dnn, sst):
    supi='imsi-99970000000000'+str(number)
    ue.run([CLI,supi,'-e','ps-release-all'],check=False)
    time.sleep(1)
    raw=ue.run([CLI,supi,'-e','ps-list'],check=False)
    if 'PS-ACTIVE' not in raw:
        ue.run([CLI,supi,'-e',f'ps-establish IPv4 --sst {sst} --sd {sst} --dnn {dnn}'],check=False)
    return pdu(ue,number,dnn)


def main():
    settings=get_settings()
    core,ue=Lab(settings,2222),Lab(settings,2226)
    tag=uuid4().hex[:8]
    out=ROOT/'.work/c5-charging/evidence'/('services-'+tag)
    out.mkdir()
    result={'startedAt':datetime.now(timezone.utc).isoformat(),'cases':{}}
    try:
        for number in (2,5,3,6):
            supi='imsi-99970000000000'+str(number)
            urllc=number in (2,5)
            dnn='5g-plus' if urllc else 'corporate'
            item=result['cases'][str(number)]={}
            original=api(core,'/admin/v1/accounts/'+supi)
            try:
                if urllc:
                    item['zeroCredit']=api(core,'/admin/v1/accounts/'+supi,dict(supi=supi,quotaBytes=original['consumed_bytes']+original['reserved_bytes'],enabled=True),'PUT')
                    assert item['zeroCredit']['available_bytes']==0
                item['pduBefore']=recreate(ue,number,dnn,2 if urllc else 3)
                time.sleep(1.2)
                sessions=api(core,'/admin/v1/sessions?supi='+supi+'&limit=10')['items']
                active=next(s for s in sessions if s['status']=='OPEN' and s.get('policy',{}).get('mode')==('ZERO_RATED' if urllc else 'MESSAGE_QUOTA'))
                item['session']=active
                ref=active['charging_data_ref']
                address=item['pduBefore']['address']
                item['beforeTraffic']=api(core,'/admin/v1/accounts/'+supi)
                if urllc:
                    item['traffic']=ue.run(['ping','-I',address,'-s','1000','-c','300','-i','.03','-W','1','172.31.48.2'],sudo=True,check=False,timeout=30)
                    assert '300 received' in item['traffic'],'URLLC echo traffic had loss'
                    assert pdu(ue,number,dnn)['address']==address,'URLLC PDU changed during traffic'
                else:
                    # 3 telemetry bursts, each with 20 unique 64-byte application records.
                    # Each echo is a second IP packet. Account actual UL+DL packets.
                    code='''import socket,json,time,struct
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind((ADDRESS,0));s.settimeout(1)
bursts=[]
for burst in range(3):
 records=[]
 for i in range(20):
  seq=burst*20+i
  payload=struct.pack('!II',RUN,seq)+json.dumps({'sensor':NUMBER,'temperature':20+i%5}).encode()
  payload=payload.ljust(64,b' ')
  start=time.monotonic();s.sendto(payload,('10.46.0.1',8765))
  try: data,peer=s.recvfrom(256);ok=data==payload and peer==('10.46.0.1',8765)
  except socket.timeout:ok=False
  records.append({'sequence':seq,'ack':ok,'rttMs':(time.monotonic()-start)*1000})
  time.sleep(.02)
 bursts.append(records);time.sleep(1.4)
print(json.dumps({'applicationMessages':60,'expectedIpPackets':120,'payloadBytes':64,'bursts':bursts}))
'''
                    prefix=f'ADDRESS={address!r}\nNUMBER={number}\nRUN={int(tag,16)}\n'
                    item['traffic']=json.loads(ue.run(['python3','-c',prefix+code],timeout=90))
                    assert all(r['ack'] for burst in item['traffic']['bursts'] for r in burst),'MIoT echo acknowledgement failed'
                time.sleep(1.5)
                item['afterTraffic']=api(core,'/admin/v1/accounts/'+supi)
                if urllc:
                    assert item['afterTraffic']['consumed_bytes']==original['consumed_bytes']
                    assert item['afterTraffic']['available_bytes']==0
                else:
                    assert item['afterTraffic']['messages']['consumed_messages']>item['beforeTraffic']['messages']['consumed_messages']
                ue.run([CLI,supi,'-e','ps-release-all'])
                for _ in range(30):
                    cdrs=api(core,'/admin/v1/cdrs?supi='+supi+'&limit=15')['items']
                    cdr=next((x['record'] for x in cdrs if x['charging_data_ref']==ref),None)
                    if cdr:break
                    time.sleep(.5)
                assert cdr,'Native release CDR missing'
                item['cdr']=cdr
                if urllc:
                    assert cdr['totalBytes']>=600000 and cdr['debitedBytes']==0
                    assert cdr['sumOfReplacementGrantsBytes']>=1000000,'No native quota renewal observed'
                else:
                    assert cdr['serviceSpecificUnit']=='IP_PACKET'
                    assert cdr['totalPackets']==cdr['debitedPackets'] and cdr['totalPackets']>=120
                    assert cdr['overrunPackets']==0 and cdr['totalBytes']>=11040
                item['status']='PASS'
            finally:
                if urllc:
                    item['restoredAccount']=api(core,'/admin/v1/accounts/'+supi,dict(supi=supi,quotaBytes=original['quota_bytes'],enabled=bool(original['enabled'])),'PUT')
                item['restoredPdu']=recreate(ue,number,dnn,2 if urllc else 3)
                (out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        result['status']='PASS'
        result['ready']=api(core,'/ready')
    except Exception as exc:
        result['status']='FAILED'
        result['error']=str(exc)
        raise
    finally:
        result['finishedAt']=datetime.now(timezone.utc).isoformat()
        (out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        (ROOT/'.work/c5-charging/evidence/services-latest.json').write_text(json.dumps({'directory':str(out),'status':result['status']},indent=2),encoding='utf-8')
        for host in (core,ue):host.client.close()
    print(json.dumps({'status':result['status'],'evidence':str(out)},indent=2))


if __name__=='__main__':main()
