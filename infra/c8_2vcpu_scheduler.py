"""Pin critical guest service threads to vCPU1 and verify RR priority 10."""
import argparse
from datetime import datetime, timezone
import json
from c8_remote import CAMPAIGN_ROOT, LoggedLab, get_settings
from c8_scheduler import UNITS, inspect

TOPOLOGY = """import os,json,pathlib
print(json.dumps({'cpu_count':os.cpu_count(),'allowed':sorted(os.sched_getaffinity(0)),
'online':pathlib.Path('/sys/devices/system/cpu/online').read_text().strip(),
'interrupts':pathlib.Path('/proc/interrupts').read_text(),
'softirqs':pathlib.Path('/proc/softirqs').read_text()}))
"""


def verify(action, label):
    out = CAMPAIGN_ROOT / 'setup/scheduler'
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    record = {'label':label,'action':action,'at':stamp,'hosts':{},'topology':{}}
    try:
        for port, units in UNITS.items():
            host = LoggedLab(get_settings(), port, out)
            try:
                topology = json.loads(host.run(['python3','-c',TOPOLOGY],sudo=True))
                record['topology'][str(port)] = topology
                assert topology['cpu_count'] == 2 and topology['allowed'] == [0,1]
                rows = record['hosts'][str(port)] = []
                for unit in units:
                    before = inspect(host, unit)
                    rows.append({'before':before})
                    if action == 'apply':
                        host.run(['taskset','-a','-p','-c','1',str(before['pid'])],sudo=True)
                        host.run(['chrt','-r','-a','-p','10',str(before['pid'])],sudo=True)
                    after = inspect(host, unit)
                    rows[-1]['after'] = after
                    assert before['pid'] == after['pid']
                    assert all(t['affinity']==[1] and t['policy']==2 and t['priority']==10
                               for t in after['threads'])
            finally:
                host.client.close()
        record['success'] = True
    finally:
        (out / (label+'-'+stamp+'.json')).write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'label':label,'verified':True}),flush=True)
    return record


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['apply','status'])
    p.add_argument('--label',required=True);a=p.parse_args();verify(a.action,a.label)
