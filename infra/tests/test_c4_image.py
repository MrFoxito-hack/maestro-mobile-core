import copy
import importlib.util
import struct
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'upf_xdp/c4_image.py'
spec = importlib.util.spec_from_file_location('c4_image', MODULE)
c4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c4)


def registry():
    sessions = []
    for i in (2, 3):
        sessions.append({'seid': str(100+i), 'ue': f'10.47.0.{i}', 'dnn': '5g-plus',
            'pdrs': [
                {'id': 1, 'source': 0, 'qer_id': 4, 'qfi': 9, 'teid': 1000+i,
                 'sdf': False, 'far_action': 2, 'urr_ids': [7], 'upf': '10.210.50.22'},
                {'id': 2, 'source': 1, 'qer_id': 4, 'qfi': 9, 'teid': 0,
                 'sdf': False, 'far_action': 2, 'urr_ids': [7], 'dl_teid': 2000+i,
                 'gnb': '10.210.50.3'}],
            'qers': [{'id': 4, 'qfi': 9, 'gbr': [0, 0], 'mbr': [10000000, 5000000], 'gates': [0, 0]}],
            'urrs': [{'id': 7, 'quota_active': True, 'quota_expired': False,
                      'quota_limit': 10000, 'native_bytes': 2000, 'native_packets': 20}]})
    return {'schema': 1, 'instance': '1234', 'generation': '5678', 'revoked': False, 'sessions': sessions}


def test_two_sessions_preserve_qer_urr_and_never_admit_or_refill():
    native = registry()
    original = copy.deepcopy(native)
    image = c4.compile_image(native)
    assert native == original
    assert not image['admission_ready'] and not image['fallback']
    assert {r['ue'] for r in image['sessions']} == {'10.47.0.2', '10.47.0.3'}
    assert len({r['policy_key_hex'] for r in image['sessions']}) == 2
    for row in image['sessions']:
        assert struct.unpack('<QQQII', bytes.fromhex(row['policy_key_hex'])) == tuple(row['identity'])
        value = bytes.fromhex(row['policy_value_hex'])
        assert len(value) == 192
        assert struct.unpack_from('<Q', value, 184) == (0,)
        assert struct.unpack_from('<II', value) == (0, 1)
        assert struct.unpack_from('<5Q', value, 8) == (10000000, 1000000, 0, 0, 0)
        assert struct.unpack_from('<5Q', value, 48) == (5000000, 500000, 0, 0, 0)
        assert struct.unpack_from('<QQIIII', value, 152) == (8000, 0, 1, 3, 0, 0)
        assert not row['identity_verified'] and not row['admitted']


@pytest.mark.parametrize('change', [
    lambda s: s['qers'][0].update(gbr=[1, 0]),
    lambda s: s['qers'][0].update(mbr=[0, 5000000]),
    lambda s: s['qers'][0].update(mbr=[10**13, 5000000]),
    lambda s: s['qers'][0].update(gates=[True, 0]),
    lambda s: s['pdrs'][0].update(sdf=True),
    lambda s: s['pdrs'][0].update(far_action=0),
    lambda s: s['pdrs'][0].update(urr_ids=[7, 7]),
    lambda s: s['pdrs'][0].update(qer_id=999),
    lambda s: s['pdrs'][0].update(upf='10.210.50.8'),
    lambda s: s.update(urrs=[]),
    lambda s: s.update(ue='10.45.0.2'),
    lambda s: s.update(seid=True),
])
def test_unsupported_session_stays_native_without_removing_its_rules(change):
    native = registry()
    change(native['sessions'][0])
    before = copy.deepcopy(native)
    image = c4.compile_image(native)
    assert native == before
    assert len(image['sessions']) == 1
    assert image['fallback'][0]['effective_mode'] == 'kernel'
    assert not image['admission_ready']


@pytest.mark.parametrize('field,value', [('seid','102'), ('ue','10.47.0.2')])
def test_recycled_identity_cannot_silently_replace_another_session(field, value):
    native = registry()
    native['sessions'][1][field] = value
    with pytest.raises(ValueError, match='Ambiguous'):
        c4.compile_image(native)


def test_teid_collision_and_revocation_reject_entire_image():
    native = registry()
    native['sessions'][1]['pdrs'][0]['teid'] = native['sessions'][0]['pdrs'][0]['teid']
    with pytest.raises(ValueError, match='Ambiguous'): c4.compile_image(native)
    native['revoked'] = True
    with pytest.raises(ValueError, match='revoked'): c4.compile_image(native)


def test_directional_urr_and_closed_gate_are_preserved():
    native = registry()
    s = native['sessions'][0]
    s['pdrs'][1]['urr_ids'] = []
    s['qers'][0]['gates'] = [1, 0]
    s['urrs'][0]['quota_expired'] = True
    value = bytes.fromhex(c4.compile_image(native)['sessions'][0]['policy_value_hex'])
    assert struct.unpack_from('<QQIIII', value, 152) == (0, 0, 1, 1, 1, 0)


def test_reconnection_changes_key_even_when_teid_and_ip_are_reused():
    first = registry()
    second = copy.deepcopy(first)
    second['generation'] = '5679'
    a, b = c4.compile_image(first), c4.compile_image(second)
    assert a['sessions'][0]['ul_teid'] == b['sessions'][0]['ul_teid']
    assert a['sessions'][0]['policy_key_hex'] != b['sessions'][0]['policy_key_hex']


def test_native_forw_and_per_session_generation_match_live_abi():
    native = registry()
    native['sessions'][0]['generation'] = '17'
    for pdr in native['sessions'][0]['pdrs']:
        pdr['far_action'] = 512
    image = c4.compile_image(native)
    assert image['sessions'][0]['identity'][2] == 17
    assert image['layout_ready'] and not image['admission_ready']
    native['generation'] = '9000'
    assert c4.compile_image(native)['sessions'][0]['policy_key_hex'] == image['sessions'][0]['policy_key_hex']


def test_admission_requires_native_recovery_identity_and_all_sessions_eligible():
    native = registry()
    native.update(ready=True, capabilities={'restart_replay': True})
    for session in native['sessions']:
        session.update(identity_verified=True, bridge_ready=True, fast_eligible=True)
    assert c4.compile_image(native)['admission_ready']
    native['sessions'][0]['identity_verified'] = False
    assert not c4.compile_image(native)['admission_ready']
    native['sessions'] = []
    assert not c4.compile_image(native)['admission_ready']
