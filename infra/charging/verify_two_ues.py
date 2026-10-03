"""Concurrent bounded traffic and independent charging/APN evidence for two live UEs."""
import argparse
import asyncio
import json
import time
from uuid import uuid4
from e2e_native import Lab, ROOT
from lab_command import get_settings
from app.services import terminal, terminal_sessions
from app.services.charging import management_get, mask_identifiers

SECOND = 'imsi-999700000000002'


def transfer(session):
    settings = get_settings()
    ue = Lab(settings, settings.ue_ssh_port)
    try:
        results = []
        for asset in ('seg000.m4s', 'seg001.m4s', 'seg002.m4s'):
            raw = ue.run(['curl', '--silent', '--fail', '--noproxy', '*', '--interface', session['interface'],
                          '--connect-timeout', '3', '--max-time', '12', '--max-filesize', '2000000',
                          '-o', '/dev/null', '-w', '%{json}', 'http://10.210.50.1:18090/media/720p/' + asset])
            result = json.loads(raw)
            assert result['http_code'] == 200 and result['local_ip'] == session['address']
            results.append({k: result[k] for k in ('http_code', 'local_ip', 'size_download')})
        return results
    finally:
        ue.client.close()


async def verify():
    settings = get_settings()
    primary = (await terminal.read_terminal())['supi']
    ids = [primary, SECOND]
    old = {}
    evidence = {'status': 'RUNNING'}
    ue = Lab(settings, settings.ue_ssh_port)
    stopped = False
    try:
        states = {s: await terminal.snapshot(s) for s in ids}
        assert all(s['registered'] and len(s['apn_sessions']) == 2 for s in states.values())
        links = [p['interface'] for s in states.values() for p in s['apn_sessions']]
        assert len(set(links)) == 4, 'Shared interfaces between identities'
        for supi in ids:
            old[supi] = states[supi]['active_apn']
        await terminal_sessions.select('internet', primary)
        await terminal_sessions.select('corporate', SECOND)
        portal = await terminal_sessions.intranet(SECOND)
        assert (await terminal.snapshot(primary))['active_apn'] == 'internet'
        assert portal['client_ip'].startswith('10.46.')
        await terminal_sessions.select('internet', SECOND)
        sessions = {s: await terminal_sessions.resolve(s, required='internet') for s in ids}
        before = {s: management_get('/admin/v1/accounts/' + s) for s in ids}
        before_cdr = {s: management_get('/admin/v1/cdrs?supi=' + s)['total'] for s in ids}
        transfers = await asyncio.gather(*(asyncio.to_thread(transfer, sessions[s]) for s in ids))
        await asyncio.sleep(2)
        after = {s: management_get('/admin/v1/accounts/' + s) for s in ids}
        after_states = {s: await terminal.snapshot(s) for s in ids}
        for s in ids:
            assert after[s]['consumed_bytes'] > before[s]['consumed_bytes'], 'No per-UE debit'
            assert after[s]['quota_bytes'] == before[s]['quota_bytes'], 'Unexpected quota modification'
            before_rx = next(p['rx_bytes'] for p in states[s]['apn_sessions'] if p['apn'] == 'internet')
            after_rx = next(p['rx_bytes'] for p in after_states[s]['apn_sessions'] if p['apn'] == 'internet')
            assert after_rx > before_rx, 'No independently observed RX increase'
        evidence.update(before=before, after=after, transfers=transfers, portal=portal,
                        per_ue_interfaces={s: after_states[s]['apn_sessions'] for s in ids})
        # Close only UE-02 via NAS, prove UE-01 survives and CDR belongs to UE-02.
        ue.run(['/home/emsadmin/UERANSIM/build/nr-cli', SECOND, '--exec', 'deregister switch-off'])
        ue.run(['systemctl', 'stop', 'ueransim-ue-02'], sudo=True)
        stopped = True
        await asyncio.sleep(3)
        assert (await terminal.snapshot(primary))['registered'], 'Primary UE was affected'
        cdr = management_get('/admin/v1/cdrs?supi=' + SECOND)
        assert cdr['total'] > before_cdr[SECOND], 'No UE-02 closing CDR'
        assert management_get('/admin/v1/cdrs?supi=' + primary)['total'] == before_cdr[primary], 'Primary session unexpectedly closed'
        assert all(x['supi'] == SECOND for x in cdr['items'])
        assert management_get('/admin/v1/accounts/' + SECOND)['reserved_bytes'] == 0, 'UE-02 reservations not released'
        evidence['second_cdr'] = cdr
        evidence['status'] = 'PASS'
    finally:
        if stopped:
            ue.run(['systemctl', 'start', 'ueransim-ue-02'], sudo=True)
            for attempt in range(35):
                state = await terminal.snapshot(SECOND)
                if len(state['apn_sessions']) == 2:
                    break
                # UERANSIM can remain LIMITED-SERVICE after missing broadcast SI
                # at startup. Retry only this non-registered UE once, never gNB.
                if attempt == 15 and not state['registered'] and state['native_state'].get('mm-state') == 'MM-DEREGISTERED/LIMITED-SERVICE':
                    ue.run(['systemctl', 'restart', 'ueransim-ue-02'], sudo=True)
                    evidence['ue02_recovery_retry'] = 'LIMITED-SERVICE after missing SI'
                await asyncio.sleep(1)
            else:
                evidence['status'] = 'RECOVERY_FAILED'
        for supi, apn in old.items():
            try:
                await terminal_sessions.select(apn, supi)
            except Exception:
                evidence.setdefault('preference_restore_failed', []).append(supi)
        ue.client.close()
        path = ROOT / '.work' / ('two-ues-' + uuid4().hex[:10] + '.json')
        path.write_text(json.dumps(mask_identifiers(evidence), indent=2), encoding='utf-8')
        print(json.dumps({'status': evidence['status'], 'evidence': str(path)}))
    assert evidence['status'] == 'PASS'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required: consumes traffic on both accounts and restarts UE-02')
    asyncio.run(verify())
