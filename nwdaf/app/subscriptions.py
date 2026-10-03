"""Durable periodic slice subscriptions: bounded, revisioned, at-least-once delivery."""
import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from h2.exceptions import ProtocolError
from uuid import uuid4
from app.core.h2_client import callback_address, request
from app.core.contract import EVENTS

log = logging.getLogger(__name__)

BASE = '/nnwdaf-eventssubscription/v1/subscriptions'


class Subscriptions:
    def __init__(self, database, contract, allowlist=()):
        self.db, self.contract = database, contract
        self.allowlist = frozenset(allowlist)
        for uri in self.allowlist: callback_address(uri)
        with self.db.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL,
                next_due REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS notification_outbox (
                id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                revision INTEGER NOT NULL, uri TEXT NOT NULL, payload TEXT NOT NULL,
                expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_due REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', last_status INTEGER);
              CREATE INDEX IF NOT EXISTS outbox_due ON notification_outbox(status,next_due);
            ''')

    def save(self, model, subscription_id=None):
        body = model.model_dump(exclude_none=True)
        if body['notificationURI'] not in self.allowlist:
            raise ValueError('Callback URI not allowed by operator')
        self.contract.validate('NnwdafEventsSubscription', body, file=EVENTS)
        identifier = subscription_id or str(uuid4())
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if subscription_id:
                changed = db.execute('UPDATE subscriptions SET body=?,revision=revision+1,next_due=? WHERE id=? AND deleted=0',
                                     (json.dumps(body), time.time(), identifier)).rowcount
                if not changed: raise LookupError('Subscription not found')
                db.execute("UPDATE notification_outbox SET status='cancelled' WHERE subscription_id=? AND status='pending'", (identifier,))
            else:
                if db.execute('SELECT COUNT(*) FROM subscriptions WHERE deleted=0').fetchone()[0] >= 1000:
                    raise OverflowError('Subscription limit reached')
                db.execute('INSERT INTO subscriptions(id,body,revision,next_due) VALUES(?,?,1,?)',
                           (identifier, json.dumps(body), time.time()))
        return identifier, body

    def delete(self, identifier):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('UPDATE subscriptions SET deleted=1,revision=revision+1 WHERE id=? AND deleted=0', (identifier,)).rowcount:
                raise LookupError('Subscription not found')
            db.execute("UPDATE notification_outbox SET status='cancelled' WHERE subscription_id=? AND status='pending'", (identifier,))

    def enqueue(self):
        now = time.time()
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM subscriptions WHERE deleted=0 AND next_due<=? LIMIT 100', (now,)).fetchall()
            for row in rows:
                body = json.loads(row['body']); event = body['eventSubscriptions'][0]
                db.execute('UPDATE subscriptions SET next_due=? WHERE id=?', (now+event['repetitionPeriod'], row['id']))
                # One pending notification per revision: retries do not create an unbounded queue.
                if db.execute("SELECT 1 FROM notification_outbox WHERE subscription_id=? AND revision=? AND status='pending'", (row['id'],row['revision'])).fetchone(): continue
                target = json.dumps(event['snssaia'][0], sort_keys=True)
                result = db.execute('SELECT sbi_payload FROM analyses WHERE event=? AND target=? AND created>=? ORDER BY created DESC,id DESC LIMIT 1',
                                    ('LOAD_LEVEL_INFORMATION', target, now-300)).fetchone()
                if not result or not result[0]: continue
                payload = json.loads(result[0]); expiry = datetime.fromisoformat(payload['expiry']).timestamp()
                if expiry <= now: continue
                notification = {'subscriptionId': row['id'], 'eventNotifications': [{
                    'event':'SLICE_LOAD_LEVEL', 'timeStampGen':payload['timeStampGen'], 'expiry':payload['expiry'],
                    'sliceLoadLevelInfo':payload['sliceLoadLevelInfos'][0]}]}
                self.contract.validate('NnwdafEventsSubscriptionNotification', notification, file=EVENTS)
                db.execute('INSERT INTO notification_outbox(id,subscription_id,revision,uri,payload,expires,next_due) VALUES(?,?,?,?,?,?,?)',
                           (str(uuid4()),row['id'],row['revision'],body['notificationURI'],json.dumps(notification),expiry,now))

    async def dispatch_one(self):
        # Exactly one dispatcher per configured Hypercorn worker (workers=1).
        with self.db.connect() as db:
            db.execute("UPDATE notification_outbox SET status='expired' WHERE status='pending' AND expires<=?", (time.time(),))
            row = db.execute("SELECT o.* FROM notification_outbox o JOIN subscriptions s ON s.id=o.subscription_id WHERE o.status='pending' AND o.next_due<=? AND s.deleted=0 AND s.revision=o.revision ORDER BY o.next_due LIMIT 1", (time.time(),)).fetchone()
        if not row: return
        status = None
        try:
            if row['uri'] not in self.allowlist: raise ValueError('Callback no longer allowed')
            status, _, _ = await request(row['uri'], payload=json.loads(row['payload']))
        except (OSError, ValueError, TimeoutError, ProtocolError):
            pass
        attempts = row['attempts']+1
        # Protocol acknowledgement is 204; redirects never leave the allowlist.
        state = 'delivered' if status == 204 else 'failed' if attempts >= 6 or (status and 400 <= status < 500 and status != 429) else 'pending'
        with self.db.connect() as db:
            db.execute("UPDATE notification_outbox SET status=?,attempts=?,next_due=?,last_status=? WHERE id=? AND status='pending'",
                       (state,attempts,time.time()+min(60,2**attempts),status,row['id']))

    async def run(self):
        while True:
            try:
                self.enqueue()
                await self.dispatch_one()
            except Exception as exc:
                log.warning("Notification worker cycle suppressed transient error: %s", exc)
            await asyncio.sleep(.25)
