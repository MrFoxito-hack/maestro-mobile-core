"""Positive projection of authorized immutable evidence, never operator files."""
import hashlib
import math

from app.laboratory.analysis import Dataset, analyze
from app.laboratory.evidence_validation import validate_references
from app.laboratory.repository import canonical
from app.laboratory.research import Research

VERSION = 'authorized-qoe-context-v4'
DOCUMENTS = (
    {'id': 'doc_01', 'version': 'laboratory-method-v1',
     'text': 'La ejecucion completada no demuestra validez experimental. Un ACK N7/PFCP no demuestra enforcement. La recuperacion de auxiliares no verifica politicas efectivas.'},
    {'id': 'doc_02', 'version': 'qoe-measurement-v2',
     'text': 'La espera inicial incluye el reproductor y el relay SSH. P.1203 estima calidad; no es una evaluacion subjetiva. Una pareja no demuestra causalidad ni significacion.'},
    {'id': 'doc_03', 'version': 'laboratory-method-v1',
     'text': 'Comparar tratamientos requiere medios identicos, condiciones verificadas y repeticiones. El consumo CHF no se borra para recuperar una prueba.'},
    {'id': 'doc_04', 'version': 'qoe-pilot-architecture-v1',
     'text': 'El actuador de NWDAF usa politicas de PCF/SMF/UPF. No configura el reproductor ni el relay SSH. La variacion del reproductor o relay es una explicacion alternativa a caracterizar, no una capacidad de control de NWDAF.'},
)


def finite(value, minimum=0, maximum=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('invalid_authorized_measurement')
    return value


def build(store, experiment_id, dataset_id, user):
    entries = {r['id']: r for r in Research(store).evidence(experiment_id, user)}
    item = entries.get(dataset_id)
    if not item or item['kind'] != 'dataset': raise KeyError('Dataset no encontrado.')
    dataset = Dataset.model_validate(item['document'])
    if dataset.source not in ('live_campaign', 'historical_import'):
        raise ValueError('La asistencia requiere evidencia real o historica identificada.')
    if len(dataset.trials) > 12: raise ValueError('Selecciona un expediente de hasta doce ensayos.')
    analyses = [r for r in entries.values() if r['kind'] == 'analysis'
                and r['document'].get('input_id') == dataset_id
                and r['document'].get('input_sha256') == item['sha256']]
    if not analyses: raise ValueError('Analiza el dataset antes de solicitar asistencia.')
    outcome = analyze(dataset)
    references, observations = {}, []
    def add(claim, values, source):
        identity = f'ev_{len(observations)+1:02d}'
        observations.append({'id': identity, 'claim': claim, 'values': values})
        references[identity] = {'evidence_id': source['id'], 'sha256': source['sha256']}
    add('El analizador conserva un dictamen inconcluso.',
        {'valid_pairs': len(outcome['pairs']), 'excluded_pairs': len(outcome['excluded']),
         'hypothesis_outcome': 'inconclusive'}, analyses[-1])
    for index, trial in enumerate(dataset.trials):
        validate_references(entries, trial.evidence_ids, dataset.source)
        # No free text, aliases, SUPI, provenance paths, exclusion strings,
        # operator reports, teacher notes or injected-cause fields cross here.
        values = {'subject': 'observed_ue', 'trial': index + 1,
                  'controller': 'OFF' if trial.treatment == 'disabled' else 'ON',
                  'execution_status': trial.execution_status, 'validity_status': trial.validity_status}
        if trial.startup_seconds is not None:
            values['startup_seconds'] = finite(trial.startup_seconds, maximum=1000)
        source = entries[trial.evidence_ids[0]]
        if source['kind'] == 'live_qoe' and source['document'].get('source') == 'live_campaign':
            metrics = source['document'].get('metrics', {})
            if trial.startup_seconds is not None and metrics.get('startup_delay_seconds') != trial.startup_seconds:
                raise ValueError('dataset_measurement_mismatch')
            for key in ('received_payload_bytes', 'receiver_rx_delta_bytes', 'competing_received_bytes',
                        'rebuffer_count', 'rebuffer_seconds'):
                if key in metrics: values[key] = finite(metrics[key])
            if metrics.get('p1203_mos') is not None:
                values['p1203_mos'] = finite(metrics['p1203_mos'], minimum=1, maximum=5)
        # Observations are authoritative server text. Model must cite and copy;
        # quantitative output cannot be made up or changed by the model.
        # Display rounds to four decimals; raw values remain in values/evidence.
        claim = f"Ensayo {index+1}, NWDAF {values['controller']}: "
        claim += ('espera inicial '+format(values['startup_seconds'], '.4f')+' s; '
                  if 'startup_seconds' in values else 'espera inicial ausente; ')
        claim += 'validez '+trial.validity_status+'.'
        add(claim, values, source)
    model_context = {'version': VERSION, 'source': dataset.source,
                     'observations': observations, 'documents': list(DOCUMENTS),
                     'official_conclusion': 'inconclusive'}
    return {'schema_version': 1, 'model_context': model_context, 'references': references,
            'dataset_id': dataset_id, 'dataset_sha256': item['sha256'],
            'context_sha256': hashlib.sha256(canonical(model_context).encode()).hexdigest()}
