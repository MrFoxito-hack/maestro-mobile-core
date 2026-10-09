from copy import deepcopy
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from infra.policy_authority.reconcile_empty_restart import plan, NFS


def fixture():
    proof = {'authority_cycle': False, 'reserved': True, 'restored': False,
             'baseline': {'clock_boot_id': 'previous', 'version': 12,
                          'nfs': {nf: {'boot_id': 'previous', 'generation': 20} for nf in NFS}}}
    authority = {'owner': 'c3-native-proof', 'phase': 'blocked', 'boot': 'current', 'expires': 1, 'token': 478}
    adapter = {'boot': 'current', 'expires': 1, 'token': 478, 'version': 14}
    observed = {nf: {'boot_id': 'current', 'generation': 30, 'sessions': [], 'pending_native': 0,
                    'fencing': dict(enabled=True, failed=False, pending_n7=False, pending_n4=0,
                                    system=False, leased=False, token='478', version='15')} for nf in NFS}
    return proof, authority, adapter, observed


def test_empty_restart_preserves_native_floors_and_does_not_claim_policy_restore():
    args = fixture()
    before = deepcopy(args)
    result = plan(*args, boot='current', now=10)
    assert result['version'] == 15 and result['token'] == 478
    assert result['restored'] is False and result['authority_cycle'] is False
    assert args == before


@pytest.mark.parametrize('case', ['same_epoch', 'pdu', 'pending', 'deferred', 'system', 'floor', 'owner', 'lease', 'same_boot'])
def test_restart_repair_rejects_existing_sessions_owners_or_uncertain_state(case):
    proof, authority, adapter, observed = fixture()
    nf = observed['smf3']
    if case == 'same_epoch': nf.update(boot_id='previous', generation=20)
    elif case == 'pdu': nf['sessions'] = [{'supi': 'active'}]
    elif case == 'pending': nf['pending_native'] = 1
    elif case == 'deferred': nf['deferred_native'] = 1
    elif case == 'system': nf['fencing'].update(system=True, leased=True)
    elif case == 'floor': nf['fencing']['version'] = '16'
    elif case == 'owner': authority['owner'] = 'operator'
    elif case == 'lease': authority['expires'] = 100
    elif case == 'same_boot': proof['baseline']['clock_boot_id'] = 'current'
    with pytest.raises(ValueError):
        plan(proof, authority, adapter, observed, boot='current', now=10)
