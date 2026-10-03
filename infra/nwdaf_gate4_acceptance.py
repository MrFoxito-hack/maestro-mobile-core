"""Consolidate measured Gate 4 criteria; absent RMSE never becomes a PASS."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(qoe, history, gate3):
    sources = {'qoe': qoe / 'qoe-comparison.json',
               'qoe_control': qoe / 'control-verification.json',
               'forecast': history / 'forecast-evaluation.json',
               'latency': history / 'latency-decomposition.json',
               'gate3': gate3 / 'analysis.json'}
    data = {key: json.loads(path.read_text(encoding='utf-8')) for key, path in sources.items()}
    rows = data['latency']['samples']
    scores = data['forecast']['slices']
    resources = data['gate3']['pcf_resources']
    cases = {case['phase']: case for case in data['qoe']['cases']}
    checks = {
        'gate3': data['gate3']['gate3'] == 'PASSED',
        'control_latency_observed_below_50ms': bool(rows) and all(0 <= x['control_ms'] < 50 for x in rows),
        'causal_rmse_below_5_percentage_points': len(scores) == 2 and all(
            s['status'] == 'MEASURED' and bool(s['predictions']) and
            all(0 <= s['rmse_percentage_points'].get(str(h), float('inf')) < 5 for h in (900, 1800)) for s in scores),
        'paired_real_qoe_preserved': data['qoe_control']['paired_control'] == 'CONFIRMED' and
            len(data['qoe']['immutable_ledger_rows']) == 2 and cases['closed-loop']['mos'] >= cases['baseline']['mos'],
        'pcf_cpu_rss_measured': resources['cpu_percent_one_core'] >= 0 and
            resources['wall_seconds'] > 0 and resources['rss_before_kib'] > 0 and resources['rss_after_kib'] > 0,
    }
    return {'gate3': data['gate3']['gate3'], 'gate4': 'PASSED' if all(checks.values()) else 'INCOMPLETE',
            'checks': checks, 'pending': [key for key, passed in checks.items() if not passed],
            'latency_scope': 'PCF decision to PFCP ACK; acquisition and polling separately reported.',
            'qoe_scope': 'One paired real AV experiment; no population significance claim.',
            'forecast_scope': 'Causal 15/30-minute holdout on observed PM; coverage and workload limitations remain in source.',
            'evaluated_workloads': {s['object_id']:s.get('evaluated_workload', {}) for s in scores},
            'predictive_congestion_validation': 'NOT_ESTABLISHED_BY_THIS_CHECK',
            'resource_scope': 'Whole patched PCF process; not marginal patch overhead.',
            'sources': {key: {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                        for key, path in sources.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qoe', 'history', 'gate3'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--output',type=Path,help='Preserve prior campaign verdict by writing a separate dated result.')
    args = parser.parse_args()
    result = summarize(args.qoe, args.history, args.gate3)
    path = args.output or args.qoe / 'gate4-acceptance.json'
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
