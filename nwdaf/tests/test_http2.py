"""Real h2c frames over TCP, no HTTP/1.1 fallback and no ASGI mock."""
import json
import os
import socket
import subprocess
import sys
import time
import h2.connection
import h2.config
import h2.events


def test_h2c_loopback(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    env = {**os.environ, 'NWDAF_TOKEN': 'test-token-12345678901234567890',
           'NWDAF_DATABASE': str(tmp_path/'h2.sqlite3')}
    process = subprocess.Popen([sys.executable, '-m', 'hypercorn', 'app.main:app',
                               '--bind', f'127.0.0.1:{port}'], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic()+20
        while True:
            try:
                sock = socket.create_connection(('127.0.0.1', port), timeout=2)
                break
            except OSError:
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(.1)
        with sock:
            sock.settimeout(5)
            conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True, header_encoding='utf-8'))
            conn.initiate_connection()
            conn.send_headers(1, [(':method','GET'),(':scheme','http'),(':authority',f'127.0.0.1:{port}'),(':path','/health')], end_stream=True)
            sock.sendall(conn.data_to_send())
            body = b''; status = None; ended = False
            while not ended:
                data = sock.recv(65535)
                assert data, 'Connection closed before response'
                for event in conn.receive_data(data):
                    if isinstance(event, h2.events.ResponseReceived): status = dict(event.headers)[':status']
                    if isinstance(event, h2.events.DataReceived):
                        body += event.data
                        conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    if isinstance(event, h2.events.StreamEnded): ended = True
                outgoing = conn.data_to_send()
                if outgoing: sock.sendall(outgoing)
            assert status == '200'
            assert json.loads(body)['closed_loop_enabled'] is False
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
