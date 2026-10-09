"""A transport lease must never manufacture a native coverage assertion."""
from copy import deepcopy
from infra.policy_authority import native_transport
from infra.policy_authority.native_adapter import Coordinator


def test_live_lease_preserves_negative_native_coverage(monkeypatch, tmp_path):
    raw = {'writer_fenced': False, 'policy_complete': False, 'pending_native': 0,
           'fencing': {'enabled': True, 'failed': False, 'leased': True}, 'sessions': []}
    transport = native_transport.Transport.__new__(native_transport.Transport)
    transport.config = {'nfs': {'pcf': {'socket': '/run/maestro-observer-pcf/observe.sock'}}}
    monkeypatch.setattr(native_transport, 'native_request',
                        lambda *_: {'status': 'success', 'data': deepcopy(raw)})
    observed, _ = transport.request('pcf')
    assert observed == raw
    transport.observe = lambda: {nf: (deepcopy(raw), 100) for nf in native_transport.NFS}
    coordinator = Coordinator(transport, tmp_path/'adapter.db', boot='boot')
    assert all(row == raw for row in coordinator.observe().values())
    assert 'all_writers_fenced' not in coordinator.capabilities()['capabilities']
