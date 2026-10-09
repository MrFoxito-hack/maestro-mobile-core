from copy import deepcopy
import json

import pytest

from test_native_policy_registry import observations
from infra.policy_authority.native_effective import checkpoint, policy_projection
from app.services.policy_authority import AuthorityError, validate_checkpoint, policy_projection as authority_projection


def fleet(*, covered=False):
    sample = observations()
    for nf in sample.values():
        nf.update(writer_fenced=covered, policy_complete=covered, pending_native=0,
                  fencing=dict(enabled=True, failed=False, recovery_required=False,
                               token='3', version='0', pending_n7=False, pending_n4=0))
    sample['pcf']['mode'] = 'MANUAL'
    for name in ('pcf', 'smf3'):
        for row in sample[name]['sessions']:
            row['policy'] = {'five_qi': 9, 'mbr_ul': '1000000', 'mbr_dl': '2000000'}
    return sample


def make(sample):
    return checkpoint(sample, boot='boot', measured_at=100, metadata={})


def test_checkpoint_never_promotes_partial_native_coverage():
    value = make(fleet())
    assert not value['complete']
    assert not any(n['writer_fenced'] for n in value['nfs'].values())
    assert not any(s['n7_confirmed'] or s['n4_confirmed'] for s in value['sessions'])
    with pytest.raises(AuthorityError, match='checkpoint_incomplete_or_inflight'):
        validate_checkpoint(value, boot='boot', now=100)


def test_complete_fixture_matches_authority_projection_and_excludes_usage():
    sample = fleet(covered=True)
    value = make(sample)
    validate_checkpoint(value, boot='boot', now=100)
    assert policy_projection(value) == authority_projection(value)
    encoded = json.dumps(value)
    assert 'total_octets' not in encoded and 'report_sequence' not in encoded
    assert 'urr' not in value['sessions'][0]['policy']['smf']['rules']
    original = value['policy_sha256']
    sample['upf3']['sessions'][0]['usage'][0]['total_octets'] = '9999'
    assert make(sample)['policy_sha256'] == original


@pytest.mark.parametrize('nf,key', [('smf3', 'token'), ('upf3', 'version')])
def test_divergent_native_floors_block_checkpoint(nf, key):
    sample = fleet(covered=True)
    sample[nf]['fencing'][key] = '7'
    with pytest.raises(ValueError, match='divergence'):
        make(sample)


def test_qer_and_n7_effective_rates_must_agree():
    sample = fleet(covered=True)
    sample['upf3']['sessions'][0]['rules']['qer'][0]['mbr_ul'] = '900000'
    with pytest.raises(ValueError, match='not_confirmed'):
        make(sample)
    sample['smf3']['sessions'][0]['rules']['qer'][0]['mbr_ul'] = '900000'
    with pytest.raises(ValueError, match='differs_from_session_ambr'):
        make(sample)


def test_pending_native_lifecycle_prevents_complete_checkpoint():
    sample = fleet(covered=True)
    sample['smf3']['pending_native'] = 1
    value = make(sample)
    assert not value['complete'] and value['pending_n4'] == ['smf3']
    with pytest.raises(AuthorityError):
        validate_checkpoint(value, boot='boot', now=100)


def test_extended_native_urr_configuration_is_observable_but_usage_is_rejected():
    sample = fleet(covered=True)
    urr = sample['upf3']['sessions'][0]['rules']['urr'][0]
    urr.update(measurement_information=1, event_threshold=20, event_quota=40,
               quota_holding_time=15, quota_validity_time=60,
               dropped_dl_traffic_threshold={'flags': 3, 'packets': '10', 'bytes': '1000'})
    value = make(sample)
    validate_checkpoint(value, boot='boot', now=100)
    from app.services.policy_authority import restoration_policy
    desired, _ = restoration_policy(value, value)
    assert 'urr' not in desired['sessions'][0]['policy']['upf']
    urr['total_octets'] = '999'
    with pytest.raises(AuthorityError, match='invalid_effective_session'):
        validate_checkpoint(make(sample), boot='boot', now=100)
