"""Bounded real receiver probe, separate from the closed-loop campaign worker.

CLI operator tool. Student dry_run/binding permissions NEVER authorize this probe.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from app.laboratory.live import LabSSH, CORE_SCRIPT, UE_SCRIPT, session_identity
from app.laboratory.preflight import CORE_UNITS
from app.laboratory.repository import canonical, stamp


class ReceiverProbe:
    def __init__(self, transport=None):
        self.transport = transport or LabSSH()
        self.program = Path(__file__).with_name('remote_probe_guard.py').read_text(encoding='utf-8')

    def guard(self, token, operation, **values):
        return self.transport.read('ue', self.program, (canonical({'token': token, 'operation': operation, **values}),))

    def recover(self, directory):
        private = json.loads((directory / 'recovery-private.json').read_text(encoding='utf-8'))
        report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
        result = self.guard(private['token'], 'recover')
        if result.get('recovery_verified') is True:
            report['recovery_verified'] = True
            if report['execution_status'] == 'recovery_required': report['execution_status'] = 'failed'
            report['recovered_at'] = stamp()
            temp = directory / 'report.tmp'
            temp.write_text(canonical(report), encoding='utf-8'); temp.replace(directory / 'report.json')
        return result

    def run(self, supi, directory):
        import re
        if not re.fullmatch(r'imsi-\d{14,15}', supi): raise ValueError('Invalid laboratory subject')
        directory.mkdir(parents=True, exist_ok=False)
        token = uuid.uuid4().hex
        # Token is private recovery material and is deliberately excluded from public report/export.
        (directory / 'recovery-private.json').write_text(canonical({'token': token}), encoding='utf-8')
        report = {'id': directory.name, 'started_at': stamp(), 'scope': 'fixed_http_manifest_probe',
                  'source': 'live_ssh', 'execution_status': 'preflight', 'hypothesis_outcome': 'not_evaluated',
                  'metrics': None, 'recovery_verified': False}
        def save():
            temporary = directory / 'report.tmp'
            temporary.write_text(canonical(report), encoding='utf-8')
            temporary.replace(directory / 'report.json')
        save()
        reserved = False
        try:
            core = self.transport.read('core', CORE_SCRIPT, (json.dumps([supi]), json.dumps(CORE_UNITS)))
            ue = self.transport.read('ue', UE_SCRIPT, (json.dumps([supi]),))
            session = session_identity(ue['subjects'][supi], ue['interfaces'], 'internet')
            account = core.get('accounts', {}).get(supi)
            if not account or account['quota_bytes'] - account['consumed_bytes'] - account['reserved_bytes'] < 1_000_000:
                raise ValueError('insufficient_unreserved_quota')
            media = core.get('media_manifest')
            if not media or not 0 < media['bytes'] <= 16384: raise ValueError('media_not_verified')
            report['preflight'] = {'session': session, 'account_before': account, 'expected_media': media,
                                   'traffic_allowance_bytes': 1_000_000}
            report['execution_status'] = 'reserve_intent'; save()
            # Unknown reserve outcome is also reconciled with the SAME token.
            reserved = True
            reservation = self.guard(token, 'reserve')
            if reservation.get('status') != 'reserved': raise ValueError('remote_reservation_blocked')
            report['execution_status'] = 'measurement_intent'; save()
            result = self.guard(token, 'measure', interface=session['interface'], address=session['address'], expected_sha256=media['sha256'])
            report['remote_status'] = result['status']
            if result['status'] != 'measured': raise ValueError(result.get('error_code', 'measurement_unknown'))
            report['metrics'] = result['result']
            report['execution_status'] = 'measured'; save()
        except Exception as error:
            report['execution_status'] = 'failed'
            report['error_code'] = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]+', str(error)) else 'transport_or_observation_unknown'
        finally:
            if reserved:
                try:
                    recovery = self.guard(token, 'recover')
                    report['recovery_verified'] = recovery.get('recovery_verified') is True
                    if not report['recovery_verified']: report['execution_status'] = 'recovery_required'
                except Exception:
                    report['execution_status'] = 'recovery_required'
            else:
                report['recovery_verified'] = True  # no remote reservation or effect was attempted
                report['recovery_scope'] = 'no_effect_attempted'
            try:
                after = self.transport.read('core', CORE_SCRIPT, (json.dumps([supi]), json.dumps(CORE_UNITS)))
                report['account_after'] = after.get('accounts', {}).get(supi)
                report['services_after'] = {k: v.get('ActiveState') for k, v in after.get('services', {}).items()}
            except Exception:
                report['account_after'] = None
            report['finished_at'] = stamp()
            report['limitations'] = ['Tiempo HTTP y bytes recibidos, no startup de reproductor ni MOS.',
                                    'Recuperación verificada solo para procesos de esta sonda.',
                                    'CHF puede recibir reportes tardíos; no se revierte consumo.',
                                    'No calibra congestión ni acredita closed-loop o enforcement.']
            save()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--supi')
    parser.add_argument('--recover', type=Path)
    args = parser.parse_args()
    if args.recover:
        result = ReceiverProbe().recover(args.recover)
        print(json.dumps(result)); raise SystemExit(0 if result.get('recovery_verified') else 1)
    if not args.execute or not args.supi: parser.error('--execute and --supi are required for a bounded real transfer')
    directory = Path(__file__).resolve().parents[3] / '.work' / ('lab-receiver-' + uuid.uuid4().hex)
    result = ReceiverProbe().run(args.supi, directory)
    print(json.dumps({'path': str(directory), 'status': result['execution_status'], 'recovery_verified': result['recovery_verified']}))
    raise SystemExit(0 if result['execution_status'] == 'measured' and result['recovery_verified'] else 1)


if __name__ == '__main__':
    main()
