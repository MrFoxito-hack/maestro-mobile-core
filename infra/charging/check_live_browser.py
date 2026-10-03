"""Run browser checks with a short-lived operator token.

Video and optional N6 checks generate real UE traffic and consume lab credit.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.security import create_token
from app.db import connection

with connection() as conn:
    user = conn.execute("SELECT username,role FROM users WHERE enabled=1 AND role='teacher' ORDER BY username LIMIT 1").fetchone()
if not user:
    raise SystemExit('No enabled teacher')
env = os.environ | {'MAESTRO_CHECK_TOKEN': create_token(user['username'], user['role']),
                    'MAESTRO_CHECK_USER': user['username']}
script = ('tools/check-terminal-apn.mjs' if '--apn' in sys.argv else
          'tools/check-terminal-video.mjs' if '--video' in sys.argv else
          'tools/check-terminal.mjs' if '--terminal' in sys.argv else 'tools/check-chf-live.mjs')
raise SystemExit(subprocess.call(['node', script], cwd=ROOT / 'frontend', env=env))
