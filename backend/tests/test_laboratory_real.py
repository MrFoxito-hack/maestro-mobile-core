"""Real-mode software contracts. These fixtures are not live measurements."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.laboratory.qoe_adapter import QoEClosedLoopAdapter
from app.laboratory.repository import ConflictError
from app.laboratory.research import Research
from app.laboratory.schemas import AssignmentCreate, BindingCreate, Descriptor
from app.laboratory.worker import Worker
from test_laboratory_runtime import setup, drain  # noqa: F401
from test_laboratory import EXPERIMENT


def test_v3_migration_preserves_live_queue_binding_and_immutable_journal(tmp_path):
    from pathlib import Path
    from app.laboratory.migrations import RUNTIME_SCHEMA, RESEARCH_SCHEMA
    from app.laboratory.repository import Repository
    from app.laboratory.runtime import Runtime
    from app.models import UserPublic, Role
    store = Repository(tmp_path / 'v3.db')
    with store.connect() as db:
        db.executescript((Path(__file__).parent / 'fixtures/laboratory_v1.sql').read_text())
        db.executescript(RUNTIME_SCHEMA)
        db.executescript(RESEARCH_SCHEMA)
    user = UserPublic(username='teacher', role=Role.teacher, testbed='local')
    runtime = Runtime(store)
    grant = runtime.grant(user, user, 'grant', AssignmentCreate(username='teacher', observed_ue='video', competing_ue='load'))
    exp = store.create_experiment(user, 'exp', EXPERIMENT)
    revision = store.create_revision(exp['id'], user, 'rev', Descriptor(observed_ue='video', competing_ue='load', load_mbps=[19], repetitions=1,
        measurement_seconds=10, campaign_budget_bytes=70_000_000, capture_budget_bytes=4_000_000).model_dump())
    campaign = store.create_campaign(revision['id'], user, 'plan')
    binding = Research(store).bind(grant['id'], user, 'bind', BindingCreate(observed_supi='imsi-999700000000001', competing_supi='imsi-999700000000004'))
    with store.connect() as db:
        db.execute("INSERT INTO lab_executions(id,campaign_id,assignment_id,owner,testbed,mode,status,token,created_at,updated_at) VALUES('job',?,?,'teacher','local','dry_run','queued','token','now','now')", (campaign['id'], grant['id']))
        db.execute("INSERT INTO lab_leases VALUES('dry-run:local','job','token',9999999999)")
        db.execute("INSERT INTO lab_journal(execution_id,event,cursor,token,detail,created_at) VALUES('job','queued',0,'token','{}','now')")
    store.initialize()
    store.initialize()
    with store.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT token,real_context,outcome FROM lab_executions').fetchone()[:] == ('token', '{}', '{}')
        assert db.execute('SELECT id FROM lab_bindings').fetchone()[0] == binding['id']
        assert db.execute('SELECT execution_id FROM lab_leases').fetchone()[0] == 'job'
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE lab_journal SET event='changed'")


def real_job(setup, *, bind=True, enqueue=True):
    store, runtime, _, student, teacher, _, _ = setup
    grant = runtime.grant(teacher, student, 'real-grant', AssignmentCreate(
        username=student.username, observed_ue='video', competing_ue='load', mode='real'))
    exp = store.create_experiment(student, 'real-experiment', EXPERIMENT)
    descriptor = Descriptor(observed_ue='video', competing_ue='load', load_mbps=[19], repetitions=1,
                            measurement_seconds=10, campaign_budget_bytes=70_000_000, capture_budget_bytes=4_000_000)
    revision = store.create_revision(exp['id'], student, 'real-revision', descriptor.model_dump())
    campaign = store.create_campaign(revision['id'], student, 'real-campaign')
    if bind:
        Research(store, clock=runtime.clock).bind(grant['id'], teacher, 'real-binding', BindingCreate(
            observed_supi='imsi-999700000000001', competing_supi='imsi-999700000000004'))
    if not enqueue: return grant, campaign
    return runtime.enqueue(campaign['id'], grant['id'], student, 'real-start', mode='real')


def test_dry_run_assignment_never_authorizes_network(setup):
    _, runtime, _, student, _, grant, campaign = setup
    with pytest.raises(PermissionError, match='modo'):
        runtime.enqueue(campaign['id'], grant['id'], student, 'escalate', mode='real')
    assert runtime.assignments(student)[0]['used_jobs'] == 0


def test_extracted_actuator_is_blocked_before_preparation_or_playback(tmp_path):
    from app.laboratory.acceptance import RealPreflightBlocked, real_availability
    from app.laboratory.qoe_session import session
    adapter = QoEClosedLoopAdapter(None, root=tmp_path)
    with pytest.raises(RealPreflightBlocked, match='real_actuator_not_accepted'):
        adapter.prepare({})
    with pytest.raises(RealPreflightBlocked): adapter.apply('controller_enabled', {})
    generator = session(None, None, None, None, None, None, None)
    with pytest.raises(RealPreflightBlocked): next(generator)
    assert real_availability()['real_pilot_available']
    assert not real_availability()['full_core_acceptance']
    assert list(tmp_path.iterdir()) == []


def test_clean_auxiliaries_cannot_certify_effective_policy_recovery(tmp_path, monkeypatch):
    class Host:
        client = SimpleNamespace(close=lambda: None)
        def __init__(self, *_): pass
        def run(self, args, **_):
            if args[:2] == ['systemctl', 'is-active']:
                return 'active' if args[2] == 'maestro-nwdaf' else 'inactive'
            return '[]' if args[0] == 'ip' and '-j' in args else ''
    monkeypatch.setattr('app.laboratory.qoe_transport.Lab', Host)
    monkeypatch.setattr('app.laboratory.qoe_session.accounts', lambda _: [])
    adapter = QoEClosedLoopAdapter(None, root=tmp_path)
    adapter.save = lambda _: None
    with pytest.raises(RuntimeError, match='recovery_not_verified'):
        adapter.verify_recovery({'execution_id': 'job', 'trials': {'1': {'ordinal': 1, 'tag': 'labqoe-test'}}})
    report = json.loads((tmp_path / 'job/recovery-report.json').read_text())
    assert all(report['trials'][0]['checks'].values())
    assert report['verified'] is False and report['policy_recovery_verified'] is False


def test_real_grant_requires_binding_and_pins_it_at_enqueue(setup):
    store, runtime, _, student, teacher, _, _ = setup
    grant, campaign = real_job(setup, bind=False, enqueue=False)
    with pytest.raises(ConflictError, match='vincular'):
        runtime.enqueue(campaign['id'], grant['id'], student, 'missing-binding', mode='real')
    binding = Research(store, clock=runtime.clock).bind(grant['id'], teacher, 'bind-real', BindingCreate(
        observed_supi='imsi-999700000000001', competing_supi='imsi-999700000000004'))
    job = runtime.enqueue(campaign['id'], grant['id'], student, 'bound', mode='real')
    with store.connect() as db:
        context = json.loads(db.execute('SELECT real_context FROM lab_executions WHERE id=?', (job['id'],)).fetchone()[0])
        assert context['binding_id'] == binding['id']
        assert db.execute('SELECT resource FROM lab_leases').fetchone()[0] == 'real-core:configured-vms'
    assert job['mode'] == 'real'
    assert job['metrics'] is None
    assert runtime.resource('another-testbed', 'real') == runtime.resource('local', 'real')


@pytest.mark.parametrize('free', [17_022_595, 22_000_000, 0])
def test_low_quota_fails_before_any_mutation_and_exports_recovered_attempt(setup, tmp_path, free):
    store, runtime, _, student, _, _, _ = setup
    job = real_job(setup)
    class Collector:
        raw = {'synthetic_test': True}
        def collect(self, bindings, budget):
            assert budget == 22_000_001
            return {'subjects': {'observed': {'unreserved_bytes': free}}, 'errors': {}, 'checks': []}
    class Adapter(QoEClosedLoopAdapter):
        def prepare(self, context): raise AssertionError('Low quota must not prepare resources')
        def apply(self, treatment, context): raise AssertionError('Low quota must not generate traffic')
    worker = Worker(runtime)
    worker.real_adapter = Adapter(runtime, collector_factory=Collector, root=tmp_path / 'runs')
    drain(worker)
    state = runtime.detail(job['id'], student)
    assert state['status'] == 'failed'
    assert state['error_code'] == 'insufficient_chf_quota'
    assert state['recovery_verified']
    assert state['validity_status'] == 'inconclusive'
    assert state['hypothesis_outcome'] == 'not_evaluated'
    assert not state['network_measurements'] and state['metrics'] is None
    directory = tmp_path / 'runs' / job['id']
    recovery = json.loads((directory / 'recovery-report.json').read_text())
    assert recovery['scope'] == 'no_mutation_attempted'
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert {'descriptor.json', 'recovery-report.json', 'execution-events.jsonl'} <= {e['path'] for e in manifest['files']}
    for item in manifest['files']:
        assert hashlib.sha256((directory / item['path']).read_bytes()).hexdigest() == item['sha256']
    events = [json.loads(s) for s in (directory / 'execution-events.jsonl').read_text().splitlines()]
    assert events[-1]['event'] == 'finished'
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM lab_sandbox').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM lab_leases').fetchone()[0] == 0


def test_cancelling_prepared_generator_never_enters_playback(tmp_path):
    effects = []
    def generator():
        try:
            yield {'phase': 'prepared'}
            effects.append('playback')
            yield {'phase': 'played'}
        finally:
            effects.append('cleanup')
    adapter = QoEClosedLoopAdapter(SimpleNamespace(), root=tmp_path)
    adapter.row = {'id': 'run'}
    adapter.recover_remote = lambda _: effects.append('remote_recovery')
    gen = generator()
    next(gen)
    adapter.generators[('run', 1)] = gen
    adapter.phases[('run', 1)] = 'prepared'
    adapter.compensate({'trials': {'1': {'ordinal': 1}}})
    assert effects == ['cleanup', 'remote_recovery']


class SoftwareAdapter(QoEClosedLoopAdapter):
    """No network activity; exercises state transitions and evidence integration."""
    def validate(self, context): return {'source': 'synthetic_test'}
    def prepare(self, context):
        run = context['current_run']
        context.setdefault('trials', {})[str(run['ordinal'])] = {'ordinal': run['ordinal'], 'run': run,
            'case': 'baseline' if 'disabled' in run['treatment'] else 'closed-loop'}
        return {}
    def apply(self, treatment, context): return {}
    def compensate(self, context): return {}
    def verify_recovery(self, context):
        for trial in context['trials'].values(): trial['recovered'] = True
        return {'verified': True}
    def verify(self, context):
        trial = context['trials'][str(context['current_run']['ordinal'])]
        trial['metrics'] = {'ordinal': trial['ordinal'], 'startup_delay_seconds': 1.0,
                            'p1203_mos': 4.0, 'validity_status': 'valid', 'source': 'synthetic_test'}
        return trial['metrics']


def test_two_trials_flow_through_worker_and_offline_analysis_without_promoting_hypothesis(setup, tmp_path):
    store, runtime, _, student, _, _, _ = setup
    job = real_job(setup)
    worker = Worker(runtime)
    worker.real_adapter = SoftwareAdapter(runtime, root=tmp_path)
    drain(worker)
    state = runtime.detail(job['id'], student)
    assert state['status'] == 'completed'
    assert len(state['metrics']) == 2
    assert state['source'] == 'synthetic_test' and state['network_measurements'] is False
    assert state['hypothesis_outcome'] == 'inconclusive'
    assert state['recovery_verified']
    assert json.loads((tmp_path / job['id'] / 'analysis.json').read_text())['pairs'][0]['delta_seconds'] == 0
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM lab_evidence WHERE kind='synthetic_qoe'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM lab_evidence WHERE kind='live_qoe'").fetchone()[0] == 0


def test_failed_recovery_keeps_global_real_reservation(setup, tmp_path):
    store, runtime, _, student, _, _, _ = setup
    job = real_job(setup)
    class FailingRecovery(SoftwareAdapter):
        def verify_recovery(self, context): raise RuntimeError('listener_remains')
    worker = Worker(runtime)
    worker.real_adapter = FailingRecovery(runtime, root=tmp_path)
    drain(worker)
    state = runtime.detail(job['id'], student)
    assert state['status'] == 'recovery_required'
    assert state['error_code'] == 'recovery_failed'
    assert state['recovery_verified'] is False
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM lab_leases').fetchone()[0] == 1
