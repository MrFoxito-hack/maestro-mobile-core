import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.laboratory.adapters import Cancelled, SandboxAdapter
from app.laboratory.repository import ConflictError, Repository
from app.laboratory.runtime import LEASE_SECONDS, LeaseLost, Runtime
from app.laboratory.schemas import AssignmentCreate
from app.laboratory.worker import Worker
from app.models import Role, UserPublic
from test_laboratory import EXPERIMENT, complete, lab, post, BASE  # noqa: F401


@pytest.fixture
def setup(tmp_path):
    store = Repository(tmp_path / "runtime.db")
    store.initialize()
    clock = [1000.0]
    runtime = Runtime(store, clock=lambda: clock[0])
    student = UserPublic(username="student", role=Role.student, testbed="local")
    teacher = UserPublic(username="teacher", role=Role.teacher)
    grant = runtime.grant(teacher, student, "grant-0001", AssignmentCreate(username="student", observed_ue="video", competing_ue="load"))
    experiment = store.create_experiment(student, "experiment-0001", EXPERIMENT)
    revision = store.create_revision(experiment["id"], student, "revision-0001", complete().model_dump())
    campaign = store.create_campaign(revision["id"], student, "campaign-0001")
    return store, runtime, clock, student, teacher, grant, campaign


def enqueue(setup, key="start-0001"):
    _, runtime, _, student, _, grant, campaign = setup
    return runtime.enqueue(campaign["id"], grant["id"], student, key)


def drain(worker, limit=200):
    for _ in range(limit):
        if not worker.tick():
            return
    raise AssertionError("Worker did not drain")


def test_worker_completes_dry_run_and_never_invents_network_metrics(setup):
    store, runtime, _, student, _, _, _ = setup
    execution = enqueue(setup)
    drain(Worker(runtime))
    assert runtime.detail(execution["id"], student)["status"] == "completed"
    events = runtime.events(execution["id"], student)
    intents = [e for e in events if e["event"] == "step_intent"]
    results = [e for e in events if e["event"] == "step_result"]
    assert len(intents) == len(results) == 73  # preflight + 12 runs x 6 steps
    assert all(e["detail"]["metrics"] is None for e in results)
    assert all(a["id"] < b["id"] for a, b in zip(intents, results))
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM lab_leases").fetchone()[0] == 0
        assert db.execute("SELECT value FROM lab_sandbox").fetchone()[0] == "nominal"
    public = runtime.detail(execution["id"], student)
    assert "token" not in public and "worker_id" not in public
    assert public["hypothesis_outcome"] == "not_evaluated"


def test_start_is_atomic_idempotent_and_consumes_one_grant(setup):
    store, runtime, _, student, _, grant, campaign = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: enqueue(setup), range(8)))
    assert len({r["id"] for r in result}) == 1
    assert runtime.assignments(student)[0]["used_jobs"] == 1
    with pytest.raises(ConflictError):
        runtime.enqueue(campaign["id"], grant["id"], student, "different-key")
    second = store.create_campaign(campaign["revision_id"], student, "campaign-0002")
    with pytest.raises(ConflictError, match="reservado"):
        runtime.enqueue(second["id"], grant["id"], student, "different-key")
    assert runtime.assignments(student)[0]["used_jobs"] == 1


@pytest.mark.parametrize("updates", [{"max_load_mbps": 1}, {"max_runs": 2}, {"max_traffic_bytes": 1}, {"observed_ue": "different"}, {"max_capture_bytes": 1}])
def test_grant_limits_are_enforced(setup, updates):
    _, runtime, _, student, teacher, _, campaign = setup
    body = AssignmentCreate(**{"username": student.username, "observed_ue": "video", "competing_ue": "load", **updates})
    grant = runtime.grant(teacher, student, "restricted", body)
    with pytest.raises(PermissionError):
        runtime.enqueue(campaign["id"], grant["id"], student, "restricted")


