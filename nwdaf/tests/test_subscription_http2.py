"""Gate 2: real Hypercorn SBI requests and a real TCP/h2 notification consumer.

The consumer is a protocol test fixture, not a PCF or evidence of Gate 3.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode
import h2.config
import h2.connection
import h2.events
from app.core.h2_client import request
from app.core.contract import Contract, EVENTS
from app.core.database import Database
from app.subscriptions import BASE
from tests.test_subscriptions import body, seed, TOKEN


def test_full_h2_subscription_exchange(tmp_path):
    async def scenario():
        received = asyncio.Queue()
        async def consumer(reader, writer):
            conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False, header_encoding='utf-8'))
            conn.initiate_connection(); writer.write(conn.data_to_send()); await writer.drain()
            content = bytearray()
            try:
                while chunk := await reader.read(65536):
                    for event in conn.receive_data(chunk):
                        if isinstance(event, h2.events.DataReceived):
                            content.extend(event.data)
                            conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        if isinstance(event, h2.events.StreamEnded):
                            await received.put(json.loads(content))
                            conn.send_headers(event.stream_id, [(':status','204')], end_stream=True)
                    writer.write(conn.data_to_send()); await writer.drain()
            finally:
                writer.close(); await writer.wait_closed()

        server = await asyncio.start_server(consumer, '127.0.0.1', 0)
        uri = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/notify'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        database = tmp_path/'h2-subscription.db'
        seed(Database(database))
        env = {**os.environ, 'NWDAF_TOKEN': TOKEN, 'NWDAF_DATABASE':str(database),
               'NWDAF_CALLBACK_URIS':json.dumps([uri])}
        process = subprocess.Popen([sys.executable,'-m','hypercorn','app.main:app','--bind',f'127.0.0.1:{port}'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        root = f'http://127.0.0.1:{port}'
        headers = [('authorization','Bearer '+TOKEN)]
        try:
            deadline = time.monotonic()+20
            while True:
                try:
                    assert (await request(root+'/health', method='GET'))[0] == 200
                    break
                except OSError:
                    assert process.poll() is None and time.monotonic() < deadline
                    await asyncio.sleep(.1)
            params = urlencode({'event-id':'LOAD_LEVEL_INFORMATION',
                'event-filter':json.dumps({'snssais':[{'sst':1,'sd':'000001'}]})})
            status, _, data = await request(root+'/nnwdaf-analyticsinfo/v1/analytics?'+params, method='GET', headers=headers)
            assert status == 200
            Contract().validate('AnalyticsData', json.loads(data))
            status, response_headers, _ = await request(root+BASE, payload=body(uri), headers=headers)
            assert status == 201
            location = response_headers['location']
            notification = await asyncio.wait_for(received.get(), timeout=10)
            Contract().validate('NnwdafEventsSubscriptionNotification', notification, file=EVENTS)
            assert notification['subscriptionId'] == location.rsplit('/',1)[1]
            assert notification['eventNotifications'][0]['sliceLoadLevelInfo']['loadLevelInformation'] == 87
            assert (await request(root+location, method='PUT', payload=body(uri), headers=headers))[0] == 200
            assert (await request(root+location, method='DELETE', headers=headers))[0] == 204
        finally:
            process.terminate()
            await asyncio.to_thread(process.wait, 10)
            server.close(); await server.wait_closed()
    asyncio.run(scenario())
