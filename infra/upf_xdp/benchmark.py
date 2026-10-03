"""Alternating A/B lab trials. Restores the legacy mode even on failures."""
import concurrent.futures
import json
import time
from pathlib import Path
from remote import connect, run

HERE = Path(__file__).resolve().parent
E = HERE.parents[1] / 'reportes/evidencias/upf-xdp-20261002'
ADDRESS = json.loads((E / 'current-ue.json').read_text())['ue']
u, ue = connect(), connect(2226)

def execute(c, cmd, name, sudo=False, check=True):
    code, out, err = run(c, cmd, sudo, timeout=180)
    (E / name).write_text(out, encoding='utf-8')
    if err: (E / (name + '.stderr')).write_text(err, encoding='utf-8')
    if check and code:
        raise RuntimeError(f'{name}: exit {code}: {err} {out[-500:]}')
    return out

def snap(name):
    return json.loads(execute(u, 'cd /home/emsadmin/upf-xdp; python3 snapshot.py', name, True))

try:
    with u.open_sftp() as s:
        s.put(str(HERE / 'snapshot.py'), '/home/emsadmin/upf-xdp/snapshot.py')
    execute(ue, '/home/emsadmin/UERANSIM/build/nr-cli imsi-999700000000001 --exec ps-list', 'benchmark-session-before.txt')
    execute(u, 'systemctl status open5gs-upfd --no-pager; tc -s qdisc show; iptables-save', 'benchmark-environment.txt', True)
    # Independent watchdog also returns to legacy if this orchestrator is interrupted.
    execute(u, "systemctl stop 'upf-xdp-benchmark-rollback*.timer'", 'benchmark-old-watchdogs-stopped.txt', True)
    execute(u, f'systemd-run --unit=upf-xdp-benchmark-rollback-{int(time.time())} --on-active=15m /usr/bin/python3 /home/emsadmin/upf-xdp/upf_xdp_agent.py off', 'benchmark-watchdog.txt', True)
    for repeat in range(1, 4):
        for mode in (['legacy', 'xdp'] if repeat % 2 else ['xdp', 'legacy']):
            execute(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py ' + ('on' if mode == 'xdp' else 'off'), f'{repeat}-{mode}-switch.txt', True)
            # Warm up neighbor/NAT caches. Excluded from measured samples.
            execute(ue, 'ping -I uesimtun0 -c 2 -W 2 10.0.2.2', f'{repeat}-{mode}-warmup.txt')
            execute(ue, 'ping -I uesimtun0 -c 100 -i 0.02 -W 2 1.1.1.1', f'{repeat}-{mode}-internet-ping.txt', True, False)
            for direction in ['ul', 'dl']:
                name = f'{repeat}-{mode}-tcp-{direction}'
                if (E / (name + '-after.json')).exists():
                    print(name, 'retained completed trial', flush=True)
                    continue
                snap(name + '-before.json')
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    ping = pool.submit(execute, ue, 'ping -I uesimtun0 -c 800 -i 0.01 -W 2 10.0.2.2', name + '-ping.txt', True, False)
                    output = execute(ue, f'timeout -k 2 25 iperf3 -c 10.0.2.2 -p 15201 -B {ADDRESS} -t 10 -P 8 -J' + (' -R' if direction == 'dl' else ''), name + '.json')
                    ping.result()
                snap(name + '-after.json')
                result = json.loads(output)
                if 'error' in result: raise RuntimeError(result['error'])
                print(name, round(result['end']['sum_received']['bits_per_second'] / 1e6, 3), 'Mbps', flush=True)
            if repeat == 1 and not (E / f'{repeat}-{mode}-udp-ul.json').exists():
                name = f'{repeat}-{mode}-udp-ul'
                snap(name + '-before.json')
                execute(ue, f'timeout -k 2 20 iperf3 -c 10.0.2.2 -p 15201 -B {ADDRESS} -u -b 0 -l 1200 -t 5 -P 1 -J', name + '.json', check=False)
                snap(name + '-after.json')
                execute(ue, "curl --interface uesimtun0 --max-time 15 -sS -o /dev/null -w 'HTTP %{http_code} bytes=%{size_download} time=%{time_total}\\n' https://example.com", name + '-https.txt', check=False)
    execute(ue, '/home/emsadmin/UERANSIM/build/nr-cli imsi-999700000000001 --exec ps-list', 'benchmark-session-after.txt')
finally:
    execute(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py off', 'benchmark-final-mode.txt', True)
    u.close(); ue.close()
