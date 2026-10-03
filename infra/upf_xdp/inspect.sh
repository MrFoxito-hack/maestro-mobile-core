set -e
cd /home/emsadmin/upf-xdp
make 2>&1 | tee evidence/compile.txt
bpftool version
bpftool net
python3 - <<'PY'
from scapy.all import rdpcap,UDP,IP
for x in rdpcap('evidence/session.pcap'):
    print(x[IP].src,bytes(x[UDP].payload).hex())
PY
