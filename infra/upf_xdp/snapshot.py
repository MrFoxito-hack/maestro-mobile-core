#!/usr/bin/env python3
"""Read-only UPF CPU, link and BPF snapshot for benchmark deltas."""
import contextlib
import io
import json
import os
import subprocess
import time
from pathlib import Path
from upf_xdp_agent import stats

pid = int(subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', 'open5gs-upfd']))
proc = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    stats()
status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
program = json.loads(subprocess.check_output(['bpftool', '-j', 'prog', 'show', 'pinned', '/sys/fs/bpf/upf_xdp/prog']))
print(json.dumps(dict(time=time.monotonic(), clock_ticks=os.sysconf('SC_CLK_TCK'),
    cpu_count=os.cpu_count(), cpu=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]],
    upfd_pid=pid, upfd_ticks=int(proc[11]) + int(proc[12]),
    upfd_rss_kb=int(status['VmRSS'].split()[0]),
    upfd_context_switches={k:int(status[k]) for k in ['voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches']},
    bpf_program=program,
    links=json.loads(subprocess.check_output(['ip', '-j', '-s', 'link'])), bpf=json.loads(buf.getvalue()))))
