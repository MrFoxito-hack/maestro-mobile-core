"""Import an existing QoE campaign offline; never rerun or publish it to NWDAF."""
import argparse
import hashlib
import json
from pathlib import Path

from app.db import connection
from app.laboratory.analysis import Dataset, Trial
from app.laboratory.research import Research
from app.laboratory.storage import repository
from app.models import UserPublic


def import_qoe(store, user, directory: Path):
    files = {}
    for name in ('result.json', 'baseline-player.json', 'closed-loop-player.json'):
        path = directory / name
        if path.stat().st_size > 2_000_000: raise ValueError('Archivo fuera del límite de importación.')
        raw = path.read_bytes()
        files[name] = (json.loads(raw), hashlib.sha256(raw).hexdigest())
    result = files['result.json'][0]
    source_hash = files['result.json'][1]
    experiment = store.create_experiment(user, 'archive-' + source_hash, {
        'title': 'Archivo QoE · ' + directory.name,
        'question': '¿Qué diferencia de espera inicial muestran los registros históricos conservados?',
        'hypothesis': 'La espera inicial podría disminuir con el controlador; la validez causal requiere verificar las condiciones originales.'})
    research = Research(store)
    references = {}
    for name, (document, digest) in files.items():
        if name == 'result.json':
            safe = {'status': document.get('status'), 'utc': document.get('utc'),
                    'quota_unchanged': document.get('quota_unchanged'),
                    'transfers': [{k: t.get(k) for k in ('phase', 'asset', 'bytes', 'sha256', 'verified')} for t in document.get('transfers', [])]}
        else:
            player = document.get('player', {})
            safe = {'status': document.get('status'), 'phase': document.get('phase'),
                    'player': {k: player.get(k) for k in ('requested', 'firstPlaying', 'startup_seconds', 'played_seconds', 'ended', 'stalls')}}
            safe['player']['events'] = [{k: e.get(k) for k in ('type', 'monotonic_ms', 'utc_ms', 'current_time', 'ready_state', 'paused')} for e in player.get('events', [])]
        artifact = research.add_evidence(experiment['id'], user, 'source-v2-' + digest, 'historical_source',
                                       {'importer_version': 'qoe-archive-v2', 'filename': name, 'original_sha256': digest, 'source': 'historical_import', 'observations': safe})
        references[name] = artifact['id']
    trials = []
    for i, (phase, treatment) in enumerate((('baseline', 'disabled'), ('closed-loop', 'enabled')), 1):
        player = files[phase + '-player.json'][0]
        startup = player.get('player', {}).get('startup_seconds')
        transfers = [t for t in result.get('transfers', []) if t.get('phase') == phase]
        completed = player.get('status') == 'PLAYED_TO_END' and player.get('player', {}).get('ended') is True
        import math
        timing = player.get('player', {})
        clocks = [timing.get(k) for k in ('requested', 'firstPlaying', 'startup_seconds')]
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in clocks)
                or clocks[1] < clocks[0] or not math.isclose((clocks[1] - clocks[0]) / 1000, clocks[2], abs_tol=1e-6)):
            completed = False
        receipt = bool(transfers) and all(t.get('verified') is True for t in transfers)
        trials.append(Trial(ordinal=i, block=1, treatment=treatment, startup_seconds=startup,
                            execution_status='completed' if completed else 'failed',
                            validity_status='inconclusive' if completed and receipt else 'invalid',
                            exclusion_reason='historical_baseline_and_recovery_not_independently_verified' if completed and receipt else 'incomplete_player_or_transfer_evidence',
                            evidence_ids=[references['result.json'], references[phase + '-player.json']]))
    dataset = Dataset(source='historical_import', provenance='Existing archive ' + directory.name + '; original result sha256=' + source_hash, trials=trials)
    data = research.add_evidence(experiment['id'], user, 'dataset-v2-' + source_hash, 'dataset', dataset.model_dump())
    analysis = research.analyze(experiment['id'], data['id'], user, 'analysis-v2-' + source_hash)
    return {'experiment_id': experiment['id'], 'dataset_id': data['id'], 'analysis_id': analysis['id'], 'source': 'historical_import'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    with connection() as db:
        row = db.execute("SELECT username,role,testbed,assigned_imsi FROM users WHERE username=? AND enabled=1 AND role IN ('teacher','admin')", (args.owner,)).fetchone()
    if not row: raise SystemExit('Se requiere un docente/admin activo como propietario.')
    store = repository(); store.initialize()
    print(json.dumps(import_qoe(store, UserPublic(**dict(row)), args.directory.resolve())))


if __name__ == '__main__':
    main()
