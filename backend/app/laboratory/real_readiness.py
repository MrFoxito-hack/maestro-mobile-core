"""Read-only commissioning report. Never grants authority to mutate the Core."""
import hashlib
import json
from pathlib import Path
import uuid

from app.laboratory.live import LabSSH, LivePreflight
from app.laboratory.repository import canonical, stamp
from app.laboratory.policy_observer import PCF_SNAPSHOT, project_snapshot

PCF_INSPECT = r'''
import socket,json,uuid,pathlib,hashlib,subprocess
s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
s.settimeout(4)
s.bind('\0maestro-lab-inspect-'+uuid.uuid4().hex)
try:
 s.sendto(b'{"operation":"status"}', '/var/lib/open5gs/nwdaf/mml.sock')
 reply=json.loads(s.recv(65536))
 # Explicit projection: never persist arbitrary data from a privileged service.
 result={'status':reply.get('status'),'mode':reply.get('mode'),
         'response_fields':sorted(reply.keys())}
finally: s.close()
pid=subprocess.check_output(['systemctl','show','open5gs-pcfd','--property=MainPID','--value'],timeout=3,text=True).strip()
if not pid.isdigit() or int(pid)<=0: raise ValueError('pcf_not_running')
result['pid']=int(pid)
result['binary_sha256']=hashlib.sha256(pathlib.Path('/proc/'+pid+'/exe').read_bytes()).hexdigest()
result['boot_id']=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
print(json.dumps(result))
'''

# These are implementation requirements, not inferred properties of an active NF.
PENDING = {
    'policy_checkpoint': 'No verified per-session policy/QER snapshot and in-flight N7 state reader is integrated.',
    'remote_mutation_fencing': 'SQLite leases and the receiver guard do not fence PCF, competing traffic or other EMS writers.',
    'independent_policy_recovery': 'No independent Core guard restores the checkpoint after worker or SSH loss.',
    'recovery_predicates': 'No verifier proves removal of owned traffic and restoration of effective session policies.',
    'shared_resource_calibration': 'Two active Internet PDUs do not establish a calibrated shared bottleneck.',
    'player_and_budget': 'The integrated receiver measures a manifest only; player, traffic and capture budgets need integration.',
}


def collect(directory: Path, *, competing_index=4, transport=None):
    if type(competing_index) is not int or competing_index not in range(2, 7):
        raise ValueError('secondary_only')
    transport = transport or LabSSH()
    directory.mkdir(parents=True, exist_ok=False)
    descriptor = {'schema_version': 1, 'kind': 'real_core_readiness',
                  'observed_alias': 'video', 'competing_alias': 'load',
                  'competing_index': competing_index, 'read_only': True,
                  'quota_screen_bytes_per_ue': 1_000_000}
    events = []
    def event(name, detail):
        value = {'timestamp': stamp(), 'event': name, 'detail': detail}
        events.append(value)
        with (directory / 'execution-events.jsonl').open('a', encoding='utf-8') as out:
            out.write(canonical(value) + '\n')
    (directory / 'descriptor.json').write_text(canonical(descriptor), encoding='utf-8')
    event('observation_started', {'network_mutation': False})
    bindings = {'observed': {'alias': 'video', 'supi': 'imsi-999700000000001', 'dnn': 'internet'},
                'competing': {'alias': 'load', 'supi': f'imsi-9997000000000{competing_index:02d}', 'dnn': 'internet'}}
    observer = LivePreflight(transport)
    preflight = observer.collect(bindings, descriptor['quota_screen_bytes_per_ue'])
    event('preflight_observed', {'checks': preflight['checks']})
    try:
        pcf = transport.read('core', PCF_INSPECT, privileged=True)
    except Exception:
        pcf = {'status': 'unavailable', 'mode': None, 'response_fields': []}
    event('pcf_status_observed', pcf)
    try:
        controller = project_snapshot(transport.read('core', PCF_SNAPSHOT, privileged=True), bindings)
    except Exception:
        controller = project_snapshot({}, bindings)
    event('pcf_controller_observed', controller)
    report = {'schema_version': 1, 'source': 'live_ssh', 'scope': 'real_core_readiness',
              'actuator_scope': 'legacy_policy_mutating_pilot',
              'c6_acquisition': {'preset': 'c6-embb-qoe-local-advisor-v1',
                                 'runner': 'infra/c6_qoe_campaign.py',
                                 'policy_mutation': False, 'separate_live_admission_required': True},
              'execution_status': 'completed' if not preflight['errors'] and pcf.get('status') == 'success' else 'failed',
              'validity_status': 'inconclusive', 'hypothesis_outcome': 'not_evaluated',
              'execution_ready': False, 'network_measurements': False, 'metrics': None,
              'preflight': preflight, 'pcf': pcf, 'controller_observation': controller, 'blocked_requirements': PENDING,
              'limitations': ['This report is not a campaign, authorization, player measurement or restoration proof.',
                              'The quota screen is not a total campaign traffic budget.',
                              'PCF response fields describe this status operation, not every possible interface.']}
    (directory / 'report.json').write_text(canonical(report), encoding='utf-8')
    (directory / 'raw-observations.json').write_text(canonical(observer.raw), encoding='utf-8')
    event('campaign_not_enabled', {'blocked_requirements': list(PENDING)})
    files = []
    for name in ('descriptor.json', 'report.json', 'raw-observations.json', 'execution-events.jsonl'):
        data = (directory / name).read_bytes()
        files.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    (directory / 'manifest.json').write_text(canonical({'schema_version': 1, 'files': files}), encoding='utf-8')
    return report


def run_check(competing_index=4):
    directory = Path(__file__).resolve().parents[3] / 'data/laboratory/readiness' / uuid.uuid4().hex
    report = collect(directory, competing_index=competing_index)
    print(json.dumps({'path': str(directory), 'execution_ready': report['execution_ready'],
                      'execution_status': report['execution_status'],
                      'blocked_requirements': list(report['blocked_requirements'])}))
    return 2  # Observation completion must not be confused with permission to run.