def test_expiration_revocation_and_roles(setup):
    _, runtime, clock, student, teacher, grant, campaign = setup
    with pytest.raises(PermissionError):
        runtime.grant(student, student, "forbidden", AssignmentCreate(username="student", observed_ue="video", competing_ue="load"))
    clock[0] = grant["expires_at"]
    with pytest.raises(ConflictError):
        enqueue(setup)
    clock[0] = 1000
    runtime.revoke(grant["id"], teacher)
    with pytest.raises(ConflictError):
        enqueue(setup)


def test_queued_cancel_releases_without_worker(setup):
    store, runtime, _, student, _, _, _ = setup
    execution = enqueue(setup)
    assert runtime.cancel(execution["id"], student)["status"] == "cancelled"
    assert runtime.cancel(execution["id"], student)["status"] == "cancelled"
    assert Worker(runtime).tick() is False
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM lab_leases").fetchone()[0] == 0


@pytest.mark.parametrize("cancel_by", ["student", "teacher"])
def test_cancel_or_revoke_after_effect_restores(setup, cancel_by):
    store, runtime, _, student, teacher, grant, _ = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    for _ in range(3):  # preflight, prepare, apply
        worker.tick()
    with store.connect() as db:
        assert db.execute("SELECT value FROM lab_sandbox").fetchone()[0] != "nominal"
    if cancel_by == "student":
        runtime.cancel(execution["id"], student)
    else:
        runtime.revoke(grant["id"], teacher)
    drain(worker)
    assert runtime.detail(execution["id"], student)["status"] == "cancelled"
    with store.connect() as db:
        assert db.execute("SELECT value FROM lab_sandbox").fetchone()[0] == "nominal"


def test_crash_after_effect_before_result_is_recovered_not_replayed(setup):
    store, runtime, clock, student, _, grant, campaign = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    worker.tick(); worker.tick()
    row = runtime.active(worker.id)
    step = runtime.work(row)[row["cursor"]]
    assert step["action"] == "apply"
    runtime.intent(row["id"], row["token"], step)
    worker.adapter.perform(row, step)  # abrupt crash here: no result journal
    clock[0] += LEASE_SECONDS + 1
    assert runtime.expire() == 1
    with pytest.raises(LeaseLost):
        worker.adapter.perform(row, step)
    second = store.create_campaign(campaign["revision_id"], student, "after-crash")
    with pytest.raises(ConflictError):
        runtime.enqueue(second["id"], grant["id"], student, "after-crash")
    # Reopen persistence as a new process would.
    restarted = Repository(store.path)
    restarted.initialize()
    drain(Worker(Runtime(restarted, clock=lambda: clock[0])))
    assert runtime.detail(execution["id"], student)["status"] == "failed"
    effects = [e for e in runtime.events(execution["id"], student) if e["event"] == "step_intent" and e["detail"]["action"] == "apply"]
    assert len(effects) == 1
    with store.connect() as db:
        assert db.execute("SELECT value FROM lab_sandbox").fetchone()[0] == "nominal"


def test_failed_recovery_holds_reservation_until_explicit_retry(setup):
    store, runtime, _, student, _, _, _ = setup
    execution = enqueue(setup)
    class BrokenRecovery(SandboxAdapter):
        def perform(self, row, step):
            if step["action"] == "restore":
                raise RuntimeError("simulated restoration failure")
            return super().perform(row, step)
    worker = Worker(runtime, BrokenRecovery(runtime))
    for _ in range(3):
        worker.tick()
    runtime.cancel(execution["id"], student)
    worker.tick()
    assert runtime.detail(execution["id"], student)["status"] == "recovery_required"
    assert Worker(runtime).tick() is False
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM lab_leases").fetchone()[0] == 1
    runtime.retry_recovery(execution["id"], student)
    drain(Worker(runtime))
    assert runtime.detail(execution["id"], student)["status"] == "cancelled"


def test_queue_timeout_does_not_modify_unclaimed_resource(setup):
    store, runtime, clock, student, _, _, _ = setup
    with store.connect() as db:
        db.execute("INSERT INTO lab_sandbox VALUES('dry-run:local','unrelated-state')")
    execution = enqueue(setup)
    clock[0] += LEASE_SECONDS + 1
    runtime.expire()
    assert runtime.detail(execution["id"], student)["error_code"] == "queue_timeout"
    with store.connect() as db:
        assert db.execute("SELECT value FROM lab_sandbox").fetchone()[0] == "unrelated-state"


