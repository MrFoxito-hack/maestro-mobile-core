import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.api.v1.endpoints.laboratory import repository, router
from app.laboratory.planner import plan_runs, validate_design
from app.laboratory.repository import Repository, canonical
from app.laboratory.schemas import Descriptor
from app.models import Role, UserPublic


@pytest.fixture
def lab(tmp_path):
    store = Repository(tmp_path / "laboratory.db")
    store.initialize()
    user = UserPublic(username="student-a", role=Role.student, testbed="lab-a")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[repository] = lambda: store
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as client:
        yield client, store, user


BASE = "/api/v1/laboratory"
EXPERIMENT = {"title": "Control y vídeo", "question": "¿Mejora el vídeo con el control?",
              "hypothesis": "La espera inicial disminuye al reducir la congestión."}


def post(client, path, body, key="request-key-0001"):
    return client.post(BASE + path, json=body, headers={"Idempotency-Key": key})


def complete():
    return Descriptor(observed_ue="video", competing_ue="load", load_mbps=[0, 5, 10],
                      repetitions=2, measurement_seconds=30,
                      campaign_budget_bytes=1_000_000_000, capture_budget_bytes=100_000_000)


def create_revision(client, descriptor=None):
    experiment = post(client, "/experiments", EXPERIMENT).json()
    response = post(client, f"/experiments/{experiment['id']}/revisions",
                    (descriptor or complete()).model_dump())
    assert response.status_code == 201, response.text
    return experiment, response.json()


def test_persistence_hash_and_idempotent_campaign(lab):
    client, store, user = lab
    experiment, revision = create_revision(client)
    assert revision["sha256"] == hashlib.sha256(canonical(revision["descriptor"]).encode()).hexdigest()
    validation = client.post(BASE + f"/revisions/{revision['id']}/validate").json()
    assert validation["design_valid"] is True
    assert validation["execution_ready"] is False
    response = post(client, "/campaigns", {"revision_id": revision["id"]})
    assert response.status_code == 201
    replay = post(client, "/campaigns", {"revision_id": revision["id"]})
    assert replay.json() == response.json()
    reopened = Repository(store.path)
    reopened.initialize()
    detail = reopened.detail(experiment["id"], user)
    assert len(detail["campaigns"]) == 1
    assert detail["campaigns"][0]["plan"] == response.json()["plan"]
    assert detail["campaigns"][0]["execution_status"] == "planned"


def test_key_conflict_and_no_duplicate_audit(lab):
    client, store, _ = lab
    first = post(client, "/experiments", EXPERIMENT)
    assert post(client, "/experiments", EXPERIMENT).json() == first.json()
    assert post(client, "/experiments", {**EXPERIMENT, "title": "Otra hipótesis"}).status_code == 409
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_revisions_are_immutable_and_numbered(lab):
    client, store, _ = lab
    experiment, first = create_revision(client)
    second = post(client, f"/experiments/{experiment['id']}/revisions",
                  complete().model_dump(), key="second-request").json()
    assert [first["number"], second["number"]] == [1, 2]
    for sql in ("UPDATE revisions SET descriptor='{}'", "DELETE FROM revisions"):
        with pytest.raises(sqlite3.IntegrityError):
            with store.connect() as db:
                db.execute(sql)


def test_private_drafts_do_not_grant_cross_user_or_cross_testbed_access(lab):
    client, _, user = lab
    experiment, revision = create_revision(client)
    for username, testbed in (("student-b", "lab-a"), ("student-a", "lab-b")):
        user.username, user.testbed = username, testbed
        assert client.get(BASE + "/experiments").json() == []
        assert client.get(BASE + f"/experiments/{experiment['id']}").status_code == 404
        assert client.post(BASE + f"/revisions/{revision['id']}/validate").status_code == 404
        assert post(client, "/campaigns", {"revision_id": revision["id"]}).status_code == 404
        assert post(client, f"/experiments/{experiment['id']}/revisions", complete().model_dump()).status_code == 404


def test_draft_can_be_saved_but_cannot_be_planned(lab):
    client, store, _ = lab
    _, revision = create_revision(client, Descriptor())
    validation = client.post(BASE + f"/revisions/{revision['id']}/validate").json()
    assert not validation["design_valid"]
    assert {i["field"] for i in validation["issues"]} >= {"observed_ue", "load_mbps", "repetitions"}
    assert post(client, "/campaigns", {"revision_id": revision["id"]}).status_code == 422
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [
    ("seed", True), ("repetitions", 0), ("measurement_seconds", 301),
    ("load_mbps", [5, 5]), ("load_mbps", [-1]), ("load_mbps", ["5"]),
    ("load_mbps", [False]), ("observed_ue", "../../shell"), ("scenario", "4g-epc"),
    ("shell_command", "restart"),
])
def test_unsupported_or_invalid_input_rejected(lab, field, value):
    client, _, _ = lab
    experiment = post(client, "/experiments", EXPERIMENT).json()
    body = {**complete().model_dump(), field: value}
    assert post(client, f"/experiments/{experiment['id']}/revisions", body).status_code == 422


def test_nonfinite_levels_rejected():
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            Descriptor(load_mbps=[value])


def test_plan_pairs_every_condition_and_reproduces_order():
    descriptor = complete()
    runs = plan_runs(descriptor)
    assert runs == plan_runs(descriptor)
    assert len(runs) == 12
    for index in range(0, len(runs), 2):
        a, b = runs[index:index + 2]
        assert a["block"] == b["block"]
        assert (a["load_mbps"], a["repetition"]) == (b["load_mbps"], b["repetition"])
        assert {a["treatment"], b["treatment"]} == {"controller_disabled_verified", "controller_enabled_verified"}
    validation = validate_design(descriptor)
    assert validation["estimates"] == {"runs": 12, "measurement_seconds": 360,
                                        "competing_payload_bytes": 225_000_000}


def test_budget_and_duplicate_subjects_block_planning():
    descriptor = complete().model_copy(update={"competing_ue": "video", "campaign_budget_bytes": 1})
    assert {i["code"] for i in validate_design(descriptor)["issues"]} == {"same_subject", "budget_exceeded"}
    with pytest.raises(ValueError):
        plan_runs(descriptor)


def test_concurrent_idempotency_is_atomic(lab):
    _, store, user = lab
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.create_experiment(user, "shared-key", EXPERIMENT), range(8)))
    assert len({result["id"] for result in results}) == 1
    assert len(store.list_experiments(user)) == 1


def test_no_network_execution_routes_or_live_claims(lab):
    client, _, _ = lab
    capabilities = client.get(BASE + "/capabilities").json()
    assert capabilities["live_verified"] is False
    assert next(c for c in capabilities["items"] if c["id"] == "slicing_fault")["status"] == "simulated"
    assert post(client, "/campaigns/anything/start", {"assignment_id": "unknown", "mode": "remote"}).status_code == 422
    assert client.get(BASE + "/templates").json()[0]["execution_available"] is False


def test_authentication_and_idempotency_header_required(lab):
    client, _, _ = lab
    assert client.post(BASE + "/experiments", json=EXPERIMENT).status_code == 422
    client.app.dependency_overrides.pop(current_user)
    assert client.get(BASE + "/experiments").status_code == 401


def test_future_schema_is_not_silently_downgraded(tmp_path):
    store = Repository(tmp_path / "future.db")
    with store.connect() as db:
        db.execute("PRAGMA user_version=6")
    with pytest.raises(RuntimeError):
        store.initialize()
