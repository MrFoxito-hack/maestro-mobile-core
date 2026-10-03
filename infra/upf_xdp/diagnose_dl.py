import concurrent.futures
import time
import json
from pathlib import Path
from remote import connect, run
u, ue, g = connect(), connect(2226), connect(2225)
try:
    address = json.loads((Path(__file__).resolve().parents[2] / 'reportes/evidencias/upf-xdp-20261002/current-ue.json').read_text())['ue']
    print(run(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py on', True))
    with concurrent.futures.ThreadPoolExecutor() as ex:
        capture = ex.submit(run, u, "timeout 10 tcpdump -ni enp0s3 -s 160 -w /home/emsadmin/upf-xdp/evidence/dl-debug.pcap host 10.0.2.16", True)
        gcap = ex.submit(run, g, 'timeout 10 tcpdump -ni enp0s8 -w /tmp/xdp-gnb-dl.pcap udp port 2152', True)
        ucap = ex.submit(run, ue, 'timeout 10 tcpdump -ni uesimtun0 -w /tmp/xdp-ue-dl.pcap', True)
        time.sleep(1)
        result = run(ue, f'timeout -k 2 8 iperf3 -c 10.0.2.2 -p 15201 -B {address} -t 3 -P 1 -R -J')
        print(result)
        print(capture.result())
        print(gcap.result()); print(ucap.result())
        for c, name in [(g, 'gnb'), (ue, 'ue')]:
            with c.open_sftp() as s:
                s.get('/tmp/xdp-'+name+'-dl.pcap',str(Path(__file__).resolve().parents[2] / ('reportes/evidencias/upf-xdp-20261002/'+name+'-dl.pcap')))
    print(run(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py stats; tcpdump -nn -vv -r evidence/dl-debug.pcap -c 15', True))
finally:
    print(run(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py off', True))
    u.close(); ue.close(); g.close()
