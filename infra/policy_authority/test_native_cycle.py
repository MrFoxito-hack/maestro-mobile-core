"""Root/Core integration proof for the native actuator, before C3 admission.

Run under systemd with this same script's recover mode as ExecStopPost.
The supervisor must be stopped first; its durable lock is held. The deployed
adapter stays active: all native operations use the actual v1 datagram socket.
This is explicitly NOT an Authority.acquire/submit/commit test.
"""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
import signal
from pathlib import Path
import time
import uuid

from server import Native
from policy_authority import Authority, digest, restoration_policy, policy_projection


def persist(path, body):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as f:
        json.dump(body, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def reserve(authority, version, *, recovering=False):
    with authority._db() as db:
        row = db.execute('SELECT * FROM authority').fetchone()
        allowed = {'idle', 'system_preparing', 'native_validation'}
        if recovering and row['owner'] == 'c3-native-proof':
            allowed.add('blocked')
        if row['phase'] not in allowed or row['owner'] not in {None, 'c3-native-proof'}:
            raise RuntimeError('existing_owner_must_not_be_displaced')
        token = row['token'] + 1
        expires = time.monotonic() + 30
        db.execute("UPDATE authority SET token=?,version=?,owner='c3-native-proof',phase='native_validation',boot=?,expires=? WHERE id=1",
                   (token, version, authority.boot, expires))
    return token, expires


def main(args):
    os.umask(0o077)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    report = json.loads(args.report.read_text()) if args.report.exists() else {'authority_cycle': False, 'restored': False}
    if args.mode == 'recover' and (report.get('restored') or not report.get('reserved')):
        return
    with ExitStack() as stack:
        for path in ('/var/lib/maestro-policy-authority/supervisor.lock',):
            lock = stack.enter_context(open(path, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        coordinator = Native('/run/maestro-policy-native/control.sock')
        authority = Authority('/var/lib/maestro-policy-authority/authority.sqlite3', coordinator, boot=boot)
        try:
            if args.mode == 'run':
                if args.report.exists():
                    raise RuntimeError('integration_report_already_exists')
                report['baseline'] = coordinator.checkpoint()
                targets = [next(s for s in report['baseline']['sessions'] if s['supi'] == supi)
                           for supi in args.supis.split(',')]
                token, expires = reserve(authority, report['baseline']['version'])
                report['reserved'] = True
                persist(args.report, report)
                coordinator.fence(token=token, boot=boot, expires=expires)
                version = report['baseline']['version']
                report['actions'] = []
                for row in targets:
                    old_rate = int(row['policy']['pcf']['mbr_dl'])
                    target = max(1000, (old_rate // 2000) * 1000)
                    if target == old_rate:
                        raise RuntimeError('baseline_rate_cannot_be_reduced')
                    command = {'operation': 'qos', 'supi': row['supi'], 'dnn': row['dnn'], 'pdu_id': row['pdu_id'],
                               'five_qi': row['policy']['pcf']['five_qi'], 'mbr_dl_mbps': target / 1e6}
                    receipt = coordinator.apply({'owner': 'c3-native-proof', 'token': token, 'boot_id': boot,
                        'expected_version': version, 'action_id': 'native-proof-' + uuid.uuid4().hex,
                        'command': command, 'command_sha256': digest(command)})
                    version = receipt['version']
                    report['actions'].append({'command': command, 'receipt': receipt})
                    persist(args.report, report)
                report['mutated'] = coordinator.checkpoint()
                report['mutation_verified'] = True
                persist(args.report, report)
                if args.crash_after_apply:
                    # systemd ExecStopPost must recover without this process,
                    # EMS or an SSH-side finally block.
                    os.kill(os.getpid(), signal.SIGKILL)
        except Exception as error:
            report['error'] = type(error).__name__ + ':' + str(error)
            persist(args.report, report)
            raise
        finally:
            if report.get('reserved'):
                current = coordinator.checkpoint()
                token, expires = reserve(authority, current['version'], recovering=True)
                coordinator.fence(token=token, boot=boot, expires=expires)
                # A lost native finish response may be reconciled by fencing.
                current = coordinator.checkpoint()
                report['quiescence'] = coordinator.quiesce(token=token)
                policy, expected = restoration_policy(report['baseline'], current)
                coordinator.restore(token=token, expected_version=current['version'], policy=policy)
                report['after'] = coordinator.checkpoint()
                if policy_projection(report['after']) != expected:
                    raise RuntimeError('native_integration_restoration_not_verified')
                if report['after'].get('traffic_verified') is not True:
                    raise RuntimeError('native_integration_traffic_not_verified')
                report['restored'] = True
                with authority._db() as db:
                    if db.execute('SELECT token FROM authority').fetchone()['token'] != token:
                        raise RuntimeError('native_proof_fenced')
                    db.execute("UPDATE authority SET version=?,phase='idle',owner=NULL,expires=0,baseline=NULL,error=NULL WHERE id=1",
                               (report['after']['version'],))
                persist(args.report, report)
    print(json.dumps({key: report.get(key) for key in ('authority_cycle', 'mutation_verified', 'restored', 'error')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run', 'recover'))
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--crash-after-apply', action='store_true')
    parser.add_argument('--supis', default='imsi-999700000000001,imsi-999700000000002,imsi-999700000000005')
    main(parser.parse_args())
