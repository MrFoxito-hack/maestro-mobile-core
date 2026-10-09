"""Core-owned native protocol endpoint and durable seven-NF coordination.

Capabilities remain unavailable until the effective checkpoint and recovery
contract can be established. Lifecycle grants do not imply C3 acceptance.
"""
import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import time
import uuid

if __package__:
    from .native_transport import Transport, NFS
    from .native_effective import checkpoint as effective_checkpoint, digest, policy_projection
    from .native_effective import effective_sessions, policy_without_charging, intended_policy
    from .native_runtime import owned_traffic_cleanup, validate_traffic_probes
else:
    from native_transport import Transport, NFS
    from native_effective import checkpoint as effective_checkpoint, digest, policy_projection
    from native_effective import effective_sessions, policy_without_charging, intended_policy
    from native_runtime import owned_traffic_cleanup, validate_traffic_probes


class Coordinator:
    def __init__(self, transport, database, *, boot, clock=time.monotonic):
        self.transport, self.database, self.boot, self.clock = transport, str(database), boot, clock
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO state VALUES(1, ?)',
                       (json.dumps({'token': 0, 'version': 0, 'boot': boot, 'expires': 0, 'role': 'none'}),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, isolation_level=None)
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

    def load(self):
        with self.db() as db:
            return json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])

    def save(self, body):
        with self.db() as db:
            db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(body, allow_nan=False),))

    def observe(self):
        samples = self.transport.observe()
        observations = {nf: body for nf, (body, _) in samples.items()}
        for state in observations.values():
            fencing = state.get('fencing', {})
            if fencing.get('enabled') is not True or fencing.get('failed') is not False:
                raise ValueError('native_guard_not_ready')
        return observations

    def commands(self, commands):
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = {nf: pool.submit(self.transport.request, nf, command) for nf, command in commands.items()}
            return {nf: future.result()[0] for nf, future in futures.items()}

    def barrier(self):
        # Stop new AMF admissions first. In-flight SMF setup still needs PCF.
        # Close PCF only after those existing SMF transactions have drained.
        for group in (('smf', 'smf2', 'smf3'), ('pcf',)):
            observations = self.observe()
            commands = {}
            for nf in group:
                guard = observations[nf]['fencing']
                if int(guard['token']) == 0:
                    commands[nf] = 'bootstrap-close-v1\n'
                elif guard['system'] and guard['leased']:
                    commands[nf] = f"system-close-v1 {guard['token']}\n"
            self.commands(commands)
            deadline = self.clock() + 2
            while True:
                observations = self.observe()
                if all(observations[nf]['pending_native'] == 0 for nf in group):
                    break
                if self.clock() >= deadline:
                    raise ValueError('native_lifecycle_drain_timeout')
                time.sleep(0.025)
        return observations

    def system_status(self):
        observed, state = self.observe(), self.load()
        tokens = {int(nf['fencing']['token']) for nf in observed.values()}
        versions = {int(nf['fencing']['version']) for nf in observed.values()}
        if len(versions) != 1:
            raise ValueError('native_version_divergence')
        ready = (tokens == {state['token']} and state['role'] == 'system' and
                 state['boot'] == self.boot and state['expires'] - self.clock() > 15 and
                 all(nf['fencing']['system'] and nf['fencing']['leased'] for nf in observed.values()) and
                 all(observed[nf]['fencing']['system_admission'] for nf in ('pcf', 'smf', 'smf2', 'smf3')))
        return {'token': max(tokens), 'version': versions.pop(), 'renew_required': not ready}

    def fence(self, *, token, boot, expires):
        state = self.load()
        if (type(token) is not int or token <= state['token'] or boot != self.boot or
                type(expires) not in (int, float) or not math.isfinite(expires) or expires > self.clock() + 60):
            raise ValueError('invalid_coordinator_fence')
        self.barrier()
        # Persist the intent before the first remote write. A lost response
        # burns the token; a subsequent attempt must use a strictly newer one.
        state.update(token=token, boot=boot, expires=expires, role='fencing')
        self.save(state)
        result = self.transport.fence(token=token, boot=boot, expires=expires)
        state['role'] = 'owner' if expires > self.clock() else 'closed'
        self.save(state)
        if state['role'] == 'owner':
            self.resume_finalization(token)
        return result

    def resume_finalization(self, token):
        """Finish only a journaled, already effective policy under a new fence.

        Never replays MML or charging. Each native version may be exactly v or
        v+1; only the remaining v instances receive finish. Even an all-finished
        but lost response must match the durable intent and every NF epoch.
        """
        state = self.owner(token)
        intent = state.get('pending_finish')
        pending = state.get('pending_action')
        derived = not intent
        if not intent and pending and pending.get('finalize', not state.get('recovery_intent')):
            envelope, baseline = pending['envelope'], pending['baseline']
            if (envelope['command_sha256'] != digest(envelope['command']) or
                    envelope['expected_version'] != baseline['version']):
                raise ValueError('native_commit_journal_invalid')
            intent = {'version': baseline['version'], 'nfs': baseline['nfs'],
                      'policy': intended_policy(baseline, envelope['command']),
                      'receipt': {k: envelope[k] for k in ('action_id', 'command_sha256')},
                      'kind': 'apply'}
        if not intent:
            return
        version = intent['version']
        if type(version) is not int or state['version'] != version:
            raise ValueError('native_commit_journal_version_conflict')

        def verify(observed, *, allow_unapplied=False):
            epochs = {name: {key: nf[key] for key in ('boot_id', 'generation', 'writer_fenced')}
                      for name, nf in observed.items()}
            for epoch in epochs.values():
                epoch['generation'] = int(epoch['generation'])
            if epochs != intent['nfs']:
                raise ValueError('native_commit_epoch_changed')
            for nf in observed.values():
                guard = nf['fencing']
                if (int(guard['token']) != token or int(guard['version']) not in {version, version + 1} or
                        nf['pending_native'] or guard['pending_n7'] or guard['pending_n4']):
                    raise ValueError('native_commit_floor_or_inflight_conflict')
            current = {'mode': observed['pcf']['mode'], 'sessions': effective_sessions(observed)}
            projection = policy_without_charging(current)
            if (allow_unapplied and all(int(nf['fencing']['version']) == version for nf in observed.values()) and
                    projection == policy_without_charging(pending['baseline'])):
                return False
            if projection != intent['policy']:
                raise ValueError('native_commit_policy_not_effective')
            return True

        observed = self.observe()
        if not verify(observed, allow_unapplied=derived):
            state.pop('pending_action')
            state['last_intent_reconciliation'] = {'token': token, 'version': version,
                                                   'result': 'unapplied_intent_aborted'}
            self.save(state)
            return
        # Persist derived legacy intent too, before completing a partial fanout.
        state['pending_finish'] = intent
        self.save(state)
        remaining = [nf for nf, body in observed.items() if int(body['fencing']['version']) == version]
        cookie = secrets.token_hex(32)
        self.commands({nf: f'prepare-v1 {token} {version} 0 {cookie}\n' for nf in remaining
                       if not observed[nf]['fencing'].get('prepared')})
        self.commands({nf: f'finish-v1 {token} {version}\n' for nf in remaining})
        observed = self.observe()
        verify(observed)
        if any(int(nf['fencing']['version']) != version + 1 for nf in observed.values()):
            raise ValueError('native_commit_fanout_incomplete')
        self.owner(token, version)
        state.update(version=version + 1)
        state.pop('pending_finish')
        state.pop('pending_action', None)
        if intent['kind'] == 'apply':
            state['last_action'] = intent['receipt']
        else:
            state.pop('recovery_intent', None)
        self.save(state)

    def system_fence(self, *, token, version, boot, expires):
        status = self.system_status()
        if type(version) is not int or version != status['version']:
            raise ValueError('system_version_conflict')
        self.fence(token=token, boot=boot, expires=expires)
        cookie = secrets.token_hex(32)
        self.commands({nf: f'system-prepare-v1 {token} {version} {cookie}\n' for nf in NFS})
        # All UPFs have their grant before PDU admission is opened anywhere.
        self.commands({nf: f'system-open-v1 {token}\n' for nf in ('pcf', 'smf', 'smf2', 'smf3')})
        state = self.load()
        state.update(role='system', version=version)
        self.save(state)
        return {'token': token, 'version': version}

    def checkpoint(self):
        metadata = self.load()
        value = effective_checkpoint(self.observe(), boot=self.boot,
                                     measured_at=self.clock(), metadata=metadata)
        proof = metadata.get('traffic_proof', {})
        value['traffic_verified'] = (proof.get('token') == value['fencing_token'] and
                                     proof.get('policy_sha256') == value['policy_sha256'] and
                                     0 <= self.clock() - proof.get('measured_at', -10) <= 2)
        return value

    def capabilities(self):
        observed = self.observe()
        capabilities = ['remote_fencing', 'effective_rules', 'policy_only_restore',
                        'owned_traffic_cleanup', 'xdp_gate_close']
        if all(nf['writer_fenced'] is True and nf['policy_complete'] is True for nf in observed.values()):
            capabilities.append('all_writers_fenced')
        return {'protocol': 1, 'nfs': sorted(NFS), 'capabilities': capabilities}

    def owner(self, token, version=None):
        state = self.load()
        if (state['role'] != 'owner' or state['boot'] != self.boot or
                state['token'] != token or state['expires'] <= self.clock()):
            raise ValueError('native_owner_lease_unavailable')
        if version is not None and state['version'] != version:
            raise ValueError('native_owner_version_conflict')
        return state

    @staticmethod
    def mml(command, cookie, token, version):
        envelope = b'M5GFENCE' + b'\x01' + token.to_bytes(8, 'big') + version.to_bytes(8, 'big') + bytes.fromhex(cookie)
        body = {**command, 'authority': envelope.hex()}
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            client.bind('\0maestro-native-action-' + uuid.uuid4().hex)
            client.sendto(json.dumps(body, allow_nan=False).encode(), '/var/lib/open5gs/nwdaf/mml.sock')
            raw, _, flags, _ = client.recvmsg(8192)
        reply = json.loads(raw)
        if flags & socket.MSG_TRUNC or reply.get('status') != 'success':
            raise ValueError('native_pcf_action_not_confirmed')
        return reply

    def apply(self, envelope, *, finalize=True):
        token, version = envelope['token'], envelope['expected_version']
        state = self.owner(token, version)
        command = deepcopy(envelope['command'])
        if (envelope['boot_id'] != self.boot or envelope['command_sha256'] != digest(command) or
                not isinstance(envelope['action_id'], str) or not 8 <= len(envelope['action_id']) <= 100 or
                state.get('pending_action')):
            raise ValueError('native_action_envelope_invalid')
        observations = self.observe()
        before = effective_checkpoint(observations, boot=self.boot, measured_at=self.clock(), metadata=state)
        if before['pending_n7'] or before['pending_n4'] or before['version'] != version or before['fencing_token'] != token:
            raise ValueError('native_action_not_quiescent')
        targets = {nf: 0 for nf in NFS}
        if command.get('operation') == 'qos':
            matches = [s for s in before['sessions'] if s['supi'] == command.get('supi') and
                       ('pdu_id' not in command or command['pdu_id'] == s['pdu_id']) and
                       ('dnn' not in command or command['dnn'] == s['dnn'])]
            if len(matches) != 1:
                raise ValueError('native_qos_session_ambiguous')
            selected = matches[0]
            policy = selected['policy']['pcf']
            if command.get('five_qi', policy['five_qi']) != policy['five_qi']:
                raise ValueError('default_5qi_change_requires_ran_procedure')
            command.update(dnn=selected['dnn'], pdu_id=selected['pdu_id'], five_qi=policy['five_qi'])
            for direction in ('ul', 'dl'):
                key = 'mbr_' + direction + '_mbps'
                command.setdefault(key, int(policy['mbr_' + direction]) / 1000000)
                rate = command[key]
                if (type(rate) not in (int, float) or not math.isfinite(rate) or
                        not .001 <= rate <= 100000 or abs(rate * 1000 - round(rate * 1000)) > 1e-6):
                    raise ValueError('native_mbr_requires_integer_kbps')
            targets['pcf'] = next(s['context_id'] for s in observations['pcf']['sessions']
                                  if s['supi'] == selected['supi'] and s['pdu_id'] == selected['pdu_id'])
            targets[selected['smf']] = int(selected['smf_seid'])
            targets[selected['upf']] = int(selected['upf_seid'])
        elif command.get('operation') == 'mode' and set(command) == {'operation', 'mode'}:
            if command['mode'] not in {'MANUAL', 'AUTONOMOUS'}:
                raise ValueError('invalid_native_mode')
        else:
            raise ValueError('native_operation_unsupported')
        cookie = secrets.token_hex(32)
        # Cookies never enter the durable journal. Recovery uses a newer fence.
        state['pending_action'] = {'envelope': deepcopy(envelope), 'baseline': before, 'finalize': finalize}
        self.save(state)
        self.commands({nf: f'prepare-v1 {token} {version} {target} {cookie}\n' for nf, target in targets.items()})
        self.mml(command, cookie, token, version)
        after = self.checkpoint()
        if command['operation'] == 'mode':
            if after['mode'] != command['mode'] or before['sessions'] != after['sessions']:
                raise ValueError('native_mode_not_effective')
        else:
            current = next(s for s in after['sessions'] if s['generation'] == selected['generation'])
            for direction in ('ul', 'dl'):
                if int(current['policy']['pcf']['mbr_' + direction]) != round(command['mbr_' + direction + '_mbps'] * 1e6):
                    raise ValueError('native_qos_not_effective')
            for previous, current in zip(before['sessions'], after['sessions']):
                if (previous['generation'] != current['generation'] or
                        previous['rules']['pdr'] != current['rules']['pdr'] or
                        previous['rules']['far'] != current['rules']['far'] or
                        [r['id'] for r in previous['rules']['urr']] != [r['id'] for r in current['rules']['urr']]):
                    raise ValueError('native_non_qer_policy_changed')
        if finalize:
            state['pending_finish'] = {'version': version, 'nfs': after['nfs'],
                'policy': policy_without_charging(after), 'kind': 'apply',
                'receipt': {k: envelope[k] for k in ('action_id', 'command_sha256')}}
            self.save(state)
            self.resume_finalization(token)
            state = self.load()
        else:
            self.commands({nf: f'cancel-v1 {token}\n' for nf in NFS})
            state.pop('pending_action')
            self.save(state)
        return {'version': state['version'], 'last_action': state.get('last_action')}

    def restore(self, *, token, expected_version, policy):
        state = self.owner(token, expected_version)
        before = self.checkpoint()
        actual = policy_projection(before)
        desired = deepcopy(policy)
        if (set(desired) != {'mode', 'sessions'} or desired['mode'] not in {'MANUAL', 'AUTONOMOUS'} or
                len(desired['sessions']) != len(actual['sessions'])):
            raise ValueError('native_restore_shape_invalid')
        commands = []
        for old, live in zip(desired['sessions'], actual['sessions']):
            if {k: v for k, v in old.items() if k != 'policy'} != {k: v for k, v in live.items() if k != 'policy'}:
                raise ValueError('native_restore_session_generation_changed')
            if 'urr' in old['policy']['upf'] or 'urr' in old['policy']['smf'].get('rules', {}):
                raise ValueError('native_restore_must_not_replay_charging_rules')
            # Preflight every session before the first mutation. Only the MBR
            # of an existing QER and the PCF mode belong to this adapter.
            comparison = deepcopy(live)
            comparison['policy']['upf'].pop('urr')
            for layer in ('pcf', 'smf'):
                for key in ('mbr_ul', 'mbr_dl'):
                    comparison['policy'][layer][key] = old['policy'][layer][key]
            for layer in ('smf', 'upf'):
                source = old['policy'][layer]['rules'] if layer == 'smf' else old['policy'][layer]
                target = comparison['policy'][layer]['rules'] if layer == 'smf' else comparison['policy'][layer]
                if len(source['qer']) != 1 or len(target['qer']) != 1:
                    raise ValueError('native_restore_qer_profile_unsupported')
                for key in ('mbr_ul', 'mbr_dl'):
                    target['qer'][0][key] = source['qer'][0][key]
                    if source['qer'][0][key] != old['policy']['pcf'][key]:
                        raise ValueError('native_restore_ambr_inconsistent')
            if comparison != old:
                raise ValueError('native_restore_non_ambr_change_unsupported')
            if old['policy']['pcf'] != live['policy']['pcf']:
                commands.append({'operation': 'qos', 'supi': old['supi'], 'pdu_id': old['pdu_id'], 'dnn': old['dnn'],
                                 'five_qi': old['policy']['pcf']['five_qi'],
                                 'mbr_ul_mbps': int(old['policy']['pcf']['mbr_ul']) / 1e6,
                                 'mbr_dl_mbps': int(old['policy']['pcf']['mbr_dl']) / 1e6})
        if actual['mode'] != desired['mode']:
            commands.append({'operation': 'mode', 'mode': desired['mode']})
        state['recovery_intent'] = desired
        state.pop('pending_action', None)
        self.save(state)
        for command in commands:
            self.apply({'owner': 'independent-recovery', 'token': token, 'boot_id': self.boot,
                        'expected_version': expected_version, 'action_id': 'restore-' + uuid.uuid4().hex,
                        'command_sha256': digest(command), 'command': command}, finalize=False)
        confirmed = self.checkpoint()
        final = policy_projection(confirmed)
        for row in final['sessions']:
            row['policy']['upf'].pop('urr')
        if final != desired:
            raise ValueError('native_restore_projection_not_effective')
        state = self.load()
        state['pending_finish'] = {'version': expected_version, 'nfs': confirmed['nfs'],
            'policy': desired, 'kind': 'restore'}
        self.save(state)
        self.resume_finalization(token)
        state = self.load()
        if state.get('quiesced_token') == token:
            # This path runs on Core and the mTLS UPF relays; it has no EMS or
            # SSH dependency. Ordinary diagnostic restores do not claim it.
            probe_baseline = self.checkpoint()
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {nf: pool.submit(self.transport.request, nf, operation='traffic_probe')
                           for nf in ('upf', 'upf2', 'upf3')}
                probes = {nf: future.result()[0] for nf, future in futures.items()}
            verified = self.checkpoint()
            if (verified['policy_sha256'] != probe_baseline['policy_sha256'] or
                    verified['nfs'] != probe_baseline['nfs']):
                raise ValueError('native_restored_traffic_policy_changed')
            validate_traffic_probes(verified, probes)
            self.owner(token, expected_version + 1)
            state['traffic_proof'] = {'token': token, 'policy_sha256': verified['policy_sha256'],
                                      'measured_at': self.clock()}
            self.save(state)
        return {'version': expected_version + 1, 'policy_restored': True}

    def quiesce(self, *, token):
        self.owner(token)
        result = {'core': owned_traffic_cleanup()}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {nf: pool.submit(self.transport.request, nf, operation='quiesce') for nf in ('upf', 'upf2', 'upf3')}
            result.update({nf: future.result()[0] for nf, future in futures.items()})
        if (not all(row.get('owned_traffic_quiescent') is True for row in result.values()) or
                result['upf3'].get('xdp_gate_closed') is not True):
            raise ValueError('native_traffic_quiescence_not_verified')
        state = self.load()
        state['quiesced_token'] = token
        self.save(state)
        return result

    def dispatch(self, body):
        operation = body.get('operation')
        if operation == 'capabilities' and set(body) == {'operation'}:
            return self.capabilities()
        if operation == 'system_status' and set(body) == {'operation'}:
            return self.system_status()
        if operation == 'system_fence' and set(body) == {'operation', 'token', 'version', 'boot', 'expires'}:
            return self.system_fence(**{key: body[key] for key in body if key != 'operation'})
        if operation == 'fence' and set(body) == {'operation', 'token', 'boot', 'expires'}:
            return self.fence(**{key: body[key] for key in body if key != 'operation'})
        if operation == 'checkpoint' and set(body) == {'operation'}:
            return self.checkpoint()
        if operation == 'apply' and set(body) == {'operation', 'owner', 'token', 'boot_id', 'expected_version', 'action_id', 'command_sha256', 'command'}:
            return self.apply({key: body[key] for key in body if key != 'operation'})
        if operation == 'restore' and set(body) == {'operation', 'token', 'expected_version', 'policy'}:
            return self.restore(**{key: body[key] for key in body if key != 'operation'})
        if operation == 'quiesce' and set(body) == {'operation', 'token'}:
            return self.quiesce(token=body['token'])
        raise ValueError('effective_native_contract_incomplete')


def serve(args):
    import fcntl  # Linux service; pure coordinator tests also run on Windows.
    os.umask(0o077)
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.state / 'adapter.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        coordinator = Coordinator(Transport(json.loads(args.transport.read_text())),
                                  args.state / 'adapter.sqlite3',
                                  boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        args.socket.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(str(args.socket))
            os.chmod(args.socket, 0o600)
            while True:
                raw, _, flags, peer = server.recvmsg(262144)
                try:
                    if flags & socket.MSG_TRUNC:
                        raise ValueError('native_request_too_large')
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError('native_request_invalid')
                    reply = {'status': 'success', 'data': coordinator.dispatch(body)}
                except Exception as error:
                    # No cookies, credentials or request content enter logs.
                    code = str(error) if isinstance(error, ValueError) else 'native_operation_failed'
                    reply = {'status': 'blocked', 'error_code': code}
                try:
                    server.sendto(json.dumps(reply, allow_nan=False).encode(), peer)
                except OSError:
                    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', required=True, type=Path)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/maestro-policy-native'))
    parser.add_argument('--socket', type=Path, default=Path('/run/maestro-policy-native/control.sock'))
    serve(parser.parse_args())
