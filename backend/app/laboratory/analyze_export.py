"""Verify an authorized ZIP and regenerate paired analyses entirely offline."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from app.laboratory.analysis import Dataset, analyze
from app.laboratory.evidence_validation import validate_references
from app.laboratory.measurements import EffectEvidence, assess_effect, references
from app.laboratory.repository import canonical
from app.laboratory.player_observation import startup_from_player


def verify_and_analyze(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 5000: raise ValueError('Duplicate or excessive ZIP entries')
        if sum(i.file_size for i in archive.infolist()) > 25_000_000: raise ValueError('ZIP exceeds export limit')
        if any(Path(n).is_absolute() or '..' in Path(n).parts or '\\' in n or ':' in n for n in names):
            raise ValueError('Unsafe archive entry')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema_version') != 1: raise ValueError('Unsupported manifest')
        if len({e['path'] for e in manifest['files']}) != len(manifest['files']):
            raise ValueError('Duplicate manifest entries')
        if set(names) != {'manifest.json', *(e['path'] for e in manifest['files'])}: raise ValueError('Incomplete manifest')
        for entry in manifest['files']:
            raw = archive.read(entry['path'])
            if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('Artifact hash mismatch')
        evidence = json.loads(archive.read('evidence.json'))
        by_id = {e['id']: e for e in evidence}
        if len(by_id) != len(evidence): raise ValueError('Duplicate evidence IDs')
        for e in evidence:
            if hashlib.sha256(canonical(e['document']).encode()).hexdigest() != e['sha256']:
                raise ValueError('Evidence hash mismatch')
        results, player_measurements = [], []
        for e in evidence:
            if e['kind'] != 'historical_source': continue
            trace = e['document'].get('observations', {}).get('player')
            if isinstance(trace, dict):
                player_measurements.append({'evidence_id': e['id'], 'source': e['document']['source'],
                                            **startup_from_player(trace)})
        for e in evidence:
            if e['kind'] == 'effect_evidence':
                document = EffectEvidence.model_validate(e['document'])
                validate_references(by_id, references(document), document.source)
                results.append({'input_id': e['id'], 'input_sha256': e['sha256'], **assess_effect(document)})
            if e['kind'] != 'dataset': continue
            dataset = Dataset.model_validate(e['document'])
            for trial in dataset.trials:
                validate_references(by_id, trial.evidence_ids, dataset.source)
            results.append({'input_id': e['id'], 'input_sha256': e['sha256'], **analyze(dataset)})
        return {'archive_verified': True, 'network_access': False, 'analyses': results,
                'recomputed_player_measurements': player_measurements}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_and_analyze(args.archive), indent=2))


if __name__ == '__main__': main()
