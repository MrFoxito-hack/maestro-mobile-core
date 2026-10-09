"""Apply/status/restore OE4 CPU scheduling without restarting network services.

Runtime only; rerun after service restarts. Restore uses SCHED_OTHER priority 0,
the recorded pre-OE4 policy of all these services (setup/oe4/scheduler.json).
"""
import argparse
from datetime import datetime, timezone
import json
from c8_remote import CAMPAIGN_ROOT, LoggedLab, get_settings

UNITS = {2226:['maestro-ue-vehicle','ueransim-ue-05'], 2225:['ueransim-gnb'],
         2223:['open5gs-upfd-urllc','maestro-terminal-echo-mec']}
READ = '''import os,json,pathlib,sys
pid=int(sys.argv[1]);rows=[]
for t in pathlib.Path('/proc',str(pid),'task').iterdir():
 tid=int(t.name);rows.append({'tid':tid,'policy':os.sched_getscheduler(tid),'priority':os.sched_getparam(tid).sched_priority,'affinity':sorted(os.sched_getaffinity(tid))})
print(json.dumps(rows))
'''


def inspect(host, unit):
    pid = int(host.run(['systemctl','show',unit,'-p','MainPID','--value']))
    if pid <= 1: raise ValueError('service_not_running_'+unit)
    return {'unit':unit,'pid':pid,'threads':json.loads(host.run(['python3','-c',READ,str(pid)],sudo=True))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['apply','status','restore'])
    p.add_argument('--label',default='')
    a=p.parse_args()
    out=CAMPAIGN_ROOT/('setup/scheduler' if a.label else 'setup/oe4');out.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=out/f'scheduler-{stamp}-{a.action}.json';record={'action':a.action,'at':stamp,'label':a.label,'hosts':{}}
    try:
        for port,units in UNITS.items():
            host=LoggedLab(get_settings(),port,out)
            try:
                rows=record['hosts'][str(port)]=[]
                for unit in units:
                    before=inspect(host,unit)
                    rows.append({'before':before})
                    if a.action!='status':
                        host.run(['chrt','-r' if a.action=='apply' else '-o','-a','-p',
                                  '10' if a.action=='apply' else '0',str(before['pid'])],sudo=True)
                    after=inspect(host,unit);rows[-1]['after']=after
                    assert after['pid']==before['pid']
                    if a.action!='restore':
                        assert all(t['policy']==2 and t['priority']==10 for t in after['threads'])
            finally:host.client.close()
        record['success']=True
    finally:path.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'evidence':str(path),'success':record.get('success',False)}))


if __name__=='__main__':main()
