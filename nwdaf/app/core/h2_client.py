"""Bounded HTTP/2 prior-knowledge client, including cleartext h2c.

No proxy environment, DNS, redirects, HTTP/1 fallback, or forwarded credentials.
Callers additionally require an exact operator-configured callback URI allowlist.
"""
import asyncio
import ipaddress
import json
import ssl
from urllib.parse import urlsplit
import h2.connection
import h2.config
import h2.events


def callback_address(uri):
    parsed = urlsplit(uri)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Invalid callback URI')
    address = ipaddress.ip_address(parsed.hostname or '')
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError('Invalid callback IP')
    return parsed, str(address), parsed.port or (443 if parsed.scheme == 'https' else 80)


async def request(uri, method='POST', payload=None, headers=(), timeout=5):
    parsed, host, port = callback_address(uri)
    data = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode() if payload is not None else b''
    if len(data) > 32768:
        raise ValueError('HTTP/2 request exceeds bounded client profile')
    tls = None
    if parsed.scheme == 'https':
        tls = ssl.create_default_context(); tls.set_alpn_protocols(['h2'])
    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_connection(host, port, ssl=tls)
        try:
            if tls and writer.get_extra_info('ssl_object').selected_alpn_protocol() != 'h2':
                raise ValueError('Peer did not negotiate HTTP/2')
            conn = h2.connection.H2Connection(config=h2.config.H2Configuration(header_encoding='utf-8'))
            conn.initiate_connection()
            path = parsed.path or '/'
            if parsed.query: path += '?' + parsed.query
            conn.send_headers(1, [(':method', method), (':scheme', parsed.scheme),
                (':authority', parsed.netloc), (':path', path),
                ('content-type','application/json'), ('content-length', str(len(data))), *headers], end_stream=not data)
            # Wait for peer SETTINGS before sending DATA (window/frame limits can shrink).
            writer.write(conn.data_to_send()); await writer.drain()
            offset = 0; body = bytearray(); status = None; response_headers = {}
            while True:
                chunk = await reader.read(65536)
                if not chunk: raise ConnectionError('HTTP/2 response incomplete')
                done = False
                for event in conn.receive_data(chunk):
                    if isinstance(event, h2.events.ResponseReceived):
                        response_headers = dict(event.headers); status = int(response_headers[':status'])
                    elif isinstance(event, h2.events.DataReceived):
                        body.extend(event.data)
                        if len(body) > 1024*1024: raise ValueError('Response too large')
                        conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    elif isinstance(event, h2.events.StreamEnded): done = True
                    elif isinstance(event, (h2.events.StreamReset, h2.events.ConnectionTerminated)):
                        raise ConnectionError('HTTP/2 peer terminated exchange')
                if done: return status, response_headers, bytes(body)
                while offset < len(data):
                    size = min(conn.local_flow_control_window(1), conn.max_outbound_frame_size, len(data)-offset)
                    if size <= 0: break
                    conn.send_data(1, data[offset:offset+size], end_stream=offset+size == len(data)); offset += size
                writer.write(conn.data_to_send()); await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
