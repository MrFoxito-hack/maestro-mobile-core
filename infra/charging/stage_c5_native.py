"""Stage and build C5 in a fresh directory; no activation or shared mutation."""
import json
from pathlib import Path
from e2e_native import Lab, ROOT
from lab_command import get_settings


def main():
    core = Lab(get_settings(), 2222)
    out = ROOT / '.work/c5-charging/evidence'
    try:
        directory = core.run(['mktemp','-d','/home/emsadmin/c5-native-XXXXXX']).strip()
        for name in ('build_c5_smf.py','c5_packets.h','c5_packets.c','c5_packets_test.c'):
            core.write(directory+'/'+name, Path(__file__).with_name(name).read_bytes().replace(b'\r\n',b'\n'))
        result = core.run(['python3',directory+'/build_c5_smf.py','/home/emsadmin/maestro-charging/open5gs',
                          '/home/emsadmin/maestro-c3-build-uiEwJT/out',directory+'/out'],check=False,timeout=120)
        (out/'native-build.log').write_text(result,encoding='utf-8')
        state = {'directory':directory}
        try:
            state['manifest'] = json.loads(core.read(directory+'/out/manifest.json'))
            (out/'native-c5.patch').write_bytes(core.read(directory+'/out/c5.patch'))
        except FileNotFoundError:
            print(result[-5000:])
            raise
        (out/'native-build.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
        print(json.dumps(state,indent=2))
    finally:
        core.client.close()


if __name__=='__main__':
    main()
