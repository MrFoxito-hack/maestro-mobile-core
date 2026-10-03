"""Configure user-approved 20 Mbps experimental DL budget per real slice.

This is an analytics reference budget, NOT a measured physical capacity or an
assertion that an aggregate shaper has been installed. No QoS policy is changed.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

if __name__ == '__main__':
    settings=get_settings(); host=Lab(settings,settings.ssh_port)
    target='/home/emsadmin/maestro-charging/nwdaf/service.env'
    try:
        maps = [dict(snssai={'sst':1,'sd':sd},testbed_id='local',object_id='nf:'+nf,
                     counter_id='nwdaf.slice.dl.bps',capacity_units=20000000,
                     interval_seconds=300,period=12,source_max_gap_seconds=90,
                     label=dnn+' DL / approved experimental 20 Mbps budget')
                for sd,nf,dnn in [('000001','upf-01','internet'),('000002','upf-02','corporate')]]
        old=host.read(target)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        host.write(target+'.pre-budget-'+stamp,old)
        lines=[line for line in old.decode().splitlines()
               if not line.startswith(('NWDAF_SLICE_MAPS=','NWDAF_INGESTION_CYCLE='))]
        # systemd EnvironmentFile consumes quotes; single-quote the JSON value.
        lines += ["NWDAF_SLICE_MAPS='"+json.dumps(maps,separators=(',',':'))+"'",
                  'NWDAF_INGESTION_CYCLE=60']
        with host.client.open_sftp() as sftp:
            with sftp.open(target,'w') as out: out.write(('\n'.join(lines)+'\n').encode())
            sftp.chmod(target,0o600)
        host.run(['systemctl','restart','maestro-nwdaf'],sudo=True)
        print('Configured two slice mappings, 20 Mbps DL reference budget each. No live actuation enabled.')
    finally:
        host.client.close()
