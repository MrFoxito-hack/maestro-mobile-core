"""Authority contract tests. NativeDouble is NOT evidence of Open5GS enforcement."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import time

import pytest

from app.services.policy_authority import (
    Authority, AuthorityError, CAPABILITIES, NFS, digest, policy_projection, validate_checkpoint,
)


def effective():
    rules = {
        'pdr': [{'id': 1, 'active': True, 'far_id': 1, 'qer_ids': [1], 'urr_ids': [1]}],
        'far': [{'id': 1, 'active': True, 'action': 'FORW'}],
        'qer': [{'id': 1, 'active': True, 'mbr_dl': 20000000, 'mbr_ul': 10000000, 'qfi': 1}],
        'urr': [{'id': 1, 'active': True, 'measurement_method': 'VOLUM'}],
    }
    return {'schema_version': 2, 'scope': 'effective_policy', 'clock_boot_id': 'boot-a',
            'complete': True, 'pending_n7': [], 'pending_n4': [], 'version': 0, 'fencing_token': 0,
            'mode': 'MANUAL', 'nfs': {nf: {'boot_id': nf + '-boot', 'generation': 1, 'writer_fenced': True} for nf in NFS},
            'sessions': [{'supi': 'imsi-999700000000002', 'pdu_id': 1, 'dnn': '5g-plus',
                          'smf': 'smf3', 'upf': 'upf3', 'smf_seid': '18', 'upf_seid': '21',
                          'generation': 'session-1', 'n7_confirmed': True, 'n4_confirmed': True,
                          'rules': rules, 'policy': {'pcf': {'five_qi': 9}, 'smf': {'five_qi': 9}, 'upf': rules}}],
            'traffic_verified': True}


class NativeDouble:
    """File-backed data-plane double survives termination of the EMS test process."""
    def __init__(self, path, clock=lambda: 100):
        self.path, self.clock = Path(path), clock
        self.lose_ack = False
        self.ack_only = False
        self.apply_calls = 0
        self.restore_calls = 0
        self.read_calls = 0
        if not self.path.exists():
            self.save(effective())

    def save(self, state):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state), encoding='utf-8')
        tmp.replace(self.path)

    def load(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def capabilities(self):
        return {'protocol': 1, 'capabilities': list(CAPABILITIES), 'nfs': list(NFS)}

    def checkpoint(self):
        self.read_calls += 1
        raw = self.load()
        raw['measured_at'] = self.clock()
        raw['policy_sha256'] = digest(policy_projection(raw))
        return raw

    def fence(self, *, token, boot, expires):
        state = self.load()
        if token <= state['fencing_token']:
            raise AuthorityError('native_stale_fence')
        state.update(fencing_token=token, lease_boot=boot, expires=expires)
        self.save(state)

    def apply(self, envelope):
        self.apply_calls += 1
        state = self.load()
        if state['fencing_token'] != envelope['token'] or state['expires'] <= self.clock():
            raise AuthorityError('native_stale_fence')
        if self.ack_only:
            return {'status': 'success'}
        command = envelope['command']
        if command['operation'] == 'mode':
            state['mode'] = command['mode']
        else:
            state['sessions'][0]['rules']['qer'][0]['mbr_dl'] = 1000000
            state['sessions'][0]['policy']['upf'] = state['sessions'][0]['rules']
        state['last_action'] = {k: envelope[k] for k in ('action_id', 'command_sha256')}
        state['version'] += 1
        self.save(state)
        if self.lose_ack:
            raise TimeoutError('ACK lost after actual apply')

    def quiesce(self, *, token):
        assert self.load()['fencing_token'] == token

    def restore(self, *, token, expected_version, policy):
        self.restore_calls += 1
        state = self.load()
        assert token == state['fencing_token'] and expected_version == state['version']
        state['mode'] = policy['mode']
        for s, p in zip(state['sessions'], policy['sessions']):
            assert 'urr' not in p['policy']['upf']
            retained_urr = s['rules']['urr']
            s['policy'] = deepcopy(p['policy'])
            s['policy']['upf']['urr'] = retained_urr
            s['rules'] = s['policy']['upf']
        state['version'] += 1
        self.save(state)


@pytest.fixture
def authority(tmp_path):
    clock = [100.0]
    native = NativeDouble(tmp_path / 'native.json', lambda: clock[0])
    guard = Authority(tmp_path / 'authority.db', native, boot='boot-a', clock=lambda: clock[0])
    return guard, native, clock


def submit(guard, lease, **overrides):
    return guard.submit(**{**{k: lease[k] for k in ('owner', 'token', 'expected_version')},
                           'action_id': 'action-0001', 'command': {'operation': 'qos'}, **overrides})


def test_only_one_concurrent_owner_and_monotonic_across_restart(authority):
    guard, native, clock = authority
    def take(owner):
        try:
            return guard.acquire(owner, 0)
        except AuthorityError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(take, ['worker-one', 'worker-two']))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 'lease_busy_or_recovery_required' in results
    first = next(r for r in results if isinstance(r, dict))
    clock[0] += 31
    restarted = Authority(guard.path, native, boot='boot-a', clock=lambda: clock[0])
    assert restarted.recover()['status'] == 'recovered'
    next_lease = restarted.acquire('new-worker', restarted.status()['version'])
    assert next_lease['token'] > first['token']
    with pytest.raises(AuthorityError, match='lease_expired_or_fenced'):
        submit(restarted, first)


@pytest.mark.parametrize('field,value,code', [
    ('token', 0, 'lease_expired_or_fenced'), ('owner', 'another-worker', 'lease_expired_or_fenced'),
    ('expected_version', 1, 'policy_version_conflict'), ('token', True, 'lease_expired_or_fenced'),
])
def test_stale_and_desynchronized_requests_never_actuate(authority, field, value, code):
    guard, native, _ = authority
    lease = guard.acquire('ems', 0)
    with pytest.raises(AuthorityError, match=code):
        submit(guard, lease, **{field: value})
    assert native.apply_calls == 0


def test_expired_request_rejected_at_authority_and_native_boundary(authority):
    guard, native, clock = authority
    lease = guard.acquire('ems', 0)
    clock[0] += 31
    with pytest.raises(AuthorityError, match='lease_expired_or_fenced'):
        submit(guard, lease)
    with pytest.raises(AuthorityError, match='native_stale_fence'):
        native.apply({'token': lease['token']})


def test_idempotent_replay_and_reused_key_with_changed_body(authority):
    guard, native, clock = authority
    lease = guard.acquire('ems', 0)
    first = submit(guard, lease)
    clock[0] += 31
    replay = submit(guard, lease)
    assert replay['replay'] is True and replay['version'] == first['version']
    assert native.apply_calls == 1
    with pytest.raises(AuthorityError, match='idempotency_key_conflict'):
        submit(guard, lease, command={'operation': 'mode', 'mode': 'MANUAL'})


def test_two_concurrent_mutations_cannot_overwrite_newer_version(authority):
    guard, native, _ = authority
    lease = guard.acquire('ems', 0)
    def apply(action):
        try:
            return submit(guard, lease, action_id=action)
        except AuthorityError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, ['action-one', 'action-two']))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert native.apply_calls == 1


def test_http_ack_is_not_effective_state(authority):
    guard, native, _ = authority
    lease = guard.acquire('ems', 0)
    native.ack_only = True
    with pytest.raises(AuthorityError, match='effective_action_not_confirmed'):
        submit(guard, lease)
    assert guard.status()['phase'] == 'blocked'


def test_lost_ack_reads_effective_state_and_restores_policy_only(authority):
    guard, native, clock = authority
    baseline = policy_projection(native.checkpoint())
    lease = guard.acquire('ems', 0)
    native.lose_ack = True
    with pytest.raises(TimeoutError):
        submit(guard, lease)
    assert policy_projection(native.checkpoint()) != baseline
    with pytest.raises(AuthorityError, match='action_outcome_unknown'):
        submit(guard, lease)
    # Accounting deliberately advances while the EMS is disconnected.
    state = native.load()
    state['live_urr_octets'] = 9001
    state['chf_balance'] = 12345
    native.save(state)
    clock[0] += 31
    before_reads = native.read_calls
    result = guard.recover()
    assert result['status'] == 'recovered' and native.read_calls >= before_reads + 2
    assert policy_projection(native.checkpoint()) == baseline
    assert native.load()['live_urr_octets'] == 9001
    assert native.load()['chf_balance'] == 12345
    assert native.apply_calls == 1 and native.restore_calls == 1


def test_recovery_does_not_restore_into_a_different_session(authority):
    guard, native, clock = authority
    guard.acquire('ems', 0)
    state = native.load()
    state['sessions'][0]['generation'] = 'new-session'
    native.save(state)
    clock[0] += 31
    with pytest.raises(AuthorityError, match='native_epoch_or_session_changed'):
        guard.recover()
    assert native.restore_calls == 0 and guard.status()['phase'] == 'blocked'


def test_recovery_checks_live_coverage_after_replacing_expired_fence(authority):
    guard, native, clock = authority
    lease = guard.acquire('ems', 0)
    original = native.capabilities
    def live_capabilities():
        caps = original()
        if native.load()['expires'] <= clock[0]:
            caps['capabilities'].remove('all_writers_fenced')
        return caps
    native.capabilities = live_capabilities
    clock[0] += 31
    assert guard.recover()['status'] == 'recovered'
    assert native.load()['fencing_token'] > lease['token']
    assert native.restore_calls == 1


def test_transient_post_apply_checkpoint_is_read_again_without_reapply(authority, monkeypatch):
    guard, native, _ = authority
    lease = guard.acquire('ems', 0)
    original = native.checkpoint
    calls = []
    def checkpoint():
        value = original()
        calls.append(value)
        if len(calls) == 1:
            value['complete'] = False
        return value
    native.checkpoint = checkpoint
    monkeypatch.setattr('app.services.policy_authority.time.sleep', lambda _: None)
    assert submit(guard, lease)['effective_policy_verified'] is True
    assert len(calls) == 2 and native.apply_calls == 1


def test_persistent_incomplete_checkpoint_still_blocks_without_reapply(authority, monkeypatch):
    guard, native, _ = authority
    lease = guard.acquire('ems', 0)
    original = native.checkpoint
    def checkpoint():
        value = original()
        value['complete'] = False
        return value
    native.checkpoint = checkpoint
    monkeypatch.setattr('app.services.policy_authority.time.sleep', lambda _: None)
    with pytest.raises(AuthorityError, match='checkpoint_incomplete_or_inflight'):
        submit(guard, lease)
    assert native.apply_calls == 1 and guard.status()['phase'] == 'blocked'


def test_recovery_never_replays_an_old_chf_quota_grant(authority):
    guard, native, clock = authority
    lease = guard.acquire('ems', 0)
    submit(guard, lease)
    state = native.load()
    # N4 can renew a quota while policy control is disconnected.
    state['sessions'][0]['rules']['urr'][0]['volume_quota'] = 456
    state['sessions'][0]['policy']['upf'] = state['sessions'][0]['rules']
    native.save(state)
    clock[0] += 31
    assert guard.recover()['status'] == 'recovered'
    assert native.load()['sessions'][0]['rules']['urr'][0]['volume_quota'] == 456


def test_commit_revokes_old_token_and_preserves_authorized_policy(authority):
    guard, native, clock = authority
    lease = guard.acquire('ems', 0)
    result = submit(guard, lease)
    guard.commit(owner='ems', token=lease['token'], expected_version=result['version'])
    clock[0] += 31
    assert guard.recover() == {'status': 'idle'}
    assert native.restore_calls == 0
    with pytest.raises(AuthorityError, match='lease_expired_or_fenced'):
        submit(guard, lease, action_id='late-action')


@pytest.mark.parametrize('damage,code', [
    (lambda r: r.update(scope='pcf_nwdaf_controller_only'), 'effective_checkpoint_unavailable'),
    (lambda r: r.update(measured_at=90), 'checkpoint_stale'),
    (lambda r: r.update(clock_boot_id='another-boot'), 'checkpoint_stale'),
    (lambda r: r.update(pending_n4=['pfcp-pending']), 'checkpoint_incomplete'),
    (lambda r: r['nfs'].pop('upf3'), 'native_writers_not_observed'),
    (lambda r: r['sessions'][0]['rules']['pdr'][0].update(far_id=9), 'invalid_effective_session'),
    (lambda r: r['sessions'][0]['rules']['urr'][0].update(total_octets=100), 'invalid_effective_session'),
    (lambda r: r.update(policy_sha256='invented'), 'checkpoint_digest_mismatch'),
])
def test_incomplete_or_stale_checkpoint_rejected(authority, damage, code):
    _, native, _ = authority
    raw = native.checkpoint()
    damage(raw)
    with pytest.raises(AuthorityError, match=code):
        validate_checkpoint(raw, now=100, boot='boot-a')


def test_missing_native_contract_cannot_admit(authority):
    guard, native, _ = authority
    native.capabilities = lambda: {'protocol': 1, 'capabilities': ['http_ack']}
    with pytest.raises(AuthorityError, match='native_authority_contract_unavailable'):
        guard.acquire('ems', 0)
    assert guard.status()['token'] == 0


def interrupted_controller(directory):
    directory = Path(directory)
    native = NativeDouble(directory / 'native.json')
    guard = Authority(directory / 'authority.db', native, boot='boot-a', clock=lambda: 100)
    lease = guard.acquire('doomed-ems', 0)
    original = native.apply
    def apply_then_wait(envelope):
        original(envelope)
        (directory / 'applied.signal').write_text('applied', encoding='utf-8')
        time.sleep(30)
    native.apply = apply_then_wait
    submit(guard, lease)


def test_killed_controller_leaves_durable_intent_for_independent_recovery(tmp_path):
    process = multiprocessing.get_context('spawn').Process(target=interrupted_controller, args=(str(tmp_path),))
    process.start()
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / 'applied.signal').exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / 'applied.signal').exists(), 'controller did not reach post-apply/pre-ACK window'
        process.terminate()
        process.join(timeout=5)
        assert not process.is_alive()
        native = NativeDouble(tmp_path / 'native.json', lambda: 140)
        supervisor = Authority(tmp_path / 'authority.db', native, boot='boot-a', clock=lambda: 140)
        assert supervisor.status()['phase'] == 'mutating'
        assert native.load()['sessions'][0]['rules']['qer'][0]['mbr_dl'] == 1000000
        assert supervisor.recover()['status'] == 'recovered'
        assert native.load()['sessions'][0]['rules']['qer'][0]['mbr_dl'] == 20000000
        assert native.apply_calls == 0  # No resend of the interrupted mutation.
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)


def test_smf3_infoapi_uses_observed_live_endpoint():
    from app.services.execution import _info_address
    assert _info_address('smf3') == ('10.210.50.18', 9092)


def test_missing_lease_rejected_before_ssh():
    from fastapi import HTTPException
    from app.services.pcf_control import control_request
    with pytest.raises(HTTPException) as error:
        control_request({'operation': 'qos', 'supi': 'imsi-999700000000002'})
    assert error.value.status_code == 428


def test_pcf_mutation_uses_only_remote_authority(monkeypatch):
    from app.services import pcf_control, policy_authority_client
    calls = []
    monkeypatch.setattr(policy_authority_client, 'request', lambda p: calls.append(p) or {'status': 'success'})
    context = {'owner': 'ems', 'token': 4, 'expected_version': 2, 'action_id': 'action-123'}
    pcf_control.control_request({'operation': 'mode', 'mode': 'MANUAL', 'authority': context})
    assert calls == [{'operation': 'submit', **context, 'command': {'operation': 'mode', 'mode': 'MANUAL'}}]


def test_authority_api_keeps_operator_rbac(client, student_headers):
    assert client.get('/api/v1/operations/policy-authority', headers=student_headers).status_code == 403
    assert client.post('/api/v1/operations/policy-authority/lease?expected_version=0', headers=student_headers).status_code == 403


def test_api_rejects_policy_without_lease_and_preserves_conflict(client, teacher_headers, monkeypatch):
    from app.services import policy_authority_client
    from fastapi import HTTPException
    body = {'scenario_id': '5g-sa', 'component_id': 'pcf', 'operation_id': 'pcf.qos',
            'parameters': {'imsi': '999700000000004'}}
    assert client.post('/api/v1/operations/execute', headers=teacher_headers, json=body).status_code == 428
    def stale(payload):
        assert payload['owner'] == 'local/docente'
        raise HTTPException(409, 'lease_expired_or_fenced')
    monkeypatch.setattr(policy_authority_client, 'request', stale)
    body['authority'] = {'token': 1, 'expected_version': 0, 'action_id': 'old-action'}
    response = client.post('/api/v1/operations/execute', headers=teacher_headers, json=body)
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == 'lease_expired_or_fenced'
