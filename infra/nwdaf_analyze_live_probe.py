"""Export packet subsets and measured findings from a completed live probe."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
from app.services.nwdaf import get_health, nwdaf_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', help='nwdaf-g3-<10 hex digits>')
    args = parser.parse_args()
    if not re.fullmatch(r'nwdaf-g3-[a-f0-9]{10}', args.run):
        parser.error('Invalid run ID')
    local = ROOT / '.work' / args.run
    result = json.loads((local / 'result.json').read_text(encoding='utf-8'))
    remote = result['remote_evidence']
    if not re.fullmatch('/home/emsadmin/' + re.escape(args.run) + r'-[A-Za-z0-9]{6}', remote):
        raise ValueError('Unexpected remote artifact directory')
    settings = get_settings()
    core = Lab(settings, settings.ssh_port)
    analysis = {'utc': datetime.now(timezone.utc).isoformat(), 'gate3': 'NOT_PASSED',
                'gate4': 'NOT_RUN_PREREQUISITES_MISSING'}
    try:
        pcap = remote + '/control.pcap'
        if hashlib.sha256(core.read(pcap)).hexdigest() != result['pcap_sha256']:
            raise ValueError('Remote capture does not match original evidence')
        # N23 port subset includes the controller's /health requests; those are
        # explicitly excluded from the PCF analytics request count.
        for name, filt in {
            'n23-port-8085': 'tcp.port == 8085',
            'n7-pcf-smf-transport': 'tcp.port == 7777 && (ip.addr == 127.0.0.13 || ip.addr == 127.0.0.4)',
            'n4-pfcp': 'udp.port == 8805',
        }.items():
            path = remote + '/' + name + '.pcap'
            core.run(['tshark', '-r', pcap, '-Y', filt, '-F', 'pcap', '-w', path])
            data = core.read(path)
            (local / (name + '.pcap')).write_bytes(data)
            frames = core.run(['tshark', '-r', path, '-T', 'fields', '-e', 'frame.number']).splitlines()
            analysis[name] = {'frames': len(frames), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        pfcp = core.run(['tshark', '-r', pcap, '-Y', 'pfcp', '-T', 'fields', '-e', 'pfcp.msg_type'])
        analysis['pfcp_message_types'] = dict(Counter(pfcp.split()))
        modifications = core.run(['tshark', '-r', pcap, '-Y', 'pfcp.msg_type == 52', '-T', 'json'])
        (local / 'pfcp-modifications.json').write_text(modifications, encoding='utf-8')
        detail = core.run(['tshark', '-r', pcap, '-Y', 'pfcp.msg_type == 52', '-V'])
        (local / 'pfcp-modifications.txt').write_text(detail, encoding='utf-8')
        analysis['pfcp_qer_lines'] = [line.strip() for line in detail.splitlines()
                                     if any(term in line for term in ('QER', 'MBR', 'Maximum Bitrate'))]
        headers = core.run(['tshark', '-r', pcap, '-d', 'tcp.port==7777,http2', '-Y', 'http2.headers',
                            '-T', 'fields', '-e', 'frame.time_epoch', '-e', 'ip.src', '-e', 'ip.dst',
                            '-e', 'http2.headers.method', '-e', 'http2.headers.path', '-e', 'http2.headers.status'])
        (local / 'http2-headers.tsv').write_text(headers, encoding='utf-8')
        analysis['decoded_n7_notify_paths'] = [line for line in headers.splitlines() if '/update' in line or '/notify' in line]
        requests = core.run(['tshark', '-r', pcap, '-Y', 'http.request', '-T', 'fields',
                             '-e', 'frame.time_epoch', '-e', 'http.request.method', '-e', 'http.request.uri'])
        (local / 'http1-requests.tsv').write_text(requests, encoding='utf-8')
        analysis['decoded_analytics_requests'] = sum('/nnwdaf-analyticsinfo/' in line for line in (headers + requests).splitlines())
        analysis['health_final'] = get_health()
        predictions = nwdaf_request('/management/v1/predictions')
        (local / 'predictions-final.json').write_text(json.dumps(predictions, indent=2), encoding='utf-8')
        analysis['forecast_status'] = [{'snssai': p['snssai'], 'fresh': p['fresh'],
                                       'status': p['evidence'].get('status'), 'model': p['evidence'].get('model'),
                                       'forecast_points': len(p['evidence'].get('points', []))}
                                      for p in predictions['items']]
        analysis['ledger_final'] = nwdaf_request('/management/v1/closed-loop/history')
        analysis['units_after_cleanup'] = core.run(['systemctl', 'is-active', args.run + '-iperf', args.run + '-capture', args.run + '-route-cleanup.timer'], check=False)
        address = result['session']['pdu']['address']
        analysis['route_after_cleanup'] = json.loads(core.run(['ip', '-j', 'route', 'get', address]))
        iperf = json.loads((local / 'iperf.json').read_text(encoding='utf-8'))
        analysis['traffic'] = {'offered_bps': result['offered_bps'], 'upf_tun_high_bps': result['observed_high_dl_bps'],
                               'upf_tun_quiet_bps': result['observed_recovery_dl_bps'],
                               'receiver_lost_packets': iperf['end']['sum']['lost_packets'],
                               'receiver_lost_percent': iperf['end']['sum']['lost_percent'],
                               'receiver_jitter_ms': iperf['end']['sum']['jitter_ms'],
                               'loss_location': 'not determined; receiver loss does not identify UPF as drop location'}
        before = result['pcf_proc_before'].splitlines()[0]
        after = result['pcf_proc_after'].splitlines()[0]
        a = before[before.rfind(')')+2:].split()
        b = after[after.rfind(')')+2:].split()
        analysis['running_pcf_cpu_seconds_delta'] = (int(b[11]) + int(b[12]) - int(a[11]) - int(a[12])) / result['clock_ticks']
        analysis['running_pcf_rss_kib'] = {phase: int(re.search(r'^VmRSS:\s+(\d+)', result['pcf_proc_' + phase], re.M)[1]) for phase in ('before', 'after')}
        analysis['patch_overhead'] = None  # Running process is not the staged NWDAF build.
        analysis['closed_loop_latency_ms'] = None
        analysis['prediction_rmse'] = None
        analysis['p1203_mos'] = None
        analysis['quotas_unchanged'] = result['quotas_unchanged']
        analysis['service_pids_unchanged'] = result['service_pids_unchanged']
        (local / 'analysis.json').write_text(json.dumps(analysis, indent=2), encoding='utf-8')
        manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(local.iterdir()) if p.is_file() and p.name != 'sha256.json'}
        (local / 'sha256.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(json.dumps(analysis, indent=2))
    finally:
        core.client.close()


if __name__ == '__main__':
    main()
