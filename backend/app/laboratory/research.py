"""Scoped read-only preflight, immutable evidence, offline analysis and notebook."""
import hashlib
import io
import json
import time
import uuid
import zipfile

from app.laboratory.analysis import Dataset, analyze
from app.laboratory.evidence_validation import validate_references
from app.laboratory.live import LivePreflight
from app.laboratory.planner import validate_design
from app.laboratory.repository import ConflictError, canonical, stamp
from app.laboratory.runtime import Runtime
from app.laboratory.schemas import Descriptor

MAX_DOCUMENT_BYTES = 2_000_000
MAX_EXPERIMENT_BYTES = 10_000_000


class Research:
    def __init__(self, store, collector_factory=LivePreflight, clock=time.time):
        self.store, self.collector_factory, self.clock = store, collector_factory, clock
        self.runtime = Runtime(store, clock)

    def grant(self, db, assignment_id, user, teacher=False):
        row = db.execute('SELECT * FROM lab_assignments WHERE id=?', (assignment_id,)).fetchone()
        if teacher:
            allowed = row and user.role in ('teacher', 'admin') and row['grantor'] == user.username and user.testbed in (None, row['testbed'])
        else:
            allowed = row and row['owner'] == user.username and row['testbed'] == self.runtime.testbed(user)
        if not allowed:
            raise KeyError('Asignación no encontrada.')
        if not row['enabled'] or row['expires_at'] <= self.clock():
            raise ConflictError('Asignación vencida o revocada.')
        return row

    def bind(self, assignment_id, user, key, body):
        with self.store.connect() as db:
            self.grant(db, assignment_id, user, teacher=True)
        if body.observed_supi == body.competing_supi:
            raise ValueError('Los sujetos deben ser distintos.')
        def action(db):
            grant = self.grant(db, assignment_id, user, teacher=True)
            bindings = {role: {'alias': grant[alias], 'supi': supi, 'dnn': body.dnn} for role, alias, supi in (
                ('observed', 'observed_ue', body.observed_supi), ('competing', 'competing_ue', body.competing_supi))}
            raw = canonical(bindings)
            result = {'id': uuid.uuid4().hex, 'assignment_id': assignment_id, 'created_at': stamp(),
                      'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'scope': 'authorized_real_pilot' if grant['mode'] == 'real' else 'read_only_preflight'}
            db.execute('INSERT INTO lab_bindings VALUES(?,?,?,?,?,?)',
                       (result['id'], assignment_id, user.username, raw, result['sha256'], result['created_at']))
            return result
        return self.store.mutate(user, 'binding:' + assignment_id, key, body.model_dump(), action)

    def collect(self, campaign_id, assignment_id, user, key):
        with self.store.connect() as db:
            self.runtime.campaign(db, campaign_id, user)
            self.grant(db, assignment_id, user)
        created = False
        def reserve(db):
            nonlocal created
            campaign = self.runtime.campaign(db, campaign_id, user)
            grant = self.grant(db, assignment_id, user)
            descriptor = Descriptor.model_validate_json(campaign['descriptor'])
            validation = validate_design(descriptor)
            if (not validation['design_valid'] or descriptor.observed_ue != grant['observed_ue']
                    or descriptor.competing_ue != grant['competing_ue']
                    or descriptor.campaign_budget_bytes > grant['max_traffic_bytes']
                    or descriptor.capture_budget_bytes > grant['max_capture_bytes']
                    or max(descriptor.load_mbps) > grant['max_load_mbps']
                    or validation['estimates']['runs'] > grant['max_runs']):
                raise PermissionError('Diseño fuera de la asignación.')
            binding = db.execute('SELECT * FROM lab_bindings WHERE assignment_id=? ORDER BY created_at DESC LIMIT 1', (assignment_id,)).fetchone()
            if not binding: raise ConflictError('El docente debe vincular los aliases a sujetos reales para lectura.')
            # The configured infrastructure is shared even when logical testbed names differ.
            if db.execute("SELECT 1 FROM lab_preflights WHERE status='collecting' AND expires_at>?", (self.clock(),)).fetchone():
                raise ConflictError('Hay una observación viva en curso; vuelve a intentarlo al terminar.')
            result = {'id': uuid.uuid4().hex}
            db.execute('INSERT INTO lab_preflights VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (result['id'], campaign_id, assignment_id, binding['id'], 'collecting', None, None, None, stamp(), self.clock() + 60))
            created = True
            return result
        reserved = self.store.mutate(user, 'preflight:' + campaign_id, key, {'assignment_id': assignment_id}, reserve)
        if created:
            with self.store.connect() as db:
                job = db.execute('SELECT * FROM lab_preflights WHERE id=?', (reserved['id'],)).fetchone()
                binding = json.loads(db.execute('SELECT body FROM lab_bindings WHERE id=?', (job['binding_id'],)).fetchone()[0])
                campaign = self.runtime.campaign(db, campaign_id, user)
            try:
                collector = self.collector_factory()
                document = collector.collect(binding, json.loads(campaign['descriptor'])['campaign_budget_bytes'])
                raw_document = canonical(getattr(collector, 'raw', {}))
            except Exception:
                document = {'source': 'live_ssh', 'execution_ready': False, 'errors': {'observation': 'unavailable'}}
                raw_document = '{}'
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    self.grant(db, assignment_id, user)
                except (KeyError, ConflictError):
                    document['authorization_changed'] = True
                raw = canonical(document)
                db.execute("UPDATE lab_preflights SET status='completed',document=?,raw_document=?,sha256=? WHERE id=? AND status='collecting'",
                           (raw, raw_document, hashlib.sha256(raw.encode()).hexdigest(), reserved['id']))
        return self.preflight(reserved['id'], user)

    def preflight(self, identity, user):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM lab_preflights WHERE id=?', (identity,)).fetchone()
            if not row: raise KeyError('Preflight no encontrado.')
            self.runtime.campaign(db, row['campaign_id'], user)
            return {'id': row['id'], 'status': 'interrupted' if row['status'] == 'collecting' and row['expires_at'] <= self.clock() else row['status'],
                    'created_at': row['created_at'], 'sha256': row['sha256'],
                    'stale': row['expires_at'] <= self.clock(), 'execution_ready': False,
                    'document': json.loads(row['document']) if row['document'] else None}

    def preflights(self, campaign_id, user):
        with self.store.connect() as db:
            self.runtime.campaign(db, campaign_id, user)
            ids = [r[0] for r in db.execute('SELECT id FROM lab_preflights WHERE campaign_id=? ORDER BY created_at DESC LIMIT 20', (campaign_id,))]
        return [self.preflight(i, user) for i in ids]

    def add_evidence(self, experiment_id, user, key, kind, document):
        """Internal producer only: no public endpoint accepts arbitrary measured values."""
        raw = canonical(document)
        if len(raw.encode()) > MAX_DOCUMENT_BYTES: raise ValueError('Artefacto demasiado grande.')
        with self.store.connect() as db: self.store.owned(db, experiment_id, user)
        def action(db):
            self.store.owned(db, experiment_id, user)
            size = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) FROM lab_evidence WHERE experiment_id=?', (experiment_id,)).fetchone()[0]
            if size + len(raw.encode()) > MAX_EXPERIMENT_BYTES: raise ConflictError('Cuota de expediente agotada.')
            result = {'id': uuid.uuid4().hex, 'kind': kind, 'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'created_at': stamp()}
            db.execute('INSERT INTO lab_evidence VALUES(?,?,?,?,?,?)', (result['id'], experiment_id, kind, raw, result['sha256'], result['created_at']))
            return result
        return self.store.mutate(user, 'evidence:' + experiment_id, key, {'kind': kind, 'document': document}, action)

    def evidence(self, experiment_id, user):
        with self.store.connect() as db:
            self.store.owned(db, experiment_id, user)
            return [self.verified_evidence(r) for r in db.execute('SELECT * FROM lab_evidence WHERE experiment_id=? ORDER BY created_at', (experiment_id,))]

    @staticmethod
    def verified_evidence(row):
        if hashlib.sha256(row['document'].encode()).hexdigest() != row['sha256']:
            raise ConflictError('La huella del artefacto no coincide; análisis y exportación bloqueados.')
        return {**dict(row), 'document': json.loads(row['document'])}

    def analyze(self, experiment_id, evidence_id, user, key):
        entries = {r['id']: r for r in self.evidence(experiment_id, user)}
        item = entries.get(evidence_id)
        if item and item['kind'] == 'effect_evidence':
            from app.laboratory.measurements import EffectEvidence, assess_effect, references
            document = EffectEvidence.model_validate(item['document'])
            validate_references(entries, references(document), document.source)
            result = {**assess_effect(document), 'input_id': item['id'], 'input_sha256': item['sha256']}
            return self.add_evidence(experiment_id, user, key, 'effect_analysis', result)
        if not item or item['kind'] != 'dataset': raise KeyError('Dataset no encontrado.')
        dataset = Dataset.model_validate(item['document'])
        for trial in dataset.trials:
            validate_references(entries, trial.evidence_ids, dataset.source)
        result = {**analyze(dataset), 'input_id': item['id'], 'input_sha256': item['sha256']}
        return self.add_evidence(experiment_id, user, key, 'analysis', result)

    def note(self, experiment_id, user, key, body):
        entries = {r['id'] for r in self.evidence(experiment_id, user)}
        if any(i not in entries for i in body.evidence_ids): raise ValueError('Referencia ajena o inexistente.')
        def action(db):
            self.store.owned(db, experiment_id, user)
            result = {'id': uuid.uuid4().hex, 'created_at': stamp(), 'document': body.model_dump()}
            db.execute('INSERT INTO lab_notes VALUES(?,?,?,?)', (result['id'], experiment_id, canonical(body.model_dump()), result['created_at']))
            return result
        return self.store.mutate(user, 'note:' + experiment_id, key, body.model_dump(), action)

    def notes(self, experiment_id, user):
        with self.store.connect() as db:
            self.store.owned(db, experiment_id, user)
            return [{**dict(r), 'document': json.loads(r['document'])} for r in db.execute('SELECT * FROM lab_notes WHERE experiment_id=? ORDER BY created_at', (experiment_id,))]

    def export(self, experiment_id, user):
        # One WAL read snapshot: concurrent notebook/evidence/worker writes cannot
        # leave references absent from this export or mix execution generations.
        with self.store.connect() as db:
            db.execute('BEGIN')
            detail = dict(self.store.owned(db, experiment_id, user))
            detail['revisions'] = [self.store.revision_dict(r) for r in db.execute('SELECT * FROM revisions WHERE experiment_id=? ORDER BY number', (experiment_id,))]
            detail['campaigns'] = [self.store.campaign_dict(r) for r in db.execute('SELECT c.* FROM campaigns c JOIN revisions r ON r.id=c.revision_id WHERE r.experiment_id=? ORDER BY c.created_at', (experiment_id,))]
            files = {'design.json': detail,
                     'evidence.json': [self.verified_evidence(r) for r in db.execute('SELECT * FROM lab_evidence WHERE experiment_id=? ORDER BY created_at', (experiment_id,))],
                     'notebook.json': [{**dict(r), 'document': json.loads(r['document'])} for r in db.execute('SELECT * FROM lab_notes WHERE experiment_id=? ORDER BY created_at', (experiment_id,))]}
            files['investigation-history.json'] = [
                {'id': r['id'], 'investigation_id': r['investigation_id'], 'question': r['question'],
                 'status': r['status'], 'response': json.loads(r['response']) if r['response'] else None,
                 'created_at': r['created_at'], 'finished_at': r['finished_at']}
                for r in db.execute('SELECT q.* FROM lab_ai_requests q JOIN lab_evidence e ON e.id=q.investigation_id WHERE e.experiment_id=? ORDER BY q.created_at', (experiment_id,))]
            for campaign in detail['campaigns']:
                files['preflights/' + campaign['id'] + '.json'] = [
                    {'id': r['id'], 'status': r['status'], 'created_at': r['created_at'], 'sha256': r['sha256'],
                     'execution_ready': False, 'document': json.loads(r['document']) if r['document'] else None}
                    for r in db.execute('SELECT * FROM lab_preflights WHERE campaign_id=? ORDER BY created_at', (campaign['id'],))]
                for row in db.execute('SELECT * FROM lab_executions WHERE campaign_id=?', (campaign['id'],)):
                    events = [{'id': e['id'], 'event': e['event'], 'cursor': e['cursor'], 'created_at': e['created_at'], 'detail': json.loads(e['detail'])}
                              for e in db.execute('SELECT * FROM lab_journal WHERE execution_id=? ORDER BY id', (row['id'],))]
                    files['executions/' + row['id'] + '.json'] = {'execution': self.runtime.public(row), 'events': events}
        payloads = {name: canonical(value).encode() for name, value in files.items()}
        if sum(map(len, payloads.values())) > 25_000_000: raise ConflictError('Expediente demasiado grande para esta exportación.')
        manifest = {'schema_version': 1, 'scope': 'authorized_laboratory_dossier', 'created_at': stamp(),
                    'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in sorted(payloads.items())]}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in payloads.items(): archive.writestr(name, data)
            archive.writestr('manifest.json', canonical(manifest))
        return buffer.getvalue()
