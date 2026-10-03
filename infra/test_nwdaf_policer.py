"""Build and execute isolated native arithmetic tests, no UPF deployment."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    settings=get_settings(); host=Lab(settings,settings.ssh_port)
    try:
        work=host.run(['mktemp','-d','/home/emsadmin/nwdaf-policer-test-XXXXXX']).strip()
        for name in ['qer-policer.h','test-qer-policer.c','nwdaf-smf-binding.inc','test-nwdaf-smf-binding.c']:
            host.write(work+'/'+name,(ROOT/'nwdaf/native'/name).read_bytes())
        print(host.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        '-fsanitize=undefined,address',work+'/test-qer-policer.c','-o',work+'/test']))
        print(host.run([work+'/test']))
        print(host.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        '-fsanitize=undefined,address',work+'/test-nwdaf-smf-binding.c','-o',work+'/binding-test']))
        print(host.run([work+'/binding-test']))
        print('Artifacts: '+work)
    finally: host.client.close()
