import json

import pytest

from app.laboratory.policy_observer import project_snapshot

BINDINGS = {'observed': {'alias': 'video', 'supi': 'imsi-999700000000001', 'dnn': 'internet'}}


def snapshot():
    return {'status': 'success', 'schema_version': 1, 'scope': 'pcf_nwdaf_controller_only',
            'pid': 100, 'boot_id': 'boot', 'monotonic_us': 1000, 'mode': 'AUTONOMOUS',
            'internet_session_count': 1, 'controller_pending_count': 0, 'truncated': False,
            'sessions': [{'supi': BINDINGS['observed']['supi'], 'pcf_session_id': 1, 'dnn': 'internet',
                          'sst': 1, 'sd': '000001', 'nwdaf_or_mml_pending': False,
                          'controller_throttled_flag': False, 'controller_changed_monotonic_us': 0}]}


def test_nominal_controller_flag_and_zero_pending_do_not_prove_effective_policy():
    value = snapshot()
    value['credential'] = 'must-not-leak'
    result = project_snapshot(value, BINDINGS)
    assert result['status'] == 'observed'
    assert result['sessions'][0]['controller_throttled_flag'] is False
    assert not result['effective_policy_verified'] and not result['execution_ready']
    assert not result['recovery_verified']
    assert 'must-not-leak' not in json.dumps(result) and 'imsi-' not in json.dumps(result)


@pytest.mark.parametrize('update', [
    {'status': 'failed'}, {'schema_version': 2}, {'scope': 'effective_policy'},
    {'pid': False}, {'controller_pending_count': -1}, {'monotonic_us': float('inf')},
    {'controller_pending_count': 1}, {'sessions': [{}]},
])
def test_unknown_or_malformed_native_response_stays_unavailable(update):
    result = project_snapshot(snapshot() | update, BINDINGS)
    assert result['status'] == 'unavailable'
    assert not result['execution_ready']


def test_duplicate_session_or_truncated_reply_is_not_a_checkpoint():
    value = snapshot()
    value['sessions'] *= 2
    value['internet_session_count'] = 2
    result = project_snapshot(value, BINDINGS)
    assert 'observed_pcf_session_not_unique' in result['reasons']
    value['truncated'] = True
    assert 'incomplete_native_snapshot' in project_snapshot(value, BINDINGS)['reasons']
