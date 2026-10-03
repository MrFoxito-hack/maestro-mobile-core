import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.laboratory.planner import PLANNER_VERSION, plan_runs
from app.laboratory.schemas import Descriptor
from app.models import UserPublic
from app.laboratory.migrations import RUNTIME_SCHEMA, RESEARCH_SCHEMA, INVESTIGATION_SCHEMA, migrate_real, schema_lock


class ConflictError(Exception):
    pass


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    """Local SQLite store; all writes and their audit events commit atomically."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with schema_lock(self.path), self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 5:
                raise RuntimeError("Versión de laboratorio más reciente que este servidor.")
            # Concurrent API/worker startup can race on the journal-mode change.
            # SQLite may return BUSY here immediately despite busy_timeout.
            deadline = time.monotonic() + 5
            while True:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as error:
                    code = getattr(error, 'sqlite_errorcode', None)
                    if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                        raise
                    time.sleep(.025)
            if version == 0:
                db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, testbed TEXT,
                    title TEXT NOT NULL, question TEXT NOT NULL, hypothesis TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
                    number INTEGER NOT NULL, descriptor TEXT NOT NULL, sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(experiment_id, number)
                );
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id),
                    plan TEXT NOT NULL, planner_version TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    owner TEXT NOT NULL, scope TEXT NOT NULL, request_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(owner, scope, request_key)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, owner TEXT NOT NULL, action TEXT NOT NULL,
                    entity_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions
                    BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
                CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions
                    BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
                CREATE TRIGGER IF NOT EXISTS campaigns_no_update BEFORE UPDATE ON campaigns
                    BEGIN SELECT RAISE(ABORT, 'immutable campaign plan'); END;
                CREATE TRIGGER IF NOT EXISTS campaigns_no_delete BEFORE DELETE ON campaigns
                    BEGIN SELECT RAISE(ABORT, 'immutable campaign plan'); END;
                PRAGMA user_version=1;
                COMMIT;
            """)
            if version < 2:
                db.executescript(RUNTIME_SCHEMA)
            if version < 3:
                db.executescript(RESEARCH_SCHEMA)
            migrate_real(db)
            if version < 5:
                db.executescript(INVESTIGATION_SCHEMA)

    @staticmethod
    def owned(db, experiment_id: str, user: UserPublic):
        # Private drafts even for teachers; sharing/assignments require an explicit later policy.
        row = db.execute("SELECT * FROM experiments WHERE id=? AND owner=? AND testbed IS ?",
                         (experiment_id, user.username, user.testbed)).fetchone()
        if row is None:
            raise KeyError("Experimento no encontrado.")
        return row

    def list_experiments(self, user):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM experiments WHERE owner=? AND testbed IS ? ORDER BY created_at DESC LIMIT 200",
                (user.username, user.testbed))]

    def detail(self, experiment_id, user):
        with self.connect() as db:
            result = dict(self.owned(db, experiment_id, user))
            result["revisions"] = [self.revision_dict(r) for r in db.execute(
                "SELECT * FROM revisions WHERE experiment_id=? ORDER BY number DESC", (experiment_id,))]
            result["campaigns"] = [self.campaign_dict(r) for r in db.execute(
                "SELECT c.* FROM campaigns c JOIN revisions r ON c.revision_id=r.id "
                "WHERE r.experiment_id=? ORDER BY c.created_at DESC", (experiment_id,))]
            return result

    @staticmethod
    def revision_dict(row):
        return {**dict(row), "descriptor": json.loads(row["descriptor"])}

    @staticmethod
    def campaign_dict(row):
        return {**dict(row), "plan": json.loads(row["plan"]), "execution_status": "planned",
                "execution_ready": False}

    def get_revision(self, revision_id, user):
        with self.connect() as db:
            row = db.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
            if row is None:
                raise KeyError("Revisión no encontrada.")
            self.owned(db, row["experiment_id"], user)
            return self.revision_dict(row)

    def mutate(self, user, scope, key, payload, action):
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM requests WHERE owner=? AND scope=? AND request_key=?",
                                  (user.username, scope, key)).fetchone()
            if previous:
                if previous["payload_hash"] != digest:
                    raise ConflictError("La clave de idempotencia ya se usó con otro contenido.")
                return json.loads(previous["result"])
            result = action(db)
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?)",
                       (user.username, scope, key, digest, canonical(result)))
            db.execute("INSERT INTO events(owner,action,entity_id,created_at) VALUES(?,?,?,?)",
                       (user.username, scope, result["id"], stamp()))
            return result

    def create_experiment(self, user, key, payload):
        def action(db):
            result = {"id": uuid.uuid4().hex, "owner": user.username, "testbed": user.testbed,
                      **payload, "created_at": stamp()}
            db.execute("INSERT INTO experiments VALUES(:id,:owner,:testbed,:title,:question,:hypothesis,:created_at)", result)
            return result
        return self.mutate(user, "experiment.create", key, {**payload, "testbed": user.testbed}, action)

    def create_revision(self, experiment_id, user, key, descriptor):
        # Authorize before idempotent replay as well.
        with self.connect() as db:
            self.owned(db, experiment_id, user)
        def action(db):
            self.owned(db, experiment_id, user)
            number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM revisions WHERE experiment_id=?",
                                (experiment_id,)).fetchone()[0]
            raw = canonical(descriptor)
            result = {"id": uuid.uuid4().hex, "experiment_id": experiment_id, "number": number,
                      "descriptor": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest(), "created_at": stamp()}
            db.execute("INSERT INTO revisions VALUES(:id,:experiment_id,:number,:descriptor,:sha256,:created_at)", result)
            return {**result, "descriptor": descriptor}
        return self.mutate(user, "revision.create:" + experiment_id, key, descriptor, action)

    def create_campaign(self, revision_id, user, key):
        revision = self.get_revision(revision_id, user)
        plan = plan_runs(Descriptor.model_validate(revision["descriptor"]))
        def action(db):
            self.owned(db, revision["experiment_id"], user)
            result = {"id": uuid.uuid4().hex, "revision_id": revision_id, "plan": canonical({"runs": plan}),
                      "planner_version": PLANNER_VERSION, "created_at": stamp()}
            db.execute("INSERT INTO campaigns VALUES(:id,:revision_id,:plan,:planner_version,:created_at)", result)
            return self.campaign_dict(result)
        return self.mutate(user, "campaign.create", key, {"revision_id": revision_id}, action)
