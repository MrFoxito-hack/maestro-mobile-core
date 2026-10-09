"""Linux-only independent supervisor; run under the supplied systemd unit.

Native protocol v1 is mandatory. Legacy mml.sock is only read for diagnostics;
it can never satisfy this contract. No service restarts or CHF writes here.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid

from policy_authority import Authority, AuthorityError


class Native:
    def __init__(self, path):
        self.path = path

    def request(self, operation, **fields):
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            # Multi-session restoration includes several N7/N4 round trips
            # and forwarding probes. Keep it bounded inside the 30 s recovery
            # lease; a transport timeout never authorizes a blind retry.
            client.settimeout({'restore': 20, 'apply': 8, 'quiesce': 8,
                               'fence': 8, 'system_fence': 8}.get(operation, 4))
            client.bind('\0maestro-authority-' + uuid.uuid4().hex)
            try:
                client.sendto(json.dumps({'operation': operation, **fields}).encode(), self.path)
                raw = client.recv(262145)
                if len(raw) > 262144:
                    raise AuthorityError('native_response_too_large', 503)
                result = json.loads(raw)
            except (OSError, ValueError):
                raise AuthorityError('native_authority_unavailable', 503) from None
        if result.get('status') != 'success':
            raise AuthorityError('native_rejected_' + operation, 503)
        return result.get('data', {})

    def capabilities(self):
        return self.request('capabilities')

    def checkpoint(self):
        return self.request('checkpoint')

    def fence(self, **args):
        return self.request('fence', **args)

    def apply(self, envelope):
        return self.request('apply', **envelope)

    def quiesce(self, **args):
        return self.request('quiesce', **args)

    def restore(self, **args):
        return self.request('restore', **args)

    def system_status(self):
        return self.request('system_status')

    def system_fence(self, **args):
        return self.request('system_fence', **args)


def dispatch(authority, body):
    op = body.get('operation')
    if op == 'status':
        state = authority.status()
        try:
            authority._capabilities()
            authority._snapshot()
            state['admission_available'] = True
        except AuthorityError as error:
            state.update(admission_available=False, admission_error=error.code)
        return state
    if op == 'acquire':
        return authority.acquire(body.get('owner'), body.get('expected_version'), body.get('ttl', 30))
    if op == 'submit':
        return authority.submit(**{k: body.get(k) for k in ('owner', 'token', 'expected_version', 'action_id', 'command')})
    if op == 'commit':
        return authority.commit(**{k: body.get(k) for k in ('owner', 'token', 'expected_version')})
    raise AuthorityError('authority_operation_not_supported', 422)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='/var/lib/maestro-policy-authority')
    parser.add_argument('--socket', default='/run/maestro-policy-authority/control.sock')
    parser.add_argument('--native', default='/run/maestro-policy-native/control.sock')
    args = parser.parse_args()
    os.umask(0o077)
    state = Path(args.state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    control = Path(args.socket)
    control.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The lock belongs to this authority database, including across daemon restart.
    with (state / 'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        authority = Authority(state / 'authority.sqlite3', Native(args.native),
                              boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        control.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(control))
            os.chmod(control, 0o600)
            server.listen(8)
            server.settimeout(0.5)
            last_error = None
            while True:
                # This runs even when there are no EMS connections. No timer in
                # FastAPI, no replay of a queued mutation after losing a lease.
                try:
                    authority.recover()
                    authority.maintain()
                    last_error = None
                except Exception as error:
                    code = error.code if isinstance(error, AuthorityError) else 'recovery_internal_error'
                    if code != last_error:
                        print(json.dumps({'event': 'recovery_blocked', 'error_code': code}), flush=True)
                    last_error = code
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                with conn:
                    conn.settimeout(2)
                    try:
                        chunks = bytearray()
                        deadline = time.monotonic() + 2
                        while b'\n' not in chunks:
                            if time.monotonic() >= deadline:
                                raise AuthorityError('request_timeout', 408)
                            part = conn.recv(4096)
                            if not part:
                                raise AuthorityError('incomplete_request', 400)
                            chunks.extend(part)
                            if len(chunks) > 16384:
                                raise AuthorityError('request_too_large', 413)
                        body = json.loads(chunks.partition(b'\n')[0])
                        if not isinstance(body, dict):
                            raise AuthorityError('invalid_request', 422)
                        reply = {'status': 'success', 'data': dispatch(authority, body)}
                    except AuthorityError as error:
                        reply = {'status': 'blocked', 'error_code': error.code, 'http_status': error.status}
                    except (ValueError, TypeError):
                        reply = {'status': 'blocked', 'error_code': 'invalid_request', 'http_status': 422}
                    except Exception:
                        reply = {'status': 'blocked', 'error_code': 'authority_internal_error', 'http_status': 503}
                    try:
                        conn.sendall(json.dumps(reply).encode() + b'\n')
                    except OSError:
                        pass  # Durable intent/receipt survives a lost EMS socket.


if __name__ == '__main__':
    main()
