from copy import deepcopy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from infra.policy_authority.native_registry import correlate, NFS, uint
from infra.policy_authority.build_native import patch_init
from infra.policy_authority.deploy_observers import exec_arguments, override
from app.services.policy_authority import AuthorityError, validate_checkpoint


ADDRESSES = {'smf': ['10.210.50.1'], 'smf2': ['10.210.50.2'], 'smf3': ['10.210.50.18']}


def observations():
    nfs = {nf: dict(scope='native_observation', schema_version=1, boot_id=nf + '-boot',
                    generation='50000000000', pid=100, writer_fenced=False, sessions=[]) for nf in NFS}
    for i in (2, 5):
        rules = dict(pdr=[dict(id=1, active=True, far_id=1, qer_ids=[1], urr_ids=[1])],
                     far=[dict(id=1, active=True)], qer=[dict(id=1, active=True, mbr_ul='1000000', mbr_dl='2000000')],
                     urr=[dict(id=1, active=True, measurement_method=2)])
        # Deliberately >2**53: no JSON float conversion is allowed.
        seid = str(2**60 + i)
        common = dict(supi=f'imsi-99970000000000{i}', pdu_id=1, dnn='5g-plus', policy_id=str(i))
        nfs['pcf']['sessions'].append(deepcopy(common))
        nfs['smf3']['sessions'].append(dict(**common, smf_seid=seid, upf_seid=seid, context_id=i, rules=deepcopy(rules)))
        nfs['upf3']['sessions'].append(dict(dnn='5g-plus', smf_ipv4='10.210.50.18',
            smf_seid=seid, upf_seid=seid, context_id=i, ue_ipv4=f'10.47.0.{i}', rules=deepcopy(rules),
            usage=[dict(urr_id=1, total_octets='123', report_sequence=4)]))
    return nfs


def test_two_sessions_preserve_rules_and_accounting_without_claiming_authority():
    nfs = observations(); before = deepcopy(nfs)
    result = correlate(nfs, ADDRESSES)
    assert nfs == before
    assert len(result['sessions']) == 2
    assert len({r['session_key'] for r in result['sessions']}) == 2
    for row in result['sessions']:
        assert row['upf_rules']['qer'][0]['mbr_ul'] == '1000000'
        assert row['upf_rules']['pdr'][0]['urr_ids'] == [1]
        assert row['usage'][0]['total_octets'] == '123'
        assert 'usage' not in row['upf_rules']
        assert not row['xdp_eligible'] and not row['n7_confirmed'] and not row['n4_confirmed']
    with pytest.raises(AuthorityError, match='effective_checkpoint_unavailable'):
        validate_checkpoint(result, now=0, boot='boot')


def test_counter_change_does_not_change_policy_hash_or_identity():
    nfs = observations(); first = correlate(nfs, ADDRESSES)['sessions'][0]
    nfs['upf3']['sessions'][0]['usage'][0]['total_octets'] = '999'
    second = correlate(nfs, ADDRESSES)['sessions'][0]
    assert first['session_key'] == second['session_key']
    assert first['rules_sha256'] == second['rules_sha256']
    assert first['usage'] != second['usage']


def test_rule_change_invalidates_policy_hash():
    nfs = observations(); first = correlate(nfs, ADDRESSES)['sessions'][0]
    nfs['upf3']['sessions'][0]['rules']['qer'][0]['mbr_ul'] = '500000'
    second = correlate(nfs, ADDRESSES)['sessions'][0]
    assert first['rules_sha256'] != second['rules_sha256']


def test_restart_cannot_reuse_session_generation():
    nfs = observations(); first = correlate(nfs, ADDRESSES)['sessions'][0]
    nfs['upf3']['generation'] = '60000000000'
    second = correlate(nfs, ADDRESSES)['sessions'][0]
    assert first['session_key'] != second['session_key']


@pytest.mark.parametrize('damage,error', [
    (lambda n: n.pop('smf2'), 'seven_native'),
    (lambda n: n['upf3']['sessions'][0].update(smf_ipv4='10.210.50.1'), 'not_uniquely'),
    (lambda n: n['pcf']['sessions'][0].update(policy_id='old'), 'policy_not_correlated'),
    (lambda n: n['upf3']['sessions'][0]['rules']['qer'].clear(), 'dangling'),
    (lambda n: n['upf3']['sessions'].append(deepcopy(n['upf3']['sessions'][0])), 'not_uniquely'),
    (lambda n: n['smf3']['sessions'][0].update(upf_seid='0'), 'establishing'),
    (lambda n: n['smf3']['sessions'].pop(), 'uncorrelated'),
])
def test_partial_or_ambiguous_native_data_is_rejected(damage, error):
    nfs = observations(); damage(nfs)
    with pytest.raises(ValueError, match=error):
        correlate(nfs, ADDRESSES)


@pytest.mark.parametrize('value', [True, -1, 2**64, 1.0, '1.0', '١'])
def test_native_ids_reject_non_exact_unsigned_values(value):
    with pytest.raises(ValueError):
        uint(value)


def test_builder_requires_known_event_loop_and_closes_before_context_destruction():
    source = ('static ogs_thread_t *thread;\n'
              '    ogs_fsm_init(&smf_sm, smf_state_initial, smf_state_final, 0);\n'
              '            rv = ogs_queue_trypop(ogs_app()->queue, (void**)&e);\n'
              '            ogs_fsm_dispatch(&smf_sm, e);\n'
              '    ogs_fsm_fini(&smf_sm, 0);')
    patched = patch_init('smf', source)
    assert patched.index('mpo_close();') < patched.index('ogs_fsm_fini')
    assert patched.index('msd_close();') < patched.index('mpo_close();')
    assert patched.index('msd_hold(e)') < patched.index('ogs_fsm_dispatch')
    with pytest.raises(ValueError):
        patch_init('smf', source.replace('ogs_queue_trypop', 'changed_queue_api'))
    with pytest.raises(ValueError):
        patch_init('smf', source.replace('ogs_fsm_init', 'changed_api'))


def test_deployment_rejects_shell_exec_and_systemd_specifier_injection():
    with pytest.raises(ValueError):
        exec_arguments('{ path=/bin/sh ; argv[]=/bin/sh -c arbitrary-command ; ignore_errors=no }')
    with pytest.raises(ValueError):
        override('/opt/build%H', 'smf', '/etc/open5gs/smf.yaml')
    assert exec_arguments('{ path=/opt/open5gs-smfd ; argv[]=/opt/open5gs-smfd -c /etc/smf.yaml ; ignore_errors=no }') == [
        '/opt/open5gs-smfd', '-c', '/etc/smf.yaml']
