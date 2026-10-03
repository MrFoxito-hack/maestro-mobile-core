import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from app.laboratory.analysis import Dataset, analyze
from app.laboratory.live import LivePreflight, session_identity
from app.laboratory.repository import ConflictError, canonical
from app.laboratory.research import Research
from app.laboratory.schemas import BindingCreate, NotebookCreate
from app.models import Role, UserPublic
from test_laboratory_runtime import setup, enqueue, drain  # noqa: F401
from test_laboratory import lab, post, EXPERIMENT, complete, BASE  # noqa: F401


def binding():
    return BindingCreate(observed_supi='imsi-999700000000001', competing_supi='imsi-999700000000004')


def test_binding_is_teacher_only_immutable_and_never_in_export(setup):
    store, _, _, student, teacher, grant, campaign = setup
    research = Research(store)
    # This fixture's grants use a controlled clock.
    research.clock = lambda: 1000
    with pytest.raises(KeyError): research.bind(grant['id'], student, 'forbidden', binding())
    value = research.bind(grant['id'], teacher, 'bind-0001', binding())
    assert value == research.bind(grant['id'], teacher, 'bind-0001', binding())
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db: db.execute('DELETE FROM lab_bindings')
    experiment = store.get_revision(campaign['revision_id'], student)['experiment_id']
    exported = zipfile.ZipFile(io.BytesIO(research.export(experiment, student)))
    assert binding().observed_supi.encode() not in b''.join(exported.read(n) for n in exported.namelist())


def test_preflight_is_idempotent_private_and_replays_without_new_ssh(setup):
    store, _, _, student, teacher, grant, campaign = setup
    calls = []
    class Collector:
        raw = {'private': 'SUPI not public'}
        def collect(self, bindings, budget):
            calls.append(bindings)
            assert budget == 1_000_000_000
            return {'source': 'live_ssh', 'execution_ready': False, 'checks': []}
    research = Research(store, Collector, clock=lambda: 1000)
    research.bind(grant['id'], teacher, 'bind-0001', binding())
    first = research.collect(campaign['id'], grant['id'], student, 'collect-0001')
    assert research.collect(campaign['id'], grant['id'], student, 'collect-0001') == first
    assert len(calls) == 1 and first['execution_ready'] is False
    assert 'private' not in str(first)
    moved = student.model_copy(update={'testbed': 'different'})
    with pytest.raises(KeyError): research.preflight(first['id'], moved)
    research.clock = lambda: 1061
    assert research.preflight(first['id'], student)['stale'] is True


def test_revocation_during_read_is_recorded_and_no_execute_permission(setup):
    store, runtime, _, student, teacher, grant, campaign = setup
    class Collector:
        def collect(self, *_):
            runtime.revoke(grant['id'], teacher)
            return {'execution_ready': False}
    research = Research(store, Collector, clock=lambda: 1000)
    research.bind(grant['id'], teacher, 'bind-0001', binding())
    result = research.collect(campaign['id'], grant['id'], student, 'collect-0001')
    assert result['document']['authorization_changed'] is True
    assert result['execution_ready'] is False


def test_session_matching_requires_unique_pdu_and_tun():
    native = 'PDU Session1:\n state: PS-ACTIVE\n apn: internet\n address: 10.45.0.9'
    links = [{'ifname': 'uesimtun7', 'addr_info': [{'local': '10.45.0.9'}]}]
    assert session_identity(native, links, 'internet')['interface'] == 'uesimtun7'
    with pytest.raises(ValueError): session_identity(native, links * 2, 'internet')
    with pytest.raises(ValueError): session_identity(native, links, 'corporate')
    with pytest.raises(ValueError): session_identity(native, [], 'internet')


def test_missing_live_data_does_not_turn_into_zero_quota_or_valid_readiness():
    class Transport:
        def read(self, *args): raise RuntimeError('secret')
    bindings = {r: {'alias': r, 'supi': s, 'dnn': 'internet'} for r, s in [('observed', binding().observed_supi), ('competing', binding().competing_supi)]}
    report = LivePreflight(Transport()).collect(bindings, 1000)
    assert report['execution_ready'] is False
    assert all(s['unreserved_bytes'] is None for s in report['subjects'].values())
    assert 'secret' not in str(report)


def dataset(ref='ref', *, invalid=False):
    return Dataset(source='synthetic_test', provenance='isolated test fixture', trials=[
        {'ordinal': 1, 'block': 1, 'treatment': 'disabled', 'startup_seconds': 2.0,
         'execution_status': 'completed', 'validity_status': 'valid', 'evidence_ids': [ref]},
        {'ordinal': 2, 'block': 1, 'treatment': 'enabled', 'startup_seconds': None if invalid else 1.0,
         'execution_status': 'completed', 'validity_status': 'inconclusive' if invalid else 'valid', 'evidence_ids': [ref]},
    ])


