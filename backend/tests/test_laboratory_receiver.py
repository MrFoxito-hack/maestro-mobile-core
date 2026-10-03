import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from app.laboratory.receiver_probe import ReceiverProbe


@pytest.fixture
def guard(monkeypatch):
    # The deployed program is Linux-only; its pure state machine is also tested on Windows.
    if sys.platform == 'win32': monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace())
    path = Path(__file__).parents[1] / 'app/laboratory/remote_probe_guard.py'
    spec = importlib.util.spec_from_file_location('isolated_guard', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, 'getpgrp', lambda: 321, raising=False)
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE lease(id INTEGER PRIMARY KEY,token TEXT,boot TEXT,expires_at REAL,status TEXT,process_group INTEGER,intent_at REAL,result TEXT)')
    yield module, db
    db.close()


def test_remote_guard_fences_tokens_expiry_unknown_result_and_active_children(guard, monkeypatch):
    module, db = guard
    token = 'a' * 32
    def invoke(operation, token=token, now=100, alive=False, **args):
        return module.handle(db, {'operation': operation, 'token': token, **args}, clock=lambda: now, boot='boot-a', group_alive=lambda _: alive)
    assert invoke('reserve')['status'] == 'reserved'
    with pytest.raises(ValueError, match='resource_reserved'): invoke('reserve', token='b' * 32)
    with pytest.raises(ValueError, match='ownership_lost'): invoke('measure', token='b' * 32)
    with pytest.raises(ValueError, match='expired'): invoke('measure', now=131)
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **k: b'[{"addr_info":[{"local":"10.45.0.2"}]}]')
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError): invoke('measure', interface='uesimtun0', address='10.45.0.2', expected_sha256='d' * 64)
    assert db.execute('SELECT status FROM lease').fetchone()[0] == 'intent'
    with pytest.raises(ValueError, match='already_attempted'): invoke('measure')
    with pytest.raises(ValueError, match='still_active'): invoke('recover', alive=True)
    assert invoke('recover')['recovery_verified'] is True
    assert invoke('reserve', token='b' * 32)['status'] == 'reserved'
    with pytest.raises(ValueError, match='ownership_lost'): invoke('measure')


def test_remote_probe_verifies_payload_and_only_uses_fixed_curl_target(guard, monkeypatch):
    module, db = guard
    token, payload = 'c' * 32, b'#EXTM3U\n'
    digest = hashlib.sha256(payload).hexdigest()
    module.handle(db, {'operation': 'reserve', 'token': token}, clock=lambda: 1, boot='boot-a')
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **k: b'[{"addr_info":[{"local":"10.45.0.2"}]}]')
    def run(command, **kwargs):
        assert command[-1] == module.URL
        assert '--max-time' in command and '--max-filesize' in command
        assert kwargs['timeout'] == 8 and 'shell' not in kwargs
        return SimpleNamespace(returncode=0, stdout=payload + b'\nMAESTRO_META 200 0.1 0.02 8 10.45.0.2')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.handle(db, {'operation': 'measure', 'token': token, 'interface': 'uesimtun0', 'address': '10.45.0.2', 'expected_sha256': digest}, clock=lambda: 1, boot='boot-a')
    assert result['result']['received_payload_bytes'] == 8
    assert result['result']['qoe_measurement'] is False
    assert 'mos' not in result['result']


def test_receiver_probe_quota_block_never_reserves_or_transfers(tmp_path):
    class Transport:
        calls = []
        def read(self, node, script, args):
            self.calls.append(node)
            if node == 'core': return {'accounts': {'imsi-999700000000001': {'quota_bytes': 10, 'consumed_bytes': 10, 'reserved_bytes': 0}}}
            return {'subjects': {'imsi-999700000000001': 'PDU Session1:\n state: PS-ACTIVE\n apn: internet\n address: 10.45.0.2'},
                    'interfaces': [{'ifname': 'uesimtun0', 'addr_info': [{'local': '10.45.0.2'}]}]}
    transport = Transport()
    result = ReceiverProbe(transport).run('imsi-999700000000001', tmp_path / 'probe')
    assert result['error_code'] == 'insufficient_unreserved_quota'
    assert result['metrics'] is None
    assert transport.calls == ['core', 'ue', 'core']
