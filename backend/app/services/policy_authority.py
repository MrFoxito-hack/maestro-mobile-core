"""Durable authority engine. Run on the actuator VM, never in an EMS worker.

The Native interface MUST fence at the actual NF write boundary. A database
lease alone is deliberately insufficient for admission. No CHF state is ever
included in restoration. This module has only standard-library dependencies.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time


NFS = frozenset({'pcf', 'smf', 'smf2', 'smf3', 'upf', 'upf2', 'upf3'})
CAPABILITIES = frozenset({'remote_fencing', 'all_writers_fenced', 'effective_rules',
                          'policy_only_restore', 'owned_traffic_cleanup', 'xdp_gate_close'})


class AuthorityError(RuntimeError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value):
    return type(value) is int and value >= 0


def validate_checkpoint(raw, *, now, boot):
    """Validate fresh, complete N7/N4 observation, including rule references.

    measured_at is the aggregator's monotonic clock on the authority VM. Each
    remote NF reports its own boot/generation; clocks across VMs aren't compared.
    Policy contains configuration ONLY, never URR counters or CHF balances.
    """
    if not isinstance(raw, dict) or raw.get('schema_version') != 2 or raw.get('scope') != 'effective_policy':
        raise AuthorityError('effective_checkpoint_unavailable', 503)
    measured = raw.get('measured_at')
    if (raw.get('clock_boot_id') != boot or type(measured) not in (int, float)
            or not math.isfinite(measured) or not 0 <= now - measured <= 2):
        raise AuthorityError('checkpoint_stale_or_wrong_boot', 503)
    if (raw.get('complete') is not True or raw.get('pending_n7') != [] or raw.get('pending_n4') != []
            or not integer(raw.get('version')) or not integer(raw.get('fencing_token'))):
        raise AuthorityError('checkpoint_incomplete_or_inflight', 503)
    nfs = raw.get('nfs')
    if not isinstance(nfs, dict) or set(nfs) != NFS or any(
            not isinstance(n, dict) or not n.get('boot_id') or not integer(n.get('generation'))
            or n.get('writer_fenced') is not True for n in nfs.values()):
        raise AuthorityError('native_writers_not_observed', 503)
    sessions = raw.get('sessions')
    if not isinstance(sessions, list) or not sessions or raw.get('mode') not in {'MANUAL', 'AUTONOMOUS'}:
        raise AuthorityError('effective_sessions_unavailable', 503)
    keys = set()
    for s in sessions:
        try:
            key = (s['supi'], s['pdu_id'], s['smf'], s['upf'], s['smf_seid'], s['upf_seid'])
            if (key in keys or not s['supi'].startswith('imsi-') or not s['dnn']
                    or s['smf'] not in {'smf', 'smf2', 'smf3'} or s['upf'] not in {'upf', 'upf2', 'upf3'}
                    or not s['smf_seid'] or not s['upf_seid'] or not s['generation']
                    or s['n7_confirmed'] is not True or s['n4_confirmed'] is not True):
                raise ValueError()
            keys.add(key)
            rules = s['rules']
            # All four collections must be explicitly observed, even when empty.
            ids = {}
            for kind in ('pdr', 'far', 'qer', 'urr'):
                entries = rules[kind]
                if not isinstance(entries, list):
                    raise ValueError()
                ids[kind] = {r['id'] for r in entries if r['active'] is True and integer(r['id'])}
                if len(ids[kind]) != len(entries):
                    raise ValueError()
                if kind == 'urr' and any(set(r) - {
                        'id', 'active', 'measurement_method', 'reporting_triggers',
                        'volume_threshold', 'volume_quota', 'time_threshold', 'time_quota',
                        'measurement_period', 'linked_urr_ids', 'measurement_information',
                        'event_threshold', 'event_quota', 'quota_holding_time',
                        'quota_validity_time', 'dropped_dl_traffic_threshold'} for r in entries):
                    raise ValueError()  # Never restore accumulated usage/report sequence.
            if not ids['pdr'] or not ids['far']:
                raise ValueError()
            for pdr in rules['pdr']:
                if (pdr['far_id'] not in ids['far'] or not set(pdr['qer_ids']) <= ids['qer']
                        or not set(pdr['urr_ids']) <= ids['urr']):
                    raise ValueError()
            # A precomputed flag/hash is insufficient; configuration must exist.
            if not isinstance(s['policy'], dict) or not s['policy']:
                raise ValueError()
            if set(s['policy']) != {'pcf', 'smf', 'upf'} or s['policy']['upf'] != rules:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AuthorityError('invalid_effective_session', 503) from None
    policy = policy_projection(raw)
    if raw.get('policy_sha256') != digest(policy):
        raise AuthorityError('checkpoint_digest_mismatch', 503)
    return raw


def policy_projection(snapshot):
    """Restorable configuration; usage counters and financial state excluded."""
    sessions = sorted([{
        key: s[key] for key in ('supi', 'pdu_id', 'dnn', 'smf', 'upf', 'smf_seid',
                                'upf_seid', 'generation', 'policy')
    } for s in snapshot['sessions']], key=lambda s: (s['supi'], s['pdu_id']))
    return {'mode': snapshot['mode'], 'sessions': sessions}


def restoration_policy(baseline, current):
    """Do not replay an old CHF quota grant, including URR configuration.

    URR rules are observations in a checkpoint but not writable by this
    supervisor. The native restore operation must retain them in place, with
    their accumulators, report sequence, timers, quota and measurement state.
    """
    desired = deepcopy(policy_projection(baseline))
    expected = deepcopy(desired)
    for old, want, live in zip(desired['sessions'], expected['sessions'], policy_projection(current)['sessions']):
        if ([(r['id'], r['urr_ids']) for r in old['policy']['upf']['pdr']]
                != [(r['id'], r['urr_ids']) for r in live['policy']['upf']['pdr']]):
            raise AuthorityError('urr_binding_changed_reconciliation_required')
        old['policy']['upf'].pop('urr')
        want['policy']['upf']['urr'] = deepcopy(live['policy']['upf']['urr'])
    return desired, expected


class Authority:
    def __init__(self, database, native, *, boot, clock=time.monotonic):
        self.path, self.native, self.boot, self.clock = str(database), native, boot, clock
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS authority (
                  id INTEGER PRIMARY KEY CHECK(id=1), token INTEGER NOT NULL,
                  version INTEGER NOT NULL, owner TEXT, boot TEXT, expires REAL,
                  phase TEXT NOT NULL, baseline TEXT, error TEXT);
                INSERT OR IGNORE INTO authority VALUES(1,0,0,NULL,NULL,0,'idle',NULL,NULL);
                CREATE TABLE IF NOT EXISTS actions (
                  action_id TEXT PRIMARY KEY, owner TEXT NOT NULL, token INTEGER NOT NULL,
                  expected_version INTEGER NOT NULL, request_hash TEXT NOT NULL,
                  state TEXT NOT NULL, result TEXT);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _capabilities(self):
        caps = self.native.capabilities()
        if (caps.get('protocol') != 1 or set(caps.get('nfs', [])) != NFS
                or not CAPABILITIES <= set(caps.get('capabilities', []))):
            raise AuthorityError('native_authority_contract_unavailable', 503)

    def _snapshot(self):
        # A native event can finish between apply's receipt and this read.
        # Retry observations only; never resend a policy mutation or weaken
        # checkpoint validation. Persistent incomplete coverage stays blocked.
        for attempt in range(20):
            try:
                return validate_checkpoint(self.native.checkpoint(), now=self.clock(), boot=self.boot)
            except AuthorityError as error:
                if error.code != 'checkpoint_incomplete_or_inflight' or attempt == 19:
                    raise
                time.sleep(0.05)

    def maintain(self):
        """Issue native lifecycle leases from the same durable token sequence.

        A system lease cannot authorize MML/N7 policy mutations. Reserving its
        token precedes I/O, including a partial seven-NF installation or crash.
        The independent supervisor calls this only while there is no owner.
        """
        if not hasattr(self.native, 'system_status'):
            return {'status': 'unsupported'}
        with self._db() as db:
            row = db.execute('SELECT * FROM authority').fetchone()
            if row['phase'] not in {'idle', 'system_preparing'} or row['owner'] is not None:
                return {'status': 'owner_active'}
        native = self.native.system_status()
        with self._db() as db:
            row = db.execute('SELECT * FROM authority').fetchone()
            if row['phase'] not in {'idle', 'system_preparing'} or row['owner'] is not None:
                return {'status': 'owner_active'}
            if (not integer(native.get('token')) or native['token'] > row['token'] or
                    native.get('version') != row['version']):
                raise AuthorityError('native_system_floor_or_version_conflict', 503)
            if native.get('renew_required') is False and row['phase'] == 'idle':
                return {'status': 'system_active'}
            token = row['token'] + 1
            expires = self.clock() + 45
            db.execute("UPDATE authority SET token=?,phase='system_preparing' WHERE id=1", (token,))
        try:
            self.native.system_fence(token=token, version=row['version'], boot=self.boot, expires=expires)
            with self._db() as db:
                current = db.execute('SELECT * FROM authority').fetchone()
                if current['token'] != token or current['phase'] != 'system_preparing':
                    raise AuthorityError('system_lease_fenced')
                db.execute("UPDATE authority SET phase='idle',error=NULL WHERE id=1")
        except Exception:
            # Keep the durable preparation phase: a new supervisor can issue a
            # newer fence without treating this as an owner policy restoration.
            raise
        return {'status': 'system_active', 'token': token}

    def status(self):
        with self._db() as db:
            r = dict(db.execute('SELECT * FROM authority').fetchone())
        r.pop('baseline')
        r.pop('id')
        r['expired'] = r['boot'] != self.boot or r['expires'] <= self.clock()
        return r

    def acquire(self, owner, expected_version, ttl=30):
        if (not isinstance(owner, str) or not 1 <= len(owner) <= 160 or not integer(expected_version)
                or type(ttl) not in (float, int) or not math.isfinite(ttl) or not 5 <= ttl <= 60):
            raise AuthorityError('invalid_lease', 422)
        self._capabilities()
        with self._db() as db:
            row = db.execute('SELECT * FROM authority').fetchone()
            if row['phase'] != 'idle':
                raise AuthorityError('lease_busy_or_recovery_required')
            if row['version'] != expected_version:
                raise AuthorityError('policy_version_conflict')
            token = row['token'] + 1
            expires = self.clock() + ttl
            db.execute("UPDATE authority SET token=?,owner=?,boot=?,expires=?,phase='preparing',error=NULL WHERE id=1",
                       (token, owner, self.boot, expires))
        try:
            self.native.fence(token=token, boot=self.boot, expires=expires)
            baseline = self._snapshot()
            if baseline['version'] != expected_version or baseline['fencing_token'] != token:
                raise AuthorityError('native_version_or_fence_conflict')
            with self._db() as db:
                self._check(db, owner, token, expected_version, phases={'preparing'})
                db.execute("UPDATE authority SET baseline=?,phase='leased' WHERE id=1", (canonical(baseline),))
        except Exception:
            self._blocked('lease_preparation_uncertain', token)
            raise
        return {'owner': owner, 'token': token, 'expected_version': expected_version,
                'boot_id': self.boot, 'expires_at': expires}

    def _check(self, db, owner, token, version, phases=frozenset({'leased'})):
        r = db.execute('SELECT * FROM authority').fetchone()
        if (not integer(token) or not integer(version) or r['token'] != token or r['owner'] != owner
                or r['boot'] != self.boot or r['expires'] <= self.clock()):
            raise AuthorityError('lease_expired_or_fenced')
        if r['version'] != version:
            raise AuthorityError('policy_version_conflict')
        if r['phase'] not in phases:
            raise AuthorityError('mutation_inflight_or_recovery_required')
        return r

    def submit(self, *, owner, token, expected_version, action_id, command):
        if not isinstance(action_id, str) or not 8 <= len(action_id) <= 100:
            raise AuthorityError('invalid_action_id', 422)
        if not isinstance(command, dict) or command.get('operation') not in {'qos', 'mode'}:
            raise AuthorityError('mutation_not_supported', 422)
        fingerprint = digest(command)
        with self._db() as db:
            prior = db.execute('SELECT * FROM actions WHERE action_id=?', (action_id,)).fetchone()
            if prior:
                if (prior['owner'], prior['token'], prior['expected_version'], prior['request_hash']) != (
                        owner, token, expected_version, fingerprint):
                    raise AuthorityError('idempotency_key_conflict')
                if prior['state'] == 'applied':
                    # Historical receipt only: a replay NEVER reapplies policy.
                    return {**json.loads(prior['result']), 'replay': True}
                raise AuthorityError('action_outcome_unknown_recovery_required')
            self._check(db, owner, token, expected_version)
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,NULL)',
                       (action_id, owner, token, expected_version, fingerprint, 'intent'))
            db.execute("UPDATE authority SET phase='mutating' WHERE id=1")
        envelope = dict(owner=owner, token=token, boot_id=self.boot, expected_version=expected_version,
                        action_id=action_id, command_sha256=fingerprint, command=command)
        try:
            self.native.apply(envelope)
            observed = self._snapshot()
            if (observed['fencing_token'] != token or observed['version'] != expected_version + 1
                    or observed.get('last_action') != {'action_id': action_id, 'command_sha256': fingerprint}):
                raise AuthorityError('effective_action_not_confirmed', 503)
            receipt = {'status': 'success', 'action_id': action_id, 'token': token,
                       'version': observed['version'], 'effective_policy_verified': True,
                       'policy_sha256': observed['policy_sha256'], 'replay': False}
            with self._db() as db:
                self._check(db, owner, token, expected_version, phases={'mutating'})
                db.execute("UPDATE actions SET state='applied',result=? WHERE action_id=?", (canonical(receipt), action_id))
                db.execute("UPDATE authority SET phase='leased',version=? WHERE id=1", (observed['version'],))
            return receipt
        except Exception:
            self._blocked('mutation_outcome_requires_reconciliation', token)
            raise

    def _blocked(self, code, token):
        with self._db() as db:
            db.execute("UPDATE authority SET phase='blocked',error=? WHERE id=1 AND token=?", (code, token))

    def recover(self):
        """Called by a supervisor independent of EMS. No blind command retries.

        Install a newer native fence before reconciling an uncertain intent.
        Restore uses identities from the baseline, never UE IPs or balances.
        """
        with self._db() as db:
            r = db.execute('SELECT * FROM authority').fetchone()
            if r['phase'] in {'idle', 'system_preparing'}:
                return {'status': 'idle'}
            if r['boot'] == self.boot and r['expires'] > self.clock():
                return {'status': 'lease_active'}
            if not r['baseline']:
                raise AuthorityError('baseline_unavailable_manual_reconciliation_required', 503)
            baseline = json.loads(r['baseline'])
            token = r['token'] + 1
            # A failed attempt keeps the same baseline but burns its fence token.
            db.execute("UPDATE authority SET token=?,phase='recovering' WHERE id=1", (token,))
        try:
            self.native.fence(token=token, boot=self.boot, expires=self.clock() + 30)
            # Live coverage requires a lease. Renew the recovery fence before
            # checking it, while policy restoration still requires every cap.
            self._capabilities()
            before = self._snapshot()
            # Restore must not attach an old policy to a newly-created session.
            identities = lambda s: [(p['supi'], p['pdu_id'], p['smf_seid'], p['upf_seid'], p['generation'])
                                    for p in policy_projection(s)['sessions']]
            if before['nfs'] != baseline['nfs'] or identities(before) != identities(baseline):
                raise AuthorityError('native_epoch_or_session_changed')
            desired, expected = restoration_policy(baseline, before)
            self.native.quiesce(token=token)  # owned generators + XDP gate only
            self.native.restore(token=token, expected_version=before['version'],
                                policy=desired)
            after = self._snapshot()
            if (after['fencing_token'] != token or after['version'] != before['version'] + 1
                    or policy_projection(after) != expected
                    or after.get('traffic_verified') is not True):
                raise AuthorityError('restoration_not_effective', 503)
            with self._db() as db:
                current = db.execute('SELECT token FROM authority').fetchone()['token']
                if current != token:
                    raise AuthorityError('recovery_fenced')
                db.execute("UPDATE actions SET state='reconciled' WHERE state='intent'")
                db.execute("UPDATE authority SET phase='idle',version=?,owner=NULL,expires=0,error=NULL,baseline=NULL WHERE id=1",
                           (after['version'],))
            return {'status': 'recovered', 'token': token, 'version': after['version'],
                    'effective_policy_verified': True, 'traffic_verified': True}
        except Exception:
            self._blocked('independent_recovery_not_verified', token)
            raise

    def commit(self, *, owner, token, expected_version):
        """Explicitly authorize persistence, only after a fresh effective read."""
        with self._db() as db:
            self._check(db, owner, token, expected_version)
            observed = self._snapshot()
            if observed['version'] != expected_version or observed['fencing_token'] != token:
                raise AuthorityError('commit_effective_version_conflict')
            db.execute("UPDATE authority SET token=?,phase='committing' WHERE id=1", (token + 1,))
        try:
            # Persist the new fence before native I/O, including a lost reply.
            self.native.fence(token=token + 1, boot=self.boot, expires=self.clock())
            with self._db() as db:
                if db.execute('SELECT token FROM authority').fetchone()['token'] != token + 1:
                    raise AuthorityError('commit_fenced')
                db.execute("UPDATE authority SET phase='idle',owner=NULL,expires=0,baseline=NULL WHERE id=1")
        except Exception:
            self._blocked('commit_outcome_requires_reconciliation', token + 1)
            raise
        return {'status': 'committed', 'version': expected_version}