def test_analysis_preserves_pairing_and_never_claims_hypothesis_from_one_pair():
    result = analyze(dataset())
    assert result['mean_delta_seconds'] == -1
    assert result['confidence_interval'] is None
    assert result['sample_standard_deviation_seconds'] is None
    assert result['hypothesis_outcome'] == 'inconclusive'
    missing = analyze(dataset(invalid=True))
    assert missing['mean_delta_seconds'] is None
    assert len(missing['excluded']) == 1 and missing['pairs'] == []


def test_duplicate_and_nonfinite_measurements_rejected():
    document = dataset().model_dump()
    document['trials'].append(document['trials'][0])
    with pytest.raises(ValueError): Dataset.model_validate(document)
    document = dataset().model_dump()
    document['trials'][0]['startup_seconds'] = float('nan')
    with pytest.raises(ValueError): Dataset.model_validate(document)


def test_evidence_analysis_notebook_export_hashes_and_privacy(setup):
    store, runtime, _, student, _, _, campaign = setup
    experiment = store.get_revision(campaign['revision_id'], student)['experiment_id']
    research = Research(store)
    original = research.add_evidence(experiment, student, 'original-01', 'receiver_observation', {'source': 'synthetic_test', 'value': 1})
    data = research.add_evidence(experiment, student, 'dataset-01', 'dataset', dataset(original['id']).model_dump())
    analysis = research.analyze(experiment, data['id'], student, 'analysis-01')
    assert research.analyze(experiment, data['id'], student, 'analysis-01') == analysis
    research.note(experiment, student, 'note-0001', NotebookCreate(prediction='Espero una menor espera inicial.', evidence_ids=[original['id']], outcome='inconclusive'))
    outsider = UserPublic(username='outsider', role=Role.admin, testbed=student.testbed)
    for operation in (research.evidence, research.notes, research.export):
        with pytest.raises(KeyError): operation(experiment, outsider)
    with pytest.raises(ValueError): research.note(experiment, student, 'wrong-ref', NotebookCreate(prediction='Espero una menor espera inicial.', evidence_ids=['missing']))
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db: db.execute('UPDATE lab_evidence SET sha256=?', ('altered',))
    content = research.export(experiment, student)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        for entry in manifest['files']:
            raw = archive.read(entry['path'])
            assert len(raw) == entry['bytes']
            assert hashlib.sha256(raw).hexdigest() == entry['sha256']
        assert all('..' not in name for name in archive.namelist())
        entries = json.loads(archive.read('evidence.json'))
        saved = next(e for e in entries if e['kind'] == 'analysis')['document']
        reanalyzed = analyze(Dataset.model_validate(next(e for e in entries if e['kind'] == 'dataset')['document']))
        assert all(saved[k] == v for k, v in reanalyzed.items())


def test_document_and_experiment_storage_limits(setup, monkeypatch):
    from app.laboratory import research as module
    store, _, _, student, _, _, campaign = setup
    experiment = store.get_revision(campaign['revision_id'], student)['experiment_id']
    research = Research(store)
    monkeypatch.setattr(module, 'MAX_DOCUMENT_BYTES', 10)
    with pytest.raises(ValueError): research.add_evidence(experiment, student, 'limit-001', 'source', {'long': 'x' * 30})
    monkeypatch.setattr(module, 'MAX_DOCUMENT_BYTES', 100)
    monkeypatch.setattr(module, 'MAX_EXPERIMENT_BYTES', 1)
    with pytest.raises(ConflictError): research.add_evidence(experiment, student, 'limit-002', 'source', {'ok': 1})


def test_research_routes_keep_student_values_separate_from_measured_evidence(lab):
    client, _, _ = lab
    experiment = post(client, '/experiments', EXPERIMENT).json()
    path = f"/experiments/{experiment['id']}"
    assert post(client, path + '/notes', {'prediction': 'Mi predicción de prueba', 'outcome': 'supported'}).status_code == 201
    assert client.get(BASE + path + '/notes').json()[0]['document']['outcome'] == 'supported'
    assert client.get(BASE + path + '/evidence').json() == []
    assert client.get(BASE + path + '/export').headers['content-type'] == 'application/zip'
    assert post(client, path + '/evidence', {'metrics': {'mos': 5}}).status_code == 405
