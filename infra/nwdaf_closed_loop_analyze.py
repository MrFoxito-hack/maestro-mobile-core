"""Decode real N23/N7/N4 evidence; never promote acknowledgements to enforcement."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run');args=parser.parse_args()
    assert re.fullmatch(r'nwdaf-(?:loop|qoe)-[a-f0-9]{10}',args.run)
    local=ROOT/'.work'/args.run;result=json.loads((local/'result.json').read_text())
    remote=result['remote'];assert re.fullmatch('/home/emsadmin/'+args.run+'-[A-Za-z0-9]{6}',remote)
    settings=get_settings();core=Lab(settings,settings.ssh_port)
    try:
        pcap=remote+'/control.pcap'
        assert hashlib.sha256(core.read(pcap)).hexdigest()==result['pcap_sha256']
        for name,filt in {'n23':'tcp.port == 8085','n7-pcf-smf-transport':'tcp.port == 7777','n4-pfcp':'pfcp'}.items():
            path=remote+'/'+name+'.pcap'
            core.run(['tshark','-r',pcap,'-Y',filt,'-F','pcap','-w',path])
            (local/(name+'.pcap')).write_bytes(core.read(path))
        for name,filt in [('pfcp','pfcp.msg_type == 52 || pfcp.msg_type == 53'),('sbi','http2')]:
            command=['tshark','-r',pcap,'-d','tcp.port==7777,http2','-d','tcp.port==8085,http2','-Y',filt]
            (local/(name+'.json')).write_text(core.run(command+['-T','json']),encoding='utf-8')
            (local/(name+'.txt')).write_text(core.run(command+['-V']),encoding='utf-8')
        headers=core.run(['tshark','-r',pcap,'-d','tcp.port==7777,http2','-d','tcp.port==8085,http2','-Y','http2.headers',
              '-T','fields','-e','frame.time_epoch','-e','tcp.stream','-e','http2.streamid',
              '-e','http2.headers.method','-e','http2.headers.path','-e','http2.headers.status'])
        (local/'headers.tsv').write_text(headers,encoding='utf-8')
        print('\n'.join(line for line in headers.splitlines() if '/update' in line),flush=True)
        detail=(local/'pfcp.txt').read_text(encoding='utf-8')
        print('\n'.join(line for line in detail.splitlines() if any(k in line for k in ['QER','MBR','Maximum Bitrate','Maximum Bit Rate','MBR DL','Bitrate'])),flush=True)
    finally:core.client.close()

if __name__=='__main__':main()
