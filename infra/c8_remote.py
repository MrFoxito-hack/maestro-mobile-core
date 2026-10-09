"""C8 logged SSH commands; credentials use the existing backend environment."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ROOT = Path(os.environ.get('C8_CAMPAIGN_ROOT', ROOT / '.work/c8-campaign')).resolve()
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings
from app.laboratory.qoe_transport import Lab


class LoggedLab(Lab):
    def __init__(self, settings, port, evidence):
        self.port, self.evidence = port, Path(evidence)
        super().__init__(settings, port)

    def log(self, row):
        self.evidence.mkdir(parents=True, exist_ok=True)
        row.update(at=datetime.now(timezone.utc).isoformat(), port=self.port)
        with (self.evidence / f'commands-{self.port}.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def run(self, args, **kwargs):
        try:
            output = super().run(args, **kwargs)
            self.log({'argv': args, 'options': kwargs, 'output': output})
            return output
        except Exception as exc:
            self.log({'argv': args, 'options': kwargs, 'error_type': type(exc).__name__})
            raise

    def write(self, path, data, mode=0o600):
        super().write(path, data, mode)
        raw = data.encode() if isinstance(data, str) else data
        self.log({'write': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--timeout', type=int, default=30)
    p.add_argument('--evidence', type=Path, default=CAMPAIGN_ROOT / 'setup')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    host = LoggedLab(get_settings(), a.port, a.evidence)
    try:
        print(host.run(a.command, sudo=True, check=False, timeout=a.timeout))
    finally:
        host.client.close()
