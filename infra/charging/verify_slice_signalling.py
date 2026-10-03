"""Extract minimal NGAP evidence from a migration capture and compare live CHF contexts."""
import argparse
import asyncio
import json
from pathlib import Path
from e2e_native import Lab
from lab_command import get_settings
from app.services import terminal
from app.services.charging import management_get, mask_identifiers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('migration_result', type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.migration_result.read_text(encoding='utf-8'))
    remote = evidence['remote_evidence']['core']
    import re
    if not re.fullmatch(r'/home/emsadmin/maestro-slices-[a-f0-9]+-[A-Za-z0-9]+', remote):
        raise RuntimeError('Not a known migration evidence directory')
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    try:
        fields = ['frame.number', 'ngap.procedureCode', 'ngap.pDUSessionID', 'ngap.sST', 'ngap.sD']
        cmd = ['tshark', '-r', remote + '/slices.pcap', '-Y', 'ngap.sD', '-T', 'fields']
        for field in fields:
            cmd += ['-e', field]
        raw = core.run(cmd, sudo=True)
        packets = []
        for line in raw.splitlines():
            parts = line.split('\t')
            if len(parts) == len(fields) and parts[0].isdigit():
                packets.append(dict(zip(fields, parts)))
        for psi, sd in [('1', '000001'), ('2', '000002')]:
            assert any(p['ngap.pDUSessionID'] == psi and p['ngap.sD'] == sd for p in packets), 'Missing per-PDU NGAP slice evidence'
        primary = asyncio.run(terminal.read_terminal())['supi']
        charging = {}
        for supi in (primary, 'imsi-999700000000002'):
            rows = management_get('/admin/v1/sessions?supi=' + supi + '&limit=100')['items']
            current = {}
            for row in rows:
                info = row.get('context', {}).get('pduSessionInformation', {})
                dnn = info.get('dnnId')
                if row['status'] == 'OPEN' and dnn in ('internet', 'corporate') and dnn not in current:
                    current[dnn] = {'pdu_session_id': info.get('pduSessionID'),
                                    'snssai': info.get('networkSlicingInfo', {}).get('sNSSAI'),
                                    'charging_id': row.get('charging_id')}
            for dnn, sd in [('internet', '000001'), ('corporate', '000002')]:
                assert current.get(dnn, {}).get('snssai') == {'sst': 1, 'sd': sd}, 'CHF slice context mismatch'
            charging[supi] = current
        result = mask_identifiers({'status': 'PASS', 'ngap': packets, 'charging': charging,
                                   'limitation': 'NGAP and native/CHF session evidence; no claim of full NAS decoding or 3GPP certification'})
        output = args.migration_result.with_name('signalling.json')
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'status': 'PASS', 'evidence': str(output), 'ngap': packets}))
    finally:
        core.client.close()


if __name__ == '__main__':
    main()
