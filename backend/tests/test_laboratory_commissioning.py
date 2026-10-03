import hashlib
import json
from types import SimpleNamespace

import pytest

from app.laboratory.commissioning import Commissioner, INSPECT, START, OBSERVED
from app.laboratory.live import CORE_SCRIPT, UE_SCRIPT, UPF_SCRIPT, LabSSH
from app.laboratory.preflight import CORE_UNITS
from app.laboratory.real_readiness import collect, PCF_INSPECT

COMPETING = 'imsi-999700000000004'


def sessions():
    return {'subjects': {s: f'PDU Session1:\n state: PS-ACTIVE\n apn: internet\n address: 10.45.0.{i}\n s-nssai:\n  sst: 0x01\n  sd: 0x000001'
                         for s, i in ((OBSERVED, 2), (COMPETING, 3))},
            'interfaces': [{'ifname': f'uesimtun{i-2}', 'addr_info': [{'local': f'10.45.0.{i}'}]} for i in (2, 3)]}


class Transport:
    def __init__(self, active=False, wrong_identity=False, ambiguous_start=False, observed_changed=False):
        self.active, self.wrong_identity = active, wrong_identity
        self.ambiguous_start, self.observed_changed = ambiguous_start, observed_changed
        self.calls, self.inspections = [], 0

    def read(self, node, script, arguments=(), *, privileged=False):
        self.calls.append((script, privileged))
        if script == INSPECT:
            self.inspections += 1
            return {'supi': OBSERVED if self.wrong_identity else COMPETING,
                    'sessions': [{'apn': 'internet', 'slice': {'sst': 1, 'sd': 1}}],
                    'secondary': {'ActiveState': 'active' if self.active else 'inactive'},
                    'observed': {'MainPID': '2' if self.observed_changed and self.inspections > 1 else '1'},
                    'boot_id': 'boot'}
        if script == START:
            assert privileged
            self.active = True
            if self.ambiguous_start:
                raise TimeoutError('private ssh diagnostic')
            return {'start_acknowledged': True}
        if script == UE_SCRIPT:
            return sessions()
        if script == CORE_SCRIPT:
            return {'services': {u: {'ActiveState': 'active'} for u in CORE_UNITS},
                    'accounts': {s: {'quota_bytes': 2_000_000, 'consumed_bytes': 0, 'reserved_bytes': 0}
                                 for s in (OBSERVED, COMPETING)}}
        if script == UPF_SCRIPT:
            return {'service': 'active', 'interfaces': []}
        if script == PCF_INSPECT:
            assert privileged
            return {'status': 'success', 'mode': 'AUTONOMOUS', 'response_fields': ['status', 'detail', 'mode']}
        raise AssertionError('Uncatalogued remote program')


def test_starts_only_validated_secondary_and_preserves_observed(tmp_path):
    transport = Transport()
    report = Commissioner(transport).ensure(4, tmp_path / 'result', execute=True)
    assert report['execution_status'] == 'completed'
    assert report['validity_status'] == 'valid'
    assert sum(s == START for s, _ in transport.calls) == 1
    assert report['competing']['interface'] == 'uesimtun1'
    assert report['hypothesis_outcome'] == 'not_evaluated'


@pytest.mark.parametrize('active,execute,expected', [(True, False, 'completed'), (True, True, 'completed'), (False, False, 'failed')])
def test_never_restarts_active_or_starts_without_execute(tmp_path, active, execute, expected):
    transport = Transport(active=active)
    report = Commissioner(transport).ensure(4, tmp_path / 'result', execute=execute)
    assert report['execution_status'] == expected
    assert not any(s == START for s, _ in transport.calls)


def test_rejects_observed_index_and_wrong_subscriber_without_start(tmp_path):
    transport = Transport(wrong_identity=True)
    with pytest.raises(ValueError, match='secondary_only'):
        Commissioner(transport).ensure(1, tmp_path / 'wrong', execute=True)
    assert not transport.calls
    report = Commissioner(transport).ensure(4, tmp_path / 'result', execute=True)
    assert report['error_code'] == 'secondary_identity_or_slice_mismatch'
    assert not any(s == START for s, _ in transport.calls)


def test_unknown_start_is_not_retried_or_reported_as_recovered(tmp_path):
    transport = Transport(ambiguous_start=True)
    path = tmp_path / 'result'
    report = Commissioner(transport).ensure(4, path, execute=True)
    assert report['execution_status'] == 'failed'
    assert report['validity_status'] == 'inconclusive'
    assert report['start_attempted']
    assert sum(s == START for s, _ in transport.calls) == 1
    assert 'private ssh diagnostic' not in (path / 'report.json').read_text()