def test_second_worker_cannot_claim_running_job(setup):
    _, runtime, _, _, _, _, _ = setup
    enqueue(setup)
    first, second = Worker(runtime), Worker(runtime)
    assert first.tick() is True
    assert second.tick() is False


def test_private_execution_and_append_only_journal(setup):
    store, runtime, _, student, _, _, _ = setup
    execution = enqueue(setup)
    stranger = UserPublic(username="another", role=Role.student, testbed="local")
    for operation in (runtime.detail, runtime.cancel, runtime.retry_recovery, runtime.events):
        with pytest.raises(KeyError):
            operation(execution["id"], stranger)
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:
            db.execute("DELETE FROM lab_journal")
    events = runtime.events(execution["id"], student)
    assert runtime.events(execution["id"], student, after=events[-1]["id"]) == []


def test_api_assignment_resolution_and_execution(lab, monkeypatch):
    from app.api.v1.endpoints import laboratory
    client, store, user = lab
    experiment = post(client, "/experiments", EXPERIMENT).json()
    revision = post(client, f"/experiments/{experiment['id']}/revisions", complete().model_dump()).json()
    campaign = post(client, "/campaigns", {"revision_id": revision["id"]}).json()
    body = {"username": user.username, "observed_ue": "video", "competing_ue": "load"}
    assert post(client, "/assignments", body).status_code == 403
    subject = user.model_copy()
    user.role = Role.teacher
    monkeypatch.setattr(laboratory, "resolve_subject", lambda _: subject)
    grant = post(client, "/assignments", body).json()
    user.role = Role.student
    response = post(client, f"/campaigns/{campaign['id']}/start", {"assignment_id": grant["id"], "mode": "dry_run"})
    assert response.status_code == 202, response.text
    execution = response.json()
    drain(Worker(Runtime(store)))
    assert client.get(BASE + f"/executions/{execution['id']}").json()["status"] == "completed"
    assert client.get(BASE + f"/executions/{execution['id']}/events").status_code == 200
    assert post(client, f"/campaigns/{campaign['id']}/start", {"assignment_id": grant["id"], "mode": "remote"}).status_code == 422


def test_migration_from_frozen_v1_preserves_all_design_data(tmp_path):
    store = Repository(tmp_path / "v1.db")
    with store.connect() as db:
        db.executescript((Path(__file__).parent / "fixtures/laboratory_v1.sql").read_text())
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'lab_%'").fetchall()
    student = UserPublic(username="student", role=Role.student, testbed="local")
    experiment = store.create_experiment(student, "original-create", EXPERIMENT)
    revision = store.create_revision(experiment["id"], student, "original-revision", complete().model_dump())
    campaign = store.create_campaign(revision["id"], student, "original-plan")
    tables = ("experiments", "revisions", "campaigns", "requests", "events")
    with store.connect() as db:
        before = {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t}")] for t in tables}
    store.initialize()
    store.initialize()
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert before == {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t}")] for t in tables}
        assert json.loads(db.execute("SELECT plan FROM campaigns WHERE id=?", (campaign["id"],)).fetchone()[0]) == campaign["plan"]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(store.list_experiments(student)) == 1
    assert store.create_experiment(student, "original-create", EXPERIMENT) == experiment
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:
            db.execute("UPDATE revisions SET sha256='tampered'")
    with store.connect() as db:
        db.execute("PRAGMA user_version=6")
    with pytest.raises(RuntimeError, match="más reciente"):
        store.initialize()


def race(*operations):
    barrier = Barrier(len(operations))
    def run(operation):
        barrier.wait()
        try:
            return operation()
        except LeaseLost:
            return "lease_lost"
    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


