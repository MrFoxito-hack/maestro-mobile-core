"""Durable queue, scoped grants and fenced dry-run resource ownership.

Fencing protects the SQLite sandbox only. A future real adapter must supply
its own remote fencing/watchdog before network execution can be enabled.
"""

import json
import time
import uuid

from app.laboratory.planner import validate_design
from app.laboratory.repository import ConflictError, Repository, canonical, stamp
from app.laboratory.schemas import AssignmentCreate, Descriptor
from app.models import Role, UserPublic

TERMINAL = ("completed", "cancelled", "failed")
LEASE_SECONDS = 30


class LeaseLost(ConflictError):
    pass


class Runtime:
    def __init__(self, repository: Repository, clock=time.time):
        self.repository = repository
        self.clock = clock

    @staticmethod
    def resource(testbed, mode='dry_run'):
        # The configured VMs are shared even if users have different testbed labels.
        return 'real-core:configured-vms' if mode == 'real' else 'dry-run:' + testbed

    @staticmethod
    def testbed(user):
        return user.testbed or "local"

    @staticmethod
    def public(row):
        # Explicit public projection: future internal fields must not leak into API/AI context.
        fields = ("id", "campaign_id", "assignment_id", "owner", "testbed", "mode", "status",
                  "cursor", "cancel_requested", "error_code", "created_at", "updated_at", "finished_at")
        result = {key: row[key] for key in fields}
        result['execution_status'] = row['status']
        if row['mode'] == 'real':
            outcome = json.loads(row['outcome'])
            return {**result, 'source': outcome.get('source', 'live_campaign'),
                    'network_measurements': outcome.get('network_measurements', False),
                    'metrics': outcome.get('metrics'),
                    'validity_status': outcome.get('validity_status', 'inconclusive'),
                    'hypothesis_outcome': outcome.get('hypothesis_outcome', 'not_evaluated'),
                    'recovery_verified': outcome.get('recovery_verified', False),
                    'recovery_scope': outcome.get('recovery_scope')}
        return {**result, "source": "dry_run", "network_measurements": False,
                "metrics": None, "validity_status": "not_applicable", "hypothesis_outcome": "not_evaluated"}

    @staticmethod
    def journal(db, row, event, detail=None):
        db.execute("INSERT INTO lab_journal(execution_id,event,cursor,token,detail,created_at) VALUES(?,?,?,?,?,?)",
                   (row["id"], event, row["cursor"], row["token"], canonical(detail or {}), stamp()))

    def grant(self, teacher: UserPublic, subject: UserPublic, key: str, body: AssignmentCreate):
        if teacher.role not in (Role.teacher, Role.admin):
            raise PermissionError("Solo docentes o administradores asignan prácticas.")
        if teacher.testbed is not None and teacher.testbed != self.testbed(subject):
            raise PermissionError("El destinatario pertenece a otro testbed.")
        if (not subject.testbed and subject.role == Role.student) or subject.username != body.username:
            raise ValueError("El destinatario necesita un testbed asignado.")
        if body.observed_ue == body.competing_ue:
            raise ValueError("La práctica requiere aliases de UE diferentes.")

        def action(db):
            result = {"id": uuid.uuid4().hex, "grantor": teacher.username, "owner": subject.username,
                      "testbed": self.testbed(subject), "template": body.template, "mode": body.mode,
                      "observed_ue": body.observed_ue, "competing_ue": body.competing_ue,
                      "max_runs": body.max_runs, "max_load_mbps": body.max_load_mbps,
                      "max_traffic_bytes": body.max_traffic_bytes, "max_capture_bytes": body.max_capture_bytes,
                      "max_jobs": body.max_jobs, "used_jobs": 0,
                      "expires_at": self.clock() + body.validity_hours * 3600,
                      "enabled": 1, "created_at": stamp()}
            db.execute("INSERT INTO lab_assignments VALUES(:id,:grantor,:owner,:testbed,:template,:mode,"
                       ":observed_ue,:competing_ue,:max_runs,:max_load_mbps,:max_traffic_bytes,"
                       ":max_capture_bytes,:max_jobs,:used_jobs,:expires_at,:enabled,:created_at)", result)
            return result
        return self.repository.mutate(teacher, "assignment.create", key,
                                      {**body.model_dump(), "testbed": self.testbed(subject)}, action)

    def assignments(self, user):
        with self.repository.connect() as db:
            rows = db.execute("SELECT * FROM lab_assignments WHERE owner=? OR grantor=? ORDER BY created_at DESC LIMIT 200",
                              (user.username, user.username))
            return [dict(r) for r in rows if (r["owner"] == user.username and r["testbed"] == self.testbed(user))
                    or (user.role in (Role.teacher, Role.admin) and r["grantor"] == user.username
                        and user.testbed in (None, r["testbed"]))]

    def revoke(self, assignment_id, user):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_assignments WHERE id=?", (assignment_id,)).fetchone()
            if not row or user.role not in (Role.teacher, Role.admin) or row["grantor"] != user.username or user.testbed not in (None, row["testbed"]):
                raise KeyError("Asignación no encontrada.")
            if row["enabled"]:
                db.execute("UPDATE lab_assignments SET enabled=0 WHERE id=?", (assignment_id,))
                affected = db.execute("SELECT * FROM lab_executions WHERE assignment_id=? AND status NOT IN ('completed','failed','cancelled')", (assignment_id,)).fetchall()
                db.execute("UPDATE lab_executions SET cancel_requested=1 WHERE assignment_id=? AND status NOT IN ('completed','failed','cancelled')", (assignment_id,))
                for execution in affected:
                    self.journal(db, execution, "assignment_revoked")
                    if execution["status"] == "queued":
                        db.execute("UPDATE lab_executions SET status='cancelled',finished_at=?,updated_at=? WHERE id=?", (stamp(), stamp(), execution["id"]))
                        db.execute("DELETE FROM lab_leases WHERE execution_id=?", (execution["id"],))
                        self.journal(db, execution, "cancelled_before_claim")
                db.execute("INSERT INTO events(owner,action,entity_id,created_at) VALUES(?,?,?,?)",
                           (user.username, "assignment.revoke", assignment_id, stamp()))
            return {"id": assignment_id, "enabled": False}

    def campaign(self, db, campaign_id, user):
        row = db.execute("SELECT c.*,r.experiment_id,r.descriptor FROM campaigns c JOIN revisions r ON r.id=c.revision_id WHERE c.id=?",
                         (campaign_id,)).fetchone()
        if not row:
            raise KeyError("Plan no encontrado.")
        self.repository.owned(db, row["experiment_id"], user)
        return row

    def enqueue(self, campaign_id, assignment_id, user, key, mode='dry_run'):
        if mode not in ('dry_run', 'real'):
            raise ValueError('Modo de ejecución inválido.')
        with self.repository.connect() as db:
            self.campaign(db, campaign_id, user)

        def action(db):
            campaign = self.campaign(db, campaign_id, user)
            if mode == 'real' and db.execute('SELECT 1 FROM lab_ai_slots WHERE expires_at>?', (self.clock(),)).fetchone():
                raise ConflictError('Hay inferencia local o enfriamiento pendiente; vuelve a iniciar al terminar.')
            grant = db.execute("SELECT * FROM lab_assignments WHERE id=? AND owner=? AND testbed IS ?",
                               (assignment_id, user.username, self.testbed(user))).fetchone()
            if not grant:
                raise PermissionError("Necesitas una asignación docente para este testbed.")
            if grant['mode'] != mode:
                raise PermissionError('La asignación no autoriza este modo de ejecución.')
            if not grant["enabled"] or grant["expires_at"] <= self.clock() or grant["used_jobs"] >= grant["max_jobs"]:
                raise ConflictError("La asignación está revocada, vencida o agotada.")
            descriptor = Descriptor.model_validate_json(campaign["descriptor"])
            validation = validate_design(descriptor)
            if not validation["design_valid"]:
                raise ValueError("Diseño incompleto.")
            if (descriptor.template != grant["template"] or descriptor.observed_ue != grant["observed_ue"]
                    or descriptor.competing_ue != grant["competing_ue"]
                    or validation["estimates"]["runs"] > grant["max_runs"]
                    or max(descriptor.load_mbps) > grant["max_load_mbps"]
                    or descriptor.campaign_budget_bytes > grant["max_traffic_bytes"]
                    or descriptor.capture_budget_bytes > grant["max_capture_bytes"]):
                raise PermissionError("El diseño excede el alcance de la práctica asignada.")
            if db.execute("SELECT 1 FROM lab_executions WHERE campaign_id=?", (campaign_id,)).fetchone():
                raise ConflictError("El plan ya tiene una ejecución. Crea otro plan para repetirlo.")
            context = {}
            if mode == 'real':
                binding = db.execute('SELECT * FROM lab_bindings WHERE assignment_id=? ORDER BY created_at DESC LIMIT 1', (assignment_id,)).fetchone()
                if not binding:
                    raise ConflictError('El docente debe vincular los dos UE antes de ejecutar en real.')
                import hashlib
                if hashlib.sha256(binding['body'].encode()).hexdigest() != binding['sha256']:
                    raise ConflictError('Integridad de vinculación inválida.')
                bindings = json.loads(binding['body'])
                if (bindings['observed']['supi'] != 'imsi-999700000000001'
                        or bindings['competing']['supi'] != 'imsi-999700000000004'):
                    raise ValueError('El piloto real admite exclusivamente UE 001 y UE 004.')
                if descriptor.load_mbps != [19] or descriptor.measurement_seconds != 10 or descriptor.repetitions != 1:
                    raise ValueError('El piloto real admite un bloque, 19 Mbps y 10 segundos de carga por tratamiento.')
                if descriptor.campaign_budget_bytes < 70_000_000 or descriptor.capture_budget_bytes < 4_000_000:
                    raise ValueError('Presupuesto real insuficiente: tráfico 70 MB y captura 4 MB como mínimo.')
                context = {'binding_id': binding['id'], 'bindings': bindings,
                           'descriptor': descriptor.model_dump(), 'experiment_id': campaign['experiment_id'],
                           'owner_role': user.role, 'owner_testbed': user.testbed}
            resource = self.resource(self.testbed(user), mode)
            if db.execute("SELECT 1 FROM lab_leases WHERE resource=?", (resource,)).fetchone():
                raise ConflictError("El espacio de ensayo está reservado; una reserva vencida requiere recuperación.")
            execution_id, token = uuid.uuid4().hex, uuid.uuid4().hex
            now = stamp()
            db.execute("INSERT INTO lab_executions(id,campaign_id,assignment_id,owner,testbed,mode,status,token,created_at,updated_at,real_context) VALUES(?,?,?,?,?,?,'queued',?,?,?,?)",
                       (execution_id, campaign_id, assignment_id, user.username, self.testbed(user), mode, token, now, now, canonical(context)))
            db.execute("INSERT INTO lab_leases VALUES(?,?,?,?)", (resource, execution_id, token, self.clock() + LEASE_SECONDS))
            db.execute("UPDATE lab_assignments SET used_jobs=used_jobs+1 WHERE id=?", (assignment_id,))
            row = db.execute("SELECT * FROM lab_executions WHERE id=?", (execution_id,)).fetchone()
            self.journal(db, row, "queued", {"mode": mode, "resource": resource})
            return self.public(row)
        return self.repository.mutate(user, "execution.start:" + campaign_id, key,
                                      {"assignment_id": assignment_id, "mode": mode}, action)

    def owned_execution(self, db, execution_id, user):
        row = db.execute("SELECT * FROM lab_executions WHERE id=? AND owner=? AND testbed IS ?",
                         (execution_id, user.username, self.testbed(user))).fetchone()
        if not row:
            raise KeyError("Ejecución no encontrada.")
        return row

    def executions(self, campaign_id, user):
        with self.repository.connect() as db:
            self.campaign(db, campaign_id, user)
            return [self.public(r) for r in db.execute("SELECT * FROM lab_executions WHERE campaign_id=?", (campaign_id,))]

    def detail(self, execution_id, user):
        with self.repository.connect() as db:
            return self.public(self.owned_execution(db, execution_id, user))

    def events(self, execution_id, user, after=0):
        with self.repository.connect() as db:
            self.owned_execution(db, execution_id, user)
            return [{"id": r["id"], "event": r["event"], "cursor": r["cursor"],
                     "detail": json.loads(r["detail"]), "created_at": r["created_at"]}
                    for r in db.execute("SELECT * FROM lab_journal WHERE execution_id=? AND id>? ORDER BY id LIMIT 200", (execution_id, after))]

    def cancel(self, execution_id, user):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.owned_execution(db, execution_id, user)
            if row["status"] not in TERMINAL and not row["cancel_requested"]:
                db.execute("UPDATE lab_executions SET cancel_requested=1,updated_at=? WHERE id=?", (stamp(), execution_id))
                self.journal(db, row, "cancel_requested")
                if row["status"] == "queued":
                    # No worker ever claimed this job; no action can be in flight.
                    db.execute("UPDATE lab_executions SET status='cancelled',finished_at=? WHERE id=?", (stamp(), execution_id))
                    db.execute("DELETE FROM lab_leases WHERE execution_id=?", (execution_id,))
                    self.journal(db, row, "cancelled_before_claim")
            return self.public(db.execute("SELECT * FROM lab_executions WHERE id=?", (execution_id,)).fetchone())

    def fenced(self, db, execution_id, token):
        row = db.execute("SELECT e.*,l.expires_at FROM lab_executions e JOIN lab_leases l ON l.execution_id=e.id AND l.token=e.token WHERE e.id=? AND e.token=?",
                         (execution_id, token)).fetchone()
        if not row or row["expires_at"] <= self.clock() or row["status"] in TERMINAL:
            raise LeaseLost("Propiedad de ejecución perdida; actuación rechazada.")
        return row

    def claim(self, worker_id):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_executions WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            if row['mode'] == 'real' and db.execute(
                    'SELECT 1 FROM lab_ai_slots WHERE expires_at>?', (self.clock(),)).fetchone():
                return None
            self.fenced(db, row["id"], row["token"])
            token = uuid.uuid4().hex
            resource = self.resource(row["testbed"], row['mode'])
            baseline = 'nwdaf_active_no_owned_auxiliaries'
            if row['mode'] == 'dry_run':
                db.execute("INSERT OR IGNORE INTO lab_sandbox VALUES(?,'nominal')", (resource,))
                baseline = db.execute("SELECT value FROM lab_sandbox WHERE resource=?", (resource,)).fetchone()[0]
            db.execute("UPDATE lab_executions SET status='running',worker_id=?,token=?,baseline=?,updated_at=? WHERE id=?",
                       (worker_id, token, baseline, stamp(), row["id"]))
            db.execute("UPDATE lab_leases SET token=?,expires_at=? WHERE execution_id=?", (token, self.clock() + LEASE_SECONDS, row["id"]))
            result = dict(db.execute("SELECT * FROM lab_executions WHERE id=?", (row["id"],)).fetchone())
            self.journal(db, result, "claimed", {"baseline": 'operational_regime' if row['mode'] == 'real' else 'sandbox_checkpoint'})
            return result

    def expire(self):
        """Independent watchdog: revoke stale tokens, retain reservations until recovery."""
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stale = db.execute("SELECT e.* FROM lab_executions e JOIN lab_leases l ON l.execution_id=e.id WHERE l.expires_at<=? AND e.status IN ('queued','running','recovering')", (self.clock(),)).fetchall()
            for row in stale:
                if row["status"] == "queued":
                    # Never claimed, so no adapter could have modified the sandbox.
                    db.execute("UPDATE lab_executions SET status='failed',error_code='queue_timeout',finished_at=?,updated_at=? WHERE id=?", (stamp(), stamp(), row["id"]))
                    db.execute("DELETE FROM lab_leases WHERE execution_id=?", (row["id"],))
                    self.journal(db, row, "queue_timeout")
                    continue
                token = uuid.uuid4().hex
                db.execute("UPDATE lab_executions SET status='recovery_required',token=?,worker_id=NULL,error_code='lease_expired',updated_at=? WHERE id=?", (token, stamp(), row["id"]))
                db.execute("UPDATE lab_leases SET token=? WHERE execution_id=?", (token, row["id"]))
                self.journal(db, {**dict(row), "token": token}, "lease_expired")
            return len(stale)

    def claim_recovery(self, worker_id):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_executions WHERE status='recovery_required' AND error_code!='recovery_failed' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE lab_executions SET status='recovering',worker_id=?,token=?,updated_at=? WHERE id=?", (worker_id, token, stamp(), row["id"]))
            db.execute("UPDATE lab_leases SET token=?,expires_at=? WHERE execution_id=?", (token, self.clock() + LEASE_SECONDS, row["id"]))
            result = dict(db.execute("SELECT * FROM lab_executions WHERE id=?", (row["id"],)).fetchone())
            self.journal(db, result, "recovery_claimed")
            return result

    def retry_recovery(self, execution_id, user):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.owned_execution(db, execution_id, user)
            if row["status"] != "recovery_required":
                raise ConflictError("La ejecución no requiere recuperación pendiente.")
            db.execute("UPDATE lab_executions SET error_code='recovery_retry',updated_at=? WHERE id=?", (stamp(), execution_id))
            self.journal(db, row, "recovery_retry_requested")
            return self.public(db.execute("SELECT * FROM lab_executions WHERE id=?", (execution_id,)).fetchone())

    def active(self, worker_id):
        with self.repository.connect() as db:
            row = db.execute("SELECT * FROM lab_executions WHERE worker_id=? AND status IN ('running','recovering') ORDER BY created_at LIMIT 1", (worker_id,)).fetchone()
            return dict(row) if row else None

    def work(self, row):
        with self.repository.connect() as db:
            plan = json.loads(db.execute("SELECT plan FROM campaigns WHERE id=?", (row["campaign_id"],)).fetchone()[0])
        steps = [{"action": "preflight", "run": None}]
        for run in plan["runs"]:
            actions = ('prepare', 'apply', 'restore', 'verify_restored', 'verify', 'measure') if row['mode'] == 'real' else ('prepare', 'apply', 'verify', 'measure', 'restore', 'verify_restored')
            steps.extend({"action": action, "run": run} for action in actions)
        return steps

    def heartbeat(self, execution_id, token):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.fenced(db, execution_id, token)
            grant = db.execute("SELECT * FROM lab_assignments WHERE id=?", (row["assignment_id"],)).fetchone()
            cancelled = row["cancel_requested"] or not grant["enabled"] or grant["expires_at"] <= self.clock()
            if cancelled:
                db.execute("UPDATE lab_executions SET cancel_requested=1 WHERE id=?", (execution_id,))
                if not row["cancel_requested"]:
                    self.journal(db, row, "assignment_expired_or_revoked")
            db.execute("UPDATE lab_leases SET expires_at=? WHERE execution_id=?", (self.clock() + LEASE_SECONDS, execution_id))
            return bool(cancelled)

    def intent(self, execution_id, token, step):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.fenced(db, execution_id, token)
            if not step.get("recovery") and db.execute(
                "SELECT 1 FROM lab_journal WHERE execution_id=? AND cursor=? AND event='step_intent'",
                (execution_id, row["cursor"]),
            ).fetchone():
                raise ConflictError("Resultado anterior desconocido; se requiere recuperación, no repetición.")
            self.journal(db, row, "step_intent", step)

    def result(self, execution_id, token, result):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.fenced(db, execution_id, token)
            self.journal(db, row, "step_result", result)
            db.execute("UPDATE lab_executions SET cursor=cursor+1,updated_at=? WHERE id=?", (stamp(), execution_id))

    def require_recovery(self, execution_id, token, error_code):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.fenced(db, execution_id, token)
            self.journal(db, row, "recovery_required", {"error_code": error_code})
            new_token = uuid.uuid4().hex
            db.execute("UPDATE lab_executions SET status='recovery_required',error_code=?,worker_id=NULL,token=?,updated_at=? WHERE id=?", (error_code, new_token, stamp(), execution_id))
            db.execute("UPDATE lab_leases SET token=? WHERE execution_id=?", (new_token, execution_id))

    def begin_recovery(self, execution_id, token):
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.fenced(db, execution_id, token)
            if row["status"] != "recovering":
                db.execute("UPDATE lab_executions SET status='recovering',updated_at=? WHERE id=?", (stamp(), execution_id))
                self.journal(db, row, "recovery_started")

    def finish(self, execution_id, token, status):
        if status not in TERMINAL:
            raise ValueError("Estado final inválido.")
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.fenced(db, execution_id, token)
            sandbox = db.execute("SELECT value FROM lab_sandbox WHERE resource=?", (self.resource(row["testbed"]),)).fetchone()
            if row['mode'] == 'dry_run' and (sandbox is None or sandbox[0] != row["baseline"]):
                raise ConflictError("El recurso simulado no está restaurado.")
            if row['mode'] == 'real' and not json.loads(row['outcome']).get('recovery_verified'):
                raise ConflictError('Recuperación real no verificada; reserva retenida.')
            grant = db.execute("SELECT * FROM lab_assignments WHERE id=?", (row["assignment_id"],)).fetchone()
            if row["cancel_requested"] or not grant["enabled"] or grant["expires_at"] <= self.clock():
                status = "cancelled"
            now = stamp()
            self.journal(db, row, "finished", {"status": status, "network_measurements": json.loads(row['outcome']).get('network_measurements', False)})
            db.execute("UPDATE lab_executions SET status=?,finished_at=?,updated_at=? WHERE id=?", (status, now, now, execution_id))
            db.execute("DELETE FROM lab_leases WHERE execution_id=? AND token=?", (execution_id, token))

    def save_real(self, execution_id, token, *, context=None, outcome=None):
        with self.repository.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.fenced(db, execution_id, token)
            if row['mode'] != 'real':
                raise ValueError('Real state cannot be written to a sandbox execution')
            if context is not None:
                db.execute('UPDATE lab_executions SET real_context=? WHERE id=?', (canonical(context), execution_id))
            if outcome is not None:
                db.execute('UPDATE lab_executions SET outcome=? WHERE id=?', (canonical(outcome), execution_id))
