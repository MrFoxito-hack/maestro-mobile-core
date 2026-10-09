"""Restricted mTLS transport to native NF datagram sockets on a UPF VM.

No remote shell, database access, policy synthesis or capability assertions.
The Core adapter uses this service for the two UPFs outside its own VM.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import uuid

if __package__:
    from . import native_runtime
else:
    import native_runtime

MAX_REQUEST = 4096
MAX_REPLY = 196608
CONTROL = re.compile(
    r'(?:bootstrap-close-v1|(?:fence-v1|fence-at-v1) [0-9]{1,20} [0-9]{1,20} [0-9a-f-]{36}'
    r'|prepare-v1 [0-9]{1,20} [0-9]{1,20} [0-9]{1,20} [0-9a-f]{64}'
    r'|system-prepare-v1 [0-9]{1,20} [0-9]{1,20} [0-9a-f]{64}'
    r'|(?:system-open-v1|system-close-v1) [0-9]{1,20}'
    r'|finish-v1 [0-9]{1,20} [0-9]{1,20}|cancel-v1 [0-9]{1,20})\n\Z')


def native_request(path, command, *, timeout=1.5):
    """A pathname reply socket works across UPF network namespaces.

    Its parent is the NF's private RuntimeDirectory. The reply socket is owned
    by the NF so its unprivileged event thread can reply; only root/the NF can
    traverse the directory. Abstract reply addresses are namespace-local.
    """
    path = Path(path)
    endpoint, directory = path.lstat(), path.parent.lstat()
    if (not stat.S_ISSOCK(endpoint.st_mode) or not stat.S_ISDIR(directory.st_mode)
            or endpoint.st_uid != directory.st_uid or stat.S_IMODE(directory.st_mode) != 0o700
            or stat.S_IMODE(endpoint.st_mode) != 0o600):
        raise ValueError('unsafe_native_endpoint_permissions')
    reply_path = path.parent / ('r-' + uuid.uuid4().hex)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.settimeout(timeout)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, MAX_REPLY * 2)
        try:
            client.bind(str(reply_path))
            os.chown(reply_path, endpoint.st_uid, endpoint.st_gid)
            os.chmod(reply_path, 0o600)
            client.sendto(command.encode('ascii'), str(path))
            raw, _, flags, _ = client.recvmsg(MAX_REPLY + 1)
            if flags & socket.MSG_TRUNC or len(raw) > MAX_REPLY:
                raise ValueError('native_reply_truncated')
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('status') not in {'success', 'blocked'}:
                raise ValueError('invalid_native_reply')
            return result
        finally:
            reply_path.unlink(missing_ok=True)


class Relay:
    def __init__(self, endpoints):
        self.endpoints = dict(endpoints)
        if not self.endpoints or not set(self.endpoints) <= {'upf', 'upf2', 'upf3'}:
            raise ValueError('invalid_relay_nf_inventory')
        for nf, path in self.endpoints.items():
            if path != f'/run/maestro-observer-{nf}/observe.sock':
                raise ValueError('unexpected_native_endpoint')

    def request(self, body):
        if not isinstance(body, dict) or set(body) != {'nf', 'operation', 'command'}:
            raise ValueError('invalid_relay_request')
        nf, operation, command = (body[key] for key in ('nf', 'operation', 'command'))
        if nf not in self.endpoints:
            raise ValueError('unknown_native_nf')
        if operation == 'quiesce' and command is None:
            result = native_runtime.owned_traffic_cleanup()
            if nf == 'upf3':
                result.update(native_runtime.xdp_gate_close())
            return {'status': 'success', 'data': result}
        if operation == 'traffic_probe' and command is None:
            observed = native_request(self.endpoints[nf], 'observe\n')
            if observed.get('status') != 'success':
                raise ValueError('native_probe_observation_unavailable')
            return {'status': 'success', 'data': native_runtime.traffic_probe(nf, observed['data'])}
        if operation == 'observe' and command is None:
            command = 'observe\n'
        elif operation != 'control' or not isinstance(command, str) or not CONTROL.fullmatch(command):
            raise ValueError('unsupported_native_operation')
        return native_request(self.endpoints[nf], command)


def handler(relay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def do_POST(self):
            status = 200
            try:
                size = self.headers.get('Content-Length', '')
                if (self.path != '/v1/native' or self.headers.get('Transfer-Encoding') or
                        not size.isdecimal() or not 0 < int(size) <= MAX_REQUEST):
                    raise ValueError('invalid_relay_frame')
                raw = self.rfile.read(int(size))
                if len(raw) != int(size):
                    raise ValueError('incomplete_relay_frame')
                response = relay.request(json.loads(raw))
            except (ValueError, TypeError, KeyError):
                status = 400
                response = {'status': 'blocked', 'error_code': 'invalid_relay_request'}
            except (OSError, native_runtime.subprocess.SubprocessError):
                status = 503
                response = {'status': 'blocked', 'error_code': 'native_endpoint_unavailable'}
            body = json.dumps(response, separators=(',', ':'), allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, *_):
            pass  # Never log cookies or request bodies.

    return Handler


def serve(config):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['certificate'], config['key'])
    context.load_verify_locations(config['ca'])
    context.verify_mode = ssl.CERT_REQUIRED
    class Server(ThreadingHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(3)
            # The worker performs the bounded handshake, not the accept loop.
            return context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False), address

    with Server((config['address'], config['port']), handler(Relay(config['nfs']))) as server:
        server.serve_forever(poll_interval=0.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    serve(json.loads(args.config.read_text()))
