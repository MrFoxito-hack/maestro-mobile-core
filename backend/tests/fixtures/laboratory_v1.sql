-- Frozen schema from delivery 01: no runtime tables.

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
