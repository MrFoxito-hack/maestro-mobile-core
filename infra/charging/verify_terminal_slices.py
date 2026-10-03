"""Four real UE-bound HTTP probes plus relay verification; restore APN selection."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from e2e_native import Lab
from lab_command import get_settings
from app.services import terminal, terminal_sessions


async def verify():
    settings = get_settings()
    data = await terminal.read_terminal()
    description = terminal_sessions.describe(data)
    available = {s['apn']: s for s in description['apn_sessions']}
    if set(available) != {'internet', 'corporate'}:
        raise RuntimeError('Two observed active PDU sessions required')
    ue = Lab(settings, settings.ue_ssh_port)
    upf1 = Lab(settings, settings.upf_ssh_port)
    upf2 = Lab(settings, settings.upf2_ssh_port)
    evidence = {'sessions': available, 'matrix': [], 'rules_before': {}, 'rules_after': {}}
    targets = {'video': 'http://10.210.50.1:18090/media/720p/index.m3u8',
               'intranet': 'http://10.46.0.1:8080/intranet'}
    try:
        for name, host in [('upf1', upf1), ('upf2', upf2)]:
            evidence['rules_before'][name] = json.loads(host.run(['nft', '-j', 'list', 'table', 'inet', 'maestro_terminal'], sudo=True))
        for apn, session in available.items():
            for destination, url in targets.items():
                raw = ue.run(['curl', '--silent', '--fail', '--noproxy', '*', '--interface', session['interface'],
                              '--connect-timeout', '3', '--max-time', '5', '--max-filesize', '8192',
                              '-o', '/dev/null', '-w', '%{json}', url], check=False)
                result = json.loads(raw)
                allowed = (apn, destination) in [('internet', 'video'), ('corporate', 'intranet')]
                evidence['matrix'].append({'apn': apn, 'interface': session['interface'], 'destination': destination,
                    'expected_allowed': allowed, 'http_status': result['http_code'], 'exit_code': result['exitcode'],
                    'local_ip': result['local_ip'], 'received_bytes': result['size_download']})
                if allowed:
                    assert result['local_ip'] == session['address'], 'Wrong source interface'
                    assert result['http_code'] == 200 and result['exitcode'] == 0 and result['size_download'] > 0
                else:
                    # libcurl leaves local_ip empty when TCP is rejected before connection.
                    assert result['exitcode'] in (7, 28) and result['size_download'] == 0, 'Forbidden access succeeded or invalid interface'
        for name, host in [('upf1', upf1), ('upf2', upf2)]:
            evidence['rules_after'][name] = json.loads(host.run(['nft', '-j', 'list', 'table', 'inet', 'maestro_terminal'], sudo=True))
            def dropped_packets(rules):
                return sum(expr['counter']['packets'] for obj in rules['nftables']
                           if obj.get('rule', {}).get('chain') == 'forward'
                           for expr in obj['rule']['expr'] if 'counter' in expr)
            assert dropped_packets(evidence['rules_after'][name]) > dropped_packets(evidence['rules_before'][name]), 'No firewall evidence'
        await terminal_sessions.select('corporate')
        evidence['portal'] = await terminal_sessions.intranet()
        assert evidence['portal']['client_ip'] == available['corporate']['address']
        await terminal_sessions.select('internet')
        from fastapi import HTTPException
        try:
            await terminal_sessions.intranet()
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError('Intranet relay allowed Internet selection')
        evidence['status'] = 'PASS'
    finally:
        await terminal_sessions.select(description['active_apn'])
        for host in (ue, upf1, upf2):
            host.client.close()
        path = Path(__file__).resolve().parents[2] / '.work' / ('terminal-slices-' + uuid4().hex[:12] + '.json')
        path.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(json.dumps({'status': evidence.get('status', 'FAIL'), 'evidence': str(path), 'matrix': evidence['matrix']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required: sends real traffic and consumes lab credit')
    asyncio.run(verify())
