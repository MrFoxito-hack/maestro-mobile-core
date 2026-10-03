"""Synthetic fixtures only: validate decisions, never create real network evidence."""
import json
import zipfile

import pytest

from app.laboratory.analyze_export import verify_and_analyze
from app.laboratory.measurements import CounterSample, EffectEvidence, ReceiverWindow, SessionIdentity, assess_effect, normalize
from app.laboratory.research import Research
from app.laboratory.evidence_validation import validate_references
from test_laboratory_runtime import setup  # noqa: F401


def identity():
    return SessionIdentity(subject_alias='video', generation='generation-1',
                           pdu_session_id=1, nf_instance='upf-lab', boot_id='boot-1')


def window():
    before = CounterSample(evidence_id='before', session=identity(), host='receiver',
                           clock_epoch='boot-1', monotonic_ns=1_000_000_000,
                           interface_index=7, point='receiver_payload', bytes=1000)
    after = before.model_copy(update={'evidence_id': 'after', 'monotonic_ns': 11_000_000_000, 'bytes': 5_001_000})
    return ReceiverWindow(before=before, after=after, coverage='complete')


def effect():
    return EffectEvidence(source='synthetic_test', session=identity(), receiver=window(),
                          policy_evidence_ids=['policy'], path_evidence_ids=['path'],
                          baseline_evidence_ids=['baseline'], recovery_evidence_ids=['recovery'],
                          offered_evidence_ids=['offered'], n7_ack_evidence_ids=['ack'],
                          correlation='exact', expected_max_bps=5_000_000, offered_bps=10_000_000)


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('bytes', 0, 'counter_reset'), ('monotonic_ns', 1_000_000_000, 'nonpositive_interval'),
    ('host', 'another', 'clock_epoch_changed'), ('clock_epoch', 'boot-2', 'clock_epoch_changed'),
    ('interface_index', 8, 'interface_changed'), ('point', 'sender_payload', 'measurement_point_changed'),
    ('evidence_id', 'before', 'duplicate_sample_reference'),
])
def test_bad_counter_intervals_are_missing_never_zero(field, value, reason):
    value_window = window()
    value_window.after = value_window.after.model_copy(update={field: value})
    result = normalize(value_window)
    assert result['throughput_bps'] is None and result['received_bytes'] is None
    assert reason in result['reasons']


def test_session_reused_address_generation_and_incomplete_coverage_are_not_measures():
    value = window()
    value.after.session = identity().model_copy(update={'generation': 'generation-2'})
    assert 'session_generation_changed' in normalize(value)['reasons']
    value = window()
    value.coverage = 'partial'
    assert normalize(value)['throughput_bps'] is None


def test_monotonic_rate_survives_wall_clock_correction_and_preserves_genuine_zero():
    value = window()
    value.before.utc_ns = 100_000_000_000
    value.after.utc_ns = 99_000_000_000
    assert normalize(value)['throughput_bps'] == 4_000_000
    value.after.bytes = value.before.bytes
    assert normalize(value)['throughput_bps'] == 0


def test_ack_without_reception_is_never_successful_enforcement():
    value = effect()
    value.receiver = None
    value.pfcp_ack_evidence_ids = ['pfcp-accepted']
    result = assess_effect(value)
    assert result['control_ack_observed']
    assert result['effect_finding'] == 'inconclusive'
    assert 'receiver_measurement_missing' in result['reasons']
    assert result['hypothesis_outcome'] == 'not_evaluated'


def test_measured_violation_is_valid_observation_without_supported_hypothesis():
    value = effect()
    value.receiver.after.bytes = 10_001_000
    result = assess_effect(value)
    assert result['measurement']['throughput_bps'] == 8_000_000
    assert result['validity_status'] == 'valid'
    assert result['effect_finding'] == 'limit_not_observed'
    assert result['hypothesis_outcome'] == 'not_evaluated'


