import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from infra.policy_authority import native_runtime


@pytest.mark.parametrize('size', [184, 192])
def test_emergency_closes_legacy_and_c4_without_rewriting_usage(tmp_path, monkeypatch, size):
    import json
    import struct
    roots = [tmp_path/'legacy', tmp_path/'c4']
    for root in roots:
        (root/'maps').mkdir(parents=True)
        (root/'maps/enabled').touch()
    policy = roots[1]/'maps/c4_policy_v1'
    policy.touch()
    (roots[1]/'policy').touch()
    runtime = tmp_path/'runtime'; runtime.mkdir()
    monkeypatch.setattr(native_runtime, 'XDP_PIN_ROOTS', roots)
    monkeypatch.setattr(native_runtime, 'C4_RUNTIME', runtime)
    gates = {str(root/'maps/enabled'): 1 for root in roots}
    value = bytearray(size)
    struct.pack_into('<Q', value, 128, 77)  # Existing XDP counter must survive.
    struct.pack_into('<I', value, 180, 1)
    key = bytes(range(32))

    def run(argv):
        path = argv[argv.index('pinned')+1]
        if 'update' in argv:
            assert path in gates, 'Must never replace a live policy value'
            assert (runtime/'emergency-stop').exists()
            gates[path] = 0
            return ''
        if 'lookup' in argv:
            return json.dumps({'value': ['0x00']*4})
        if 'show' in argv:
            return json.dumps({'bytes_key': 32, 'bytes_value': size})
        if 'dump' in argv:
            return json.dumps([{'key': [hex(v) for v in key], 'value': [hex(v) for v in value]}])
        raise AssertionError(argv)

    def quiesce(program, received):
        assert received == key and all(v == 0 for v in gates.values())
        struct.pack_into('<I', value, 180, 0)

    monkeypatch.setattr(native_runtime, 'run', run)
    monkeypatch.setattr(native_runtime, 'quiesce_policy', quiesce)
    result = native_runtime.xdp_gate_close()
    assert result['quiesced_generations'] == 1 and len(result['closed_gates']) == 2
    assert struct.unpack_from('<Q', value, 128) == (77,)


def test_emergency_rejects_an_unclosed_gate(tmp_path, monkeypatch):
    import json
    (tmp_path/'enabled').touch()
    monkeypatch.setattr(native_runtime, 'XDP_PIN_ROOTS', [tmp_path])
    monkeypatch.setattr(native_runtime, 'C4_RUNTIME', tmp_path)
    monkeypatch.setattr(native_runtime, 'run', lambda argv: json.dumps({'value': ['0x01','0x00','0x00','0x00']}))
    with pytest.raises(ValueError, match='xdp_gate_close_not_verified'):
        native_runtime.xdp_gate_close()


def test_cleanup_requires_both_reserved_name_and_ownership_description(monkeypatch):
    unit = 'maestro-policy-traffic-' + 'a' * 32 + '.service'
    calls = []

    def run(argv):
        calls.append(argv)
        return unit + ' loaded active running\n' if 'list-units' in argv else 'Unrelated workload\n'

    monkeypatch.setattr(native_runtime, 'run', run)
    with pytest.raises(ValueError, match='ownership_not_confirmed'):
        native_runtime.owned_traffic_cleanup()
    assert not any('stop' in argv for argv in calls)


def test_cleanup_rechecks_that_owned_cgroup_stopped(monkeypatch):
    unit = 'maestro-policy-traffic-' + 'a' * 32 + '.service'

    def run(argv):
        if 'list-units' in argv:
            return unit + ' loaded active running\n'
        if 'Description' in argv:
            return 'MAEstro authority-owned traffic\n'
        if 'ActiveState' in argv:
            return 'active\n'
        return ''

    monkeypatch.setattr(native_runtime, 'run', run)
    with pytest.raises(ValueError, match='still_active'):
        native_runtime.owned_traffic_cleanup()


def test_probe_rejects_addresses_outside_observed_nf_pool_before_execution(monkeypatch):
    calls = []
    monkeypatch.setattr(native_runtime, 'run', lambda argv: calls.append(argv))
    with pytest.raises(ValueError, match='outside_native_pool'):
        native_runtime.traffic_probe('upf3', {'sessions': [{'ue_ipv4': '127.0.0.1'}]})
    assert calls == []


def test_urllc_probe_uses_live_session_address_in_its_namespace(monkeypatch):
    calls = []
    monkeypatch.setattr(native_runtime, 'run', lambda argv: calls.append(argv))
    result = native_runtime.traffic_probe('upf3', {'sessions': [dict(ue_ipv4='10.47.0.23', smf_seid='123', upf_seid='456')]})
    assert result['traffic_verified'] and result['sessions'][0]['upf_seid'] == '456'
    assert calls[0][:4] == ['ip', 'netns', 'exec', 'maestro-urllc']
    assert calls[0][-1] == '10.47.0.23'


def test_empty_upf_never_claims_verified_traffic(monkeypatch):
    calls = []
    monkeypatch.setattr(native_runtime, 'run', lambda argv: calls.append(argv))
    assert native_runtime.traffic_probe('upf2', {'sessions': []})['traffic_verified'] is False
    assert not calls


@pytest.mark.parametrize('failure', ['missing', 'reboot', 'replacement', 'duplicate', 'old_token', 'old_version', 'loss'])
def test_traffic_receipts_must_cover_same_restored_sessions(failure):
    from copy import deepcopy
    from test_native_effective import fleet
    from infra.policy_authority.native_effective import checkpoint
    snapshot = checkpoint(fleet(), boot='boot', measured_at=100, metadata={})
    probes = {}
    for nf in ('upf', 'upf2', 'upf3'):
        probes[nf] = {**snapshot['nfs'][nf], 'token': snapshot['fencing_token'],
                      'version': snapshot['version'], 'traffic_verified': True,
                      'sessions': [{k: s[k] for k in ('smf_seid', 'upf_seid')}
                                   for s in snapshot['sessions'] if s['upf'] == nf]}
    native_runtime.validate_traffic_probes(snapshot, probes)
    target = probes['upf3']
    if failure == 'missing': target['sessions'] = []
    if failure == 'reboot': target['boot_id'] = 'another-boot'
    if failure == 'replacement': target['sessions'][0]['upf_seid'] = '99999'
    if failure == 'duplicate': target['sessions'].append(deepcopy(target['sessions'][0]))
    if failure == 'old_token': target['token'] -= 1
    if failure == 'old_version': target['version'] -= 1
    if failure == 'loss': target['traffic_verified'] = False
    with pytest.raises(ValueError, match='native_restored_traffic_'):
        native_runtime.validate_traffic_probes(snapshot, probes)
