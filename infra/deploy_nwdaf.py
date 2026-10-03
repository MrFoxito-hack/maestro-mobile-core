"""Deploy NWDAF without restarting Open5GS. Uses backend operator SSH config.

Creates a private Python environment and token; never prints credentials.
Existing deployments must be inspected before using this initial installer.
"""
import io
import json
from pathlib import Path
import shlex
import sys
import tarfile
import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings

DEST = '/home/emsadmin/maestro-charging/nwdaf'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    settings = get_settings()
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    if not settings.ssh_strict_host_key:
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(settings.testbed_host, port=settings.ssh_port, username=settings.ssh_user,
                password=settings.ssh_password, look_for_keys=False, allow_agent=False, timeout=10)

    def run(command, sudo=False):
        if sudo: command = 'sudo -S -p "" sh -c ' + shlex.quote(command)
        stdin, stdout, stderr = ssh.exec_command(command, timeout=900)
        if sudo:
            stdin.write(settings.ssh_password + '\n'); stdin.flush()
        for line in stdout: print(line, end='', flush=True)
        error = stderr.read().decode()
        code = stdout.channel.recv_exit_status()
        if error: print(error, file=sys.stderr)
        if code: raise RuntimeError(f'Remote command failed: {code}')

    try:
        if '--resume' in sys.argv:
            run('test -f '+DEST+'/deployment.tar.gz && test ! -f /etc/systemd/system/maestro-nwdaf.service')
        else:
            run('test ! -e ' + DEST + ' && mkdir -p ' + DEST)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as tar:
            for relative in ('app', 'tools', 'tests', 'requirements.txt', 'requirements-research.txt', 'hypercorn.toml'):
                path = ROOT/'nwdaf'/relative
                paths = path.rglob('*') if path.is_dir() else [path]
                for file in paths:
                    if file.is_file() and '__pycache__' not in file.parts:
                        tar.add(file, arcname='nwdaf/'+file.relative_to(ROOT/'nwdaf').as_posix())
            for file in (ROOT/'.work/nwdaf-contract').rglob('*'):
                if file.is_file(): tar.add(file, arcname='.work/nwdaf-contract/'+file.relative_to(ROOT/'.work/nwdaf-contract').as_posix())
        archive.seek(0)
        with ssh.open_sftp() as sftp:
            sftp.putfo(archive, DEST+'/deployment.tar.gz')
        run('tar -xzf '+DEST+'/deployment.tar.gz -C /home/emsadmin/maestro-charging')
        run('python3 -m venv '+DEST+'/.bootstrap && '+DEST+'/.bootstrap/bin/pip install uv==0.11.0')
        run(DEST+'/.bootstrap/bin/uv python install 3.11 && '+DEST+'/.bootstrap/bin/uv venv --python 3.11 '+DEST+'/.venv')
        run(DEST+'/.bootstrap/bin/uv pip install --python '+DEST+'/.venv/bin/python -r '+DEST+'/requirements.txt')
        run(DEST+'/.bootstrap/bin/uv pip install --python '+DEST+'/.venv/bin/python -r '+DEST+'/requirements-research.txt')
        # Secret is generated inside the VM, never transported in logs or arguments.
        secret_program = "import os,secrets; p="+repr(DEST)+"; os.umask(0o077); t=secrets.token_urlsafe(48); open(p+'/nwdaf.token','x').write(t); open(p+'/service.env','x').write('NWDAF_TOKEN='+t+'\\n')"
        run(DEST+'/.venv/bin/python -c '+shlex.quote(secret_program))
        unit = '\n'.join([
            '[Unit]', 'Description=MAEstro NWDAF research analytics', 'After=network.target',
            '[Service]', 'User=emsadmin', 'Group=emsadmin', 'WorkingDirectory='+DEST,
            'EnvironmentFile='+DEST+'/service.env', 'ExecStart='+DEST+'/.venv/bin/hypercorn --bind 127.0.0.1:8085 --workers 1 app.main:app',
            'Restart=on-failure', 'RestartSec=5', 'UMask=0077', 'NoNewPrivileges=true',
            'PrivateTmp=true', 'ProtectSystem=strict', 'ProtectHome=read-only', 'ReadWritePaths='+DEST,
            '[Install]', 'WantedBy=multi-user.target', ''])
        with ssh.open_sftp() as sftp:
            with sftp.open(DEST+'/maestro-nwdaf.service', 'w') as file: file.write(unit)
        run('cd '+DEST+' && .venv/bin/python tools/fetch_contract.py')
        run('install -m 644 '+DEST+'/maestro-nwdaf.service /etc/systemd/system/maestro-nwdaf.service && systemctl daemon-reload && systemctl enable --now maestro-nwdaf', sudo=True)
        run('systemctl is-active maestro-nwdaf; curl --retry 20 --retry-connrefused --retry-delay 1 --fail http://127.0.0.1:8085/health')
    finally:
        ssh.close()


if __name__ == '__main__': main()
