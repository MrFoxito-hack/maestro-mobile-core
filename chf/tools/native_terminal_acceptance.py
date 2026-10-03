"""Real native HTTP/2 + actual CHF DB, explicit usage fixtures (NOT UE traffic)."""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.repository import ChargingRepository
from tools.native_acceptance import OWNER, SUPI, free_port
from tools.contract_validation import validator
from tools.recover_release import parse_journal


def main():
    evidence = Path(tempfile.mkdtemp(prefix='native-terminal-', dir=ROOT.parent / '.work'))
    results = {}
    for mode, quota, expected_debit, expected_overrun in (
        ('final-usage', 10000, 10000, 280),
        ('concurrent-close', 20000, 10280, 0),
    ):
        directory = evidence / mode
        directory.mkdir()
        port = free_port()
        token = secrets.token_urlsafe(40)
        env = os.environ | {'CHF_DATABASE_PATH': str(directory / 'chf.db'),
                            'CHF_SBI_TOKENS': json.dumps({OWNER: token}),
                            'CHF_SBI_LAB_NO_AUTH': 'false', 'SMF_CHF_TOKEN': token}
        repository = ChargingRepository(directory / 'chf.db')
        repository.initialize()
        repository.upsert_account(SUPI, quota, True, 'terminal-component-fixture')
        config = {'logger': {'level': 'error'}, 'global': {'max': {'ue': 16}},
                  'smf': {'sbi': {'server': [{'address': '127.0.0.254', 'port': 18084}]},
                          'chf': {'enabled': True, 'sbi': [{'addr': '127.0.0.1', 'port': port}],
                                  'requested_units': 10000, 'nf_instance_id': OWNER,
                                  'journal_dir': str(directory / 'journal'), 'timeout_ms': 1500, 'retries': 2}}}
        config_path = directory / 'probe.yaml'
        config_path.write_text(json.dumps(config))
        with (directory / 'http.log').open('w') as log:
            server = subprocess.Popen([sys.executable, '-m', 'hypercorn', 'tools.native_fault_app:app',
                                       '--bind', f'127.0.0.1:{port}', '--access-logfile', '-',
                                       '--access-logformat', '%(H)s %(s)s %(r)s'], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/ready', timeout=.5): break
                    except OSError:
                        if time.monotonic() > deadline or server.poll() is not None: raise
                probe = subprocess.run([str(ROOT.parent / 'open5gs/build/src/smf/chf-probe'), str(config_path), mode],
                                       env=env, capture_output=True, text=True, timeout=40)
                (directory / 'probe.log').write_text(probe.stdout + probe.stderr)
                assert probe.returncode == 0, probe.stdout + probe.stderr
                assert 'NCHF_NETWORK_OWNER_DETACHED=1' in probe.stdout
                retries = [int(line.split('=')[1]) for line in probe.stdout.splitlines() if line.startswith('NCHF_RETRIES=')]
                assert 1 <= retries[-1] <= 2, retries
                requests = [json.loads(line.split('=', 1)[1]) for line in probe.stdout.splitlines()
                            if line.startswith('NCHF_REQUEST_JSON=')]
                for request in requests: validator('ChargingDataRequest').validate(request)
                release = requests[-1]['multipleUnitUsage'][0]['usedUnitContainer']
                assert [r['localSequenceNumber'] for r in release] == ([0, 1] if mode == 'final-usage' else [1, 2])
                with repository.transaction() as conn:
                    session = dict(conn.execute('SELECT * FROM charging_sessions').fetchone())
                    cdr = json.loads(conn.execute('SELECT record_json FROM charging_cdrs').fetchone()[0])
                assert session['status'] == 'RELEASED'
                assert session['observed_bytes'] == 10280
                assert session['consumed_bytes'] == expected_debit
                assert session['overrun_bytes'] == expected_overrun
                assert session['uplink_bytes'] == 5140 and session['downlink_bytes'] == 5140
                assert repository.get_account(SUPI)['reserved_bytes'] == 0
                (directory / 'fixture-cdr.json').write_text(json.dumps(cdr, indent=2))
                journal = [p for p in (directory / 'journal').iterdir() if not p.name.startswith('charging-id-')]
                assert len(journal) == 1
                entries = [json.loads(line) for line in journal[0].read_text().splitlines()]
                assert entries[-1]['event'] == 'response_valid' and entries[-1]['status'] == 204
                assert parse_journal(journal[0].read_bytes())[0]['state'] == 'closed'
                assert len([e for e in entries if e['event'] == 'pfcp_usage']) == (2 if mode == 'final-usage' else 3)
                results[mode] = {'status': 'PASS', 'reported_bytes': 10280,
                                 'debited_bytes': expected_debit, 'overrun_bytes': expected_overrun}
            finally:
                server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        http_log = (directory / 'http.log').read_text()
        assert '2 503' in http_log and '2 204' in http_log
    (evidence / 'results.json').write_text(json.dumps(results, indent=2))
    print(json.dumps({'evidence': str(evidence), 'results': results}, indent=2))


if __name__ == '__main__': main()
