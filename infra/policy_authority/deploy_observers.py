"""Deploy separately built native observers with independent timed rollback.

The caller explicitly authorizes controlled restarts with --execute. This does
not assert fencing, admission, N7 completion or C3 acceptance. All financial
databases and NF configuration files are left in place.
Run from backend/ with its configured environment.
"""
import argparse
import io
import json
from pathlib import Path
import re
import shlex
import sys
import tarfile
import time
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from infra.charging.e2e_native import Lab, get_settings

TARGETS = {
    'core': [('pcf', 'open5gs-pcfd'), ('smf', 'open5gs-smfd'),
             ('smf2', 'open5gs-smfd2'), ('smf3', 'open5gs-smfd3')],
    'upf': [('upf', 'open5gs-upfd'), ('upf3', 'open5gs-upfd-urllc')],
    'upf2': [('upf2', 'open5gs-upfd')],
}
UE_UNITS = ['ueransim-ue', 'maestro-ue-vehicle', 'maestro-ue-sensor',
            'ueransim-ue-04', 'ueransim-ue-05', 'ueransim-ue-06']
DROPIN = 'zzzzz-maestro-native-observer.conf'


def exec_arguments(raw):
    match = re.fullmatch(r'\{ path=[^;]+ ; argv\[\]=(.*?) ; ignore_errors=.*\}', raw.strip())
    if not match:
        raise ValueError('unsupported_systemd_execstart')
    args = shlex.split(match[1])
    if (len(args) != 3 or not re.fullmatch(r'/[a-zA-Z0-9_./-]+/open5gs-(pcf|smf|upf)d', args[0])
            or args[1] != '-c' or not args[2].startswith('/')):
        raise ValueError('unsupported_nf_arguments')
    return args


def override(bundle, nf, config, guard=False):
    kind = nf.rstrip('23')
    # No shell is used in ExecStart. Reject characters requiring systemd escapes.
    if not all(re.fullmatch(r'/[a-zA-Z0-9_./-]+', p) for p in (bundle, config)):
        raise ValueError('invalid_install_path')
    runtime = 'maestro-observer-' + nf
    body = ('[Service]\nExecStart=\n'
            f'ExecStart={bundle}/open5gs-{kind}d -c {config}\n'
            f'Environment=LD_LIBRARY_PATH={bundle}/lib\n'
            f'Environment=MAESTRO_POLICY_OBSERVER_SOCKET=/run/{runtime}/observe.sock\n'
            f'RuntimeDirectory={runtime}\nRuntimeDirectoryMode=0700\n')
    if guard:
        body += (f'StateDirectory=maestro-native-{nf}\nStateDirectoryMode=0700\n'
                 f'Environment=MAESTRO_POLICY_STATE_DIR=/var/lib/maestro-native-{nf}\n'
                 'Environment=MAESTRO_POLICY_PFCP_ENTERPRISE=32473\n')
    return body