@pytest.mark.parametrize('field', ['policy_evidence_ids', 'path_evidence_ids', 'baseline_evidence_ids', 'recovery_evidence_ids', 'offered_evidence_ids'])
def test_missing_decisive_evidence_blocks_finding(field):
    value = effect()
    setattr(value, field, [])
    assert assess_effect(value)['effect_finding'] == 'inconclusive'


def test_ambiguous_identity_insufficient_load_or_interface_counter_cannot_prove_limit():
    for update in ({'correlation': 'ambiguous'}, {'offered_bps': 1_000_000}):
        assert assess_effect(effect().model_copy(update=update))['effect_finding'] == 'inconclusive'
    value = effect()
    value.receiver.before.point = value.receiver.after.point = 'receiver_interface'
    assert 'payload_not_isolated' in assess_effect(value)['reasons']
    value = effect()
    value.session = identity().model_copy(update={'generation': 'wrong-session'})
    assert 'receiver_session_mismatch' in assess_effect(value)['reasons']


def test_measurement_contract_rejects_nonfinite_negative_or_unknown_fields():
    for update in ({'offered_bps': float('nan')}, {'expected_max_bps': -1}, {'pretend_enforced': True}):
        with pytest.raises(ValueError): EffectEvidence.model_validate(effect().model_dump() | update)


@pytest.mark.parametrize('source', ['live_campaign', 'historical_import'])
def test_test_fixture_cannot_be_promoted_to_measured_evidence(source):
    entries = {'test': {'kind': 'counter_sample', 'document': {'source': 'synthetic_test'}}}
    with pytest.raises(ValueError, match='procedencia'): validate_references(entries, ['test'], source)


def test_scoped_effect_analysis_export_offline_reproduction_and_tamper(setup, tmp_path):
    store, _, _, student, _, _, campaign = setup
    experiment = store.get_revision(campaign['revision_id'], student)['experiment_id']
    research = Research(store)
    value = effect().model_dump()
    for field in ('policy_evidence_ids', 'path_evidence_ids', 'baseline_evidence_ids', 'recovery_evidence_ids', 'offered_evidence_ids', 'n7_ack_evidence_ids'):
        item = research.add_evidence(experiment, student, field, 'observation', {'source': 'synthetic_test', 'description': field})
        value[field] = [item['id']]
    for point in ('before', 'after'):
        sample = research.add_evidence(experiment, student, point, 'counter_sample', {'source': 'synthetic_test', 'sample': value['receiver'][point]})
        value['receiver'][point]['evidence_id'] = sample['id']
    original = research.add_evidence(experiment, student, 'input', 'effect_evidence', value)
    result = research.analyze(experiment, original['id'], student, 'analyze')
    saved = next(e for e in research.evidence(experiment, student) if e['id'] == result['id'])['document']
    assert saved['effect_finding'] == 'consistent_with_limit'
    archive = tmp_path / 'dossier.zip'
    archive.write_bytes(research.export(experiment, student))
    offline = verify_and_analyze(archive)
    assert offline['network_access'] is False
    assert offline['analyses'] == [saved]
    with zipfile.ZipFile(archive) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    entries['evidence.json'] = b'[]'
    with zipfile.ZipFile(archive, 'w') as z:
        for name, data in entries.items(): z.writestr(name, data)
    with pytest.raises(ValueError, match='hash mismatch'): verify_and_analyze(archive)
    value['path_evidence_ids'] = ['another-experiment-reference']
    missing = research.add_evidence(experiment, student, 'missing', 'effect_evidence', value)
    with pytest.raises(ValueError, match='Referencia'): research.analyze(experiment, missing['id'], student, 'invalid')


def test_offline_archive_rejects_duplicate_manifest_and_traversal(tmp_path):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('../outside.json', '{}')
    with pytest.raises(ValueError, match='Unsafe'): verify_and_analyze(path)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps({'schema_version': 1, 'files': [{'path': 'data'}, {'path': 'data'}]}))
    with pytest.raises(ValueError, match='Duplicate manifest'): verify_and_analyze(path)
