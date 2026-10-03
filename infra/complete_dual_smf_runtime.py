"""Preserve CHF authentication and enable explicit NSSF selection for this lab."""
import json
from pathlib import Path
import re
import secrets
import sys
import time
import yaml
from deploy_dual_smf import STATE, ROOT, evidence, save, Lab, get_settings

sys.stdout.reconfigure(encoding='utf-8')
state = json.loads(STATE.read_text())
settings = get_settings()
core = Lab(settings, settings.ssh_port)
remote = state['remote']
try:
    # All existing NF token bindings remain; SMF-02 gets its own private token.
    nf_id = yaml.safe_load(core.run(['cat', '/etc/open5gs/smf2.yaml'], sudo=True))['smf']['chf']['nf_instance_id']
    inspect = '''import pathlib,json,subprocess
pid=subprocess.check_output(['systemctl','show','open5gs-chfd','-p','MainPID','--value']).decode().strip()
env=dict(v.split(b'=',1) for v in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\\0') if b'=' in v)
print(env[b'CHF_SBI_TOKENS'].decode())
'''
    tokens = json.loads(core.run(['python3', '-c', inspect], sudo=True))
    assert nf_id not in tokens, 'Already configured; do not rotate blindly'
    tokens[nf_id] = secrets.token_urlsafe(40)
    core.write(remote + '/smf2.env', 'SMF_CHF_TOKEN=' + tokens[nf_id] + '\n')
    core.write(remote + '/chf-dual.env', "CHF_SBI_TOKENS='" + json.dumps(tokens) + "'\n")
    chf_drop = '/etc/systemd/system/open5gs-chfd.service.d/zzzz-dual-smf.conf'
    smf_drop = '/etc/systemd/system/open5gs-smfd2.service.d/10-chf.conf'
    amf_drop = '/etc/systemd/system/open5gs-amfd.service.d/zzzz-nssf-selection.conf'
    source = '/home/emsadmin/maestro-charging/open5gs/src/amf/gmm-handler.c'
    original = core.read(source).decode()
    core.write(remote + '/gmm-handler.c.original', original)
    rollback = core.read(remote + '/rollback.sh').decode()
    extra = 'rm -f ' + chf_drop + ' ' + smf_drop + ' ' + amf_drop + '\n'
    extra += 'cp -a ' + remote + '/gmm-handler.c.original ' + source + '\n'
    rollback = rollback.replace('systemctl daemon-reload\n', extra + 'systemctl daemon-reload\nsystemctl restart open5gs-chfd\n')
    core.write(remote + '/rollback-extended.sh', rollback, 0o700)
    core.run(['cp', remote + '/rollback-extended.sh', remote + '/rollback.sh'])
    core.run(['sh', '-n', remote + '/rollback.sh'])
    for path in (remote + '/smf2.env', remote + '/chf-dual.env', remote + '/rollback.sh'):
        core.run(['chown', 'root:root', path], sudo=True)
        core.run(['chmod', '0600' if path.endswith('.env') else '0700', path], sudo=True)
    for directory in ('/etc/systemd/system/open5gs-chfd.service.d', '/etc/systemd/system/open5gs-smfd2.service.d'):
        core.run(['mkdir', '-p', directory], sudo=True)
    for dest, body in [(chf_drop, '[Service]\nEnvironmentFile=' + remote + '/chf-dual.env\n'),
                       (smf_drop, '[Service]\nEnvironmentFile=' + remote + '/smf2.env\n')]:
        staged = remote + '/staged/' + ('chf-drop.conf' if dest == chf_drop else 'smf2-chf-drop.conf')
        core.write(staged, body)
        core.run(['install', '-m', '0644', staged, dest], sudo=True)
    core.run(['chmod', '0700', '/var/lib/open5gs/chf-journal-smf2'], sudo=True)
    core.run(['systemctl', 'daemon-reload'], sudo=True)
    core.run(['systemctl', 'restart', 'open5gs-chfd', 'open5gs-smfd2'], sudo=True)
    print('CHF: separate authenticated SMF identities configured', flush=True)
    # Capture outside dumpcap's restricted /home path. No credentials are printed.
    for unit, interface, capture_filter, filename in [
        ('maestro-dual-smf-sbi-v2', 'lo', 'tcp port 7777', 'nssf_nsselection_smf2.pcap'),
        ('maestro-dual-smf-pfcp-v2', state['interface'], 'udp port 8805', 'n4_dual_smf.pcap'),
    ]:
        path = '/tmp/' + filename
        core.run(['sh', '-c', 'test ! -e ' + path])
        core.run(['systemd-run', '--unit=' + unit, '--property=RuntimeMaxSec=1500',
                  '/usr/bin/tshark', '-i', interface, '-f', capture_filter, '-w', path], sudo=True)
    time.sleep(2)
    for unit in ('maestro-dual-smf-sbi-v2', 'maestro-dual-smf-pfcp-v2'):
        assert core.run(['systemctl', 'is-active', unit]).strip() == 'active'
    # Upstream AMF caches SMF profiles. Opt-in always-NSSF policy applies only
    # to initial SM context creation, preserving stock behavior without the env.
    start = '                v_smf_instance = OGS_SBI_GET_NF_INSTANCE(\n'
    end = '                if (v_smf_instance) {\n                    ogs_info("V-SMF Instance [%s]", v_smf_instance->id);'
    assert original.count(start) == 1 and original.count(end) == 1
    modified = original.replace(start, '                /* MAEstro: consult NSSF for each new PDU session when enabled. */\n                if (!getenv("MAESTRO_NSSF_ALWAYS")) {\n' + start)
    modified = modified.replace(end, '                }\n\n' + end)
    core.write(remote + '/gmm-handler.c.modified', modified)
    core.run(['cp', remote + '/gmm-handler.c.modified', source])
    import difflib
    evidence(state, 'amf-always-nssf.patch', ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile='a/src/amf/gmm-handler.c', tofile='b/src/amf/gmm-handler.c')))
    print('Building opt-in AMF NSSF policy...', flush=True)
    build = '/home/emsadmin/maestro-charging/open5gs/build'
    output = core.run(['ninja', '-C', build, 'src/amf/open5gs-amfd'], timeout=180)
    evidence(state, 'amf-build.log', output)
    binary = build + '/src/amf/open5gs-amfd'
    deps = core.run(['ldd', binary])
    assert 'not found' not in deps
    libdirs = sorted(set(p.rsplit('/', 1)[0] for p in re.findall(r'=> (\S+/maestro-charging/open5gs/build/\S+)', deps)))
    assert libdirs
    body = '[Service]\nExecStart=\nExecStart=' + binary + ' -c /etc/open5gs/amf.yaml\nEnvironment=LD_LIBRARY_PATH=' + ':'.join(libdirs) + '\nEnvironment=MAESTRO_NSSF_ALWAYS=1\n'
    core.write(remote + '/staged/amf-nssf.conf', body)
    core.run(['mkdir', '-p', '/etc/systemd/system/open5gs-amfd.service.d'], sudo=True)
    core.run(['install', '-m', '0644', remote + '/staged/amf-nssf.conf', amf_drop], sudo=True)
    core.run(['systemctl', 'daemon-reload'], sudo=True)
    state['amf_nssf_policy'] = 'MAESTRO_NSSF_ALWAYS=1; custom native build'
    state['chf_smf2_nf_instance_id'] = nf_id
    save(state)
    print('AMF override staged; coordinated restart required', flush=True)
finally:
    core.client.close()
