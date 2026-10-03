"""Transport extracted from the accepted infra QoE campaign; no CLI main import."""
import shlex
import json
import http.client
import paramiko
import uuid
from pathlib import Path

class Lab:
    def __init__(self, settings, port, check_ownership=lambda: None):
        self.check_ownership = check_ownership
        self.settings = settings
        self.guard_owner = None
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        if not settings.ssh_strict_host_key:
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(settings.testbed_host, port=port, username=settings.ssh_user,
                            password=settings.ssh_password, look_for_keys=False,
                            allow_agent=False, timeout=10,
                            key_filename=str(settings.ssh_key_path) if settings.ssh_key_path else None)

    def run(self, args, *, sudo=False, check=True, timeout=30):
        self.check_ownership()
        if self.guard_owner:
            request={**self.guard_owner,'operation':'run','action_id':uuid.uuid4().hex,
                     'argv':[str(x) for x in args],'timeout':timeout,
                     'privileged':sudo,'unix_user':self.settings.ssh_user}
            return self._guard(request, timeout=timeout+5, check=check)
        return self._execute(args, sudo=sudo, check=check, timeout=timeout)

    def _execute(self, args, *, sudo=False, check=True, timeout=30):
        command = shlex.join(["timeout", "--kill-after=2", str(timeout), *[str(x) for x in args]])
        stdin, out, _ = self.client.exec_command(("sudo -S -p '' " if sudo else '') + command, timeout=timeout)
        out.channel.set_combine_stderr(True)
        if sudo and self.settings.ssh_password:
            stdin.write(self.settings.ssh_password + '\n')
        stdin.flush()
        stdin.channel.shutdown_write()
        raw = out.read(2_000_001)
        if len(raw)>2_000_000: raise RuntimeError('Remote output limit exceeded')
        data = raw.decode(errors='replace')
        code = out.channel.recv_exit_status()
        if code and (check or (code==73 and self.guard_owner)):
            # Commands never contain credentials; do not echo potential process output.
            raise RuntimeError(f'Remote command exited with status {code}')
        return data

    def _guard(self, request, *, timeout=15, check=True):
        source=Path(__file__).with_name('remote_execution_guard.py').read_text(encoding='utf-8')
        # Never include the guard request (ownership token) in exception messages.
        try:
            return self._execute(['python3','-c',source,json.dumps(request)],sudo=True,timeout=timeout,check=check)
        except RuntimeError as error:
            # Exit status is safe; the source/request and ownership token are not.
            if str(error).startswith('Remote command exited with status '):
                raise RuntimeError(str(error)) from None
            raise RuntimeError('Guarded remote operation failed; reconcile before retry') from None
        except Exception:
            raise RuntimeError('Guarded remote operation failed; reconcile before retry') from None

    def bind_owner(self, owner, *, recovery=False):
        self.check_ownership()
        self._guard({**owner,'operation':'recover' if recovery else 'acquire'})
        self.guard_owner=owner

    def release_owner(self):
        self.check_ownership()
        if self.guard_owner:
            self._guard({**self.guard_owner,'operation':'release','recovery_verified':True})
            self.guard_owner=None

    def read(self, path):
        with self.client.open_sftp() as sftp, sftp.open(path, 'rb') as file:
            return file.read()

    def write(self, path, data, mode=0o600):
        self.check_ownership()
        if self.guard_owner:
            import base64
            raw=data.encode() if isinstance(data,str) else data
            # Linux limits each argv entry. Journal each bounded chunk; an
            # uncertain chunk is never retried. Offset checks reject duplicates.
            script="""import os,sys,base64
p,payload,mode,offset=sys.argv[1:]; offset=int(offset)
flags=os.O_WRONLY|os.O_NOFOLLOW|(os.O_CREAT|os.O_EXCL if offset==0 else 0)
fd=os.open(p,flags,int(mode))
with os.fdopen(fd,'wb') as f:
 if os.fstat(f.fileno()).st_size!=offset: raise ValueError('unexpected_file_offset')
 f.seek(offset); f.write(base64.b64decode(payload)); f.flush(); os.fsync(f.fileno())
"""
            for offset in range(0,max(1,len(raw)),32768):
                payload=base64.b64encode(raw[offset:offset+32768]).decode()
                self.run(['python3','-c',script,path,payload,str(mode),str(offset)])
            return None
        with self.client.open_sftp() as sftp:
            # All outputs are inside unique, freshly-created evidence directories.
            with sftp.open(path, 'wx') as file:
                file.write(data.encode() if isinstance(data, str) else data)
            sftp.chmod(path, mode)

    def start(self, unit, args, directory, *, properties=()):
        return self.run(['systemd-run', '--unit=' + unit, '--collect',
                         '--property=RuntimeMaxSec=420', '--property=TimeoutStopSec=15',
                         '--property=WorkingDirectory=' + directory,
                         '--property=StandardOutput=append:' + directory + '/' + unit + '.log',
                         '--property=StandardError=inherit', *properties, *args], sudo=True)

def api(core, token, method, path, body=None):
    core.check_ownership()
    if core.guard_owner:
        script="""import urllib.request,json,pathlib,sys
token=pathlib.Path(sys.argv[1]).read_text().strip()
body=json.loads(sys.argv[4])
req=urllib.request.Request('http://127.0.0.1:8085'+sys.argv[3],method=sys.argv[2],
data=json.dumps(body).encode() if body is not None else None,
headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=8) as response: print(response.read().decode())
"""
        raw=core.run(['python3','-c',script,core.settings.nwdaf_token_file,method,path,json.dumps(body)],timeout=12)
        return json.loads(raw) if raw.strip() else None
    connection=http.client.HTTPConnection('127.0.0.1',8085,timeout=8)
    channel=core.client.get_transport().open_channel('direct-tcpip',('127.0.0.1',8085),('127.0.0.1',0),timeout=8)
    channel.settimeout(8);connection.sock=channel
    try:
        connection.request(method,path,body=json.dumps(body) if body is not None else None,
                           headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read()
        if response.status not in (200,201,204): raise RuntimeError('NWDAF API status '+str(response.status))
        return json.loads(raw) if raw else None
    finally:connection.close()

COUNTERS="""import json,pathlib,time
p=pathlib.Path('/sys/class/net/ogstun')
print(json.dumps(dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
ifindex=int((p/'ifindex').read_text()),tx_bytes=int((p/'statistics/tx_bytes').read_text()),
uptime=float(pathlib.Path('/proc/uptime').read_text().split()[0]),timestamp=time.time())))
"""
