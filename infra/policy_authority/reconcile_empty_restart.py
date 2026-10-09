"""Reconcile ONLY an interrupted native integration proof after an empty restart.

Run on Core with adapter and supervisor stopped. This is a control-journal
repair, never policy restoration or C3 acceptance. No CHF database is opened.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3
import time

NFS = {'pcf', 'smf', 'smf2', 'smf3', 'upf', 'upf2', 'upf3'}


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def plan(proof, authority, adapter, observed, *, boot, now):
    baseline = proof.get('baseline', {})
    if (proof.get('authority_cycle') is not False or proof.get('reserved') is not True or
            proof.get('restored') is not False or baseline.get('clock_boot_id') in {None, boot}):
        raise ValueError('not_an_interrupted_proof_from_previous_boot')
    if (authority['owner'] != 'c3-native-proof' or authority['phase'] not in {'blocked', 'native_validation'} or
            (authority['boot'] == boot and authority['expires'] > now) or
            (adapter['boot'] == boot and adapter['expires'] > now)):
        raise ValueError('existing_owner_or_active_lease')
    if set(observed) != NFS or set(baseline.get('nfs', {})) != NFS:
        raise ValueError('seven_native_epochs_required')
    epochs, versions, tokens = {}, set(), set()
    for name, nf in observed.items():
        guard = nf['fencing']
        if (nf.get('sessions') != [] or nf.get('pending_native') != 0 or nf.get('deferred_native', 0) != 0 or
                guard.get('enabled') is not True or guard.get('failed') is not False or
                guard.get('pending_n7') or guard.get('pending_n4') or
                (guard.get('system') and guard.get('leased'))):
            raise ValueError('fleet_not_empty_and_closed')
        epochs[name] = {key: nf[key] for key in ('boot_id', 'generation')}
        old = baseline['nfs'][name]
        if (nf['boot_id'], int(nf['generation'])) == (old['boot_id'], int(old['generation'])):
            raise ValueError('native_epoch_did_not_restart')
        versions.add(int(guard['version']))
        tokens.add(int(guard['token']))
    if len(versions) != 1 or len(tokens) != 1:
        raise ValueError('native_floors_diverged')
    version, token = versions.pop(), tokens.pop()
    if (version < baseline['version'] or adapter['version'] not in {version, version - 1} or
            token != adapter['token'] or authority['token'] != token):
        raise ValueError('unexpected_control_journal_floor')
    return {'reason': 'integration_proof_abandoned_after_empty_fleet_restart',
            'restored': False, 'authority_cycle': False, 'boot': boot,
            'token': token, 'version': version, 'epochs': epochs,
            'proof_sha256': sha(proof), 'authority_sha256': sha(authority), 'adapter_sha256': sha(adapter)}


def run(args):
    import fcntl
    from native_adapter import Coordinator
    from native_transport import Transport
    from policy_authority import Authority
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    with ExitStack() as stack:
        for path in ('/var/lib/maestro-policy-authority/supervisor.lock',
                     '/var/lib/maestro-policy-native/adapter.lock'):
            lock = stack.enter_context(open(path, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        coordinator = Coordinator(Transport(json.loads(args.transport.read_text())),
            '/var/lib/maestro-policy-native/adapter.sqlite3', boot=boot)
        authority = Authority('/var/lib/maestro-policy-authority/authority.sqlite3', coordinator, boot=boot)
        with authority._db() as db:
            authority_before = dict(db.execute('SELECT * FROM authority').fetchone())
        adapter_before = coordinator.load()
        proof = json.loads(args.proof.read_text())
        record = plan(proof, authority_before, adapter_before, coordinator.observe(), boot=boot, now=time.monotonic())
        record['plan_sha256'] = sha(record)
        (args.evidence / 'plan.json').write_text(json.dumps(record, indent=2))
        for label, source in [('authority', '/var/lib/maestro-policy-authority/authority.sqlite3'),
                              ('adapter', '/var/lib/maestro-policy-native/adapter.sqlite3')]:
            with sqlite3.connect(source) as original, sqlite3.connect(args.evidence / (label + '.sqlite3')) as backup:
                original.backup(backup)
                if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('control_backup_integrity_failed')
        # Revalidate the entire plan immediately before the first journal write.
        with authority._db() as db:
            current = dict(db.execute('SELECT * FROM authority').fetchone())
        fresh = plan(proof, current, coordinator.load(), coordinator.observe(), boot=boot, now=time.monotonic())
        if sha(fresh) != record['plan_sha256']:
            raise ValueError('restart_reconciliation_plan_changed')
        updated = dict(adapter_before)
        for key in ('pending_action', 'pending_finish', 'recovery_intent', 'traffic_proof', 'quiesced_token', 'last_action'):
            updated.pop(key, None)
        updated.update(version=record['version'], boot=boot, expires=0, role='closed',
                       last_empty_restart_reconciliation=record)
        coordinator.save(updated)
        with authority._db() as db:
            if dict(db.execute('SELECT * FROM authority').fetchone()) != authority_before:
                raise ValueError('authority_changed_during_reconciliation')
            db.execute("UPDATE authority SET version=?,boot=?,phase='idle',owner=NULL,expires=0,baseline=NULL,error=NULL WHERE id=1",
                       (record['version'], boot))
        (args.evidence / 'result.json').write_text(json.dumps(record | {'journals_reconciled': True}, indent=2))
        return {'journals_reconciled': True, 'restored': False, 'token': record['token'],
                'version': record['version'], 'plan_sha256': record['plan_sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', required=True)
    parser.add_argument('--transport', type=Path, required=True)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2))
