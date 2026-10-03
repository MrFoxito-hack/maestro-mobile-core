"""Install bounded durable-ledger replay without restarting any NF."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__=='__main__':
    settings=get_settings();core=Lab(settings,settings.ssh_port)
    try:
        stage=core.run(['mktemp','-d','/home/emsadmin/nwdaf-replay-XXXXXX']).strip()
        core.write(stage+'/replay_decisions.py',(ROOT/'nwdaf/tools/replay_decisions.py').read_bytes(),0o644)
        service='''[Unit]
Description=MAEstro NWDAF durable decision replay
After=maestro-nwdaf.service
[Service]
Type=oneshot
User=open5gs
Group=open5gs
UMask=0077
Environment=MAESTRO_NWDAF_AUDIT_FILE=/var/lib/open5gs/nwdaf/audit.jsonl
Environment=MAESTRO_NWDAF_REPLAY_CHECKPOINT=/var/lib/open5gs/nwdaf/replay.json
Environment=MAESTRO_NWDAF_TOKEN_FILE=/home/emsadmin/maestro-charging/nwdaf/nwdaf.token
ExecStart=/usr/bin/python3 /usr/local/lib/maestro-nwdaf/replay_decisions.py
TimeoutStartSec=15
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/open5gs/nwdaf
PrivateTmp=true
'''
        timer='''[Unit]
Description=Retry NWDAF ledger delivery after network or process restart
[Timer]
OnBootSec=20
OnUnitInactiveSec=15
Unit=maestro-nwdaf-audit-replay.service
[Install]
WantedBy=timers.target
'''
        for name,body in [('maestro-nwdaf-audit-replay.service',service),('maestro-nwdaf-audit-replay.timer',timer)]:
            assert core.run(['python3','-c','import pathlib,sys; print(int(pathlib.Path(sys.argv[1]).exists()))','/etc/systemd/system/'+name]).strip()=='0', 'Existing unit must be reviewed before replacement'
            core.write(stage+'/'+name,body,0o644)
            core.run(['install','-m','644',stage+'/'+name,'/etc/systemd/system/'+name],sudo=True)
        core.run(['install','-D','-m','644',stage+'/replay_decisions.py','/usr/local/lib/maestro-nwdaf/replay_decisions.py'],sudo=True)
        core.run(['systemctl','daemon-reload'],sudo=True)
        core.run(['systemctl','enable','--now','maestro-nwdaf-audit-replay.timer'],sudo=True)
        core.run(['systemctl','start','maestro-nwdaf-audit-replay.service'],sudo=True)
        print(core.run(['journalctl','-u','maestro-nwdaf-audit-replay','--no-pager','-n','8'],sudo=True))
    finally:core.client.close()
