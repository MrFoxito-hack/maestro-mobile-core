"""Reproducible lab deployment and evidence collection, invoked from Windows."""
import sys
from pathlib import Path
from remote import connect, run

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parents[1] / 'reportes/evidencias/upf-xdp-20261002'
EVIDENCE.mkdir(parents=True, exist_ok=True)
c = connect()
try:
    with c.open_sftp() as s:
        for name in ['upf_xdp_kern.c', 'Makefile', 'upf_xdp_agent.py', 'loader.sh']:
            s.put(str(HERE / name), '/home/emsadmin/upf-xdp/' + name)
    cmd = '''set -e
cd /home/emsadmin/upf-xdp
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=600 install -y make > evidence/make-install.txt 2>&1 || { cat evidence/make-install.txt; exit 1; }
make > evidence/compile.txt 2>&1 || { cat evidence/compile.txt; exit 1; }
cat evidence/compile.txt
uname -a
clang --version
bpftool version
bpftool feature probe kernel > evidence/bpf-features.txt
cat > probe.c <<'C'
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
SEC("xdp") int probe(struct xdp_md *ctx) { return XDP_PASS; }
char LICENSE[] SEC("license") = "GPL";
C
clang -O2 -target bpf -I/usr/include/x86_64-linux-gnu -c probe.c -o probe.o
for dev in enp0s8 enp0s3; do
  ip -j -d link show dev "$dev" | python3 -c 'import json,sys; assert not json.load(sys.stdin)[0].get("xdp",{}).get("attached"), "Existing XDP program"'
  echo "Native probe: $dev"
  if ip link set dev "$dev" xdpdrv obj probe.o sec xdp; then
    ip -d link show "$dev"
    ip link set dev "$dev" xdpdrv off
  fi
  echo "Generic probe: $dev"
  ip link set dev "$dev" xdpgeneric obj probe.o sec xdp
  ip -d link show "$dev"
  ip link set dev "$dev" xdpgeneric off
done
bash loader.sh load
python3 upf_xdp_agent.py register --pcap evidence/session.pcap --ue 10.45.0.83 --output evidence/registered-session.json
bash loader.sh status
'''
    status, out, err = run(c, cmd, sudo=True)
    (EVIDENCE / 'deployment.txt').write_text(out + err, encoding='utf-8')
    print(out + err)
    sys.exit(status)
finally:
    c.close()
