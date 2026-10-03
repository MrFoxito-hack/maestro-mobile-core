"""Bounded JSON proposals. References and numbers are checked outside the LLM."""
import re
from typing import Literal
from pydantic import Field
from app.laboratory.schemas import StrictModel

class Observation(StrictModel):
    claim: str = Field(min_length=5, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=1)

class Hypothesis(StrictModel):
    id: str = Field(pattern=r'^h[1-3]$')
    description: str = Field(min_length=20, max_length=350, pattern=r'^Podr[ií]a ser que .+')
    supporting_evidence: list[str] = Field(max_length=6)
    contradicting_evidence: list[str] = Field(max_length=6)
    missing_information: list[str] = Field(min_length=1, max_length=3)

class EmptyParameters(StrictModel):
    pass

class ProposedTest(StrictModel):
    catalog_action: Literal['review_policy_evidence', 'compare_player_observations', 'review_receiver_counters']
    parameters: EmptyParameters
    expected_observations_by_hypothesis: dict[str, str]
    limitations: list[str] = Field(default_factory=list, max_length=3)

class Proposal(StrictModel):
    observations: list[Observation] = Field(min_length=1, max_length=3)
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    proposed_test: ProposedTest
    conclusion_status: Literal['supported', 'not_supported', 'inconclusive']

def validate(raw, context):
    proposal = Proposal.model_validate_json(raw)
    evidence = {e['id']: e for e in context['observations']}
    for observation in proposal.observations:
        identity = observation.evidence_ids[0]
        if identity not in evidence or observation.claim != evidence[identity]['claim']:
            raise ValueError('observation_not_grounded')
    measured_ids = {identity for identity, e in evidence.items() if 'startup_seconds' in e.get('values', {})}
    if measured_ids and not any(o.evidence_ids[0] in measured_ids for o in proposal.observations):
        raise ValueError('measured_observation_required')
    identities = [h.id for h in proposal.hypotheses]
    if len(set(identities)) != len(identities): raise ValueError('duplicate_hypothesis')
    prose = []
    for hypothesis in proposal.hypotheses:
        cited = hypothesis.supporting_evidence + hypothesis.contradicting_evidence
        if not cited or any(i not in evidence for i in cited): raise ValueError('unknown_evidence_reference')
        if set(hypothesis.supporting_evidence) & set(hypothesis.contradicting_evidence):
            raise ValueError('contradictory_reference_roles')
        prose.extend([hypothesis.description, *hypothesis.missing_information])
    expected = proposal.proposed_test.expected_observations_by_hypothesis
    if set(expected) != set(identities): raise ValueError('missing_discriminating_observations')
    prose.extend([*expected.values(), *proposal.proposed_test.limitations])
    trials = {e.get('values', {}).get('trial') for e in evidence.values()}
    for text in prose:
        def trial_reference(match):
            if int(match[1]) not in trials: raise ValueError('unknown_trial_reference')
            return 'ensayo citado'
        without_ids = re.sub(r'\bensayo\s+(\d+)\b', trial_reference, text, flags=re.I)
        if (not 3 <= len(text) <= 500 or re.search(r'\d|https?://|imsi-|\b(?:sudo|powershell|curl|bash)\b|\bssh\s+-', without_ids, re.I)):
            raise ValueError('unsupported_quantitative_or_command_prose')
    if proposal.conclusion_status != context['official_conclusion']:
        raise ValueError('model_cannot_override_scientific_outcome')
    return proposal

def output_schema(context):
    schema = Proposal.model_json_schema()
    measured = [e for e in context['observations'] if 'startup_seconds' in e.get('values', {})]
    schema['$defs']['Observation']['properties']['claim']['enum'] = [e['claim'] for e in (measured or context['observations'])]
    schema['$defs']['Observation']['properties']['evidence_ids']['items']['enum'] = [e['id'] for e in context['observations']]
    return schema
