"""Read UE/Core association evidence before any session repair."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings
if __name__=='__main__':
    settings=get_settings()
    for name,port in [('core',settings.ssh_port),('ue',settings.ue_ssh_port)]:
        host=Lab(settings,port)
        try:
            if name=='core':
                for service in ['open5gs-amfd','open5gs-smfd']:
                    print(host.run(['journalctl','-u',service,'--no-pager','-n','35'],sudo=True))
                print(host.run(['ip','route','show']))
            else:
                print(host.run(['systemctl','list-units','--all','ueransim*','--no-pager']))
                for supi in ['imsi-999700000000001','imsi-999700000000004']:
                    print(supi,host.run(['/home/emsadmin/UERANSIM/build/nr-cli',supi,'--exec','status']))
                print(host.run(['journalctl','-u','ueransim-ue','--no-pager','-n','25'],sudo=True))
        finally:host.client.close()