@pytest.mark.parametrize("stop", ["cancel", "revoke", "expire_assignment"])
def test_stop_between_heartbeat_and_apply_prevents_effect(setup, stop):
    store, runtime, clock, student, teacher, grant, _ = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    worker.tick(); worker.tick()
    row = runtime.active(worker.id)
    runtime.heartbeat(row["id"], row["token"])
    if stop == "cancel":
        runtime.cancel(row["id"], student)
    elif stop == "revoke":
        runtime.revoke(grant["id"], teacher)
    else:
        with store.connect() as db:
            db.execute("UPDATE lab_assignments SET expires_at=?", (clock[0],))
    step = runtime.work(row)[row["cursor"]]
    runtime.intent(row["id"], row["token"], step)
    with pytest.raises(Cancelled):
        worker.adapter.perform(row, step)
    drain(worker)
    assert runtime.detail(execution["id"], student)["status"] == "cancelled"
    assert not [e for e in runtime.events(row["id"], student) if e["event"] == "step_result" and e["detail"]["action"] == "apply"]


def test_heartbeat_never_resurrects_expired_lease_and_old_owner_cannot_write(setup):
    _, runtime, clock, student, _, _, _ = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    worker.tick(); worker.tick(); worker.tick()
    row = runtime.active(worker.id)
    clock[0] += LEASE_SECONDS
    race(lambda: runtime.heartbeat(row["id"], row["token"]), runtime.expire,
         lambda: runtime.cancel(row["id"], student))
    recoverer = Worker(runtime)
    drain(recoverer)
    for operation in (
        lambda: runtime.heartbeat(row["id"], row["token"]),
        lambda: runtime.intent(row["id"], row["token"], {"action": "apply"}),
        lambda: runtime.result(row["id"], row["token"], {}),
        lambda: runtime.finish(row["id"], row["token"], "completed"),
        lambda: worker.adapter.perform(row, {"action": "restore", "run": None}),
    ):
        with pytest.raises(LeaseLost):
            operation()
    assert runtime.detail(execution["id"], student)["status"] == "cancelled"


def test_unknown_result_is_not_replayed_even_by_same_worker(setup):
    _, runtime, _, student, _, _, _ = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    worker.tick(); worker.tick()
    row = runtime.active(worker.id)
    step = runtime.work(row)[row["cursor"]]
    runtime.intent(row["id"], row["token"], step)
    worker.adapter.perform(row, step)
    drain(worker)
    assert runtime.detail(execution["id"], student)["status"] == "failed"
    assert len([e for e in runtime.events(row["id"], student) if e["event"] == "step_intent" and e["detail"]["action"] == "apply"]) == 1


def test_watchdog_recovers_without_claiming_new_campaigns(setup):
    store, runtime, clock, student, _, grant, campaign = setup
    execution = enqueue(setup)
    watchdog = Worker(runtime)
    assert watchdog.tick(recovery_only=True) is False
    worker = Worker(runtime)
    worker.tick(); worker.tick(); worker.tick()
    clock[0] += LEASE_SECONDS + 1
    assert watchdog.tick(recovery_only=True) is True
    assert runtime.detail(execution["id"], student)["status"] == "failed"
    new_plan = store.create_campaign(campaign["revision_id"], student, "after-watchdog")
    runtime.enqueue(new_plan["id"], grant["id"], student, "after-watchdog")
    assert watchdog.tick(recovery_only=True) is False


def test_missing_sandbox_does_not_count_as_verified_recovery(setup):
    store, runtime, _, student, _, _, _ = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    worker.tick()
    with store.connect() as db:
        db.execute("DELETE FROM lab_sandbox")
    runtime.cancel(execution["id"], student)
    worker.tick()
    assert runtime.detail(execution["id"], student)["error_code"] == "recovery_failed"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM lab_leases").fetchone()[0] == 1


def test_revoke_racing_claim_and_two_recoverers(setup):
    _, runtime, clock, student, teacher, grant, _ = setup
    execution = enqueue(setup)
    worker = Worker(runtime)
    race(worker.tick, lambda: runtime.revoke(grant["id"], teacher))
    clock[0] += LEASE_SECONDS + 1
    first, second = Worker(runtime), Worker(runtime)
    race(first.tick, second.tick)
    drain(first); drain(second)
    assert runtime.detail(execution["id"], student)["status"] == "cancelled"


