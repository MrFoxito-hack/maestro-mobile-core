"""Isolated fabricated evidence and model responses; no GPU/Core calls."""
import asyncio
import json
import sqlite3
import time
import httpx
import pytest

from app.laboratory.investigation.context import build
from app.laboratory.investigation.model_client import ModelClient, ModelUnavailable, MODEL, MODEL_DIGEST
from app.laboratory.investigation.proposals import validate
from app.laboratory.investigation.service import Investigations, InvestigationCreate, SuggestRequest
from app.laboratory.research import Research
from app.laboratory.repository import canonical, ConflictError
from test_laboratory_runtime import setup  # noqa: F401
from test_laboratory_real import real_job
from test_laboratory import EXPERIMENT


def artifact(setup):
    store, _, _, student, _, _, _ = setup
    research = Research(store)
    exp = store.create_experiment(student, 'ai-test-exp', EXPERIMENT)
    measure = research.add_evidence(exp['id'], student, 'ai-test-measure', 'live_qoe', {
        'source': 'live_campaign', 'hidden_cause': 'TEACHER_SECRET', 'supi': 'imsi-999700000000001',
        'metrics': {'startup_delay_seconds': 2.5, 'p1203_mos': 3.2, 'received_payload_bytes': 1000,
                    'operator_note': 'TEACHER_SECRET', 'ip': '10.45.0.2'}})
    dataset = research.add_evidence(exp['id'], student, 'ai-test-dataset', 'dataset', {
        'source': 'live_campaign', 'provenance': 'TEACHER_SECRET private path',
        'trials': [{'ordinal': 1, 'block': 1, 'treatment': 'disabled', 'startup_seconds': 2.5,
                    'execution_status': 'completed', 'validity_status': 'inconclusive',
                    'exclusion_reason': 'TEACHER_SECRET', 'evidence_ids': [measure['id']]}]})
    research.analyze(exp['id'], dataset['id'], student, 'ai-test-analysis')
    return exp['id'], dataset['id']


def proposal(context):
    ev = context['observations'][-1]
    return {'observations': [{'claim': ev['claim'], 'evidence_ids': [ev['id']]}],
            'hypotheses': [{'id': 'h1', 'description': 'Podria ser que la espera incluya efectos del reproductor.',
                'supporting_evidence': [ev['id']], 'contradicting_evidence': [],
                'missing_information': ['Falta separar la contribucion del relay.']}],
            'proposed_test': {'catalog_action': 'compare_player_observations', 'parameters': {},
                'expected_observations_by_hypothesis': {'h1': 'La espera variaria con las condiciones del reproductor.'},
                'limitations': ['La revision no demuestra causalidad.']}, 'conclusion_status': 'inconclusive'}


def test_context_allowlist_hides_truth_and_other_owner(setup):
    store, _, _, user, teacher, _, _ = setup
    exp, dataset = artifact(setup)
    ctx = build(store, exp, dataset, user)
    raw = canonical(ctx)
    assert all(secret not in raw for secret in ('TEACHER_SECRET', 'imsi-', '10.45.', 'operator_note', 'private path'))
    assert ctx['model_context']['observations'][1]['values']['startup_seconds'] == 2.5
    with pytest.raises(KeyError): build(store, exp, dataset, teacher)
    with pytest.raises(KeyError): build(store, exp, dataset, user.model_copy(update={'testbed': 'elsewhere'}))


