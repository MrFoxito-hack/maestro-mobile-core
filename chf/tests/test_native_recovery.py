import json
import os

import pytest

from conftest import charging_request
from tools.recover_release import locked_journal, parse_journal, validate_target

BASE = 'http://127.0.0.1:8081'
RESOURCE = BASE + '/nchf-convergedcharging/v3/chargingdata/00100000-0000-4000-8000-000000000001'


def journal():
    create = charging_request(1)
    release = charging_request(2, requested=0)
    release['multipleUnitUsage'][0]['usedUnitContainer'] = [
        {'localSequenceNumber': 0, 'totalVolume': 12, 'uplinkVolume': 5, 'downlinkVolume': 7}]
    return [
        {'event': 'request', 'invocationSequenceNumber': 1, 'uri': RESOURCE.rsplit('/', 1)[0], 'body': json.dumps(create)},
        {'event': 'response_valid', 'invocationSequenceNumber': 1, 'status': 201},
        {'event': 'pfcp_usage', 'body': json.dumps({'sequence': 0, 'uplink': 5, 'downlink': 7})},
        {'event': 'release_queued'},
        {'event': 'request', 'invocationSequenceNumber': 2, 'uri': RESOURCE + '/release', 'body': json.dumps(release)},
    ]


def encode(entries):
    return b''.join(json.dumps(e).encode() + b'\n' for e in entries)


def test_exact_pending_release_and_durable_confirmation():
    entries = journal()
    state, request = parse_journal(encode(entries))
    assert state['state'] == 'release_pending' and state['reports'] == 1
    assert request['body'] == entries[-1]['body']  # Never reserialize/resequence a retry.
    assert 'imsi' not in json.dumps(state)
    entries.append({'event': 'recovery_release_confirmed', 'status': 204, 'invocationSequenceNumber': 2})
    state, request = parse_journal(encode(entries))
    assert state['state'] == 'closed' and request is None


@pytest.mark.parametrize('mutation', ['truncated', 'conflict', 'gap', 'missing', 'invalid-final', 'bad-response'])
def test_recovery_refuses_incomplete_or_inconsistent_evidence(mutation):
    entries = journal()
    if mutation == 'conflict':
        entries.insert(3, {'event': 'pfcp_usage', 'body': json.dumps({'sequence': 0, 'uplink': 6, 'downlink': 7})})
    elif mutation == 'gap':
        entries[2]['body'] = json.dumps({'sequence': 1, 'uplink': 5, 'downlink': 7})
    elif mutation == 'missing':
        entries.pop(2)
    elif mutation == 'invalid-final':
        entries.insert(3, {'event': 'invalid_final_usage'})
    elif mutation == 'bad-response':
        entries.append({'event': 'response_valid', 'status': 200, 'invocationSequenceNumber': 2})
    data = encode(entries)
    if mutation == 'truncated':
        data = data[:-1]
    with pytest.raises(ValueError):
        parse_journal(data)


def test_open_session_is_not_automatically_forgiven():
    state, request = parse_journal(encode(journal()[:2]))
    assert state['state'] == 'manual_reconciliation_required' and request is None


@pytest.mark.parametrize('uri,base', [
    (RESOURCE + '/release', 'http://127.0.0.2:8081'),
    (RESOURCE + '/update', BASE),
    (RESOURCE + '/release?redirect=admin', BASE),
    (RESOURCE.replace('127.0.0.1', '192.0.2.1') + '/release', BASE.replace('127.0.0.1', '192.0.2.1')),
])
def test_recovery_does_not_forward_credentials_to_other_targets(uri, base):
    with pytest.raises(ValueError):
        validate_target(uri, base)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX lock tested on deployment VM')
def test_native_owner_lock_and_permissions(tmp_path):
    path = tmp_path / 'journal'
    path.write_bytes(encode(journal()))
    path.chmod(0o600)
    with locked_journal(path):
        with pytest.raises(BlockingIOError):
            with locked_journal(path, writable=True):
                pytest.fail('Concurrent accounting owner must not acquire lock')
    path.chmod(0o644)
    with pytest.raises(ValueError):
        with locked_journal(path):
            pytest.fail('Public journal must not be accepted')
