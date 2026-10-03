"""Offline measurement contracts: scoped identities, clock epochs and evidence.

No interpolation, no network access, and no inference of enforcement from ACK.
The unit under examination is one receiver/session flow, never a whole slice.
"""
from typing import Literal

from pydantic import Field

from app.laboratory.schemas import StrictModel

NORMALIZER_VERSION = 'receiver-evidence-v1'


class SessionIdentity(StrictModel):
    subject_alias: str = Field(min_length=1, max_length=80)
    generation: str = Field(min_length=1, max_length=128)
    pdu_session_id: int = Field(ge=1, le=255, strict=True)
    nf_instance: str = Field(min_length=1, max_length=128)
    boot_id: str = Field(min_length=1, max_length=128)


class CounterSample(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    session: SessionIdentity
    host: str = Field(min_length=1, max_length=128)
    clock_epoch: str = Field(min_length=1, max_length=128)
    monotonic_ns: int = Field(ge=0, strict=True)
    utc_ns: int | None = Field(default=None, ge=0, strict=True)
    interface_index: int = Field(ge=1, strict=True)
    point: Literal['receiver_payload', 'receiver_interface', 'sender_payload']
    bytes: int = Field(ge=0, strict=True)


class ReceiverWindow(StrictModel):
    before: CounterSample
    after: CounterSample
    coverage: Literal['complete', 'partial', 'unknown'] = 'unknown'


def normalize(window: ReceiverWindow) -> dict:
    before, after = window.before, window.after
    reasons = []
    if before.session != after.session:
        reasons.append('session_generation_changed')
    if before.host != after.host or before.clock_epoch != after.clock_epoch:
        reasons.append('clock_epoch_changed')
    if before.interface_index != after.interface_index:
        reasons.append('interface_changed')
    if before.point != after.point:
        reasons.append('measurement_point_changed')
    if after.monotonic_ns <= before.monotonic_ns:
        reasons.append('nonpositive_interval')
    if after.bytes < before.bytes:
        reasons.append('counter_reset')
    if before.evidence_id == after.evidence_id:
        reasons.append('duplicate_sample_reference')
    if window.coverage != 'complete':
        reasons.append('incomplete_coverage')
    duration = None if reasons else (after.monotonic_ns - before.monotonic_ns) / 1e9
    delta = None if reasons else after.bytes - before.bytes
    return {'normalizer_version': NORMALIZER_VERSION,
            'validity_status': 'inconclusive' if reasons else 'valid',
            'reasons': reasons, 'duration_seconds': duration, 'counter_delta_bytes': delta,
            'received_bytes': delta if before.point.startswith('receiver_') else None,
            'throughput_bps': delta * 8 / duration if duration else None,
            'measurement_point': before.point,
            'evidence_ids': [before.evidence_id, after.evidence_id],
            'limitations': ['La tasa pertenece al intervalo y punto declarados.',
                            'Los bytes de interfaz incluyen tráfico ajeno al payload si no hay filtro.']}


class EffectEvidence(StrictModel):
    schema_version: Literal[1] = 1
    source: Literal['live_campaign', 'historical_import', 'synthetic_test']
    session: SessionIdentity
    policy_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    n7_ack_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    pfcp_ack_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    receiver: ReceiverWindow | None = None
    # These references must identify the independent observations, not an ACK.
    path_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    baseline_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    recovery_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    correlation: Literal['exact', 'derived', 'ambiguous', 'unavailable'] = 'unavailable'
    expected_max_bps: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    offered_bps: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    offered_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    tolerance_fraction: float = Field(default=0, ge=0, le=0.25, allow_inf_nan=False)
    minimum_window_seconds: float = Field(default=5, ge=1, le=300, allow_inf_nan=False)


def references(document: EffectEvidence) -> set[str]:
    result = set()
    for name in ('policy_evidence_ids', 'n7_ack_evidence_ids', 'pfcp_ack_evidence_ids',
                 'path_evidence_ids', 'baseline_evidence_ids', 'recovery_evidence_ids',
                 'offered_evidence_ids'):
        result.update(getattr(document, name))
    if document.receiver:
        result.update((document.receiver.before.evidence_id, document.receiver.after.evidence_id))
    return result


def assess_effect(document: EffectEvidence) -> dict:
    reasons = []
    measured = normalize(document.receiver) if document.receiver else None
    if not measured:
        reasons.append('receiver_measurement_missing')
    elif measured['validity_status'] != 'valid':
        reasons.extend(measured['reasons'])
    else:
        if document.receiver.before.session != document.session:
            reasons.append('receiver_session_mismatch')
        if measured['measurement_point'] != 'receiver_payload':
            reasons.append('payload_not_isolated')
        if measured['duration_seconds'] < document.minimum_window_seconds:
            reasons.append('measurement_window_too_short')
    if document.correlation != 'exact': reasons.append('correlation_not_exact')
    for field, reason in (
        ('policy_evidence_ids', 'policy_observation_missing'),
        ('path_evidence_ids', 'receiver_path_unverified'),
        ('baseline_evidence_ids', 'baseline_unverified'),
        ('recovery_evidence_ids', 'recovery_unverified'),
        ('offered_evidence_ids', 'offered_load_unverified'),
    ):
        if not getattr(document, field): reasons.append(reason)
    if document.expected_max_bps is None:
        reasons.append('expected_limit_missing')
    if (document.offered_bps is None or document.expected_max_bps is None
            or document.offered_bps <= document.expected_max_bps * (1 + document.tolerance_fraction)):
        reasons.append('offered_load_not_above_limit')
    finding = 'inconclusive'
    if not reasons:
        finding = ('consistent_with_limit' if measured['throughput_bps'] <=
                   document.expected_max_bps * (1 + document.tolerance_fraction) else 'limit_not_observed')
    return {'analyzer_version': NORMALIZER_VERSION, 'source': document.source,
            'scope': 'single_session_receiver_payload',
            'control_ack_observed': bool(document.n7_ack_evidence_ids or document.pfcp_ack_evidence_ids),
            'effect_finding': finding, 'validity_status': 'inconclusive' if reasons else 'valid',
            'hypothesis_outcome': 'not_evaluated', 'reasons': reasons, 'measurement': measured,
            'evidence_ids': sorted(references(document)),
            'limitations': ['ACK N7/PFCP no demuestra enforcement.',
                            'Una tasa compatible no demuestra causalidad de la política.',
                            'MBR por sesión no es límite agregado por slice.']}
