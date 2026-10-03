"""Lab SSH transport. Password is read from UPF_SSH_PASSWORD, never saved."""
import argparse
import os
import shlex
import sys
from pathlib import Path
import paramiko


def connect(port=2223):
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    keys = Path(__file__).resolve().parents[2] / '.work' / 'xdp_known_hosts'
    keys.parent.mkdir(exist_ok=True)
    if keys.exists():
        c.load_host_keys(str(keys))
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('127.0.0.1', port=port, username='emsadmin',
              password=os.environ['UPF_SSH_PASSWORD'], timeout=15)
    c.save_host_keys(str(keys))
    return c


def run(c, cmd, sudo=False, timeout=600):
    if sudo:
        cmd = 'sudo -S -p "" bash -c ' + shlex.quote(cmd)
    i, o, e = c.exec_command(cmd, timeout=timeout)
    if sudo:
        i.write(os.environ['UPF_SSH_PASSWORD'] + '\n')
        i.flush()
    out = o.read().decode(errors='replace')
    err = e.read().decode(errors='replace')
    return o.channel.recv_exit_status(), out, err


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=2223)
    p.add_argument('--sudo', action='store_true')
    p.add_argument('--cmd')
    p.add_argument('--file')
    p.add_argument('--save')
    p.add_argument('--put', nargs=2)
    p.add_argument('--get', nargs=2)
    a = p.parse_args()
    c = connect(a.port)
    try:
        if a.put or a.get:
            with c.open_sftp() as s:
                if a.put:
                    s.put(*a.put)
                else:
                    s.get(*a.get)
        if a.cmd or a.file:
            result = run(c, a.cmd or Path(a.file).read_text(encoding='utf-8'), a.sudo)
            output = result[1] + result[2]
            if a.save:
                Path(a.save).write_text(output, encoding='utf-8')
            print(output)
            sys.exit(result[0])
    finally:
        c.close()
