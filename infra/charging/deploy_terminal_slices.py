"""Install corporate portal and reversible DNN firewall rules; never flush shared rules."""
import argparse
import hashlib
from pathlib import Path
from e2e_native import Lab
from lab_command import get_settings

MARKER = 'MAEstro terminal DNN isolation'


def install_policy(host, rules):
    target = '/etc/systemd/system/maestro-terminal-isolation.service'
    try:
        old = host.read(target)
    except FileNotFoundError:
        old = b''
    if old and MARKER.encode() not in old:
        raise RuntimeError('Refusing foreign unit')
    tables = host.run(['nft', 'list', 'tables'], sudo=True)
    if 'table inet maestro_terminal' in tables and not old:
        raise RuntimeError('Refusing foreign nft table')
    stage = host.run(['mktemp', '-d', '/home/emsadmin/terminal-policy-XXXXXX']).strip()
    digest = hashlib.sha256(rules.encode()).hexdigest()[:12]
    directory = '/opt/maestro-terminal-policy-' + digest
    host.write(stage + '/rules.nft', rules)
    host.run(['nft', '--check', '-f', stage + '/rules.nft'], sudo=True)
    host.run(['install', '-d', '-m', '755', directory], sudo=True)
    host.run(['install', '-m', '644', stage + '/rules.nft', directory + '/rules.nft'], sudo=True)
    unit = ('[Unit]\nDescription=' + MARKER + '\nAfter=network-online.target\n'
            '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
            'ExecStart=/usr/sbin/nft -f ' + directory + '/rules.nft\n'
            'ExecStop=/usr/sbin/nft delete table inet maestro_terminal\n'
            '[Install]\nWantedBy=multi-user.target\n')
    host.write(stage + '/unit', unit)
    if old:
        host.write(stage + '/previous-unit', old)
        host.run(['systemctl', 'stop', 'maestro-terminal-isolation'], sudo=True)
    host.run(['install', '-m', '644', stage + '/unit', target], sudo=True)
    host.run(['systemctl', 'daemon-reload'], sudo=True)
    host.run(['systemctl', 'enable', '--now', 'maestro-terminal-isolation'], sudo=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute:
        parser.error('--execute required: blocks cross-DNN traffic and corporate forwarding')
    settings = get_settings()
    internet = Lab(settings, settings.upf_ssh_port)
    corporate = Lab(settings, settings.upf2_ssh_port)
    try:
        # No DROP policy on shared chains: narrow owned rules only; preserve management/N3/N4.
        install_policy(internet, '''table inet maestro_terminal {
 chain forward {
  type filter hook forward priority -10; policy accept;
  iifname "ogstun" ip daddr 10.46.0.0/16 counter reject
 }
}
''')
        install_policy(corporate, '''table inet maestro_terminal {
 chain forward {
  type filter hook forward priority -10; policy accept;
  iifname "ogstun" counter reject
 }
 chain input {
  type filter hook input priority -10; policy accept;
  ip daddr 10.46.0.1 tcp dport 8080 iifname "ogstun" ip saddr 10.46.0.0/16 counter accept
  iifname "ogstun" ip saddr 10.46.0.0/16 ip daddr 10.46.0.1 icmp type echo-request counter accept
  iifname "ogstun" counter reject
  ip daddr 10.46.0.1 tcp dport 8080 counter reject
 }
}
''')
        source = Path(__file__).with_name('corporate_portal.py').read_bytes()
        digest = hashlib.sha256(source).hexdigest()[:12]
        directory = '/opt/maestro-corporate-' + digest
        stage = corporate.run(['mktemp', '-d', '/home/emsadmin/corporate-portal-XXXXXX']).strip()
        target = '/etc/systemd/system/maestro-corporate-portal.service'
        try:
            old = corporate.read(target)
        except FileNotFoundError:
            old = b''
        if old and b'Description=MAEstro corporate portal' not in old:
            raise RuntimeError('Foreign portal service')
        if old:
            corporate.write(stage + '/previous-unit', old)
        corporate.write(stage + '/portal.py', source)
        corporate.run(['install', '-d', '-m', '755', directory], sudo=True)
        corporate.run(['install', '-m', '644', stage + '/portal.py', directory + '/portal.py'], sudo=True)
        unit = ('[Unit]\nDescription=MAEstro corporate portal\nAfter=network-online.target open5gs-upfd.service maestro-terminal-isolation.service\n'
                '[Service]\nDynamicUser=yes\nExecStart=/usr/bin/python3 ' + directory + '/portal.py\n'
                'Restart=on-failure\nRestartSec=3\nNoNewPrivileges=yes\nPrivateTmp=yes\n'
                'ProtectSystem=strict\nProtectHome=yes\nRestrictAddressFamilies=AF_INET\nMemoryMax=64M\nTasksMax=16\n'
                '[Install]\nWantedBy=multi-user.target\n')
        corporate.write(stage + '/unit', unit)
        corporate.run(['install', '-m', '644', stage + '/unit', target], sudo=True)
        corporate.run(['systemctl', 'daemon-reload'], sudo=True)
        corporate.run(['systemctl', 'enable', '--now', 'maestro-corporate-portal'], sudo=True)
        corporate.run(['systemctl', 'restart', 'maestro-corporate-portal'], sudo=True)
        print(corporate.run(['systemctl', 'is-active', 'maestro-corporate-portal', 'maestro-terminal-isolation']))
        print('Rollback on UPF-02: sudo systemctl disable --now maestro-corporate-portal maestro-terminal-isolation')
        print('Rollback on UPF-01: sudo systemctl disable --now maestro-terminal-isolation')
    finally:
        internet.client.close()
        corporate.client.close()


if __name__ == '__main__':
    main()