@pytest.mark.parametrize('fault', ['unknown', 'invented_number', 'semantic_observation', 'numeric_hypothesis', 'command', 'extra', 'conclusion', 'missing_prediction'])
def test_reject_invalid_model_proposals(setup, fault):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup); ctx = build(store, exp, dataset, user)['model_context']
    p = proposal(ctx)
    if fault == 'unknown': p['hypotheses'][0]['supporting_evidence'] = ['ev_99']
    if fault == 'invented_number': p['observations'][0]['claim'] = p['observations'][0]['claim'].replace('2.5', '200')
    if fault == 'semantic_observation': p['observations'][0]['claim'] = 'La politica efectiva fue restaurada.'
    if fault == 'numeric_hypothesis': p['hypotheses'][0]['description'] = 'Podria ser que la tasa recibida fuera 900 Mbps.'
    if fault == 'command': p['proposed_test']['catalog_action'] = 'run_shell'
    if fault == 'extra': p['proposed_test']['parameters'] = {'shell': 'anything'}
    if fault == 'conclusion': p['conclusion_status'] = 'supported'
    if fault == 'missing_prediction': p['proposed_test']['expected_observations_by_hypothesis'] = {}
    with pytest.raises(ValueError): validate(canonical(p), ctx)


def test_suggestions_persist_idempotently_and_are_advisory(setup):
    store, _, _, user, teacher, _, _ = setup
    exp, dataset = artifact(setup); calls = []
    class Client:
        async def generate(self, ctx, question):
            calls.append(question)
            return canonical(proposal(ctx)), {'model': 'test-double'}
    service = Investigations(store, enabled=True, client=Client(), clock=lambda: 1000)
    inv = service.create(exp, user, 'context-key', InvestigationCreate(dataset_id=dataset))
    result = asyncio.run(service.suggest(inv['id'], user, 'question-key', SuggestRequest()))
    assert result['status'] == 'completed' and result['commands_executed'] is False
    assert asyncio.run(service.suggest(inv['id'], user, 'question-key', SuggestRequest())) == result
    assert len(calls) == 1
    assert service.detail(inv['id'], user)['history'][0] == result
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(Research(store).export(exp, user))) as exported:
        history = json.loads(exported.read('investigation-history.json'))
        assert history[0]['response'] == result
    with pytest.raises(KeyError): service.detail(inv['id'], teacher)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM lab_ai_slots').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM lab_executions').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError): db.execute("UPDATE lab_ai_requests SET response='{}'")


def test_ssh_transport_term_is_not_an_executable_command(setup):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup); ctx = build(store, exp, dataset, user)['model_context']
    p = proposal(ctx)
    p['hypotheses'][0]['description'] = 'Podria ser que el relay SSH contribuya a la espera.'
    assert validate(canonical(p), ctx).proposed_test.parameters.model_dump() == {}
    p['hypotheses'][0]['description'] = 'Podria ser que ejecutar ssh -p cambie la configuracion.'
    with pytest.raises(ValueError): validate(canonical(p), ctx)


def test_accented_tentative_language_and_existing_trial_references(setup):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup); ctx = build(store, exp, dataset, user)['model_context']
    p = proposal(ctx)
    p['hypotheses'][0]['description'] = 'Podría ser que el relay explique parte de la espera del ensayo 1.'
    assert validate(canonical(p), ctx)
    p['hypotheses'][0]['description'] = p['hypotheses'][0]['description'].replace('ensayo 1', 'ensayo 99')
    with pytest.raises(ValueError, match='unknown_trial'): validate(canonical(p), ctx)


@pytest.mark.parametrize('reason', ['offline', 'timeout'])
def test_inference_failure_preserves_lab_and_has_bounded_exclusion(setup, reason):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup)
    class Client:
        async def generate(self, *_): raise ModelUnavailable(reason)
    service = Investigations(store, enabled=True, client=Client(), clock=lambda: 1000)
    inv = service.create(exp, user, 'context-key', InvestigationCreate(dataset_id=dataset))
    result = asyncio.run(service.suggest(inv['id'], user, 'question-key', SuggestRequest()))
    assert result['status'] == 'unavailable' and result['reason'] == reason
    assert len(Research(store).evidence(exp, user)) == 4
    with store.connect() as db:
        slots = db.execute('SELECT expires_at FROM lab_ai_slots').fetchall()
        assert bool(slots) == (reason == 'timeout')
        if slots: assert slots[0][0] == 1120