@pytest.mark.parametrize("abrupt", [False, True])
def test_actual_worker_process_shutdown_and_independent_watchdog(setup, tmp_path, abrupt):
    store, _, _, student, _, grant, campaign = setup
    runtime = Runtime(store)
    with store.connect() as db:
        db.execute("UPDATE lab_assignments SET expires_at=?", (time.time() + 3600,))
    execution = runtime.enqueue(campaign["id"], grant["id"], student, "process-start")
    stop_file = tmp_path / "worker.stop"
    env = {**os.environ, "EMS_LABORATORY_DATABASE_PATH": str(store.path),
           "EMS_DATABASE_PATH": str(tmp_path / "ems.db"),
           "EMS_CAPTURE_DIR": str(tmp_path / "captures"), "EMS_BACKUP_DIR": str(tmp_path / "backups"),
           "EMS_ALLOWED_CONFIG_ROOTS": json.dumps([str(tmp_path / "config")])}
    command = [sys.executable, "-m", "app.laboratory.worker"]
    process = subprocess.Popen([*command, "--stop-file", str(stop_file)], env=env,
                               cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with store.connect() as db:
                state = db.execute("SELECT value FROM lab_sandbox").fetchone()
                changed = state and state[0] != "nominal"
            if changed:
                break
            assert process.poll() is None, process.communicate()
            time.sleep(0.02)
        else:
            pytest.fail("Worker process did not apply sandbox intervention")
        if abrupt:
            process.kill()
        else:
            stop_file.touch()
        process.communicate(timeout=10)
        if not abrupt:
            assert process.returncode == 0
            assert runtime.detail(execution["id"], student)["error_code"] == "worker_stopped"
        else:
            # Advance expiry in this isolated test instead of sleeping for 30 seconds.
            with store.connect() as db:
                db.execute("UPDATE lab_leases SET expires_at=?", (time.time() - 1,))
        recovered = subprocess.run([*command, "--watchdog-only", "--once"], env=env,
                                   cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10)
        assert recovered.returncode == 0, recovered.stderr
        assert runtime.detail(execution["id"], student)["status"] == "failed"
        with store.connect() as db:
            assert db.execute("SELECT value FROM lab_sandbox").fetchone()[0] == "nominal"
            assert db.execute("SELECT COUNT(*) FROM lab_leases").fetchone()[0] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_concurrent_initialization_of_empty_store(tmp_path):
    path = tmp_path / "concurrent.db"
    race(*(lambda: Repository(path).initialize() for _ in range(3)))
    with Repository(path).connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_execution_rejects_moved_testbed_and_grant_budget_exhaustion(setup):
    store, runtime, _, student, _, grant, campaign = setup
    execution = enqueue(setup)
    moved = student.model_copy(update={"testbed": "different"})
    for operation in (runtime.detail, runtime.events, runtime.cancel, runtime.retry_recovery):
        with pytest.raises(KeyError):
            operation(execution["id"], moved)
    assert runtime.assignments(moved) == []
    drain(Worker(runtime))
    with store.connect() as db:
        db.execute("UPDATE lab_assignments SET used_jobs=max_jobs")
    plan = store.create_campaign(campaign["revision_id"], student, "exhausted-plan")
    with pytest.raises(ConflictError, match="agotada"):
        runtime.enqueue(plan["id"], grant["id"], student, "exhausted-start")


def test_event_cursor_paginates_without_omissions(setup):
    store, runtime, _, student, _, grant, original = setup
    old_revision = store.get_revision(original["revision_id"], student)
    descriptor = complete().model_copy(update={"repetitions": 4})
    revision = store.create_revision(old_revision["experiment_id"], student, "long-revision", descriptor.model_dump())
    campaign = store.create_campaign(revision["id"], student, "long-campaign")
    execution = runtime.enqueue(campaign["id"], grant["id"], student, "long-start")
    drain(Worker(runtime))
    first = runtime.events(execution["id"], student)
    second = runtime.events(execution["id"], student, first[-1]["id"])
    assert len(first) == 200 and second[-1]["event"] == "finished"
    ids = [e["id"] for e in first + second]
    assert ids == sorted(set(ids))
    with store.connect() as db:
        assert len(ids) == db.execute("SELECT COUNT(*) FROM lab_journal WHERE execution_id=?", (execution["id"],)).fetchone()[0]
