#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
root=/sys/fs/bpf/upf_xdp
case ${1:-status} in
  load)
    test ! -e "$root" || { echo 'Existing deployment: unload it first'; exit 1; }
    for dev in enp0s8 enp0s3; do
      ip -j -d link show dev "$dev" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; assert not x.get("xdp",{}).get("attached"), "Existing XDP program"'
    done
    mkdir -p "$root/maps"
    trap 'ip link set dev enp0s8 xdpgeneric off; ip link set dev enp0s3 xdpgeneric off; rm -rf /sys/fs/bpf/upf_xdp' ERR
    bpftool prog load upf_xdp_kern.o "$root/prog" type xdp pinmaps "$root/maps"
    ip link set dev enp0s8 xdpgeneric pinned "$root/prog"
    ip link set dev enp0s3 xdpgeneric pinned "$root/prog"
    trap - ERR
    ;;
  unload)
    if test -f "$root/prog"; then
      python3 upf_xdp_agent.py off
      own=$(bpftool -j prog show pinned "$root/prog" | python3 -c 'import sys,json; x=json.load(sys.stdin); print((x[0] if isinstance(x,list) else x)["id"])')
      for dev in enp0s8 enp0s3; do
        current=$(ip -j -d link show "$dev" | python3 -c 'import sys,json; print(json.load(sys.stdin)[0].get("xdp",{}).get("prog",{}).get("id",0))')
        test "$current" != "$own" || ip link set dev "$dev" xdpgeneric off
      done
      rm -rf /sys/fs/bpf/upf_xdp
    fi
    ;;
  status) bpftool net; test ! -d "$root" || python3 upf_xdp_agent.py stats ;;
  *) echo 'Usage: loader.sh load|unload|status'; exit 2 ;;
esac
