"""Real TCP + h2c prior-knowledge exchange. Not an Open5GS E2E test."""
import json
import os
import socket
import subprocess
import sys
import time

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, ResponseReceived, StreamEnded

from conftest import ROOT, SMF_ID, SMF_TOKEN, charging_request


def test_nchf_over_real_http2(settings, account):
    with socket.socket() as allocator:
        allocator.bind(('127.0.0.1', 0))
        port = allocator.getsockname()[1]
    env = {**os.environ, 'CHF_DATABASE_PATH': str(settings.database_path),
           'CHF_SBI_TOKENS': json.dumps({SMF_ID: SMF_TOKEN}), 'CHF_SBI_LAB_NO_AUTH': 'false',
           'CHF_DEFAULT_GRANT_BYTES': '1000'}
    process = subprocess.Popen([sys.executable, '-m', 'hypercorn', 'app.main:app', '--bind', f'127.0.0.1:{port}'],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, 'HTTP/2 server startup failed'
            try:
                connection = socket.create_connection(('127.0.0.1', port), timeout=1)
                break
            except (ConnectionRefusedError, TimeoutError):
                assert time.monotonic() < deadline, 'HTTP/2 server readiness timeout'
                time.sleep(0.05)
        with connection:
            connection.settimeout(5)
            h2 = H2Connection(H2Configuration(client_side=True, header_encoding='utf-8'))
            h2.initiate_connection()
            headers = {}
            def exchange(path, payload):
                stream = h2.get_next_available_stream_id()
                data = json.dumps(payload).encode()
                h2.send_headers(stream, [(':method', 'POST'), (':scheme', 'http'),
                    (':authority', f'127.0.0.1:{port}'), (':path', path), ('content-type', 'application/json'),
                    ('authorization', 'Bearer ' + SMF_TOKEN), ('content-length', str(len(data)))])
                h2.send_data(stream, data, end_stream=True)
                connection.sendall(h2.data_to_send())
                body = b''
                while True:
                    incoming = connection.recv(65536)
                    assert incoming, 'HTTP/2 server disconnected'
                    events = h2.receive_data(incoming)
                    for event in events:
                        if isinstance(event, ResponseReceived) and event.stream_id == stream:
                            headers[stream] = dict(event.headers)
                        elif isinstance(event, DataReceived) and event.stream_id == stream:
                            body += event.data
                            h2.acknowledge_received_data(event.flow_controlled_length, stream)
                        elif isinstance(event, StreamEnded) and event.stream_id == stream:
                            return headers[stream], body
                    pending = h2.data_to_send()
                    if pending:
                        connection.sendall(pending)
            response, body = exchange(ROOT, charging_request(1))
            assert response[':status'] == '201'
            assert json.loads(body)['multipleUnitInformation'][0]['grantedUnit']['totalVolume'] == 1000
            resource = response['location']
            response, _ = exchange(resource + '/update', charging_request(2, used=100))
            assert response[':status'] == '200'
            response, body = exchange(resource + '/release', charging_request(3, used=200, requested=0))
            assert response[':status'] == '204' and body == b''
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
