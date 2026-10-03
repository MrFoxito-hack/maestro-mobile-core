"""Consume the current lab bag through the actual browser, preserve debit/CDRs.

Requires explicit --execute; restores the initial remaining credit with an
audited, idempotent topup of exactly the bytes consumed by this test. No reset.
"""
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4
from lab_command import get_settings
from app.services import terminal
from app.services.charging import management_get, management_request, mask_identifiers


def main():
    if '--execute' not in sys.argv:
        raise SystemExit('--execute required: consumes lab quota and replenishes test debit')
    data = json.loads(asyncio.run(terminal.adapter()._run('python3 -c ' + shlex.quote(terminal.STATUS_SCRIPT), port=get_settings().ue_ssh_port)))
    supi = data['supi']
    path = '/admin/v1/accounts/' + supi
    before = management_get(path)
    if not 5_000_000 <= before['available_bytes'] <= 60_000_000:
        raise SystemExit('Expected 5–60 MB available in the lab account; no account changes made')
    baseline = management_get('/admin/v1/cdrs?supi=' + supi + '&limit=100')
    evidence = {'before': before, 'cdrs_before': baseline}
    root = Path(__file__).resolve().parents[2]
    run_id = uuid4()
    try:
        subprocess.run([sys.executable, str(Path(__file__).with_name('check_live_browser.py')), '--video'],
                       env=os.environ | {'MAESTRO_VIDEO_EXHAUST': '1'}, check=True, timeout=360)
        time.sleep(3)
        after = management_get(path)
        cdrs = management_get('/admin/v1/cdrs?supi=' + supi + '&limit=100')
        evidence.update(after=after, cdrs_after=cdrs)
        assert after['consumed_bytes'] > before['consumed_bytes'], 'No confirmed debit'
        assert after['available_bytes'] == 0, 'Failure is not proven to be quota exhaustion'
        assert cdrs['total'] > baseline['total'], 'No new CDR'
        evidence['status'] = 'PASS'
        print('PASS: real browser stopped, available quota zero, confirmed debit and new CDR', flush=True)
    finally:
        current = management_get(path)
        debit = current['consumed_bytes'] - before['consumed_bytes']
        if 0 < debit <= 100_000_000 and current['quota_bytes'] == before['quota_bytes']:
            evidence['replenished'] = management_request(path + '/topup', payload={'requestId': str(run_id), 'amountBytes': debit})
            print('Replenished test debit with one audited topup: ' + str(debit) + ' bytes', flush=True)
        # Resume UE registration after quota-triggered release without altering its config.
        awaitable = terminal.airplane(False)
        asyncio.run(awaitable)
        if evidence.get('status') == 'PASS' and 'replenished' in evidence:
            for _ in range(20):
                state = asyncio.run(terminal.snapshot())
                if state['registered'] and state['interfaces']:
                    break
                time.sleep(1)
            evidence['recovery'] = {}
            for quality in ('720p', '1080p'):
                recovery = subprocess.run([sys.executable, str(Path(__file__).with_name('check_live_browser.py')), '--video'],
                    env=os.environ | {'MAESTRO_VIDEO_EXHAUST': '0', 'MAESTRO_VIDEO_QUALITY': quality}, timeout=75)
                evidence['recovery'][quality] = 'PASS' if recovery.returncode == 0 else 'FAIL'
        output = root / '.work' / ('video-quota-' + str(run_id) + '.json')
        output.write_text(json.dumps(mask_identifiers(evidence), indent=2), encoding='utf-8')
        print('Evidence: ' + str(output), flush=True)
        if evidence.get('recovery') and any(value != 'PASS' for value in evidence['recovery'].values()):
            raise RuntimeError('Post-topup playback failed; inspect recovery evidence')


if __name__ == '__main__':
    main()
