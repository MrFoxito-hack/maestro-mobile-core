"""Install a versioned, reversible native charging profile in the authorized lab.

No stock binary, YAML or accounting row is overwritten. Dedicated systemd
drop-ins point to private versioned artifacts. All affected NFs must be healthy
before rollback timers are cancelled. This is lab activation, not production HA.
"""
import argparse
import json
import re
import secrets
import shlex
import time
import uuid

import yaml
from e2e_native import Lab, BUILD, CHF, ROOT
from lab_command import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    if not args.execute and not args.inspect:
        parser.error('Explicit --execute required: restarts SMF and both UPFs')
    settings = get_settings()
    hosts = {name: Lab(settings, port) for name, port in
             [('core', settings.ssh_port), ('upf1', settings.upf_ssh_port), ('upf2', settings.upf2_ssh_port)]}
    core = hosts['core']
    tag = 'maestro-charging-' + uuid.uuid4().hex[:12]
    result = {'release': tag, 'hosts': {}}
    prepared = []
    try:
        if args.inspect:
            print(core.run(['python3', '-c', '''import pathlib,json,subprocess
pid=subprocess.check_output(['systemctl','show','open5gs-chfd','-p','MainPID','--value']).decode().strip()
env=dict(v.split(b'=',1) for v in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\\0') if b'=' in v)
print(json.dumps({k.decode(): (v.decode() if k in [b'CHF_DATABASE_PATH',b'CHF_SBI_LAB_NO_AUTH'] else 'present') for k,v in env.items() if k.startswith(b'CHF_')}))
'''], sudo=True))
            return
        # Obtain the active server identity/token privately; never print credentials.
        inspect = '''import pathlib,json
found=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  if b'app.main:app' not in (p/'cmdline').read_bytes(): continue
  env=dict(v.split(b'=',1) for v in (p/'environ').read_bytes().split(b'\\0') if b'=' in v)
  if env.get(b'CHF_DATABASE_PATH')!=b'/home/emsadmin/maestro-charging/charging.sqlite3': continue
  tokens=json.loads(env.get(b'CHF_SBI_TOKENS',b'{}'))
  if len(tokens)==1 and tokens not in found: found.append(tokens)
 except (OSError,ValueError): pass
if len(found)>1: raise SystemExit('Ambiguous live CHF credentials')
print(json.dumps(found[0] if found else {}))
'''
        tokens = json.loads(core.run(['python3', '-c', inspect], sudo=True))
        if not tokens:
            tokens = {str(uuid.uuid4()): secrets.token_urlsafe(40)}
            authdir = core.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-auth-XXXXXX']).strip()
            authdrop = '/etc/systemd/system/open5gs-chfd.service.d/90-maestro-auth.conf'
            core.run(['sh', '-c', 'test ! -e ' + shlex.quote(authdrop)])
            core.write(authdir + '/runtime.env', "CHF_SBI_LAB_NO_AUTH=false\nCHF_SBI_TOKENS='" + json.dumps(tokens) + "'\n")
            core.write(authdir + '/dropin.conf', '[Service]\nEnvironment=CHF_SBI_LAB_NO_AUTH=false\nEnvironmentFile=' + authdir + '/runtime.env\n')
            rollback = authdir + '/rollback.sh'
            core.write(rollback, '#!/bin/sh\nset -eu\nif test -f ' + authdrop + '; then mv ' + authdrop + ' ' + authdir + '/disabled-dropin.conf; fi\nsystemctl daemon-reload\nsystemctl restart open5gs-chfd\n', 0o700)
            core.run(['chown', '-R', 'root:root', authdir], sudo=True)
            timer = tag + '-auth-rollback'
            core.run(['systemd-run', '--unit=' + timer, '--on-active=300s', '/bin/sh', rollback], sudo=True)
            prepared.append((core, rollback, timer))
            core.run(['install', '-d', '-m', '755', '/etc/systemd/system/open5gs-chfd.service.d'], sudo=True)
            core.run(['install', '-m', '644', authdir + '/dropin.conf', authdrop], sudo=True)
            result['chf_rollback'] = rollback
        owner, token = next(iter(tokens.items()))
        uuid.UUID(owner)
        if not re.fullmatch(r'[A-Za-z0-9_\-]+', token):
            raise RuntimeError('Unsupported token encoding; do not rewrite credentials')
        for name, host in hosts.items():
            kind = 'smf' if name == 'core' else 'upf'
            service = 'open5gs-' + kind + 'd'
            dropdir = '/etc/systemd/system/' + service + '.service.d'
            dropin = dropdir + '/90-maestro-charging.conf'
            if host.run(['test', '-e', dropin], check=False) or host.run(['sh', '-c', 'test ! -e ' + shlex.quote(dropin) + ' && echo absent']).strip() != 'absent':
                raise RuntimeError('Existing activation requires explicit upgrade, not overwrite')
            host.run(['systemctl', 'is-active', service])
            stage = host.run(['mktemp', '-d', '/home/emsadmin/' + tag + '-XXXXXX']).strip()
            target = '/opt/maestro-charging/' + tag
            host.run(['mkdir', '-m', '700', stage + '/lib'])
            binary = BUILD + '/src/' + kind + '/' + service
            dependencies = core.run(['ldd', binary])
            for dep in [binary] + re.findall(r'=> (\S+/maestro-charging/open5gs/build/\S+)', dependencies):
                dest = stage + '/' + service if dep == binary else stage + '/lib/' + dep.rsplit('/', 1)[-1]
                host.write(dest, core.read(dep), 0o700 if dep == binary else 0o600)
            config = yaml.safe_load(host.read('/etc/open5gs/' + kind + '.yaml'))
            if kind == 'smf':
                config[kind]['chf'] = {'enabled': True, 'sbi': [{'addr': '127.0.0.1', 'port': 8081}],
                    'requested_units': 10000, 'journal_dir': '/var/lib/open5gs/chf-journal',
                    'timeout_ms': 2000, 'retries': 2, 'nf_instance_id': owner}
                host.write(stage + '/runtime.env', 'SMF_CHF_TOKEN=' + token + '\n')
                host.run(['install', '-d', '-o', 'open5gs', '-g', 'open5gs', '-m', '700', '/var/lib/open5gs/chf-journal'], sudo=True)
            else:
                config[kind]['charging_enforcement'] = True
            host.write(stage + '/' + kind + '.yaml', yaml.safe_dump(config))
            host.run(['install', '-d', '-m', '755', '/opt/maestro-charging'], sudo=True)
            host.run(['cp', '-a', stage, target], sudo=True)
            host.run(['chown', '-R', 'root:open5gs', target], sudo=True)
            host.run(['chmod', '-R', 'g+rX', target], sudo=True)
            ldd = host.run(['runuser', '-u', 'open5gs', '--', 'env', 'LD_LIBRARY_PATH=' + target + '/lib', 'ldd', target + '/' + service], sudo=True)
            if 'not found' in ldd or not all(target in line for line in ldd.splitlines() if 'libogs' in line or 'libprom.so' in line):
                raise RuntimeError('Private library resolution failed')
            drop = '[Service]\nExecStart=\nExecStart=' + target + '/' + service + ' -c ' + target + '/' + kind + '.yaml\nEnvironment=LD_LIBRARY_PATH=' + target + '/lib\n'
            if kind == 'smf':
                drop += 'EnvironmentFile=' + target + '/runtime.env\n'
            host.write(stage + '/dropin.conf', drop)
            rollback = stage + '/rollback.sh'
            host.write(rollback, '#!/bin/sh\nset -eu\n' +
                'if test -f ' + dropin + '; then mv ' + dropin + ' ' + stage + '/disabled-dropin.conf; fi\n' +
                'systemctl daemon-reload\nsystemctl restart ' + service + '\n', 0o700)
            # Prevent an unprivileged edit of a script later executed by root.
            host.run(['chown', '-R', 'root:root', stage], sudo=True)
            timer = tag + '-' + name + '-rollback'
            host.run(['systemd-run', '--unit=' + timer, '--on-active=300s', '/bin/sh', rollback], sudo=True)
            prepared.append((host, rollback, timer))
            host.run(['install', '-d', '-m', '755', dropdir], sudo=True)
            host.run(['install', '-m', '644', stage + '/dropin.conf', dropin], sudo=True)
            host.run(['systemctl', 'daemon-reload'], sudo=True)
            result['hosts'][name] = {'target': target, 'service': service, 'rollback': rollback}
        # UPF first, then SMF; no UE configuration is changed.
        core.run(['systemctl', 'daemon-reload'], sudo=True)
        core.run(['systemctl', 'restart', 'open5gs-chfd'], sudo=True)
        core.run(['systemctl', 'is-active', 'open5gs-chfd'])
        for name in ('upf1', 'upf2', 'core'):
            hosts[name].run(['systemctl', 'restart', result['hosts'][name]['service']], sudo=True)
        time.sleep(8)
        for name, host in hosts.items():
            host.run(['systemctl', 'is-active', result['hosts'][name]['service']])
            count = host.run(['systemctl', 'show', result['hosts'][name]['service'], '-p', 'NRestarts', '--value']).strip()
            if count != '0':
                raise RuntimeError('Service restart loop detected')
        core.run(['systemctl', 'enable', 'open5gs-chfd', 'maestro-chf-management'], sudo=True)
        for host, _, timer in prepared:
            host.run(['systemctl', 'stop', timer + '.timer'], sudo=True)
        (ROOT / '.work' / (tag + '.json')).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    except BaseException:
        for host, rollback, timer in reversed(prepared):
            try:
                host.run(['/bin/sh', rollback], sudo=True)
                host.run(['systemctl', 'stop', timer + '.timer'], sudo=True)
            except Exception:
                pass  # Independent timer remains armed if SSH is unavailable.
        raise
    finally:
        for host in hosts.values():
            host.client.close()


if __name__ == '__main__':
    main()
