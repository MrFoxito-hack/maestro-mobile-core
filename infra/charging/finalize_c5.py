"""Certify C5 only when component tests and all native evidence pass."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from e2e_native import Lab, ROOT
from lab_command import get_settings
from verify_c5_live import api
from verify_c5_services import pdu


def main():
    evidence=ROOT/'.work/c5-charging/evidence'
    embb=json.loads((evidence/'live-acceptance.json').read_text())
    pointer=json.loads((evidence/'services-latest.json').read_text())
    services=json.loads((Path(pointer['directory'])/'result.json').read_text())
    replays=json.loads((evidence/'native-journal-replays.json').read_text())
    assert embb['status']=='EMBB_PASS' and services['status']=='PASS'
    assert set(services['cases'])=={'2','5','3','6'}
    assert all(v['status']=='PASS' for v in services['cases'].values())
    assert len(replays)==4 and all(v['exactReleaseReplay']=='HTTP_204_NO_ADDITIONAL_DEBIT' for v in replays.values())
    assert '69 passed, 1 skipped' in (evidence/'pytest.txt').read_text(encoding='utf-16')
    assert '70 passed' in (evidence/'pytest-core.txt').read_text()
    assert 'Native CHF checks passed: 50198' in (evidence/'native-build.log').read_text()
    assert 'PASS C5 native' in (evidence/'native-build.log').read_text()
    build=json.loads((evidence/'native-build.json').read_text())
    cdrs=[r['record'] for r in embb['cdrsAfterCut']['items'] if r['record']['endTimestamp']>=embb['startedAt'] and r['record']['totalBytes']>0]
    assert len(cdrs)==1
    e_cdr=cdrs[0]
    debit_delta=embb['accountAfterTraffic']['consumed_bytes']-embb['limited']['consumed_bytes']
    assert debit_delta==500000 and e_cdr['totalBytes']==e_cdr['debitedBytes']+e_cdr['overrunBytes']
    settings=get_settings()
    core,ue,upf=Lab(settings,2222),Lab(settings,2226),Lab(settings,2223)
    try:
        accounts=[api(core,'/admin/v1/accounts/imsi-99970000000000'+str(i)) for i in range(1,7)]
        for a in accounts:
            assert a['consumed_bytes']+a['reserved_bytes']<=a['quota_bytes']
            m=a.get('messages')
            if m:assert m['consumed_messages']+m['reserved_messages']<=m['quota_messages']
        pdus={str(i):pdu(ue,i,{1:'internet',2:'5g-plus',3:'corporate'}[(i-1)%3+1]) for i in range(1,7)}
        upf_state=upf.run(['systemctl','show','open5gs-upfd-urllc','-p','MainPID','-p','ActiveEnterTimestamp'])
        activation=json.loads((evidence/'urllc-activation.json').read_text())
        assert upf_state==activation['upf3Before']
        installed=core.run(['sha256sum',json.loads((evidence/'miot-activation.json').read_text())['bundle']+'/open5gs-smfd']).split()[0]
        assert installed==build['manifest']['binarySha256']
        frozen=core.run(['sha256sum','/opt/maestro-observer-8e3dbed04906/open5gs-smfd']).split()[0]
        assert frozen==json.loads((evidence/'matching-smf-base.json').read_text())['sha256']
        result={'certifiedAt':datetime.now(timezone.utc).isoformat(),'c5Status':'COMPLETE','completionPercent':100,
                'scope':'C5 laboratory charging acceptance; not full 3GPP certification',
                'ready':api(core,'/ready'),'accounts':accounts,'pdus':pdus,'policies':api(core,'/admin/v1/service-policies'),
                'embb':{'cdr':e_cdr,'testDebitDeltaBytes':debit_delta,'pduRecoverySeconds':embb['pduRecoverySeconds'],'httpVerifiedSeconds':embb['httpRecoverySeconds']},
                'services':{k:{'status':v['status'],'cdr':v['cdr']} for k,v in services['cases'].items()},
                'tests':{'windows':'69 passed, 1 POSIX skipped','core':'70 passed','nativeLegacyChecks':50198,'nativeC5':'PASS'},
                'upf3Unrestarted':upf_state,'frozenSmfBinaryUnchanged':frozen,
                'nativeC5Binary':installed,'nativePreservedObjects':build['manifest']['preservedObjects'],
                'journalReplayCount':len(replays),
                'limits':['MIoT units are measured IP packets UL+DL, not application-message estimates.',
                          'MIoT settles once per second; excess between reports is recorded as overrun, not charged beyond reserved credit.',
                          'Historical open byte reservations were retained; they were not invented, removed or used to simulate zero-rating.']}
        assert result['ready']=={'status':'ready','schemaVersion':3}
        result['evidenceSha256']={p.relative_to(evidence).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [evidence/'live-acceptance.json',Path(pointer['directory'])/'result.json',evidence/'native-journal-replays.json',evidence/'pytest.txt',evidence/'pytest-core.txt',evidence/'native-build.log']}
        (evidence/'certification-status.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({'status':result['c5Status'],'percent':100,'embbCdr':e_cdr,'pdus':pdus},indent=2))
    finally:
        for host in (core,ue,upf):host.client.close()


if __name__=='__main__':main()
