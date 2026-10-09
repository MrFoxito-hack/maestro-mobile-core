"""Read native C3 admission, or execute the public mode round trip on Core."""
import argparse
from pathlib import Path

try:
    from .diagnose_c3_writers import diagnose
    from .run_c3_authority_cycle import main as cycle_main
except ImportError:
    from diagnose_c3_writers import diagnose
    from run_c3_authority_cycle import main as cycle_main
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    status = sub.add_parser('status', help='Read all seven native observers')
    status.add_argument('--transport', type=Path)
    status.add_argument('--evidence', type=Path)
    cycle = sub.add_parser('cycle', help='Acquire, submit and commit both modes')
    cycle.add_argument('--dry-run', action='store_true', help='Reads only; never acquire')
    cycle.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    if args.command == 'cycle':
        forwarded = ['--dry-run'] if args.dry_run else []
        if args.evidence:
            forwarded += ['--evidence', str(args.evidence)]
        return cycle_main(forwarded)
    report = diagnose(transport=args.transport)
    if args.evidence:
        args.evidence.mkdir(parents=True, exist_ok=False)
        (args.evidence / 'diagnosis.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['diagnosis']['ready_for_cycle'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