def test_no_inference_during_queued_real_acquisition(setup):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup)
    real_job(setup)
    class Client:
        async def generate(self, *_): pytest.fail('Inference must not start')
    service = Investigations(store, enabled=True, client=Client(), clock=lambda: 1000)
    inv = service.create(exp, user, 'context-key', InvestigationCreate(dataset_id=dataset))
    result = asyncio.run(service.suggest(inv['id'], user, 'question-key', SuggestRequest()))
    assert result['reason'] == 'acquisition_active'


def test_model_client_loopback_json_and_real_deadline():
    context = {'observations': [{'id': 'ev_01', 'claim': 'Registro de prueba controlada.'}]}
    seen = []
    async def handler(request):
        seen.append(str(request.url))
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': MODEL, 'digest': MODEL_DIGEST}]})
        body = json.loads(request.content)
        assert body['model'] == MODEL and 'tools' not in body
        assert body['options']['num_ctx'] == 4096
        return httpx.Response(200, content=b'{"message":{"content":"{}"},"done":false}\n{"done":true,"done_reason":"stop"}\n')
    raw, metrics = asyncio.run(ModelClient(transport=httpx.MockTransport(handler)).generate(context, 'fixture'))
    assert raw == '{}' and metrics['first_token_seconds'] >= 0
    assert all(url.startswith('http://127.0.0.1:11434/') for url in seen)
    async def slow(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})
    start = time.perf_counter()
    with pytest.raises(ModelUnavailable, match='timeout'):
        asyncio.run(ModelClient(transport=httpx.MockTransport(slow), deadline=.02).generate(context, 'fixture'))
    assert time.perf_counter()-start < .5


def test_inference_blocks_real_enqueue_but_never_changes_assignment(setup):
    store, runtime, _, user, _, _, _ = setup
    exp, dataset = artifact(setup)
    grant, campaign = real_job(setup, enqueue=False)
    class Client:
        async def generate(self, ctx, question):
            with pytest.raises(ConflictError, match='inferencia'):
                runtime.enqueue(campaign['id'], grant['id'], user, 'blocked-job', mode='real')
            return canonical(proposal(ctx)), {}
    service = Investigations(store, enabled=True, client=Client(), clock=lambda: 1000)
    inv = service.create(exp, user, 'context-key', InvestigationCreate(dataset_id=dataset))
    assert asyncio.run(service.suggest(inv['id'], user, 'question-key', SuggestRequest()))['status'] == 'completed'
    assert runtime.enqueue(campaign['id'], grant['id'], user, 'now-allowed', mode='real')['status'] == 'queued'


def test_cancelled_generation_keeps_exclusion_and_audits_cancel(setup):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup)
    class Client:
        async def generate(self, *_): raise asyncio.CancelledError()
    service = Investigations(store, enabled=True, client=Client(), clock=lambda: 1000)
    inv = service.create(exp, user, 'context-key', InvestigationCreate(dataset_id=dataset))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.suggest(inv['id'], user, 'question-key', SuggestRequest()))
    assert service.detail(inv['id'], user)['history'][0]['status'] == 'cancelled'
    with store.connect() as db:
        assert db.execute('SELECT expires_at FROM lab_ai_slots').fetchone()[0] == 1120


def test_slow_persistence_does_not_block_event_loop(setup, monkeypatch):
    store, _, _, user, _, _, _ = setup
    exp, dataset = artifact(setup)
    service = Investigations(store, enabled=False, clock=lambda: 1000)
    inv = service.create(exp, user, 'context-key', InvestigationCreate(dataset_id=dataset))
    original = service.begin
    def slow(*args):
        time.sleep(.15)
        return original(*args)
    monkeypatch.setattr(service, 'begin', slow)
    async def check():
        job = asyncio.create_task(service.suggest(inv['id'], user, 'question-key', SuggestRequest()))
        ticks = 0
        while not job.done():
            await asyncio.sleep(.01); ticks += 1
        assert (await job)['reason'] == 'disabled'
        assert ticks >= 5
    asyncio.run(check())