def test_detects_observed_ue_change(tmp_path):
    report = Commissioner(Transport(observed_changed=True)).ensure(4, tmp_path / 'result', execute=True)
    assert report['error_code'] == 'observed_ue_changed_during_commissioning'
    assert report['validity_status'] == 'inconclusive'


def test_rejects_live_slice_different_from_config(tmp_path):
    class WrongSlice(Transport):
        def read(self, node, script, arguments=(), **kwargs):
            result = super().read(node, script, arguments, **kwargs)
            if script == UE_SCRIPT:
                result['subjects'][COMPETING] = result['subjects'][COMPETING].replace('0x000001', '0x000002')
            return result
    report = Commissioner(WrongSlice(active=True)).ensure(4, tmp_path / 'result')
    assert report['error_code'] == 'secondary_slice_not_verified'
    assert report['validity_status'] == 'inconclusive'


def test_healthy_services_and_pcf_mode_never_enable_real_execution(tmp_path):
    path = tmp_path / 'readiness'
    transport = Transport(active=True)
    report = collect(path, transport=transport)
    assert report['execution_status'] == 'completed'
    assert all(c['status'] == 'passed' for c in report['preflight']['checks'])
    assert report['execution_ready'] is False
    assert report['validity_status'] == 'inconclusive'
    assert report['metrics'] is None
    assert report['hypothesis_outcome'] == 'not_evaluated'
    assert 'independent_policy_recovery' in report['blocked_requirements']
    assert not any(s == START for s, _ in transport.calls)
    manifest = json.loads((path / 'manifest.json').read_text())
    for entry in manifest['files']:
        raw = (path / entry['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']
        assert len(raw) == entry['bytes']
    with pytest.raises(FileExistsError):
        collect(path, transport=transport)


def test_unavailable_pcf_cannot_be_treated_as_manual_or_ready(tmp_path):
    class Unavailable(Transport):
        def read(self, node, script, arguments=(), **kwargs):
            if script == PCF_INSPECT:
                raise TimeoutError('secret')
            return super().read(node, script, arguments, **kwargs)
    report = collect(tmp_path / 'readiness', transport=Unavailable())
    assert report['execution_status'] == 'failed'
    assert report['pcf']['mode'] is None
    assert report['execution_ready'] is False
    assert 'secret' not in json.dumps(report)


def test_privileged_transport_passes_password_only_on_stdin(monkeypatch):
    commands, inputs, closed = [], [], []
    class Channel:
        pending = True
        def recv_ready(self): return self.pending
        def recv(self, count):
            self.pending = False
            return b'{"status":"success"}'
        def recv_stderr_ready(self): return False
        def exit_status_ready(self): return True
        def recv_exit_status(self): return 0
        def shutdown_write(self): pass
    channel = Channel()
    class Client:
        def load_system_host_keys(self): pass
        def connect(self, *args, **kwargs): pass
        def exec_command(self, command, **kwargs):
            commands.append(command)
            stdin = SimpleNamespace(write=inputs.append, flush=lambda: None, channel=channel)
            return stdin, SimpleNamespace(channel=channel), None
        def close(self): closed.append(True)
    monkeypatch.setattr('app.laboratory.live.paramiko.SSHClient', Client)
    settings = SimpleNamespace(execution_mode='remote', ssh_port=2222, ue_ssh_port=2226,
                               upf_ssh_port=2223, ssh_strict_host_key=True, testbed_host='localhost',
                               ssh_user='operator', ssh_password='credential-value', ssh_key_path=None)
    result = LabSSH(settings).read('core', PCF_INSPECT, privileged=True)
    assert result == {'status': 'success'}
    assert inputs == ['credential-value\n']
    assert commands[0].startswith("sudo -S -p '' timeout --kill-after=2 18 ")
    assert 'credential-value' not in commands[0]
    assert closed == [True]


def test_worker_readiness_never_initializes_or_claims_queue(monkeypatch):
    from app.laboratory import worker
    monkeypatch.setattr('sys.argv', ['worker', '--check-real'])
    monkeypatch.setattr('app.laboratory.real_readiness.run_check', lambda index: 2)
    def forbidden(): raise AssertionError('Readiness must not access the execution queue')
    monkeypatch.setattr(worker, 'repository', forbidden)
    with pytest.raises(SystemExit) as result:
        worker.main()
    assert result.value.code == 2
