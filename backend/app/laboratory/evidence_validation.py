"""Shared reference checks for online and offline analysis."""

DERIVED_KINDS = {'dataset', 'analysis', 'effect_evidence', 'effect_analysis'}
SOURCES = {
    'synthetic_test': {'synthetic_test'},
    'historical_import': {'historical_import', 'live_campaign', 'live_ssh'},
    'live_campaign': {'live_campaign', 'live_ssh'},
}


def validate_references(entries, refs, source):
    for ref in refs:
        item = entries.get(ref)
        if not item or item['kind'] in DERIVED_KINDS:
            raise ValueError('Referencia de evidencia ausente o inválida.')
        if item['document'].get('source') not in SOURCES[source]:
            raise ValueError('La procedencia de la evidencia no corresponde al análisis; no mezcles simulación con medición.')
