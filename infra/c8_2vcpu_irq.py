"""Temporary guest IRQ0 and RPS1 configuration with exact rollback evidence."""
import argparse
import json
from c8_remote import CAMPAIGN_ROOT, LoggedLab, get_settings

READ=r'''import pathlib,json,subprocess
paths=[pathlib.Path('/proc/irq/default_smp_affinity'),*pathlib.Path('/proc/irq').glob('*/smp_affinity_list')]
paths += [p for dev in ['enp0s8','murllc-host'] for p in pathlib.Path('/sys/class/net',dev).glob('queues/rx-*/rps_cpus')]
print(json.dumps({'values':{str(p):p.read_text().strip() for p in paths},
'irqbalance_active':subprocess.run(['systemctl','is-active','irqbalance'],capture_output=True,text=True).stdout.strip()=='active',
'network_irq':{dev:pathlib.Path('/sys/class/net',dev,'device/irq').read_text().strip() for dev in ['enp0s3','enp0s8']},
'effective':{str(p):p.read_text().strip() for p in pathlib.Path('/proc/irq').glob('*/effective_affinity_list')},
'interrupts':pathlib.Path('/proc/interrupts').read_text(),'softirqs':pathlib.Path('/proc/softirqs').read_text()}))'''
APPLY=r'''import json,pathlib,subprocess,sys
old=json.loads(sys.argv[1]);subprocess.run(['systemctl','stop','irqbalance'],check=True);changes=[];errors=[]
for path,before in old['values'].items():
 value='2' if path.endswith('rps_cpus') else ('1' if path.endswith('default_smp_affinity') else '0')
 try:
  pathlib.Path(path).write_text(value);changes.append({'path':path,'before':before,'after':pathlib.Path(path).read_text().strip()})
 except OSError as exc:errors.append({'path':path,'error':str(exc)})
print(json.dumps({'changes':changes,'nonmovable_irq_errors':errors}))'''
RESTORE=r'''import json,pathlib,subprocess,sys
old=json.loads(sys.argv[1]);changes=json.loads(sys.argv[2])
for row in changes:
 p=pathlib.Path(row['path']);p.write_text(old['values'][row['path']]);assert p.read_text().strip()==old['values'][row['path']]
if old['irqbalance_active']:subprocess.run(['systemctl','start','irqbalance'],check=True)
print(json.dumps({'restored':True}))'''


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['apply','restore']);a=p.parse_args()
    path=CAMPAIGN_ROOT/'setup/guest-irq-isolation.json'
    if a.action=='apply':
        assert not path.exists(),'no_silent_reapply'
        record={'status':'applying','hosts':{}}
    else:record=json.loads(path.read_text())
    try:
        for port in ([2223,2225,2226] if a.action=='apply' else [int(p) for p in record['hosts']]):
            h=LoggedLab(get_settings(),port,CAMPAIGN_ROOT/'setup/irq-runtime')
            try:
                if a.action=='apply':
                    row=record['hosts'][str(port)]={'before':json.loads(h.run(['python3','-c',READ],sudo=True))}
                    path.write_text(json.dumps(record,indent=2)+'\n')
                    row['application']=json.loads(h.run(['python3','-c',APPLY,json.dumps(row['before'])],sudo=True))
                    row['after']=json.loads(h.run(['python3','-c',READ],sudo=True))
                    for dev,irq in row['after']['network_irq'].items():
                        assert row['after']['values'][f'/proc/irq/{irq}/smp_affinity_list']=='0',dev
                        assert row['after']['effective'][f'/proc/irq/{irq}/effective_affinity_list']=='0',dev
                    row['network_irq_cpu0_verified']=True
                else:
                    row=record['hosts'][str(port)]
                    current=json.loads(h.run(['python3','-c',READ],sudo=True))
                    changes=[{'path':p} for p,value in row['before']['values'].items() if current['values'][p]!=value]
                    row['restoration']=json.loads(h.run(['python3','-c',RESTORE,json.dumps(row['before']),json.dumps(changes)],sudo=True))
            finally:h.client.close()
        record['status']='applied' if a.action=='apply' else 'restored'
    finally:path.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'guest_irq_isolation':record['status']}),flush=True)


if __name__=='__main__':main()
