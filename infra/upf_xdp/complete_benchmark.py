"""Fresh, bounded TCP/UDP A/B campaign, with snapshots and ogstun captures.

Original unlimited UDP attempts remain in the parent evidence directory.
Each invocation requires a new output directory; failed trials are never reused.
"""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import time

from remote import connect, run

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1] / 'reportes/evidencias/upf-xdp-20261002'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(BASE / 'campaign-complete'))
    parser.add_argument('--udp-rate', default='10M')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--xdp-only', action='store_true')
    parser.add_argument('--settle-seconds', type=float, default=0)
    parser.add_argument('--tcp-streams', type=int, default=8)
    parser.add_argument('--tcp-seconds', type=int, default=10)
    parser.add_argument('--tcp-rate')
    parser.add_argument('--ping-interval', type=float, default=0.01)
    args = parser.parse_args()
    evidence = Path(args.output)
    evidence.mkdir(parents=True, exist_ok=False)
    upf, ue = connect(), connect(2226)
    original_stats = None
    capture_pid = None
    metadata = {'started_utc': datetime.now(timezone.utc).isoformat(),
                'udp_rate': args.udp_rate, 'tcp_seconds': args.tcp_seconds, 'udp_seconds': 5,
                'tcp_streams': args.tcp_streams, 'tcp_rate': args.tcp_rate,
                'udp_streams': 1, 'udp_payload_bytes': 1200,
                'ping_interval_seconds': args.ping_interval, 'repetitions': args.repetitions,
                'xdp_only': args.xdp_only, 'settle_seconds': args.settle_seconds, 'trials': []}

    def execute(client, command, filename, sudo=False, check=True):
        code, out, err = run(client, command, sudo, timeout=80)
        (evidence / filename).write_text(out, encoding='utf-8')
        (evidence / (filename + '.execution.json')).write_text(json.dumps(
            {'command': command, 'exit_code': code, 'stderr': err,
             'finished_utc': datetime.now(timezone.utc).isoformat()}, indent=2), encoding='utf-8')
        if check and code:
            raise RuntimeError(f'{filename}: exit {code}: {err} {out[-500:]}')
        return out

    def snapshot(name):
        return execute(upf, 'cd /home/emsadmin/upf-xdp; python3 snapshot.py', name, True)

    try:
        with upf.open_sftp() as sftp:
            sftp.put(str(HERE / 'snapshot.py'), '/home/emsadmin/upf-xdp/snapshot.py')
            sftp.get('/home/emsadmin/upf-xdp/current-session.json', str(evidence / 'registered-session.json'))
        session = execute(ue, '/home/emsadmin/UERANSIM/build/nr-cli imsi-999700000000001 --exec ps-list', 'session-before.txt')
        address = re.search(r'address: (10\.45\.\d+\.\d+)', session).group(1)
        registered = json.loads((evidence / 'registered-session.json').read_text())
        if address != registered['ue']:
            raise RuntimeError('Refresh BPF session before running the campaign')
        metadata['ue'] = address
        execute(upf, "systemctl stop 'upf-xdp-benchmark-rollback*.timer'", 'old-watchdogs.txt', True)
        unit = f'upf-xdp-benchmark-rollback-{int(time.time())}'
        execute(upf, f'systemd-run --unit={unit} --on-active=15m /usr/bin/python3 /home/emsadmin/upf-xdp/upf_xdp_agent.py off', 'watchdog.txt', True)
        original_stats = execute(upf, 'sysctl -n kernel.bpf_stats_enabled', 'bpf-stats-original.txt', True).strip()
        execute(upf, 'sysctl -w kernel.bpf_stats_enabled=1', 'bpf-stats-enabled.txt', True)
        execute(upf, 'uname -a; lscpu; ip -d link; ethtool -k enp0s3; ethtool -k enp0s8; tc -s qdisc show; bpftool -j map show; free -k; iptables-save', 'environment.txt', True)
        for port, name in [(2225, 'gnb'), (2226, 'ue')]:
            client = connect(port)
            try:
                execute(client, 'ip -d link; iperf3 --version', name + '-environment.txt', True, False)
            finally:
                client.close()
        for repeat in range(1, args.repetitions + 1):
            modes = ['xdp'] if args.xdp_only else (['legacy', 'xdp'] if repeat % 2 else ['xdp', 'legacy'])
            for mode in modes:
                prefix = f'{repeat}-{mode}'
                execute(upf, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py ' + ('on' if mode == 'xdp' else 'off'), prefix + '-switch.txt', True)
                execute(upf, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py status', prefix + '-status.json', True)
                execute(ue, 'ping -I uesimtun0 -c 2 -W 2 10.0.2.2', prefix + '-warmup.txt')
                if args.settle_seconds:
                    print(f'{prefix}: allowing {args.settle_seconds}s for residual traffic to drain', flush=True)
                    time.sleep(args.settle_seconds)
                for protocol in ['tcp', 'udp']:
                    for direction in ['ul', 'dl']:
                        name = f'{prefix}-{protocol}-{direction}'
                        remote_base = '/home/emsadmin/upf-xdp/evidence/complete-' + name
                        capture_pid = int(execute(upf, f'tcpdump -U -n -i ogstun -w {remote_base}.pcap > {remote_base}.log 2>&1 & echo $!', name + '-capture-pid.txt', True).strip())
                        time.sleep(0.3)
                        snapshot(name + '-before.json')
                        seconds = args.tcp_seconds if protocol == 'tcp' else 5
                        command = f'timeout -k 2 25 iperf3 -c 10.0.2.2 -p 15201 -B {address} -t {seconds} -J --get-server-output'
                        command += f' -P {args.tcp_streams}' if protocol == 'tcp' else f' -u -b {shlex.quote(args.udp_rate)} -l 1200 -P 1'
                        if protocol == 'tcp' and args.tcp_rate:
                            command += ' -b ' + shlex.quote(args.tcp_rate)
                        if direction == 'dl':
                            command += ' -R'
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            ping = pool.submit(execute, ue, f'ping -D -I uesimtun0 -c {int(seconds * .8 / args.ping_interval)} -i {args.ping_interval} -W 2 10.0.2.2', name + '-ping.txt', True, False)
                            out = execute(ue, command, name + '.json', check=False)
                            ping.result()
                        snapshot(name + '-after.json')
                        execute(upf, f'kill -INT {capture_pid}; sleep 0.3; cat {remote_base}.log', name + '-capture.txt', True)
                        capture_pid = None
                        with upf.open_sftp() as sftp:
                            sftp.get(remote_base + '.pcap', str(evidence / (name + '-ogstun.pcap')))
                        execute(upf, f'tcpdump -nn -r {remote_base}.pcap -c 30', name + '-ogstun-sample.txt', True)
                        result = json.loads(out)
                        if result.get('error'):
                            raise RuntimeError(f'{name}: {result["error"]}')
                        metadata['trials'].append(name)
                        (evidence / 'manifest.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                        print(name, json.dumps(result['end'].get('sum_received', result['end'].get('sum'))), flush=True)
                execute(ue, "curl --interface uesimtun0 --max-time 15 -sS -o /dev/null -w 'HTTP %{http_code} bytes=%{size_download} time=%{time_total}\\n' https://example.com", prefix + '-https.txt', check=False)
        execute(ue, '/home/emsadmin/UERANSIM/build/nr-cli imsi-999700000000001 --exec ps-list', 'session-after.txt')
        metadata['completed'] = True
    finally:
        if capture_pid:
            execute(upf, f'kill -INT {capture_pid}', 'capture-cleanup.txt', True, False)
        execute(upf, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py off; python3 upf_xdp_agent.py status', 'final-mode.json', True)
        if original_stats is not None:
            execute(upf, f'sysctl -w kernel.bpf_stats_enabled={int(original_stats)}', 'bpf-stats-restored.txt', True)
        metadata['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (evidence / 'manifest.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        upf.close()
        ue.close()


if __name__ == '__main__':
    main()
