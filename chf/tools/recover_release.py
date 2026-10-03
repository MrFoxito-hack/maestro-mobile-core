"""Inspect native journals; explicitly replay ONLY a complete durable Release.

No quota is invented, no Update/Create is automatically replayed and no open
PFCP context is administratively forgiven. Requires the producer's exclusive
file lock to have been released. Linux deployment tool; summaries hide SUPIs.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, ResponseReceived, StreamEnded, StreamReset, ConnectionTerminated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import ChargingDataRequest

MAX_JOURNAL = 4 * 1024 * 1024
COLLECTION = '/nchf-convergedcharging/v3/chargingdata/'


def parse_journal(data):
    if not data or len(data) > MAX_JOURNAL or not data.endswith(b'\n'):
        raise ValueError('Empty, oversized or truncated journal; manual investigation required')
    entries = [json.loads(line) for line in data.splitlines()]
    reports, confirmed = {}, set()
    pending = None
    queued = False
    completed = False
    no_user_plane = False
    last_sequence = None
    for entry in entries:
        event = entry['event']
        if event == 'pfcp_usage':
            report = json.loads(entry['body'])
            sequence, ul, dl = (report[k] for k in ('sequence', 'uplink', 'downlink'))
            if any(type(x) is not int or x < 0 for x in (sequence, ul, dl)):
                raise ValueError('Invalid PFCP volume evidence')
            if sequence in reports and reports[sequence] != (ul, dl):
                raise ValueError('Conflicting PFCP retransmission')
            if sequence not in reports and sequence != len(reports):
                raise ValueError('PFCP sequence gap; no automatic settlement')
            reports[sequence] = (ul, dl)
        elif event == 'invalid_final_usage':
            raise ValueError('Final PFCP evidence unavailable')
        elif event == 'release_queued':
            queued = True
        elif event == 'no_user_plane':
            if reports:
                raise ValueError('Pre-N4 cancellation conflicts with recorded usage')
            no_user_plane = True
        elif event == 'request':
            if pending is not None:
                raise ValueError('Previous invocation has no durable confirmation')
            request = ChargingDataRequest.model_validate_json(entry['body'])
            sequence = request.invocationSequenceNumber
            if last_sequence is not None and sequence != last_sequence + 1:
                raise ValueError('Invocation sequence gap')
            if entry['invocationSequenceNumber'] != sequence:
                raise ValueError('Journal/request sequence differs')
            for unit in request.multipleUnitUsage:
                for used in unit.usedUnitContainer:
                    if reports.get(used.localSequenceNumber) != (used.uplinkVolume, used.downlinkVolume):
                        raise ValueError('Request volume differs from durable PFCP evidence')
            pending = (entry, request)
            last_sequence = sequence
        elif event in ('response_valid', 'recovery_release_confirmed'):
            if pending is None or entry['invocationSequenceNumber'] != last_sequence:
                raise ValueError('Response without matching request')
            uri = pending[0]['uri']
            expected = 204 if uri.endswith('/release') else 200 if uri.endswith('/update') else 201
            if entry['status'] != expected:
                raise ValueError('Unexpected success status')
            if expected == 204 and entry.get('body'):
                raise ValueError('Release response must have no body')
            for unit in pending[1].multipleUnitUsage:
                confirmed.update(u.localSequenceNumber for u in unit.usedUnitContainer)
            completed = expected == 204
            pending = None
        elif event not in ('response_invalid', 'reconciliation_required', 'shutdown_pending'):
            raise ValueError('Unknown journal event; review version before recovery')
    if completed:
        if pending or set(reports) != confirmed:
            raise ValueError('Usage remains after a claimed terminal confirmation')
        return {'state': 'closed', 'reports': len(reports)}, None
    if not pending or not pending[0]['uri'].endswith('/release') or not queued:
        return {'state': 'manual_reconciliation_required', 'reports': len(reports)}, None
    entry, request = pending
    if not reports and not no_user_plane:
        raise ValueError('Missing terminal PFCP evidence or pre-N4 cancellation')
    usage = [u.localSequenceNumber for unit in request.multipleUnitUsage for u in unit.usedUnitContainer]
    if len(set(usage)) != len(usage) or set(usage) != set(reports) - confirmed:
        raise ValueError('Release does not cover exactly the unconfirmed PFCP evidence')
    digest = hashlib.sha256(entry['body'].encode()).hexdigest()
    return {'state': 'release_pending', 'reports': len(reports),
            'invocationSequenceNumber': request.invocationSequenceNumber, 'payload_sha256': digest}, entry


@contextmanager
def locked_journal(path, *, writable=False):
    import fcntl  # Deliberately fails on a platform without POSIX lock semantics.
    fd = os.open(path, (os.O_RDWR | os.O_APPEND if writable else os.O_RDONLY) | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Journal must be a private regular file owned by the recovery user')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = bytearray()
        while len(data) <= MAX_JOURNAL:
            part = os.read(fd, min(65536, MAX_JOURNAL + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        yield fd, bytes(data)
    finally:
        os.close(fd)


def validate_target(uri, base):
    target, configured = urlsplit(uri), urlsplit(base)
    if (target.scheme, target.netloc) != (configured.scheme, configured.netloc):
        raise ValueError('Recovery target differs from explicitly configured CHF origin')
    if configured.path not in ('', '/') or configured.query or configured.fragment:
        raise ValueError('Base URL must contain only the CHF origin')
    if target.username or target.password or target.query or target.fragment or not target.hostname:
        raise ValueError('Unsafe target URI')
    if target.scheme == 'http':
        if not ipaddress.ip_address(target.hostname).is_loopback:
            raise ValueError('Cleartext recovery is restricted to numeric loopback addresses')
    elif target.scheme != 'https':
        raise ValueError('Unsupported transport')
    if not target.path.startswith(COLLECTION) or not target.path.endswith('/release'):
        raise ValueError('Only Nchf Release can be replayed')
    reference = target.path[len(COLLECTION):-len('/release')]
    if str(UUID(reference)) != reference:
        raise ValueError('Invalid resource reference')
    return target


def replay_http2(entry, base, token, *, ca_file=None):
    target = validate_target(entry['uri'], base)
    if len(token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('A nonempty, safe NF credential is required')
    body = entry['body'].encode()
    if len(body) > 262144:
        raise ValueError('Request exceeds supported payload size')
    connection = socket.create_connection((target.hostname, target.port or (443 if target.scheme == 'https' else 80)), timeout=5)
    try:
        if target.scheme == 'https':
            context = ssl.create_default_context(cafile=ca_file)
            context.set_alpn_protocols(['h2'])
            connection = context.wrap_socket(connection, server_hostname=target.hostname)
            if connection.selected_alpn_protocol() != 'h2':
                raise ValueError('CHF did not negotiate HTTP/2')
        h2 = H2Connection(H2Configuration(client_side=True, header_encoding='utf-8'))
        h2.initiate_connection()
        h2.send_headers(1, [(':method', 'POST'), (':scheme', target.scheme),
                           (':authority', target.netloc), (':path', target.path),
                           ('content-type', 'application/json'), ('authorization', 'Bearer ' + token)])
        sent, received, status = 0, 0, None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            while sent < len(body):
                amount = min(h2.local_flow_control_window(1), h2.max_outbound_frame_size, len(body) - sent)
                if not amount:
                    break
                h2.send_data(1, body[sent:sent + amount], end_stream=sent + amount == len(body))
                sent += amount
            connection.sendall(h2.data_to_send())
            packet = connection.recv(65536)
            if not packet:
                raise ValueError('Connection ended before Release confirmation')
            for event in h2.receive_data(packet):
                if isinstance(event, ResponseReceived) and event.stream_id == 1:
                    status = int(dict(event.headers)[':status'])
                elif isinstance(event, DataReceived):
                    received += len(event.data)
                    if received > 262144:
                        raise ValueError('Response exceeds supported size')
                    h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, StreamEnded) and event.stream_id == 1:
                    if status != 204 or received:
                        raise ValueError(f'Release not confirmed (HTTP {status}); journal retained')
                    return
                elif isinstance(event, (StreamReset, ConnectionTerminated)):
                    raise ValueError('HTTP/2 stream ended without confirmation')
        raise TimeoutError('Release confirmation deadline exceeded')
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal', type=Path)
    parser.add_argument('--replay-release', action='store_true')
    parser.add_argument('--base-url')
    parser.add_argument('--token-env', default='SMF_CHF_TOKEN')
    parser.add_argument('--ca-file')
    args = parser.parse_args()
    if args.replay_release and not args.base_url:
        parser.error('--replay-release requires --base-url')
    with locked_journal(args.journal, writable=args.replay_release) as (fd, data):
        summary, entry = parse_journal(data)
        if args.replay_release and entry:
            replay_http2(entry, args.base_url, os.environ.get(args.token_env, ''), ca_file=args.ca_file)
            record = json.dumps({'event': 'recovery_release_confirmed', 'status': 204,
                                 'invocationSequenceNumber': summary['invocationSequenceNumber'],
                                 'payload_sha256': summary['payload_sha256']}).encode() + b'\n'
            with os.fdopen(os.dup(fd), 'ab', buffering=0) as file:
                written = 0
                while written < len(record):
                    count = file.write(record[written:])
                    if not count:
                        raise OSError('Could not persist recovery confirmation')
                    written += count
                os.fsync(file.fileno())
            summary['state'] = 'closed'
        print(json.dumps(summary))
        if summary['state'] != 'closed':
            raise SystemExit(2)


if __name__ == '__main__':
    main()
