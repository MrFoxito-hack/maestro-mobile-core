"""Public C3 mode round trip; failures and dry runs never claim acceptance.

Run on Core as root. Uses supervisor acquire/submit/commit and adapter reads.
A lost mutation response is never retried. The supervisor owns expiry recovery.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

try:
    from .diagnose_c3_writers import stream_request, dgram_request, success, AUTHORITY_SOCK, ADAPTER_SOCK
except ImportError:
    from diagnose_c3_writers import stream_request, dgram_request, success, AUTHORITY_SOCK, ADAPTER_SOCK


def persist(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as file:
        json.dump(value, file, indent=2, allow_nan=False)
        file.write('\n')
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)
    if os.name == 'posix':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sanitized(value):
    if isinstance(value, dict):
        return {key: ('<redacted>' if key in {'token', 'fencing_token', 'authority', 'cookie'} else sanitized(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return value


def version_of(value):
    version = value.get('version')
    if type(version) is not int or version < 0:
        raise ValueError('invalid_authority_version')
    return version


def checkpoint(path):
    value = success(dgram_request(path, {'operation': 'checkpoint'}))
    measured = value.get('measured_at')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if (value.get('schema_version') != 2 or value.get('scope') != 'effective_policy' or
            value.get('complete') is not True or value.get('pending_n7') != [] or
            value.get('pending_n4') != [] or not value.get('sessions') or
            value.get('clock_boot_id') != boot or type(measured) not in (int, float) or
            not 0 <= time.monotonic() - measured <= 2):
        raise ValueError('effective_checkpoint_not_complete')
    return value


def run(*, evidence, authority_sock=AUTHORITY_SOCK, adapter_sock=ADAPTER_SOCK,
        owner='c3-closure-maestro', ttl=30, dry_run=False):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'dry_run': dry_run,
              'c3_accepted': False, 'authority_cycle_executed': False,
              'steps': [], 'restoration_required': False}
    path = evidence / 'cycle-result.json'

    def record(label, value):
        report['steps'].append({'step': label, 'at': datetime.now(timezone.utc).isoformat(),
                                'data': sanitized(value)})
        persist(path, report)

    def request(label, body):
        # Persist the intent before sending. A timeout leaves its outcome unknown.
        record(label + '/intent', body)
        reply = stream_request(authority_sock, body, timeout=25)
        record(label + '/response', reply)
        return success(reply)

    try:
        state = request('preflight/status', {'operation': 'status'})
        version = version_of(state)
        report['initial_version'] = version
        if state.get('admission_available') is not True or state.get('admission_error') is not None:
            raise ValueError(state.get('admission_error') or 'admission_unavailable')
        if state.get('phase') != 'idle' or state.get('owner') is not None:
            raise ValueError('authority_not_idle')
        baseline = checkpoint(adapter_sock)
        record('preflight/checkpoint', baseline)
        if baseline.get('mode') != 'AUTONOMOUS' or version_of(baseline) != version:
            raise ValueError('autonomous_baseline_at_current_version_required')
        if dry_run:
            report['preflight_passed'] = True
            return report
        for index, mode in enumerate(('MANUAL', 'AUTONOMOUS'), 1):
            label = 'cycle' + str(index)
            acquired = request(label + '/acquire', {'operation': 'acquire', 'owner': owner,
                               'expected_version': version, 'ttl': ttl})
            token = acquired.get('token')
            if type(token) is not int or token <= 0 or acquired.get('expected_version') != version:
                raise ValueError('invalid_acquire_receipt')
            action = 'c3-mode-' + uuid.uuid4().hex
            submitted = request(label + '/submit', {'operation': 'submit', 'owner': owner,
                                'token': token, 'expected_version': version, 'action_id': action,
                                'command': {'operation': 'mode', 'mode': mode}})
            new_version = version_of(submitted)
            if (submitted.get('effective_policy_verified') is not True or
                    submitted.get('action_id') != action or new_version != version + 1):
                raise ValueError('submit_not_verified')
            effective = checkpoint(adapter_sock)
            record(label + '/checkpoint', effective)
            if (effective.get('mode') != mode or version_of(effective) != new_version or
                    effective.get('last_action', {}).get('action_id') != action):
                raise ValueError('effective_mode_or_action_not_verified')
            # submit advances the version; commit must use that returned version.
            if mode != baseline['mode']:
                # A lost commit reply may still have persisted MANUAL.
                report['restoration_required'] = True
            committed = request(label + '/commit', {'operation': 'commit', 'owner': owner,
                                'token': token, 'expected_version': new_version})
            if committed.get('status') != 'committed' or version_of(committed) != new_version:
                raise ValueError('commit_not_verified')
            version = new_version
            state = request(label + '/status', {'operation': 'status'})
            if state.get('phase') != 'idle' or version_of(state) != version:
                raise ValueError('committed_state_not_verified')
        final = checkpoint(adapter_sock)
        record('final/checkpoint', final)
        if (final.get('mode') != baseline['mode'] or version != report['initial_version'] + 2 or
                version_of(final) != version or final.get('policy_sha256') != baseline.get('policy_sha256') or
                state.get('admission_available') is not True):
            raise ValueError('baseline_not_restored')
        report.update(c3_accepted=True, authority_cycle_executed=True,
                      restoration_required=False, final_version=version, final_phase=state['phase'])
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        persist(path, report)
        print(json.dumps({key: value for key, value in report.items() if key != 'steps'}, indent=2))
        print('Evidence: ' + str(path))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=Path('.work/c3-closure') /
                        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    parser.add_argument('--authority-sock', default=AUTHORITY_SOCK)
    parser.add_argument('--adapter-sock', default=ADAPTER_SOCK)
    parser.add_argument('--owner', default='c3-closure-maestro')
    parser.add_argument('--ttl', type=float, default=30)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not 5 <= args.ttl <= 60:
        parser.error('ttl must be between 5 and 60 seconds')
    result = run(**vars(args))
    return 0 if result.get('c3_accepted') or (args.dry_run and result.get('preflight_passed')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
