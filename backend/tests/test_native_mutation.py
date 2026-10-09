"""Adapter policy invariants; these doubles are not Open5GS acceptance proof."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from test_native_effective import fleet
from test_native_system_lease import Fleet
from infra.policy_authority.native_adapter import Coordinator
from infra.policy_authority.native_effective import digest, policy_projection


class RulesFleet(Fleet):
    def __init__(self):
        super().__init__()
        self.states = fleet(covered=False)
        for nf in self.states.values():
            nf['fencing'].update(system=False, leased=True, system_admission=False, prepared=False)
        for i, row in enumerate(self.states['pcf']['sessions'], 1):
            row['context_id'] = i

    def request(self, nf, command):
        op = command.split()[0]
        self.events.append((nf, op))
        guard = self.states[nf]['fencing']
        if op == 'prepare-v1':
            assert not guard['prepared']
            guard['prepared'] = True
        elif op == 'cancel-v1':
            guard['prepared'] = False
        if op == 'finish-v1':
            assert guard['prepared']
            assert int(command.split()[2]) == int(guard['version'])
            guard['version'] = str(int(guard['version']) + 1)
            guard['prepared'] = False
        return {}, 0

    def fence(self, **kwargs):
        result = super().fence(**kwargs)
        for nf in self.states.values():
            nf['fencing']['prepared'] = False
        return result

    def mml(self, command, *_):
        if command['operation'] == 'mode':
            self.states['pcf']['mode'] = command['mode']
            return {}
        for index, pcf in enumerate(self.states['pcf']['sessions']):
            if pcf['supi'] != command['supi']:
                continue
            smf = self.states['smf3']['sessions'][index]
            upf = self.states['upf3']['sessions'][index]
            for direction in ('ul', 'dl'):
                key = 'mbr_' + direction
                rate = str(round(command[key + '_mbps'] * 1e6))
                pcf['policy'][key] = smf['policy'][key] = rate
                smf['rules']['qer'][0][key] = upf['rules']['qer'][0][key] = rate
        return {}


def setup(tmp_path):
    transport = RulesFleet()
    coordinator = Coordinator(transport, tmp_path / 'adapter.db', boot='boot', clock=lambda: 100)
    coordinator.save(dict(token=3, version=0, role='owner', boot='boot', expires=130))
    coordinator.mml = transport.mml
    return coordinator, transport


def envelope(command, version=0):
    return dict(owner='operator', token=3, boot_id='boot', expected_version=version,
                action_id='action-' + str(version) + '-verified', command=command, command_sha256=digest(command))


def desired(snapshot):
    value = policy_projection(snapshot)
    for session in value['sessions']:
        session['policy']['upf'].pop('urr')
    return value


def test_two_session_ambr_restore_commits_one_version_and_keeps_live_urr(tmp_path):
    coordinator, transport = setup(tmp_path)
    baseline = coordinator.checkpoint()
    for i, supi in enumerate(('imsi-999700000000002', 'imsi-999700000000005')):
        coordinator.apply(envelope({'operation': 'qos', 'supi': supi, 'mbr_dl_mbps': 1}, i))
    for row in transport.states['upf3']['sessions']:
        row['rules']['urr'][0]['volume_quota'] = {'total': '9876'}
        row['usage'][0]['total_octets'] = '5555'
    reply = coordinator.restore(token=3, expected_version=2, policy=desired(baseline))
    assert reply['version'] == 3  # One restoration, despite two changed sessions.
    restored = coordinator.checkpoint()
    assert desired(restored) == desired(baseline)
    for row in transport.states['upf3']['sessions']:
        assert row['rules']['urr'][0]['volume_quota'] == {'total': '9876'}
        assert row['usage'][0]['total_octets'] == '5555'


def test_restore_rejects_old_charging_grant_before_any_native_write(tmp_path):
    coordinator, transport = setup(tmp_path)
    policy = policy_projection(coordinator.checkpoint())
    with pytest.raises(ValueError, match='must_not_replay_charging'):
        coordinator.restore(token=3, expected_version=0, policy=policy)
    assert not transport.events


def test_restore_preflights_all_sessions_before_first_mutation(tmp_path):
    coordinator, transport = setup(tmp_path)
    policy = desired(coordinator.checkpoint())
    policy['sessions'][-1]['policy']['upf']['pdr'][0]['urr_ids'] = []
    with pytest.raises(ValueError, match='non_ambr_change_unsupported'):
        coordinator.restore(token=3, expected_version=0, policy=policy)
    assert not transport.events


def test_unsupported_5qi_change_is_rejected_before_native_prepare(tmp_path):
    coordinator, transport = setup(tmp_path)
    with pytest.raises(ValueError, match='requires_ran_procedure'):
        coordinator.apply(envelope({'operation': 'qos', 'supi': 'imsi-999700000000002', 'five_qi': 1}))
    assert not transport.events


def test_partial_native_writer_coverage_never_satisfies_admission(tmp_path):
    coordinator, _ = setup(tmp_path)
    assert 'all_writers_fenced' not in coordinator.capabilities()['capabilities']


def test_native_version_changes_only_after_effective_policy_confirmation(tmp_path):
    coordinator, transport = setup(tmp_path)
    coordinator.mml = lambda *_: {'status': 'success'}  # N7 ACK alone is insufficient.
    with pytest.raises(ValueError, match='qos_not_effective'):
        coordinator.apply(envelope({'operation': 'qos', 'supi': 'imsi-999700000000002', 'mbr_dl_mbps': 1}))
    assert not any(op == 'finish-v1' for _, op in transport.events)
    assert coordinator.load()['pending_action']


def test_lease_expiry_during_traffic_probe_cannot_publish_recovery_success(tmp_path):
    coordinator, transport = setup(tmp_path)
    baseline = coordinator.checkpoint()
    state = coordinator.load()
    state['quiesced_token'] = 3
    coordinator.save(state)
    original = transport.request

    def request(nf, command=None, *, operation=None):
        if operation != 'traffic_probe':
            return original(nf, command)
        snapshot = coordinator.checkpoint()
        rows = [{k: s[k] for k in ('smf_seid', 'upf_seid')}
                for s in snapshot['sessions'] if s['upf'] == nf]
        # Remote reachability succeeds after the operator's lease has expired.
        coordinator.clock = lambda: 131
        return {**snapshot['nfs'][nf], 'token': snapshot['fencing_token'],
                'version': snapshot['version'], 'sessions': rows,
                'traffic_verified': bool(rows)}, 0

    transport.request = request
    with pytest.raises(ValueError, match='native_owner_lease_unavailable'):
        coordinator.restore(token=3, expected_version=0, policy=desired(baseline))
    assert 'traffic_proof' not in coordinator.load()


@pytest.mark.parametrize('finished', [0, 3, 7])
def test_restart_reconciles_partial_or_lost_version_commit_without_replaying_mml(tmp_path, finished):
    coordinator, transport = setup(tmp_path)
    baseline = coordinator.checkpoint()
    original = coordinator.commands

    def interrupted(commands):
        if commands and all(c.startswith('finish-v1 ') for c in commands.values()):
            for nf in sorted(commands)[:finished]:
                transport.request(nf, commands[nf])
            raise TimeoutError('lost_after_native_commit')
        return original(commands)

    coordinator.commands = interrupted
    with pytest.raises(TimeoutError):
        coordinator.apply(envelope({'operation': 'qos', 'supi': 'imsi-999700000000002', 'mbr_dl_mbps': 1}))
    assert coordinator.load()['version'] == 0
    assert coordinator.load()['pending_finish']
    # Actual accounting can advance while the response is lost.
    transport.states['upf3']['sessions'][0]['usage'][0]['total_octets'] = '2468'
    transport.states['upf3']['sessions'][0]['rules']['urr'][0]['volume_quota'] = {'total': '4321'}
    restarted = Coordinator(transport, tmp_path / 'adapter.db', boot='boot', clock=lambda: 100)
    restarted.mml = lambda *_: pytest.fail('Version reconciliation must not replay an N7 mutation')
    transport.events.clear()
    restarted.fence(token=4, boot='boot', expires=130)
    assert restarted.load()['version'] == 1 and 'pending_finish' not in restarted.load()
    assert len([e for e in transport.events if e[1] == 'finish-v1']) == 7 - finished
    assert {int(n['fencing']['version']) for n in transport.states.values()} == {1}
    assert transport.states['upf3']['sessions'][0]['usage'][0]['total_octets'] == '2468'
    restarted.mml = transport.mml
    restarted.restore(token=4, expected_version=1, policy=desired(baseline))
    assert desired(restarted.checkpoint()) == desired(baseline)
    assert transport.states['upf3']['sessions'][0]['rules']['urr'][0]['volume_quota'] == {'total': '4321'}


@pytest.mark.parametrize('tamper', ['epoch', 'policy', 'version'])
def test_uncertain_commit_requires_exact_epochs_policy_and_bounded_versions(tmp_path, tamper):
    coordinator, transport = setup(tmp_path)
    baseline = coordinator.checkpoint()
    command = {'operation': 'qos', 'supi': 'imsi-999700000000002', 'mbr_dl_mbps': 1}
    state = coordinator.load()
    state['pending_action'] = {'baseline': baseline, 'envelope': envelope(command)}
    coordinator.save(state)
    transport.mml({**command, 'mbr_ul_mbps': 1})
    for nf in transport.states.values():
        nf['fencing']['version'] = '1'
    if tamper == 'epoch':
        transport.states['smf3']['generation'] = '777'
    elif tamper == 'policy':
        transport.states['upf3']['sessions'][0]['rules']['far'][0]['apply_action'] = 999
    else:
        transport.states['smf3']['fencing']['version'] = '2'
    with pytest.raises(ValueError, match='native_commit_'):
        coordinator.fence(token=4, boot='boot', expires=130)
    assert not any(op == 'finish-v1' for _, op in transport.events)
    assert coordinator.load()['version'] == 0


def test_legacy_lost_reply_is_reconciled_from_exact_durable_command(tmp_path):
    coordinator, transport = setup(tmp_path)
    baseline = coordinator.checkpoint()
    command = {'operation': 'qos', 'supi': 'imsi-999700000000002', 'mbr_dl_mbps': 1}
    state = coordinator.load()
    state['pending_action'] = {'baseline': baseline, 'envelope': envelope(command)}
    coordinator.save(state)
    transport.mml({**command, 'mbr_ul_mbps': 1})
    for nf in transport.states.values():
        nf['fencing']['version'] = '1'
    coordinator.fence(token=4, boot='boot', expires=130)
    assert coordinator.load()['version'] == 1
    assert coordinator.load()['last_action']['command_sha256'] == digest(command)
    assert not any(op == 'finish-v1' for _, op in transport.events)


def test_crash_before_mml_can_abort_an_unapplied_intent_after_new_fence(tmp_path):
    coordinator, transport = setup(tmp_path)
    baseline = coordinator.checkpoint()
    command = {'operation': 'qos', 'supi': 'imsi-999700000000002', 'mbr_dl_mbps': 1}
    state = coordinator.load()
    state['pending_action'] = {'baseline': baseline, 'envelope': envelope(command), 'finalize': True}
    coordinator.save(state)
    coordinator.fence(token=4, boot='boot', expires=130)
    assert coordinator.load()['version'] == 0 and 'pending_action' not in coordinator.load()
    assert desired(coordinator.checkpoint()) == desired(baseline)
    assert not any(op == 'finish-v1' for _, op in transport.events)
