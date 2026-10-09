"""Core-side local/mTLS fan-out with conservative cross-VM lease deadlines."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import math
import ssl
import time

if __package__:
    from .native_relay import MAX_REPLY, native_request
else:
    from native_relay import MAX_REPLY, native_request

NFS = frozenset({'pcf', 'smf', 'smf2', 'smf3', 'upf', 'upf2', 'upf3'})


class Transport:
    def __init__(self, config):
        self.config = config
        if set(config['nfs']) != NFS:
            raise ValueError('seven_nf_transport_required')
        self.tls = ssl.create_default_context(cafile=config['ca'])
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.tls.load_cert_chain(config['certificate'], config['key'])

    def request(self, nf, command=None, *, operation=None):
        target = self.config['nfs'][nf]
        if 'socket' in target:
            if operation is not None:
                raise ValueError('remote_runtime_operation_required')
            if nf not in {'pcf', 'smf', 'smf2', 'smf3'} or target['socket'] != f'/run/maestro-observer-{nf}/observe.sock':
                raise ValueError('unexpected_local_nf_endpoint')
            reply = native_request(target['socket'], command or 'observe\n')
        else:
            connection = http.client.HTTPSConnection(target['address'], target['port'], timeout=6 if operation else 2, context=self.tls)
            try:
                body = json.dumps({'nf': nf, 'operation': operation or ('control' if command else 'observe'), 'command': command})
                connection.request('POST', '/v1/native', body, {'Content-Type': 'application/json'})
                response = connection.getresponse()
                raw = response.read(MAX_REPLY + 1)
                if response.status != 200 or len(raw) > MAX_REPLY:
                    raise ValueError('native_relay_unavailable')
                reply = json.loads(raw)
            finally:
                connection.close()
        received_at = time.monotonic_ns()
        if not isinstance(reply, dict) or reply.get('status') != 'success' or not isinstance(reply.get('data'), dict):
            raise ValueError('native_request_rejected')
        return reply['data'], received_at

    def observe(self):
        started = time.monotonic_ns()
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = {nf: pool.submit(self.request, nf) for nf in sorted(NFS)}
            samples = {nf: future.result() for nf, future in futures.items()}
        if time.monotonic_ns() - started > 1800000000:
            raise ValueError('native_collection_too_slow')
        return samples

    def fence(self, *, token, boot, expires):
        if (type(token) is not int or not 0 < token < 2**64 or
                type(expires) not in (float, int) or not math.isfinite(expires)):
            raise ValueError('invalid_native_fence')
        expires_ns = int(expires * 1e9)
        commands = {}
        if expires_ns <= time.monotonic_ns():
            commands = {nf: f'fence-v1 {token} 0 {boot}\n' for nf in NFS}
        else:
            samples = self.observe()
            for nf, (state, received_at) in samples.items():
                # NF measurement happened no later than receipt on Core.
                # Using that upper bound yields an earlier/equal remote expiry,
                # never a lease extended by network delay. No wall-clock sync.
                remaining = expires_ns - received_at
                measured = int(state['measured_at_ns'])
                if not 0 < remaining <= 60000000000 or measured <= 0:
                    raise ValueError('native_lease_deadline_unavailable')
                commands[nf] = f'fence-at-v1 {token} {measured + remaining} {boot}\n'
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = {nf: pool.submit(self.request, nf, command) for nf, command in commands.items()}
            replies = {nf: future.result()[0] for nf, future in futures.items()}
        if any(int(reply.get('fencing_token', -1)) != token for reply in replies.values()):
            raise ValueError('native_fence_floor_conflict')
        return replies
