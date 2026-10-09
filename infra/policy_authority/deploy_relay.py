"""Install the restricted native transport on UPF VMs and provision mTLS.

No NF restart or policy mutation. Run from backend/ with its existing SSH
configuration. Private keys are generated on and remain on their owning VM.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from infra.charging.e2e_native import Lab, get_settings


def install(host, path, content, stage, mode='600'):
    temporary = stage + '/' + uuid.uuid4().hex
    host.write(temporary, content)
    host.run(['install', '-o', 'root', '-g', 'root', '-m', mode, temporary, path], sudo=True)


def deploy(evidence):
    evidence.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    hosts, stages, deployed = {}, {}, []
    suffix = uuid.uuid4().hex[:12]
    bundle = '/opt/maestro-native-relay-' + suffix
    tls = '/etc/maestro-policy-native/tls-' + suffix
    result = {'bundle': bundle, 'tls': tls, 'accepted': False}
    try:
        for name, port in [('core', settings.ssh_port), ('upf', settings.upf_ssh_port), ('upf2', settings.upf2_ssh_port)]:
            host = hosts[name] = Lab(settings, port)
            stages[name] = host.run(['mktemp', '-d', '/home/emsadmin/c3-relay-XXXXXX']).strip()
            host.run(['mkdir', '-m', '755', bundle], sudo=True)
            host.run(['mkdir', '-p', '-m', '700', tls], sudo=True)
            for filename in ('native_relay.py', 'native_transport.py', 'native_runtime.py'):
                install(host, bundle + '/' + filename, Path(__file__).with_name(filename).read_bytes(), stages[name], '644')
        core = hosts['core']
        core.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365',
                  '-subj', '/CN=MAEstro native authority ' + suffix,
                  '-keyout', tls + '/ca.key', '-out', tls + '/ca.pem'], sudo=True)
        ca = core.run(['cat', tls + '/ca.pem'], sudo=True)
        for name, host in hosts.items():
            host.run(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes',
                      '-subj', '/CN=maestro-native-' + name,
                      '-keyout', tls + '/key.pem', '-out', tls + '/request.pem'], sudo=True)
            request = host.run(['cat', tls + '/request.pem'], sudo=True)
            csr = stages['core'] + '/' + name + '.csr'
            extension = stages['core'] + '/' + name + '.ext'
            signed = stages['core'] + '/' + name + '.pem'
            core.write(csr, request)
            if name == 'core':
                properties = 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=clientAuth\n'
            else:
                address = '10.210.50.8' if name == 'upf' else '10.210.50.9'
                properties = ('basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n'
                              'extendedKeyUsage=serverAuth\nsubjectAltName=IP:' + address + '\n')
            core.write(extension, properties)
            core.run(['openssl', 'x509', '-req', '-in', csr, '-CA', tls + '/ca.pem',
                      '-CAkey', tls + '/ca.key', '-CAserial', tls + '/serial', '-CAcreateserial',
                      '-days', '365', '-extfile', extension, '-out', signed], sudo=True)
            certificate = core.run(['cat', signed], sudo=True)
            install(host, tls + '/cert.pem', certificate, stages[name])
            if name != 'core':
                install(host, tls + '/ca.pem', ca, stages[name])
            host.run(['chmod', '600', tls + '/key.pem'], sudo=True)
        nfs = {nf: {'socket': f'/run/maestro-observer-{nf}/observe.sock'} for nf in ('pcf', 'smf', 'smf2', 'smf3')}
        for name, targets, address in [('upf', ['upf', 'upf3'], '10.210.50.8'), ('upf2', ['upf2'], '10.210.50.9')]:
            host = hosts[name]
            config = {'address': address, 'port': 9447, 'certificate': tls + '/cert.pem',
                      'key': tls + '/key.pem', 'ca': tls + '/ca.pem',
                      'nfs': {nf: f'/run/maestro-observer-{nf}/observe.sock' for nf in targets}}
            path = bundle + '/relay.json'
            install(host, path, json.dumps(config, indent=2), stages[name])
            unit = '/etc/systemd/system/maestro-native-relay.service'
            host.run(['test', '!', '-e', unit], sudo=True)
            body = ('[Unit]\nDescription=MAEstro restricted native NF transport\nAfter=network-online.target\n'
                    '[Service]\nType=simple\nUser=root\n'
                    f'ExecStart=/usr/bin/python3 {bundle}/native_relay.py {path}\n'
                    'Restart=on-failure\nRestartSec=2\nNoNewPrivileges=true\nPrivateTmp=true\n'
                    'ProtectHome=true\nProtectSystem=strict\nProtectKernelTunables=true\n'
                    'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\n'
                    # Bind /run, not individual RuntimeDirectory inodes that
                    # systemd replaces when an NF restarts.
                    'ReadWritePaths=/run\n'
                    '[Install]\nWantedBy=multi-user.target\n')
            install(host, unit, body, stages[name], '644')
            deployed.append(name)
            host.run(['systemctl', 'daemon-reload'], sudo=True)
            host.run(['systemctl', 'enable', '--now', 'maestro-native-relay'], sudo=True)
            nfs.update({nf: {'address': address, 'port': 9447} for nf in targets})
        config = {'ca': tls + '/ca.pem', 'certificate': tls + '/cert.pem', 'key': tls + '/key.pem', 'nfs': nfs}
        path = bundle + '/transport.json'
        install(core, path, json.dumps(config, indent=2), stages['core'])
        probe = '''import sys,json,time,ssl,http.client
sys.path.insert(0,sys.argv[1])
from native_transport import Transport
c=json.load(open(sys.argv[2]));t=Transport(c);start=time.monotonic()
for attempt in range(20):
 try:
  s=t.observe();break
 except (OSError,ValueError):
  if attempt==19:raise
  time.sleep(.1)
no_client_cert_rejected=False
try:
 conn=http.client.HTTPSConnection('10.210.50.8',9447,timeout=2,context=ssl.create_default_context(cafile=c['ca']))
 conn.request('POST','/v1/native','{}');conn.getresponse()
except (ssl.SSLError,OSError):no_client_cert_rejected=True
finally:conn.close()
assert no_client_cert_rejected
print(json.dumps({'elapsed_seconds':time.monotonic()-start,'unauthenticated_rejected':True,'nfs':{n:{'pid':d['pid'],'generation':d['generation'],'sessions':len(d['sessions']),'scope':d['scope']} for n,(d,_) in s.items()}}))
'''
        result['probe'] = json.loads(core.run(['python3', '-c', probe, bundle, path], sudo=True, timeout=15))
        result['transport_config'] = path
        result['accepted'] = True
        return result
    finally:
        if not result['accepted']:
            for name in deployed:
                hosts[name].run(['systemctl', 'disable', '--now', 'maestro-native-relay'], sudo=True, check=False)
        (evidence / 'deployment.json').write_text(json.dumps(result, indent=2), encoding='utf8')
        for host in hosts.values():
            host.client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(deploy(args.evidence), indent=2))
