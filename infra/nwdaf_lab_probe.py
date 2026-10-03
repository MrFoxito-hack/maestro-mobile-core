"""Read-only inventory for NWDAF throughput acceptance."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    settings = get_settings()
    for name, port in [('core',settings.ssh_port),('upf',settings.upf_ssh_port),('ue',settings.ue_ssh_port)]:
        host = Lab(settings,port)
        try:
            print(name,host.run(['sh','-c','hostname; command -v iperf3; ip -br addr; systemctl is-active open5gs-upfd ueransim-ue'],check=False))
            if name == 'upf':
                print(host.run(['tc','-s','qdisc','show'],sudo=True))
        finally: host.client.close()
