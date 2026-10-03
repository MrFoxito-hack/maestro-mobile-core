"""Durable optional suggestions. An AI failure never changes experiment state."""
import asyncio
import hashlib
import json
import time
import uuid
from pydantic import Field
from app.core.config import get_settings
from app.laboratory.repository import canonical, stamp, ConflictError
from app.laboratory.research import Research
from app.laboratory.schemas import StrictModel
from .context import build
from .model_client import ModelClient, ModelUnavailable
from .proposals import validate

class InvestigationCreate(StrictModel):
    dataset_id: str = Field(pattern=r'^[a-f0-9]{32}$')

class SuggestRequest(StrictModel):
    question: str = Field(default='Propone una hipotesis alternativa y una revision de evidencia que la distinga.', min_length=10, max_length=500)

class Investigations:
    def __init__(self, store, *, client=None, enabled=None, clock=time.time):
        self.store, self.clock = store, clock
        self.client = client or ModelClient()
        self.enabled = get_settings().laboratory_ai_enabled if enabled is None else enabled

    def create(self, experiment_id, user, key, body):
        context = build(self.store, experiment_id, body.dataset_id, user)
        return Research(self.store).add_evidence(experiment_id, user, key, 'investigation_context', context)

    def owned(self, db, identity, user):
        row = db.execute("SELECT * FROM lab_evidence WHERE id=? AND kind='investigation_context'", (identity,)).fetchone()
        if not row: raise KeyError('Investigacion no encontrada.')
        self.store.owned(db, row['experiment_id'], user)
        verified = Research.verified_evidence(row)
        context = verified['document']
        if hashlib.sha256(canonical(context['model_context']).encode()).hexdigest() != context['context_sha256']:
            raise ConflictError('Contexto alterado.')
        return verified

    def detail(self, identity, user):
        with self.store.connect() as db:
            investigation = self.owned(db, identity, user)
            history = []
            for row in db.execute('SELECT id,status,response,expires_at,created_at FROM lab_ai_requests WHERE investigation_id=? ORDER BY created_at DESC LIMIT 30', (identity,)):
                if row['response']: history.append(json.loads(row['response']))
                else: history.append({'id': row['id'], 'status': 'pending' if row['expires_at'] > self.clock() else 'interrupted'})
        return {'id': identity, 'context': investigation['document'], 'history': history, 'enabled': self.enabled}

    def reserve(self, identity, user, key, body):
        def action(db):
            investigation = self.owned(db, identity, user)
            count = db.execute('SELECT COUNT(*) FROM lab_ai_requests WHERE investigation_id=?', (identity,)).fetchone()[0]
            if count >= 100: raise ConflictError('Limite de consultas de esta investigacion alcanzado.')
            total = db.execute('SELECT COUNT(*) FROM lab_ai_requests q JOIN lab_evidence e ON e.id=q.investigation_id WHERE e.experiment_id=?',
                               (investigation['experiment_id'],)).fetchone()[0]
            if total >= 300: raise ConflictError('Limite de consultas de este expediente alcanzado.')
            result = {'id': uuid.uuid4().hex}
            db.execute('INSERT INTO lab_ai_requests VALUES(?,?,?,?,?,?,?,NULL)',
                       (result['id'], identity, body.question, 'pending', None, self.clock()+120, stamp()))
            return result
        return self.store.mutate(user, 'ai-suggest:'+identity, key, body.model_dump(), action)['id']

    def finish(self, request_id, response, *, release=True):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE lab_ai_requests SET status=?,response=?,finished_at=? WHERE id=? AND status='pending'",
                       (response['status'], canonical(response), stamp(), request_id))
            if release: db.execute('DELETE FROM lab_ai_slots WHERE request_id=?', (request_id,))

    def begin(self, identity, user, key, body):
        request_id = self.reserve(identity, user, key, body)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            investigation = self.owned(db, identity, user)
            row = db.execute('SELECT * FROM lab_ai_requests WHERE id=?', (request_id,)).fetchone()
            if row['response']: return request_id, investigation, json.loads(row['response']), False
            existing = db.execute('SELECT * FROM lab_ai_slots WHERE expires_at>?', (self.clock(),)).fetchone()
            if existing and existing['request_id'] == request_id:
                return request_id, investigation, {'id': request_id, 'status': 'pending'}, False
            code = None
            if row['expires_at'] <= self.clock(): code = 'interrupted'
            elif not self.enabled: code = 'disabled'
            elif existing: code = 'busy'
            elif db.execute("SELECT 1 FROM lab_executions WHERE mode='real' AND status NOT IN ('completed','failed','cancelled')").fetchone(): code = 'acquisition_active'
            elif db.execute("SELECT 1 FROM lab_preflights WHERE status='collecting' AND expires_at>?", (self.clock(),)).fetchone(): code = 'acquisition_active'
            if code is None:
                db.execute('INSERT OR REPLACE INTO lab_ai_slots VALUES(1,?,?)', (request_id, self.clock()+120))
        if code:
            response = {'id': request_id, 'status': 'unavailable', 'reason': code,
                        'message': 'Asistente de IA fuera de linea' if code in ('disabled','interrupted') else 'Asistente ocupado; el laboratorio sigue disponible.'}
            return request_id, investigation, response, True
        return request_id, investigation, None, False

    def authorize(self, identity, user):
        with self.store.connect() as db: self.owned(db, identity, user)

    async def suggest(self, identity, user, key, body):
        # WAL commits and filesystem sync can stall for seconds on Windows.
        # Keep all durable work off the API event loop, including final audit.
        request_id, investigation, early, persist = await asyncio.to_thread(self.begin, identity, user, key, body)
        if early is not None:
            if persist: await asyncio.to_thread(self.finish, request_id, early)
            return early
        context = investigation['document']
        response = {'id': request_id, 'context_sha256': context['context_sha256']}
        release = True
        try:
            raw, timing = await self.client.generate(context['model_context'], body.question)
            response['runtime'] = timing
            try: proposal = validate(raw, context['model_context'])
            except ValueError as error:
                import re
                code = str(error)
                response.update(status='unavailable', reason='invalid_model_output', message='La propuesta no supera la validacion de evidencia. No se muestra como resultado.')
                response['validation_code'] = code if re.fullmatch('[a-z_]{1,80}', code) else 'schema_invalid'
            else:
                response.update(status='completed', proposal=proposal.model_dump(), runtime=timing,
                                advisory_only=True, commands_executed=False)
        except ModelUnavailable as error:
            response.update(status='unavailable', reason=error.code, message='Asistente de IA fuera de linea')
            # A timed-out connection is not proof that GPU computation stopped.
            # Preserve an exclusion window instead of releasing to a Core run.
            release = error.code not in ('timeout','response_limit','incomplete_stream')
        except asyncio.CancelledError:
            response.update(status='cancelled', reason='request_cancelled')
            await asyncio.to_thread(self.finish, request_id, response, release=False)
            raise
        except Exception:
            response.update(status='unavailable', reason='runtime_error', message='Asistente de IA fuera de linea')
            release = False
        await asyncio.to_thread(self.finish, request_id, response, release=release)
        # Recheck authorization before exposing an asynchronous result.
        await asyncio.to_thread(self.authorize, identity, user)
        return response
