"""Read live logs and test UE state, no mutation."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
if __name__=='__main__':
    settings=get_settings()
    for name,port,services in [('core',settings.ssh_port,['open5gs-smfd','open5gs-pcfd']),('upf',settings.upf_ssh_port,['open5gs-upfd'])]:
        host=Lab(settings,port)
        try:
            for service in services:
                print(name,host.run(['systemctl','show',service,'--property=ExecStart,MainPID,ActiveState']))
                print(host.run(['journalctl','-u',service,'--since','-8min','--no-pager','-n','90'],sudo=True))
            if name=='core': print(host.run(['tail','-n','50','/var/log/open5gs/pcf.log'],sudo=True))
        finally:host.client.close()
    ue=Lab(settings,settings.ue_ssh_port)
    try:
        print(ue.run(['iperf3','--help']))
        print(ue.run(['journalctl','-u','ueransim-ue','--since','-10min','--no-pager','-n','35'],sudo=True))
        print(ue.run(['ip','-j','-4','addr','show']))
        for supi in ('imsi-999700000000001','imsi-999700000000004','imsi-999700000000005'):
            print(supi,ue.run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'--exec','ps-list']))
    finally:ue.client.close()
