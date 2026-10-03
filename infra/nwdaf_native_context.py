"""Export native integration source and live build identity, read-only."""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    settings = get_settings()
    destination = ROOT/'.work/nwdaf-native-context'
    destination.mkdir(exist_ok=True)
    core = Lab(settings, settings.ssh_port)
    upf = Lab(settings, settings.upf_ssh_port)
    try:
        base = '/home/emsadmin/maestro-charging/open5gs'
        files = ['src/pcf/sbi-path.c','src/pcf/sbi-path.h','src/pcf/nsmf-build.c',
                 'src/pcf/nsmf-build.h','src/pcf/context.c','src/pcf/context.h','src/pcf/init.c',
                 'src/smf/npcf-handler.c','src/smf/npcf-build.c','src/smf/nsmf-handler.c',
                 'src/smf/binding.c','src/smf/pfcp-path.c','src/smf/context.h','src/smf/context.c','src/upf/gtp-path.c',
                 'lib/sbi/conv.c','lib/core/ogs-uuid.h','lib/pfcp/context.h',
                 'lib/sbi/openapi/model/sm_policy_decision.h']
        for name in files:
            target = destination/name
            target.parent.mkdir(parents=True, exist_ok=True)
            try: target.write_bytes(core.read(base+'/'+name))
            except FileNotFoundError: print('Not present: '+name)
        for name,host,service in [('core',core,'open5gs-pcfd'),('upf',upf,'open5gs-upfd')]:
            pid=host.run(['systemctl','show',service,'--property=MainPID','--value']).strip()
            print(name,host.run(['readlink','-f','/proc/'+pid+'/exe'],sudo=True))
            print(name,host.run(['sha256sum','/proc/'+pid+'/exe'],sudo=True))
            print(name,host.run(['systemctl','show',service,'--property=ExecStart,User,Group']))
        print('build-upf',core.run(['sha256sum',base+'/build/src/upf/open5gs-upfd']))
        print('access',core.run(['namei','-l',base+'/build/src/pcf/open5gs-pcfd',settings.nwdaf_token_file],check=False))
        print('ACL tools',core.run(['sh','-c','command -v setfacl; command -v getfacl'],check=False))
        print('UPF policer debug identity',upf.run(['python3','-c',
            "import pathlib,json; d=pathlib.Path('/proc/"+upf.run(['systemctl','show','open5gs-upfd','--property=MainPID','--value']).strip()+"/exe').read_bytes(); print(json.dumps({k:k.encode() in d for k in ['qer-policer.h','maestro_qer_allow','maestro_dl_bucket','upf_quota_allow']}))"],sudo=True))
        print('pcf-notify', core.run(['ls',base+'/src/pcf']))
        print('ue help')
        ue=Lab(settings,settings.ue_ssh_port)
        try:
            print(ue.run(['/home/emsadmin/UERANSIM/build/nr-cli','--dump'],check=False))
            print(ue.run(['/home/emsadmin/UERANSIM/build/nr-cli','imsi-999700000000001','--exec','commands'],check=False))
        finally: ue.client.close()
    finally:
        core.client.close(); upf.client.close()
