"""Export deterministic CHF policy evidence. Synthetic SBI inputs, NOT UE traffic.

Run with PYTHONPATH=chf and --output pointing to a new evidence directory.
An exclusive directory protects earlier runs from accidental overwrite.
"""
import argparse
import json
from pathlib import Path

from app.models import ChargingDataRequest
from app.repository import ChargingRepository
from app.service import ChargingService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    repo = ChargingRepository(args.output / 'charging.sqlite3')
    repo.initialize()
    service = ChargingService(repo, 1000, 60)
    results = {'evidenceType': 'CHF_COMPONENT_SYNTHETIC_REQUESTS_NOT_NATIVE_UE_TRAFFIC', 'cases': {}}
    for number, dnn, mode in ((1, 'internet', 'BYTE_QUOTA'), (2, '5g-plus', 'ZERO_RATED'), (3, 'corporate', 'MESSAGE_QUOTA')):
        supi = 'imsi-99970000000000' + str(number)
        repo.upsert_account(supi, 100, True, 'c5-component-acceptance')
        repo.upsert_policy(dict(dnn=dnn, sst=number, sd=f'{number:06x}', ratingGroup=1, mode=mode, grantBlockSize=10))
        messages = mode == 'MESSAGE_QUOTA'
        if messages:
            repo.upsert_message_account(supi, 15)
        def request(seq, used=None, wanted=1000, charging_id=1, dnn_override=None):
            unit = {'ratingGroup': 1, 'requestedUnit': {'serviceSpecificUnits' if messages else 'totalVolume': wanted}}
            if used is not None:
                report = {'localSequenceNumber': seq, 'totalVolume': used * 92 if messages else used}
                if messages:
                    report['serviceSpecificUnits'] = used
                unit['usedUnitContainer'] = [report]
            return ChargingDataRequest.model_validate(dict(
                subscriberIdentifier=supi, chargingId=charging_id,
                nfConsumerIdentification={'nodeFunctionality': 'SMF', 'nFName': '00000000-0000-4000-8000-000000000001'},
                invocationTimeStamp='2026-10-06T00:00:00Z', invocationSequenceNumber=seq,
                notifyUri='http://127.0.0.1/notify', multipleUnitUsage=[unit],
                pDUSessionChargingInformation={'pduSessionInformation': {
                    'pduSessionID': 1, 'dnnId': dnn_override or dnn,
                    'networkSlicingInfo': {'sNSSAI': {'sst': number, 'sd': f'{number:06x}'}}}}))
        if mode == 'ZERO_RATED':
            # Exhaust real credit using a metered context. No enormous quota.
            ref, _ = service.create(request(1, wanted=100, charging_id=99, dnn_override='metered-control'))
            service.release(ref, request(2, used=100, charging_id=99, dnn_override='metered-control'))
        before = repo.get_account(supi)
        ref, created = service.create(request(1))
        used = 10 if messages else 1000 if mode == 'ZERO_RATED' else 100
        updated = service.update(ref, request(2, used=used))
        assert service.update(ref, request(2, used=used)) == updated
        service.release(ref, request(3, used=5 if messages else 333 if mode == 'ZERO_RATED' else 0))
        cdr = next(x['record'] for x in repo.list_records('cdrs', supi=supi)['items'] if x['charging_data_ref'] == ref)
        after = repo.get_account(supi)
        assert after['consumed_bytes'] + after['reserved_bytes'] <= after['quota_bytes']
        if mode == 'ZERO_RATED':
            assert before['available_bytes'] == after['available_bytes'] == 0
            assert cdr['debitedBytes'] == 0 and cdr['totalBytes'] == 1333
        if messages:
            assert cdr['debitedMessages'] == 15 and cdr['totalBytes'] == 1380
        results['cases'][mode] = dict(before=before, create=created.model_dump(mode='json', exclude_none=True),
            update=updated.model_dump(mode='json', exclude_none=True), after=after, cdr=cdr)
    results['readiness'] = repo.readiness()
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'status': 'PASS', 'evidenceType': results['evidenceType']}))


if __name__ == '__main__':
    main()
