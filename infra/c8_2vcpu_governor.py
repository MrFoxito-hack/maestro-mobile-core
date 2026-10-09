"""Record guest cpufreq support and set performance when exposed by the VM."""
import json
from c8_remote import CAMPAIGN_ROOT, LoggedLab, get_settings

CODE = r'''import pathlib,json
rows=[]
for p in pathlib.Path('/sys/devices/system/cpu/cpufreq').glob('policy*'):
 g=p/'scaling_governor'; old=g.read_text().strip()
 available=(p/'scaling_available_governors').read_text().split()
 assert 'performance' in available, available
 g.write_text('performance'); actual=g.read_text().strip(); assert actual=='performance'
 rows.append({'path':str(g),'before':old,'after':actual,'available':available})
print(json.dumps({'policies':rows,'status':'performance_verified' if rows else 'not_exposed_by_hypervisor',
 'cpuinfo':pathlib.Path('/proc/cpuinfo').read_text()}))'''


def apply():
    out=CAMPAIGN_ROOT/'setup/governor';out.mkdir(parents=True,exist_ok=True)
    result={}
    for port in [2223,2226]:
        h=LoggedLab(get_settings(),port,out)
        try:result[str(port)]=json.loads(h.run(['python3','-c',CODE],sudo=True))
        finally:h.client.close()
    (out/'assessment.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({p:r['status'] for p,r in result.items()}),flush=True)
    return result


if __name__=='__main__':apply()
