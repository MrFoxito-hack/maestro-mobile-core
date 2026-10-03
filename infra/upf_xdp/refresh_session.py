import concurrent.futures
import json
import re
import time
from pathlib import Path
from remote import connect, run

HERE = Path(__file__).resolve().parent
E = HERE.parents[1] / 'reportes/evidencias/upf-xdp-20261002'
u, ue = connect(), connect(2226)
try:
    code, out, err = run(u, 'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py off', True)
    if code: raise RuntimeError(err)
    with u.open_sftp() as s:
        s.put(str(HERE / 'upf_xdp_agent.py'), '/home/emsadmin/upf-xdp/upf_xdp_agent.py')
    code, out, err = run(ue, '/home/emsadmin/UERANSIM/build/nr-cli imsi-999700000000001 --exec ps-list')
    if code: raise RuntimeError(err)
    address = re.search(r'address: (10\.45\.\d+\.\d+)', out).group(1)
    (E / 'current-ue.json').write_text(json.dumps({'ue':address}))
    print(out, flush=True)
    with concurrent.futures.ThreadPoolExecutor() as ex:
        future = ex.submit(run,u,'timeout 7 tcpdump -U -ni enp0s8 -w /home/emsadmin/upf-xdp/evidence/session-current.pcap udp port 2152',True)
        time.sleep(1)
        result=run(ue,'ping -I uesimtun0 -c 4 -W 2 1.1.1.1'); print(result,flush=True)
        print(future.result(),flush=True)
    code, out, err = run(u, f'cd /home/emsadmin/upf-xdp; python3 upf_xdp_agent.py register --pcap evidence/session-current.pcap --ue {address} --output evidence/registered-session.json',True)
    print(out, err, flush=True)
    if code: raise RuntimeError('Session registration failed: ' + err)
    (E / 'registered-session.json').write_text(out, encoding='utf-8')
    with u.open_sftp() as s:
        s.get('/home/emsadmin/upf-xdp/evidence/session-current.pcap', str(E / 'session-current.pcap'))
finally:
    u.close();ue.close()
