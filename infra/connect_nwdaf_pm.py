"""Connect the exported read-only PM source, without inventing a slice capacity."""
from pathlib import Path
import sys
import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import get_settings


def main():
    s = get_settings()
    ssh = paramiko.SSHClient(); ssh.load_system_host_keys()
    if not s.ssh_strict_host_key: ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(s.testbed_host,port=s.ssh_port,username=s.ssh_user,password=s.ssh_password,
                look_for_keys=False,allow_agent=False,timeout=10)
    base = '/home/emsadmin/maestro-charging/nwdaf'
    try:
        with ssh.open_sftp() as f:
            f.stat(base+'/data/pm-source.sqlite3')
            remote = base+'/app/ingestion/scheduler.py'
            with f.open(remote) as file: original = file.read()
            backup = remote+'.pre-pm-bridge'
            with f.open(backup,'wx') as file: file.write(original)
            f.put(str(ROOT/'nwdaf/app/ingestion/scheduler.py'),remote)
            with f.open(base+'/service.env') as file: env = file.read().decode()
            if 'NWDAF_PM_DATABASE=' in env: raise RuntimeError('Source already configured; inspect before replacing')
            with f.open(base+'/service.env','w') as file:
                file.write(env.rstrip()+'\nNWDAF_PM_DATABASE='+base+'/data/pm-source.sqlite3\n')
            f.chmod(base+'/service.env',0o600)
        stdin, stdout, stderr = ssh.exec_command('sudo -S -p "" systemctl restart maestro-nwdaf',timeout=30)
        stdin.write(s.ssh_password+'\n'); stdin.flush()
        status = stdout.channel.recv_exit_status()
        if status: raise RuntimeError('NWDAF restart failed')
        print('PM snapshot connected. Slice capacity remains unconfigured; no automatic actuation.')
    finally: ssh.close()


if __name__ == '__main__': main()
