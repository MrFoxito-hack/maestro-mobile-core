"""C8 acquisition on existing PDU sessions, with no NF/policy changes.

Run from backend/. XDP setup and rollback are handled separately. Each file
is immutable after a trial; repetitions are sequential blocks, not packets.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time

from c8_remote import ROOT, CAMPAIGN_ROOT, LoggedLab, get_settings
from app.services import terminal, terminal_sessions

QUERY = (ROOT / 'infra/policy_authority/query_native.py').read_text(encoding='utf-8')
SNAPSHOT = '''import json,os,pathlib,subprocess,time
unit,sock=__import__('sys').argv[1:]
pid=int(subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value']))
stat=pathlib.Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
print(json.dumps({'monotonic_ns':time.monotonic_ns(),'epoch_s':time.time(),'pid':pid,
 'clock_ticks':os.sysconf('SC_CLK_TCK'),'process_ticks':int(stat[11])+int(stat[12]),
 'cpu':list(map(int,pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:])),
 'loadavg':pathlib.Path('/proc/loadavg').read_text(),'meminfo':pathlib.Path('/proc/meminfo').read_text(),
 'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
'''


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


async def sessions():
    result = {}
    for name, suffix, dnn in [('urllc', '002', '5g-plus'), ('miot', '003', 'corporate'), ('embb', '001', 'internet')]:
        supi = 'imsi-999700000000' + suffix
        obs = await terminal.read_terminal(supi)
        found = [s for s in terminal_sessions.sessions(obs) if s['apn'] == dnn]
        if len(found) != 1:
            raise ValueError('missing_unique_pdu_' + name)
        result[name] = {'supi': supi, **found[0]}
    return result


def snapshot(host, nf):
    unit = 'open5gs-upfd-urllc' if nf == 'upf3' else 'open5gs-upfd'
    sock = '/run/maestro-observer-' + nf + '/observe.sock'
    prefix = ['ip', 'netns', 'exec', 'maestro-urllc'] if nf == 'upf3' else []
    obs = json.loads(host.run([*prefix, 'python3', '-c', QUERY, sock], sudo=True))
    cpu = json.loads(host.run(['python3', '-c', SNAPSHOT, unit, sock], sudo=True))
    return {'native': obs, 'resources': cpu}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment', choices=['pilot', 'miot', 'isolation', 'urllc'], required=True)
    p.add_argument('--mode', choices=['kernel', 'xdp'], default='kernel')
    p.add_argument('--blocks', type=int, default=6)
    p.add_argument('--duration', type=int, default=10)
    p.add_argument('--seed', type=int, default=42017)
    p.add_argument('--block-start', type=int, default=0)
    p.add_argument('--qos', action='store_true', help='Require and archive OE4 QoS for pilot/isolation')
    a = p.parse_args()
    if not 1 <= a.duration <= 30 or not 1 <= a.blocks <= 20:
        p.error('duration 1..30 and blocks 1..20 required')
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + a.experiment + '-' + a.mode
    out = CAMPAIGN_ROOT / 'runs' / run
    out.mkdir(parents=True)
    (out/'source').mkdir()
    sources = ['c8_acquire.py','c8_traffic.py','c8_remote.py'] + (['c8_xdp_window.py'] if a.experiment=='urllc' else []) + (['c8_qos.py'] if a.qos else [])
    for source in sources:
        (out/'source'/source).write_bytes((ROOT/'infra'/source).read_bytes())
    settings = get_settings()
    hs = {}
    report = {'run_id': run, 'git_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'source_sha256': {n: hashlib.sha256((ROOT/'infra'/n).read_bytes()).hexdigest()
                                for n in sources},
              'arguments': vars(a), 'trials': [], 'status': 'started'}
    write(out/'campaign.json', report)
    unit = 'c8-echo-' + run[:20].lower()
    echo_started = False
    try:
        for name, port in [('ue', settings.ue_ssh_port), ('upf', settings.upf_ssh_port), ('upf2', settings.upf2_ssh_port)]:
            hs[name] = LoggedLab(settings, port, out)
        ss = asyncio.run(sessions())
        report['sessions'] = ss
        # NAS-active alone is insufficient: reject stale PDU/UPF combinations.
        preflight = {nf: snapshot(hs[host], nf) for nf, host in
                     [('upf3','upf'), ('upf2','upf2'), ('upf','upf')]}
        for kind, nf in [('urllc','upf3'), ('miot','upf2'), ('embb','upf')]:
            matches = [s for s in preflight[nf]['native']['sessions'] if s['ue_ipv4'] == ss[kind]['address']]
            if len(matches) != 1:
                raise ValueError('missing_native_session_' + kind)
        write(out/'preflight.json', preflight)
        if a.qos:
            from c8_qos import snapshot as qos_snapshot, assert_ready
            state = qos_snapshot(hs['ue'])
            assert_ready(state, ss)
            write(out/'qos-initial.json', state)
        report['host_versions'] = {name: h.run(['uname','-a']).strip() for name,h in hs.items()}
        targets = {'urllc': '172.31.48.2', 'miot': '10.46.0.1', 'embb': '10.45.0.1'}
        base = {k: {'slice': k, 'source': v['address'], 'interface': v['interface'],
                    'supi': v['supi'], 'target': targets[k], 'port': 28765 if k == 'embb' else 8765,
                    'payload_bytes': 64, 'pps': 100, 'sensors': 1} for k, v in ss.items()}
        if a.experiment in ('pilot', 'isolation'):
            echo = "import socket\ns=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('10.45.0.1',28765))\nwhile True:\n d,a=s.recvfrom(65535);s.sendto(d,a)"
            hs['upf'].run(['systemd-run', '--unit='+unit, '--property=RuntimeMaxSec=1800',
                           '/usr/bin/python3', '-c', echo], sudo=True)
            echo_started = True
            if hs['upf'].run(['systemctl','is-active',unit]).strip() != 'active':
                raise ValueError('echo_not_active')
        remote = hs['ue'].run(['mktemp', '-d', '/home/emsadmin/c8-traffic-XXXXXX']).strip()
        report['remote'] = remote
        hs['ue'].write(remote+'/traffic.py', (ROOT/'infra/c8_traffic.py').read_bytes())
        order = []
        rng = random.Random(a.seed)
        for block in range(a.block_start, a.block_start+a.blocks):
            levels = [10, 50, 100, 1000] if a.experiment == 'miot' else (['idle','loaded'] if a.experiment == 'isolation' else [a.mode])
            if a.experiment != 'pilot': rng.shuffle(levels)
            for level in levels: order.append((block,level))
        report['planned_order'] = order
        write(out/'campaign.json',report)
        # One full, explicitly excluded warm-up; never remove measured trials.
        for index, (block, level) in enumerate([(-1, order[0][1]), *order]):
            streams = [dict(base['urllc'])]
            if a.experiment == 'miot':
                streams = [{**base['miot'], 'sensors': level, 'pps': level / 5}]
            elif a.experiment in ('isolation', 'pilot'):
                heavy = level == 'loaded' or a.experiment == 'pilot'
                streams += [{**base['embb'], 'payload_bytes': 1200, 'pps': 500 if heavy else 10},
                            {**base['miot'], 'sensors': 1000 if heavy else 10, 'pps': 500 if heavy else 20}]
            config = {'run_id': run, 'git_sha': report['git_sha'], 'experiment': a.experiment,
                      'mode': a.mode, 'block': block, 'level': level, 'warmup': block == -1,
                      'seed': a.seed+index, 'duration_s': 2 if block == -1 else a.duration, 'streams': streams}
            if a.qos: config['qos_profile'] = 'oe4-prio-preadmission-v1'
            name = f'{index:03d}-b{block}-{level}'
            before = {'upf3': snapshot(hs['upf'], 'upf3'), 'upf2': snapshot(hs['upf2'], 'upf2'),
                      'upf': snapshot(hs['upf'], 'upf')}
            if a.experiment=='urllc':
                from c8_xdp_window import xdp_counters
                before['bpf_counters']=xdp_counters(hs['upf'])
            write(out/(name+'-before.json'), before)
            write(out/(name+'-config.json'), config)
            if a.qos:
                state = qos_snapshot(hs['ue']); assert_ready(state, ss)
                write(out/(name+'-qos-before.json'), state)
            hs['ue'].write(remote+'/'+name+'.json', json.dumps(config))
            summary = hs['ue'].run(['python3', remote+'/traffic.py', remote+'/'+name+'.json', remote+'/'+name+'-raw.json'],
                                    sudo=True, timeout=a.duration+15)
            # Raw files are user-readable because the containing directory belongs to emsadmin.
            raw = hs['ue'].read(remote+'/'+name+'-raw.json')
            (out/(name+'-raw.json')).write_bytes(raw)
            if a.qos:
                state = qos_snapshot(hs['ue']); assert_ready(state, ss)
                write(out/(name+'-qos-after.json'), state)
            time.sleep(.3)
            after = {'upf3': snapshot(hs['upf'], 'upf3'), 'upf2': snapshot(hs['upf2'], 'upf2'),
                     'upf': snapshot(hs['upf'], 'upf')}
            if a.experiment=='urllc':after['bpf_counters']=xdp_counters(hs['upf'])
            write(out/(name+'-after.json'), after)
            report['trials'].append({'name': name, 'config': config, 'summary': json.loads(summary)})
            write(out/'campaign.json',report)
            print(json.dumps({'run': run, 'trial': name, 'summary': json.loads(summary)['streams']}), flush=True)
            time.sleep(1)
        report['status'] = 'completed'
    except BaseException as exc:
        report['status'] = 'failed'
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        if echo_started:
            hs['upf'].run(['systemctl','stop',unit],sudo=True)
            report['echo_stopped'] = hs['upf'].run(['systemctl','is-active',unit],check=False).strip() != 'active'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write(out/'campaign.json',report)
        for h in hs.values(): h.client.close()
        print(json.dumps({'evidence': str(out), 'status':report['status']}),flush=True)


if __name__ == '__main__':
    main()