def wait_active(host, unit, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if host.run(['systemctl', 'is-active', unit], check=False).strip() == 'active':
            return
        time.sleep(0.5)
    raise RuntimeError('service_not_active:' + unit)


def collect(hosts, bundle):
    observations = {}
    for group, targets in TARGETS.items():
        for nf, _ in targets:
            command = ['python3', bundle + '/query_native.py', '/run/maestro-observer-' + nf + '/observe.sock']
            if nf == 'upf3':
                command = ['ip', 'netns', 'exec', 'maestro-urllc', *command]
            observations[nf] = json.loads(hosts[group].run(command, sudo=True))
    return observations


def active_pdus(host):
    cli = '/home/emsadmin/UERANSIM/build/nr-cli'
    result = set()
    for supi in host.run([cli, '--dump'], sudo=True).split():
        if not re.fullmatch(r'imsi-[0-9]{5,15}', supi):
            continue
        sessions = yaml.safe_load(host.run([cli, supi, '-e', 'ps-list'], sudo=True)) or {}
        for name, session in sessions.items():
            if session.get('state') == 'PS-ACTIVE':
                number = re.fullmatch(r'PDU Session(\d+)', name)
                if not number:
                    raise ValueError('unknown_ueransim_pdu_label')
                result.add((supi, int(number[1]), session['apn']))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true', required=True)
    p.add_argument('--guard', action='store_true', help='Enable experimental native fencing, without claiming C3 acceptance')
    p.add_argument('--build', required=True, help='Core VM output from build_native.py')
    p.add_argument('--evidence', required=True, type=Path)
    args = p.parse_args()
    if not re.fullmatch(r'/home/emsadmin/maestro-c3-build-[a-zA-Z0-9]+/out', args.build):
        p.error('Unexpected build directory')
    args.evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    hosts = {}
    tag = 'maestro-observer-' + uuid.uuid4().hex[:12]
    bundle = '/opt/' + tag
    result = {'run': tag, 'bundle': bundle, 'accepted': False, 'before': {}, 'after': {}}
    timers, rollbacks, changed, active_ues, saved = {}, {}, [], [], {}
    watchdog_active = False
    cutover_started = False
    try:
        for group, port in [('core', settings.ssh_port), ('upf', settings.upf_ssh_port),
                            ('upf2', settings.upf2_ssh_port), ('ue', settings.ue_ssh_port)]:
            hosts[group] = Lab(settings, port)
        core, ue = hosts['core'], hosts['ue']
        manifest = json.loads(core.read(args.build + '/manifest.json'))
        result['build_manifest'] = manifest
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as tar:
            filenames = ['manifest.json', *['open5gs-' + nf + 'd' for nf in ('pcf', 'smf', 'upf')],
                         *['lib/' + name for name in manifest['libraries']]]
            for name in filenames:
                data = core.read(args.build + '/' + name)
                import hashlib
                expected = (manifest['libraries'][name[4:]] if name.startswith('lib/') else
                            manifest['binaries'].get(name.removeprefix('open5gs-').removesuffix('d')))
                if expected and hashlib.sha256(data).hexdigest() != expected:
                    raise RuntimeError('build_digest_mismatch')
                info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o755
                tar.addfile(info, io.BytesIO(data))
            data = Path(__file__).with_name('query_native.py').read_bytes()
            info = tarfile.TarInfo('query_native.py'); info.size = len(data); info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
            for name in manifest['libraries']:
                short = name.split('.so')[0] + '.so'
                # freeDiameter has SONAME .so.7, discovered below using readelf.
                soname = core.run(['readelf', '-d', args.build + '/lib/' + name])
                match = re.search(r'\(SONAME\).*\[(.*?)\]', soname)
                aliases = {short, match[1] if match else name}
                for alias in aliases - {name}:
                    info = tarfile.TarInfo('lib/' + alias)
                    info.type = tarfile.SYMTYPE; info.linkname = name; info.mode = 0o777
                    tar.addfile(info)
        for group, targets in TARGETS.items():
            host = hosts[group]
            stage = host.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-XXXXXX']).strip()
            host.write(stage + '/bundle.tar.gz', archive.getvalue())
            host.run(['mkdir', '-m', '755', bundle], sudo=True)
            host.run(['tar', '-xzf', stage + '/bundle.tar.gz', '--no-same-owner', '-C', bundle], sudo=True)
            for nf, unit in targets:
                for filename in ('ExecStart', 'MainPID', 'ActiveState'):
                    result['before'].setdefault(nf, {})[filename] = host.run(
                        ['systemctl', 'show', unit, '-p', filename, '--value']).strip()
                if result['before'][nf]['ActiveState'] != 'active':
                    raise RuntimeError('baseline_nf_inactive:' + nf)
                original = exec_arguments(result['before'][nf]['ExecStart'])
                dest = '/etc/systemd/system/' + unit + '.service.d/' + DROPIN
                previous = host.run(['python3', '-c',
                    'import pathlib,sys,json;p=pathlib.Path(sys.argv[1]);print(json.dumps(p.read_text() if p.exists() else None))',
                    dest], sudo=True)
                previous = json.loads(previous)
                if previous is not None:
                    backup = bundle + '/' + nf + '.before.conf'
                    host.run(['cp', '--preserve=mode,ownership', dest, backup], sudo=True)
                    saved[(group, dest)] = backup
                candidate = stage + '/' + nf + '.conf'
                host.write(candidate, override(bundle, nf, original[2], args.guard))
                changed.append((group, dest, candidate))
                host.run(['env', 'LD_LIBRARY_PATH=' + bundle + '/lib',
                          bundle + '/open5gs-' + nf.rstrip('23') + 'd', '-v'], sudo=True)
            script = '#!/bin/sh\nset -eu\n' + ''.join(
                ('install -o root -g root -m 644 ' + shlex.quote(saved[(g, d)]) + ' ' + shlex.quote(d) + '\n'
                 if (g, d) in saved else 'rm -f -- ' + shlex.quote(d) + '\n')
                for g, d, _ in changed if g == group)
            script += 'systemctl daemon-reload\nsystemctl restart ' + shlex.join([u for _, u in targets]) + '\n'
            candidate = stage + '/rollback.sh'
            host.write(candidate, script)
            rollbacks[group] = bundle + '/rollback.sh'
            host.run(['install', '-o', 'root', '-g', 'root', '-m', '700', candidate, rollbacks[group]], sudo=True)
            timer = tag + '-rollback-' + group
            host.run(['systemd-run', '--unit=' + timer, '--on-active=600s', '/bin/sh', rollbacks[group]], sudo=True)
            timers[group] = timer
        # Protect UE recovery independently if this SSH client disappears.
        active_ues = [u for u in UE_UNITS if ue.run(['systemctl', 'is-active', u], check=False).strip() == 'active']
        watchdog_active = ue.run(['systemctl', 'is-active', 'ueransim-watchdog'], check=False).strip() == 'active'
        timer = tag + '-rollback-ue'
        ue.run(['systemd-run', '--unit=' + timer, '--on-active=630s', '/usr/bin/systemctl', 'restart',
                *active_ues, *(['ueransim-watchdog'] if watchdog_active else [])], sudo=True)
        timers['ue'] = timer
        result['ue_before'] = json.loads(ue.run(['ip', '-j', 'address', 'show']))
        baseline_pdus = active_pdus(ue)
        result['active_pdus_before'] = sorted(baseline_pdus)
        if not baseline_pdus:
            raise RuntimeError('baseline_has_no_active_pdu')
        # Close any old XDP gate before replacing the dedicated UPF lifecycle hook.
        close_gate = '''import pathlib,subprocess,json,os
p=pathlib.Path('/run/maestro-bpf/urllc/maps/enabled')
if p.exists():
 subprocess.run(['bpftool','map','update','pinned',str(p),'key','hex','00','00','00','00','value','hex','00','00','00','00'],check=True)
p=pathlib.Path('/run/maestro-urllc-xdp/native.json')
if p.exists():
 t=p.with_name('observer-revoke.tmp');t.write_text(json.dumps({'eligible':False,'reason':'native_observer_has_no_fastpath_actuator'}));os.replace(t,p)
'''
        hosts['upf'].run(['ip', 'netns', 'exec', 'maestro-urllc', 'python3', '-c', close_gate], sudo=True)
        if watchdog_active:
            ue.run(['systemctl', 'stop', 'ueransim-watchdog'], sudo=True)
        print('Prepared isolated binaries, verified hashes; starting controlled NF restart', flush=True)
        cutover_started = True
        ue.run(['systemctl', 'stop', *active_ues], sudo=True)
        for group, dest, candidate in changed:
            hosts[group].run(['install', '-D', '-o', 'root', '-g', 'root', '-m', '644', candidate, dest], sudo=True)
        for group in TARGETS:
            hosts[group].run(['systemctl', 'daemon-reload'], sudo=True)
            for nf, unit in TARGETS[group]:
                effective = hosts[group].run(['systemctl', 'show', unit, '-p', 'ExecStart', '--value'])
                if exec_arguments(effective)[0] != bundle + '/open5gs-' + nf.rstrip('23') + 'd':
                    raise RuntimeError('override_shadowed:' + nf)
        # UPF peers first, then PCF and SMFs. AMF/gNB remain running.
        for group in ('upf', 'upf2', 'core'):
            for nf, unit in TARGETS[group]:
                hosts[group].run(['systemctl', 'restart', unit], sudo=True, timeout=45)
                wait_active(hosts[group], unit)
        time.sleep(2)
        ue.run(['test', '-c', '/dev/net/tun'], sudo=True)
        ue.run(['systemctl', 'start', *active_ues], sudo=True)
        for unit in active_ues:
            wait_active(ue, unit)
        print('NFs active; native socket/session verification', flush=True)
        deadline = time.monotonic() + 65
        while True:
            result['observations'] = collect(hosts, bundle)
            result['ue_after'] = json.loads(ue.run(['ip', '-j', 'address', 'show']))
            native_pdus = {(s['supi'], s['pdu_id'], s['dnn']) for nf in ('smf', 'smf2', 'smf3')
                           for s in result['observations'][nf]['sessions'] if s['upf_seid'] != '0'}
            connected_pdus = active_pdus(ue)
            result['active_pdus_after'] = sorted(connected_pdus)
            if baseline_pdus <= native_pdus and baseline_pdus <= connected_pdus:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('baseline_pdu_reconnection_incomplete')
            time.sleep(2)
        for group, targets in TARGETS.items():
            for nf, unit in targets:
                result['after'][nf] = hosts[group].run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState'])
                if result['observations'][nf]['writer_fenced']:
                    raise RuntimeError('observer_must_not_claim_fencing')
        if watchdog_active:
            ue.run(['systemctl', 'start', 'ueransim-watchdog'], sudo=True)
        for group, timer in timers.items():
            hosts[group].run(['systemctl', 'stop', timer + '.timer'], sudo=True)
        result['accepted'] = True  # Observer deployment ONLY, never C3 acceptance.
        print(json.dumps({'observers_deployed': True, 'c3_accepted': False, 'bundle': bundle,
                          'sessions': {k: len(v['sessions']) for k, v in result['observations'].items()}}, indent=2))
    except BaseException as error:
        result['error'] = type(error).__name__ + ':' + str(error)
        # Preserve candidate failure evidence before rollback changes PIDs.
        for group, targets in TARGETS.items():
            if group not in hosts or not cutover_started:
                continue
            for nf, unit in targets:
                try:
                    journal = hosts[group].run(['journalctl', '-u', unit, '-n', '120', '--no-pager'],
                                              sudo=True, check=False)
                    (args.evidence / (nf + '-failure.log')).write_text(journal, encoding='utf8')
                except Exception:
                    pass
        result['rollback'] = {}
        for group in ('upf', 'upf2', 'core'):
            if group in rollbacks and cutover_started:
                try:
                    hosts[group].run(['/bin/sh', rollbacks[group]], sudo=True, timeout=90)
                    result['rollback'][group] = 'restored'
                except Exception as rollback_error:
                    result['rollback'][group] = str(rollback_error)
        if 'ue' in hosts and active_ues and cutover_started:
            hosts['ue'].run(['systemctl', 'restart', *active_ues,
                             *(['ueransim-watchdog'] if watchdog_active else [])], sudo=True)
        if not cutover_started or all(v == 'restored' for v in result['rollback'].values()):
            for group, timer in timers.items():
                hosts[group].run(['systemctl', 'stop', timer + '.timer'], sudo=True, check=False)
        raise
    finally:
        (args.evidence / 'deployment.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        for host in hosts.values():
            host.client.close()


if __name__ == '__main__':
    main()
