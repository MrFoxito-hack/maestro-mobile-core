"""Control-plane crash/concurrency contracts, not native NF acceptance evidence."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from infra.policy_authority.native_adapter import Coordinator
from app.services.policy_authority import Authority, AuthorityError, NFS


class Fleet:
    def __init__(self):
        self.events = []
        self.fail = None
        self.states = {nf: {'pending_native': 0, 'fencing': dict(
            enabled=True, failed=False, token='0', version='0', system=False,
            leased=False, system_admission=True)} for nf in NFS}

    def observe(self):
        return {nf: (deepcopy(body), 0) for nf, body in self.states.items()}

    def request(self, nf, command):
        self.events.append((nf, command.split()[0]))
        if self.fail == nf:
            raise TimeoutError('lost_remote')
        g = self.states[nf]['fencing']
        op = command.split()[0]
        if op in {'bootstrap-close-v1', 'system-close-v1'}:
            g['system_admission'] = False
        elif op == 'system-prepare-v1':
            g['system'] = True
        elif op == 'system-open-v1':
            g['system_admission'] = True
        return {}, 0

    def fence(self, *, token, boot, expires):
        self.events.append(('all', 'fence'))
        for g in self.states.values():
            g['fencing'].update(token=str(token), leased=True, system=False)
        return {}


def setup(tmp_path):
    fleet = Fleet()
    coordinator = Coordinator(fleet, tmp_path / 'adapter.db', boot='boot', clock=lambda: 100)
    return fleet, coordinator


def test_system_grant_opens_admission_only_after_every_upf_is_prepared(tmp_path):
    fleet, coordinator = setup(tmp_path)
    coordinator.system_fence(token=1, version=0, boot='boot', expires=145)
    opens = [i for i, (_, op) in enumerate(fleet.events) if op == 'system-open-v1']
    prepared = [i for i, (_, op) in enumerate(fleet.events) if op == 'system-prepare-v1']
    assert len(prepared) == 7 and max(prepared) < min(opens)
    assert coordinator.system_status() == {'token': 1, 'version': 0, 'renew_required': False}


def test_owner_barrier_closes_smf_before_pcf_and_fences_all(tmp_path):
    fleet, coordinator = setup(tmp_path)
    coordinator.system_fence(token=1, version=0, boot='boot', expires=145)
    fleet.events.clear()
    coordinator.fence(token=2, boot='boot', expires=130)
    closes = [nf for nf, op in fleet.events if op == 'system-close-v1']
    assert set(closes[:3]) == {'smf', 'smf2', 'smf3'} and closes[-1] == 'pcf'
    assert fleet.events[-1] == ('all', 'fence')


def test_partial_prepare_does_not_open_admission_or_claim_ready(tmp_path):
    fleet, coordinator = setup(tmp_path)
    fleet.fail = 'upf3'
    with pytest.raises(TimeoutError):
        coordinator.system_fence(token=1, version=0, boot='boot', expires=145)
    assert not any(op == 'system-open-v1' for _, op in fleet.events)
    assert coordinator.system_status()['renew_required']
    assert coordinator.load()['token'] == 1


def test_adapter_restart_requires_new_lease_after_boot_change(tmp_path):
    fleet, coordinator = setup(tmp_path)
    coordinator.system_fence(token=1, version=0, boot='boot', expires=145)
    restarted = Coordinator(fleet, tmp_path / 'adapter.db', boot='new-boot', clock=lambda: 100)
    assert restarted.system_status()['renew_required']
    with pytest.raises(ValueError, match='invalid_coordinator_fence'):
        restarted.fence(token=1, boot='new-boot', expires=145)


def test_lifecycle_grants_do_not_advertise_unimplemented_effective_checkpoint(tmp_path):
    _, coordinator = setup(tmp_path)
    coordinator.system_fence(token=1, version=0, boot='boot', expires=145)
    for operation in ('apply', 'restore'):
        with pytest.raises(ValueError, match='effective_native_contract_incomplete'):
            coordinator.dispatch({'operation': operation})


def test_supervisor_burns_system_token_before_io_and_recovers_without_baseline(tmp_path):
    fleet, coordinator = setup(tmp_path)
    authority = Authority(tmp_path / 'authority.db', coordinator, boot='boot', clock=lambda: 100)
    fleet.fail = 'upf3'
    with pytest.raises(TimeoutError):
        authority.maintain()
    assert authority.status()['token'] == 1
    assert authority.status()['phase'] == 'system_preparing'
    restarted = Authority(tmp_path / 'authority.db', coordinator, boot='boot', clock=lambda: 100)
    assert restarted.recover()['status'] == 'idle'
    fleet.fail = None
    assert restarted.maintain()['token'] == 2
    assert restarted.status()['phase'] == 'idle'
    assert restarted.maintain()['status'] == 'system_active'


def test_supervisor_never_renews_system_permission_while_owner_holds_lease(tmp_path):
    _, coordinator = setup(tmp_path)
    authority = Authority(tmp_path / 'authority.db', coordinator, boot='boot', clock=lambda: 100)
    with authority._db() as db:
        db.execute("UPDATE authority SET owner='operator',phase='leased',token=10 WHERE id=1")
    assert authority.maintain()['status'] == 'owner_active'
    assert coordinator.load()['token'] == 0


def test_supervisor_detects_native_token_ahead_of_its_durable_database(tmp_path):
    _, coordinator = setup(tmp_path)
    coordinator.system_fence(token=9, version=0, boot='boot', expires=145)
    authority = Authority(tmp_path / 'authority.db', coordinator, boot='boot', clock=lambda: 100)
    with pytest.raises(AuthorityError, match='native_system_floor_or_version_conflict'):
        authority.maintain()
