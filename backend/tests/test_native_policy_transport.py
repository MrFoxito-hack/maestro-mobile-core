from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from infra.policy_authority import native_transport
from infra.policy_authority.native_relay import Relay


def test_deadline_cannot_be_extended_by_reply_or_delivery_delay(monkeypatch):
    transport = native_transport.Transport.__new__(native_transport.Transport)
    # Remote measurement 9s occurred at Core 100.2s, received at Core 100.8s.
    # Core expires at 131s; the safe remote deadline corresponds to 130.4s.
    monkeypatch.setattr(native_transport.time, 'monotonic_ns', lambda: 101_000_000_000)
    transport.observe = lambda: {nf: ({'measured_at_ns': '9000000000'}, 100_800_000_000)
                                for nf in native_transport.NFS}
    requests = []

    def request(nf, command):
        requests.append((nf, command))
        return {'fencing_token': '7'}, 102_000_000_000

    transport.request = request
    transport.fence(token=7, boot='01234567-89ab-cdef-0123-456789abcdef', expires=131)
    assert {nf for nf, _ in requests} == native_transport.NFS
    for _, command in requests:
        operation, token, deadline, boot = command.split()
        assert operation == 'fence-at-v1' and token == '7'
        actual_core_expiry = int(deadline) - 9_000_000_000 + 100_200_000_000
        assert actual_core_expiry <= 131_000_000_000
        assert actual_core_expiry == 130_400_000_000


def test_explicit_release_does_not_require_live_clock_samples(monkeypatch):
    transport = native_transport.Transport.__new__(native_transport.Transport)
    monkeypatch.setattr(native_transport.time, 'monotonic_ns', lambda: 100_000_000_000)
    requests = []

    def request(nf, command):
        requests.append(command)
        return {'fencing_token': '8'}, 100_000_000_000

    transport.request = request
    transport.fence(token=8, boot='01234567-89ab-cdef-0123-456789abcdef', expires=100)
    assert len(requests) == 7 and all(command.startswith('fence-v1 8 0 ') for command in requests)


def test_partial_remote_fence_failure_is_not_reported_as_success(monkeypatch):
    transport = native_transport.Transport.__new__(native_transport.Transport)
    monkeypatch.setattr(native_transport.time, 'monotonic_ns', lambda: 100_000_000_000)

    def request(nf, command):
        if nf == 'upf3':
            raise TimeoutError('connection_lost')
        return {'fencing_token': '8'}, 100_000_000_000

    transport.request = request
    with pytest.raises(TimeoutError):
        transport.fence(token=8, boot='01234567-89ab-cdef-0123-456789abcdef', expires=100)


@pytest.mark.parametrize('body', [
    {'nf': 'smf', 'operation': 'observe', 'command': None},
    {'nf': 'upf', 'operation': 'shell', 'command': 'id'},
    {'nf': 'upf', 'operation': 'control', 'command': 'observe\n'},
    {'nf': 'upf', 'operation': 'control', 'command': 'cancel-v1 1\nfence-v1 2 1000 a\n'},
    {'nf': 'upf', 'operation': 'observe', 'command': None, 'path': '/etc/shadow'},
])
def test_relay_never_accepts_other_nfs_paths_or_shells(body):
    relay = Relay({'upf': '/run/maestro-observer-upf/observe.sock'})
    with pytest.raises(ValueError):
        relay.request(body)
