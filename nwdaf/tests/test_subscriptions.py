import asyncio
from datetime import datetime, timezone, timedelta
import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.database import Database
from app.core.contract import Contract
from app.models import Subscription
from app.subscriptions import Subscriptions, BASE

URI = 'http://127.0.0.1:19090/notify'
TOKEN = 'subscription-test-token-1234567890'


def body(uri=URI):
    return {'notificationURI': uri, 'eventSubscriptions': [{'event': 'SLICE_LOAD_LEVEL',
        'snssaia': [{'sst': 1, 'sd': '000001'}], 'notificationMethod': 'PERIODIC', 'repetitionPeriod': 60}]}


def seed(db):
    now = datetime.now(timezone.utc)
    payload = {'timeStampGen': now.isoformat(), 'expiry': (now+timedelta(seconds=120)).isoformat(),
        'sliceLoadLevelInfos': [{'loadLevelInformation': 87, 'snssais': [{'sst':1,'sd':'000001'}]}]}
    db.record('LOAD_LEVEL_INFORMATION', json.dumps({'sst':1,'sd':'000001'},sort_keys=True), {'fixture':1}, {}, payload)


def test_subscription_lifecycle_and_allowlist(tmp_path):
    with TestClient(create_app(tmp_path/'api.db', TOKEN, [URI])) as client:
        headers = {'Authorization': 'Bearer '+TOKEN}
        assert client.post(BASE, json=body()).status_code == 401
        assert client.post(BASE, headers=headers, json=body('http://127.0.0.1:27017/')).status_code == 400
        response = client.post(BASE, headers=headers, json=body())
        assert response.status_code == 201, response.text
        location = response.headers['location']
        assert client.put(location, headers=headers, json=body()).status_code == 200
        assert client.delete(location, headers=headers).status_code == 204
        assert client.delete(location, headers=headers).status_code == 404
        assert client.put(location, headers=headers, json=body()).status_code == 404


def test_durable_retry_and_revision_cancellation(tmp_path, monkeypatch):
    db = Database(tmp_path/'retry.db'); seed(db)
    service = Subscriptions(db, Contract(), [URI])
    identifier, _ = service.save(Subscription.model_validate(body()))
    service.enqueue(); service.enqueue()
    async def unavailable(*args, **kwargs): return 503, {}, b''
    monkeypatch.setattr('app.subscriptions.request', unavailable)
    asyncio.run(service.dispatch_one())
    with db.connect() as conn:
        rows = conn.execute('SELECT * FROM notification_outbox').fetchall()
        assert len(rows) == 1
        assert rows[0]['attempts'] == 1 and rows[0]['status'] == 'pending'
        conn.execute('UPDATE notification_outbox SET next_due=0')
    restarted = Subscriptions(Database(db.path), Contract(), [URI])
    async def accept(*args, **kwargs): return 204, {}, b''
    monkeypatch.setattr('app.subscriptions.request', accept)
    asyncio.run(restarted.dispatch_one())
    with db.connect() as conn:
        assert conn.execute('SELECT status FROM notification_outbox').fetchone()[0] == 'delivered'
        conn.execute('UPDATE subscriptions SET next_due=0')
    restarted.enqueue()
    restarted.save(Subscription.model_validate(body()), identifier)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notification_outbox WHERE status='pending'").fetchone()[0] == 0
    restarted.delete(identifier)
    with pytest.raises(LookupError): restarted.delete(identifier)
