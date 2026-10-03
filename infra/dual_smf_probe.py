"""Read-only deployment preflight using the backend's Paramiko connection."""
import json
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'infra' / 'charging'))
from e2e_native import Lab
from lab_command import get_settings

sys.stdout.reconfigure(encoding='utf-8')
settings = get_settings()
core = Lab(settings, settings.ssh_port)
try:
    for name, args in [
        ('host', ['hostname']), ('time', ['date', '-Is']),
        ('addresses', ['ip', '-br', 'addr']), ('sockets', ['ss', '-lntup']),
        ('services', ['systemctl', 'show', 'open5gs-smfd', 'open5gs-amfd', 'open5gs-nssfd', 'open5gs-bsfd', '-p', 'ExecStart', '-p', 'Environment', '-p', 'ActiveState']),
        ('active-units', ['systemctl', 'cat', 'open5gs-smfd', 'open5gs-amfd', 'open5gs-bsfd']),
        ('version', ['/usr/bin/open5gs-amfd', '-v']),
        ('source', ['sh', '-c', "grep -n -B 12 -A 20 'nnssf\|nssf.nrf' /home/emsadmin/maestro-charging/open5gs/src/amf/gsm-handler.c"]),
        ('pdu', ['curl', '-sf', '--max-time', '5', 'http://127.0.0.4:9090/pdu-info?page=0&page_size=100']),
        ('ip-free', ['sh', '-c', 'ping -c 2 -W 1 10.210.50.2; ip neigh show 10.210.50.2']),
        ('tools', ['sh', '-c', 'command -v mongodump; command -v mongosh; command -v mongo; command -v tshark; command -v arping']),
    ]:
        print(name + ':\n' + core.run(args, sudo=True, check=False), flush=True)
finally:
    core.client.close()
for role, port in [('upf2', settings.upf2_ssh_port), ('ue', settings.ue_ssh_port)]:
    host = Lab(settings, port)
    try:
        print(role + ':\n' + host.run(['systemctl', 'list-units', '--type=service', '--state=running', '--no-pager']), flush=True)
        if role == 'upf2':
            print(host.run(['systemctl', 'show', 'open5gs-upfd', '-p', 'ExecStart', '-p', 'Environment']), flush=True)
        else:
            print(host.run(['/home/emsadmin/UERANSIM/build/nr-cli', '--dump']), flush=True)
            units = host.run(['systemctl', 'list-units', 'ueransim-ue*', '--state=running', '--no-legend']).splitlines()
            for line in units:
                unit = line.split()[0]
                command = host.run(['systemctl', 'show', unit, '-p', 'ExecStart', '--value'])
                print(command, flush=True)
            print(host.run(['find', '/home/emsadmin/UERANSIM/config', '-maxdepth', '1', '-name', '*ue*.yaml']), flush=True)
    finally:
        host.client.close()
