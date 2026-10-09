"""Stage the third UPF without changing existing UPFs, SMFs or subscriber data.

Default: read-only inventory + local candidate files. --execute installs only
new namespace/service resources on UPF-01. No XDP activation or slice cutover.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
from uuid import UUID, uuid5

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'infra' / 'charging'))
from app.core.config import get_settings
from e2e_native import Lab

NETWORK = '''#!/bin/sh
set -eu
case "$1" in
start)
  ip netns add maestro-urllc
  ip link add murllc-host type veth peer name murllc-n3
  ip link set murllc-n3 netns maestro-urllc
  ip address add 172.31.47.1/30 dev murllc-host
  ip link set murllc-host up
  ip -n maestro-urllc link set lo up
  ip -n maestro-urllc address add 10.210.50.22/32 dev lo
  ip -n maestro-urllc address add 172.31.47.2/30 dev murllc-n3
  ip -n maestro-urllc link set murllc-n3 up
  ip -n maestro-urllc route add default via 172.31.47.1
  ip route add 10.210.50.22/32 via 172.31.47.2 dev murllc-host
  ip netns exec maestro-urllc ip tuntap add name ogstun mode tun user open5gs
  ip -n maestro-urllc address add 10.47.0.1/16 dev ogstun
  ip -n maestro-urllc link set ogstun mtu 1400 up
  ip netns exec maestro-urllc sysctl -q -w net.ipv4.ip_forward=1
  # No N6 breakout until the MEC network and isolation policy are provisioned.
  ip netns exec maestro-urllc nft -f - <<'NFT'
table inet maestro_urllc {
 chain input { type filter hook input priority 0; policy accept;
  iifname "ogstun" ip daddr 10.47.0.1 ip protocol icmp accept
  iifname "ogstun" drop
 }
 chain forward { type filter hook forward priority 0; policy drop; }
}
NFT
  ;;
stop)
  ip route del 10.210.50.22/32 via 172.31.47.2 dev murllc-host 2>/dev/null || true
  ip link del murllc-host 2>/dev/null || true
  ip netns del maestro-urllc 2>/dev/null || true
  ;;
*) exit 2;;
esac
'''

NETWORK_UNIT = '''[Unit]
Description=MAEstro dedicated URLLC network namespace
After=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/libexec/maestro-urllc-network start
ExecStop=/usr/local/libexec/maestro-urllc-network stop
ExecStopPost=/usr/local/libexec/maestro-urllc-network stop
[Install]
WantedBy=multi-user.target
'''


def active_config(host, unit):
    raw = host.run(['systemctl', 'show', unit, '-p', 'ExecStart', '--value'])
    match = re.search(r'path=([^ ;]+).*? -c ([^ ;]+)', raw)
    if not match:
        raise ValueError('Cannot identify active configuration: ' + unit)
    binary, config = match.groups()
    text = host.run(['cat', config], sudo=True)
    return binary, config, text, yaml.safe_load(text)


def candidates(smf, upf):
    smf = deepcopy(smf)
    smf['logger']['file']['path'] = '/var/log/open5gs/smf3.log'
    smf.setdefault('global', {}).setdefault('max', {})['ue'] = 128
    nf = smf['smf']
    nf.pop('freeDiameter', None)
    for interface in ('gtpc', 'gtpu', 'metrics'):
        if interface in nf:
            for server in nf[interface]['server']:
                server['address'] = '10.210.50.18'
    nf['metrics'] = {'server': [{'address': '10.210.50.18', 'port': 9092}]}
    nf['sbi']['server'] = [{'address': '10.210.50.18', 'port': 7777}]
    nf['pfcp'] = {'server': [{'address': '10.210.50.18'}],
                  'client': {'upf': [{'address': '10.210.50.22', 'dnn': '5g-plus'}]}}
    nf['info'] = [{'s_nssai': [{'sst': 2, 'sd': '000002', 'dnn': ['5g-plus']}]}]
    nf['session'] = [{'subnet': '10.47.0.0/16', 'gateway': '10.47.0.1', 'dnn': '5g-plus'}]
    if nf.get('chf', {}).get('enabled'):
        nf['chf']['journal_dir'] = '/var/lib/open5gs/chf-journal-smf3'
        nf['chf']['nf_instance_id'] = str(uuid5(UUID('6c4a2c9f-57e2-4fdf-ba91-4417fd79ea3d'), 'maestro-smf3'))
    upf = deepcopy(upf)
    upf['logger']['file']['path'] = '/var/log/open5gs/upf-urllc.log'
    upf.setdefault('global', {}).setdefault('max', {})['ue'] = 128
    upf['upf']['pfcp'] = {'server': [{'address': '10.210.50.22'}]}
    upf['upf']['gtpu'] = {'server': [{'address': '10.210.50.22'}]}
    upf['upf']['session'] = [{'subnet': '10.47.0.0/16', 'gateway': '10.47.0.1',
                            'dnn': '5g-plus', 'dev': 'ogstun'}]
    upf['upf']['metrics'] = {'server': [{'address': '127.0.0.1', 'port': 9090}]}
    return smf, upf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    local = ROOT / '.work' / ('multi-upf-' + tag)
    local.mkdir(parents=True)
    core, upf = Lab(settings, settings.ssh_port), Lab(settings, settings.upf_ssh_port)
    result = {'phase': 'staging', 'xdp_enabled': False, 'slice_cutover': False}
    installed = False
    remote = None
    try:
        _, smf_path, smf_raw, smf = active_config(core, 'open5gs-smfd2')
        binary, upf_path, upf_raw, upf_config = active_config(upf, 'open5gs-upfd')
        (local/'baseline-smf2.yaml').write_text(smf_raw,encoding='utf-8')
        (local/'baseline-upf1.yaml').write_text(upf_raw,encoding='utf-8')
        smf3, upf3 = candidates(smf, upf_config)
        for name, data in [('smf3.yaml', smf3), ('upf-urllc.yaml', upf3)]:
            (local / name).write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
        env = upf.run(['systemctl', 'show', 'open5gs-upfd', '-p', 'Environment', '--value'])
        lib = re.search(r'(?:^|\s)LD_LIBRARY_PATH=([^\s"\']+)', env)
        if not lib:
            raise ValueError('Expected versioned UPF library path')
        unit = f'''[Unit]
Description=MAEstro URLLC UPF (charging-enabled traditional path)
Requires=maestro-urllc-network.service
After=maestro-urllc-network.service
PartOf=maestro-urllc-network.service
[Service]
Type=simple
User=open5gs
Group=open5gs
NetworkNamespacePath=/run/netns/maestro-urllc
ExecStart={binary} -c /etc/open5gs/upf-urllc.yaml
Environment=LD_LIBRARY_PATH={lib[1]}
Restart=on-failure
RestartSec=3
RestartPreventExitStatus=1
MemoryMax=256M
TasksMax=64
[Install]
WantedBy=multi-user.target
'''
        files = {'/etc/open5gs/upf-urllc.yaml': yaml.safe_dump(upf3, sort_keys=False),
                 '/usr/local/libexec/maestro-urllc-network': NETWORK,
                 '/etc/systemd/system/maestro-urllc-network.service': NETWORK_UNIT,
                 '/etc/systemd/system/open5gs-upfd-urllc.service': unit}
        for path, content in files.items():
            (local / Path(path).name).write_text(content, encoding='utf-8')
        result.update(active_configs={'smf2':smf_path, 'upf1':upf_path},
                      source_hashes={'smf2':hashlib.sha256(smf_raw.encode()).hexdigest(),
                                     'upf1':hashlib.sha256(upf_raw.encode()).hexdigest()},
                      upf_binary=binary,
                      remaining=['SMF3 distinct CHF token/allowlist', 'Core/gNB routes to .22',
                                 'AMF/NSSF/gNB/subscriber migration', 'MEC N6 routing',
                                 'PFCP association and session acceptance', 'XDP policy integration'])
        if args.execute:
            # Refuse collisions rather than adopting somebody else's resources.
            for path in files:
                upf.run(['test', '!', '-e', path], sudo=True)
            if 'maestro-urllc' in upf.run(['ip', 'netns', 'list'], sudo=True):
                raise ValueError('Namespace already exists')
            if 'murllc-' in upf.run(['ip', '-o', 'link']):
                raise ValueError('veth already exists')
            routes = json.loads(upf.run(['ip', '-j', 'route', 'show', 'table', 'all']))
            if any(r.get('dst') in ('172.31.47.0/30', '10.210.50.22', '10.210.50.22/32') for r in routes):
                raise ValueError('Proposed route already exists')
            remote = upf.run(['mktemp','-d','/home/emsadmin/maestro-upf-stage-XXXXXX']).strip()
            upf.write(remote+'/network.sh', NETWORK, 0o700)
            upf.run(['sh', '-n', remote+'/network.sh'])
            # Rollback stops only these new resources; original services stay running.
            rollback = '#!/bin/sh\nsystemctl disable --now open5gs-upfd-urllc.service maestro-urllc-network.service\n'
            upf.write(remote+'/rollback.sh', rollback, 0o700)
            installed = True
            for index, (path, content) in enumerate(files.items()):
                staged = remote + '/' + str(index)
                upf.write(staged, content)
                upf.run(['install','-D','-o','root','-g','open5gs','-m',
                         '750' if path.endswith('maestro-urllc-network') else '640' if path.endswith('.yaml') else '644',
                         staged,path],sudo=True)
            upf.run(['systemctl','daemon-reload'],sudo=True)
            upf.run(['systemctl','start','open5gs-upfd-urllc'],sudo=True)
            upf.run(['systemctl','is-active','open5gs-upfd-urllc'],sudo=True)
            upf.run(['systemctl','is-active','open5gs-upfd'],sudo=True)
            result['metrics'] = upf.run(['ip','netns','exec','maestro-urllc','curl','-fsS','--max-time','3',
                                        'http://127.0.0.1:9090/metrics'],sudo=True)
            result['phase'] = 'upf_staged_no_slice_cutover'
            result['rollback'] = remote + '/rollback.sh'
            # Deliberately not boot-enabled before end-to-end acceptance.
        else:
            result['phase'] = 'candidates_rendered'
    except BaseException:
        if installed and remote:
            upf.run(['/bin/sh', remote+'/rollback.sh'],sudo=True,check=False)
            result['phase'] = 'staging_rolled_back'
        raise
    finally:
        (local/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({'phase':result['phase'],'evidence':str(local),'slice_cutover':False}))
        core.client.close()
        upf.client.close()


if __name__ == '__main__':
    main()
