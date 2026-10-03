"""Bounded live validation; always restore the legacy forwarding switch."""
import concurrent.futures
import json
import time
from pathlib import Path
from remote import connect, run

E = Path(__file__).resolve().parents[2] / 'reportes/evidencias/upf-xdp-20261002'
u, ue = connect(), connect(2226)

def save(c, command, name, sudo=False):
    code, out, err = run(c, command, sudo)
    (E / name).write_text(out + err, encoding='utf-8')
    print(name, 'exit=', code, (out + err)[-2500:], flush=True)
    return code

try:
    save(u, '''set -e
cd /home/emsadmin/upf-xdp
ip -j addr show enp0s3 > evidence/n6-before.json
ip addr add 10.0.2.16/32 dev enp0s3
python3 upf_xdp_agent.py on
ip -s link show ogstun
''', 'live-before.txt', True)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        cap = pool.submit(save, u, '''cd /home/emsadmin/upf-xdp
timeout 22 tcpdump -U -ni any -w evidence/live.pcap 'udp port 2152 or host 10.0.2.16 or host 10.45.0.83'
''', 'live-capture.txt', True)
        time.sleep(1)
        save(ue, '''ping -I uesimtun0 -c 4 -W 2 1.1.1.1
curl --interface uesimtun0 --max-time 12 -o /dev/null -sS -w 'HTTP %{http_code} bytes=%{size_download} time=%{time_total}\n' https://example.com
iperf3 -c 10.0.2.2 -p 15201 -B 10.45.0.83 -t 3 -P 1 -J
''', 'xdp-live-smoke.txt')
        cap.result()
    save(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py stats; ip -s link show ogstun', 'live-after.txt', True)
finally:
    save(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py off', 'live-restored.txt', True)
    u.close(); ue.close()
