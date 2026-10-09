"""C5-only SSH helper. Run from backend so its existing .env is loaded.

Remote scripts and evidence are confined to the C5 directory. Credentials
remain in the existing environment files and are never included in evidence.
"""
import argparse
import json
from pathlib import Path

from e2e_native import Lab, ROOT
from lab_command import get_settings

EVIDENCE = ROOT / '.work/c5-charging/evidence'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, choices=(2222, 2226), required=True)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--sudo', action='store_true')
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args()
    if Path(args.output).name != args.output:
        parser.error('output must be a filename')
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    host = Lab(get_settings(), args.port)
    try:
        directory = host.run(['mktemp', '-d', '/home/emsadmin/c5-charging-XXXXXX']).strip()
        remote = directory + '/task.py'
        host.write(remote, args.script.read_bytes())
        result = host.run(['python3', remote], sudo=args.sudo, check=False, timeout=args.timeout)
        (EVIDENCE / args.output).write_text(result, encoding='utf-8')
        print(json.dumps({'evidence': str(EVIDENCE / args.output), 'bytes': len(result)}))
    finally:
        host.client.close()


if __name__ == '__main__':
    main()
