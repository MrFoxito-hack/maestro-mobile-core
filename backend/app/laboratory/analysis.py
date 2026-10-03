"""Offline paired analysis. Missing/invalid runs never become zero-valued samples."""
import statistics
from typing import Literal

from pydantic import Field, model_validator

from app.laboratory.schemas import StrictModel

ANALYZER_VERSION = 'paired-startup-v1'


class Trial(StrictModel):
    ordinal: int = Field(ge=1)
    block: int = Field(ge=1)
    treatment: Literal['disabled', 'enabled']
    startup_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    execution_status: Literal['completed', 'failed', 'cancelled']
    validity_status: Literal['valid', 'invalid', 'inconclusive']
    exclusion_reason: str | None = Field(default=None, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class Dataset(StrictModel):
    schema_version: Literal[1] = 1
    source: Literal['live_campaign', 'historical_import', 'synthetic_test']
    metric: Literal['player_startup_delay_seconds'] = 'player_startup_delay_seconds'
    unit: Literal['seconds'] = 'seconds'
    provenance: str = Field(min_length=5, max_length=1000)
    trials: list[Trial] = Field(min_length=1, max_length=720)

    @model_validator(mode='after')
    def unique_trials(self):
        if len({t.ordinal for t in self.trials}) != len(self.trials):
            raise ValueError('Ensayo duplicado.')
        if len({(t.block, t.treatment) for t in self.trials}) != len(self.trials):
            raise ValueError('Tratamiento duplicado en un bloque.')
        return self


def analyze(dataset: Dataset) -> dict:
    pairs, excluded = [], []
    for block in sorted({t.block for t in dataset.trials}):
        trials = {t.treatment: t for t in dataset.trials if t.block == block}
        reasons = []
        if set(trials) != {'disabled', 'enabled'}:
            reasons.append('incomplete_pair')
        for t in trials.values():
            if t.execution_status != 'completed': reasons.append(f'run_{t.ordinal}_not_completed')
            if t.validity_status != 'valid': reasons.append(t.exclusion_reason or f'run_{t.ordinal}_{t.validity_status}')
            if t.startup_seconds is None: reasons.append(f'run_{t.ordinal}_missing_measurement')
        if reasons:
            excluded.append({'block': block, 'reasons': reasons})
            continue
        baseline, enabled = trials['disabled'], trials['enabled']
        pairs.append({'block': block, 'disabled_seconds': baseline.startup_seconds,
                      'enabled_seconds': enabled.startup_seconds,
                      'delta_seconds': enabled.startup_seconds - baseline.startup_seconds,
                      'evidence_ids': sorted(set(baseline.evidence_ids + enabled.evidence_ids))})
    deltas = [p['delta_seconds'] for p in pairs]
    return {'analyzer_version': ANALYZER_VERSION, 'metric': dataset.metric, 'unit': dataset.unit,
            'source': dataset.source, 'paired_unit': 'randomized_block', 'pairs': pairs, 'excluded': excluded,
            'mean_delta_seconds': statistics.mean(deltas) if deltas else None,
            'median_delta_seconds': statistics.median(deltas) if deltas else None,
            'sample_standard_deviation_seconds': statistics.stdev(deltas) if len(deltas) > 1 else None,
            'confidence_interval': None, 'hypothesis_outcome': 'inconclusive',
            'limitations': ['Diferencia = activado menos desactivado; valores negativos indican menor espera.',
                            'No se infiere causalidad ni significación por una diferencia observada.',
                            'Intervalos y umbral de efecto deben fijarse tras piloto antes de contrastar la hipótesis.']}
