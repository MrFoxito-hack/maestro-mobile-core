"""Operator-only commissioning of an existing secondary UE; no configuration edits.

The CLI requires --execute for systemctl start. It never restarts an active UE,
enables boot startup, releases a session or touches the observed UE service.
"""
import argparse
import json
import re
import time
from pathlib import Path

import yaml

from app.laboratory.live import LabSSH, UE_SCRIPT, session_identity
from app.laboratory.repository import canonical, stamp

OBSERVED = 'imsi-999700000000001'
INSPECT = r'''
import json,pathlib,subprocess,sys,yaml
index=int(sys.argv[1])
if index not in range(2,7): raise ValueError('secondary_only')
unit=f'ueransim-ue-{index:02d}.service'
config=yaml.safe_load(pathlib.Path(f'/home/emsadmin/UERANSIM/config/maestro-ue-{index:02d}.yaml').read_text())
def state(name):
 r=subprocess.run(['systemctl','show',name,'--property=ActiveState,MainPID,ExecMainStartTimestampMonotonic'],capture_output=True,text=True,timeout=3,check=True)
 return dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
print(json.dumps({'unit':unit,'supi':config.get('supi'),
 'sessions':[{'apn':s.get('apn'),'slice':s.get('slice')} for s in config.get('sessions',[])],
 'secondary':state(unit),'observed':state('ueransim-ue.service'),
 'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
'''
START = r'''
import json,subprocess,sys
index=int(sys.argv[1])
if index not in range(2,7): raise ValueError('secondary_only')
subprocess.run(['systemctl','start',f'ueransim-ue-{index:02d}.service'],check=True,timeout=12)
print(json.dumps({'start_acknowledged':True}))
'''


class Commissioner:
    def __init__(self, transport=None, clock=time.monotonic, wait=time.sleep):
        self.transport = transport or LabSSH()
        self.clock, self.wait = clock, wait

    def ensure(self, index, directory: Path, *, execute=False):
        if type(index) is not int or index not in range(2, 7):
            raise ValueError('secondary_only')
        directory.mkdir(parents=True, exist_ok=False)
        report = {'scope': 'secondary_ue_commissioning', 'started_at': stamp(),
                  'execution_status': 'observing', 'validity_status': 'inconclusive',
                  'hypothesis_outcome': 'not_evaluated', 'start_attempted': False}
        def save():
            temp = directory / 'report.tmp'
            temp.write_text(canonical(report), encoding='utf-8')
            temp.replace(directory / 'report.json')
        save()
        try:
            before = self.transport.read('ue', INSPECT, (str(index),))
            report['before'] = before
            supi = f'imsi-9997000000000{index:02d}'
            if before['supi'] != supi or not any(
                    s.get('apn') == 'internet' and s.get('slice') == {'sst': 1, 'sd': 1}
                    for s in before['sessions']):
                raise ValueError('secondary_identity_or_slice_mismatch')
            initial = self.transport.read('ue', UE_SCRIPT, (json.dumps([OBSERVED]),))
            observed = session_identity(initial['subjects'][OBSERVED], initial['interfaces'], 'internet')
            if before['secondary']['ActiveState'] != 'active':
                if not execute:
                    raise ValueError('secondary_inactive_execute_required')
                report['start_attempted'] = True
                report['execution_status'] = 'start_intent'
                save()  # Persist uncertainty BEFORE a systemd mutation; never retry it.
                self.transport.read('ue', START, (str(index),), privileged=True)
            deadline = self.clock() + 20
            while True:
                raw = self.transport.read('ue', UE_SCRIPT, (json.dumps([OBSERVED, supi]),))
                report['session_observation'] = raw
                try:
                    competing = session_identity(raw['subjects'][supi], raw['interfaces'], 'internet')
                    pdu = yaml.safe_load(raw['subjects'][supi])[competing['pdu_session']]
                    if pdu.get('s-nssai') != {'sst': 1, 'sd': 1}:
                        raise RuntimeError('secondary_slice_not_verified')
                    break
                except ValueError:
                    if self.clock() >= deadline:
                        raise ValueError('secondary_pdu_not_active') from None
                    self.wait(.5)
            current = session_identity(raw['subjects'][OBSERVED], raw['interfaces'], 'internet')
            after = self.transport.read('ue', INSPECT, (str(index),))
            report['after'] = after
            if (current != observed or before['observed'] != after['observed']
                    or before['boot_id'] != after['boot_id']):
                raise ValueError('observed_ue_changed_during_commissioning')
            if current['address'] == competing['address'] or current['interface'] == competing['interface']:
                raise ValueError('sessions_not_distinct')
            report.update(execution_status='completed', validity_status='valid',
                          observed=current, competing=competing)
        except Exception as error:
            report.update(execution_status='failed', validity_status='inconclusive',
                          error_code=str(error) if isinstance(error, (ValueError, RuntimeError))
                          and re.fullmatch('[a-z_]+', str(error)) else 'operation_outcome_unknown')
        finally:
            report['finished_at'] = stamp()
            report['limitations'] = ['Active PDU sessions do not prove shared congestion or QoS enforcement.',
                                    'Secondary service stays active for the authorized laboratory; boot enablement is unchanged.']
            save()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, choices=range(2, 7), default=4)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = Commissioner().ensure(args.index, args.output, execute=args.execute)
    print(json.dumps({'path': str(args.output.resolve()), 'status': result['execution_status'],
                      'error_code': result.get('error_code')}))
    return 0 if result['execution_status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
