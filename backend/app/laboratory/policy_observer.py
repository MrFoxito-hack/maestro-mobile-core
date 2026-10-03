"""Conservative projection of the native, read-only PCF controller observation.

Controller flags and ACK history are not effective SMF/UPF policy. This reader
cannot admit a campaign or certify restoration, even with an empty pending list.
"""
import math

from app.laboratory.repository import stamp

PCF_SNAPSHOT = r'''
import socket,json,uuid,pathlib
s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);s.settimeout(4)
s.bind('\0maestro-lab-snapshot-'+uuid.uuid4().hex)
try:
 s.sendto(b'{"operation":"laboratory_snapshot"}', '/var/lib/open5gs/nwdaf/mml.sock')
 raw=s.recv(60001)
 if len(raw)>60000: raise ValueError('snapshot_too_large')
 reply=json.loads(raw)
 reply['boot_id']=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 print(json.dumps(reply))
finally:s.close()
'''


def project_snapshot(raw, bindings):
    result = {'source': 'live_ssh', 'observed_at': stamp(), 'scope': 'pcf_nwdaf_controller_only',
              'execution_ready': False, 'effective_policy_verified': False,
              'recovery_verified': False, 'sessions': [], 'reasons': []}
    required = ('sessions', 'pid', 'boot_id', 'monotonic_us', 'mode',
                'internet_session_count', 'controller_pending_count', 'truncated')
    if (raw.get('status') != 'success' or raw.get('schema_version') != 1
            or raw.get('scope') != result['scope'] or any(k not in raw for k in required)):
        return {**result, 'status': 'unavailable', 'reasons': ['native_snapshot_not_supported']}
    sessions = raw['sessions']
    if (not isinstance(sessions, list) or len(sessions) > 64 or type(raw['pid']) is not int
            or raw['pid'] <= 0 or not raw['boot_id'] or raw['mode'] not in ('MANUAL', 'AUTONOMOUS')
            or type(raw['internet_session_count']) is not int or type(raw['controller_pending_count']) is not int
            or not 0 <= raw['controller_pending_count'] <= raw['internet_session_count']
            or type(raw['monotonic_us']) not in (int, float) or not math.isfinite(raw['monotonic_us'])
            or raw['monotonic_us'] < 0 or type(raw['truncated']) is not bool):
        return {**result, 'status': 'unavailable', 'reasons': ['invalid_native_snapshot']}
    if raw['truncated'] or raw['internet_session_count'] != len(sessions):
        result['reasons'].append('incomplete_native_snapshot')
    required_session = ('supi', 'pcf_session_id', 'dnn', 'sst', 'sd', 'nwdaf_or_mml_pending',
                        'controller_throttled_flag', 'controller_changed_monotonic_us')
    if any(not isinstance(s, dict) or any(k not in s for k in required_session) for s in sessions):
        return {**result, 'status': 'unavailable', 'reasons': ['invalid_native_session']}
    if any(type(s['nwdaf_or_mml_pending']) is not bool or type(s['controller_throttled_flag']) is not bool
           or type(s['pcf_session_id']) is not int or s['pcf_session_id'] <= 0 for s in sessions):
        return {**result, 'status': 'unavailable', 'reasons': ['invalid_native_session']}
    if not raw['truncated'] and sum(s['nwdaf_or_mml_pending'] for s in sessions) != raw['controller_pending_count']:
        return {**result, 'status': 'unavailable', 'reasons': ['inconsistent_pending_count']}
    result.update(status='observed', mode=raw['mode'],
                  epoch={'boot_id': raw['boot_id'], 'pid': raw['pid']},
                  monotonic_us=raw['monotonic_us'],
                  controller_pending_count=raw['controller_pending_count'])
    if raw['controller_pending_count']:
        result['reasons'].append('controller_transaction_pending')
    for role, binding in bindings.items():
        matches = [s for s in sessions if s['supi'] == binding['supi'] and s['dnn'] == binding['dnn']]
        item = {'role': role, 'subject_alias': binding['alias'], 'status': 'unavailable'}
        if len(matches) == 1:
            match = matches[0]
            item.update(status='observed', pcf_session_id=match['pcf_session_id'],
                        sst=match['sst'], sd=match['sd'], controller_pending=match['nwdaf_or_mml_pending'],
                        controller_throttled_flag=match['controller_throttled_flag'])
        else:
            result['reasons'].append(role + '_pcf_session_not_unique')
        result['sessions'].append(item)
    result['reasons'].extend(['effective_smf_upf_policy_not_observed', 'other_policy_writers_not_observed'])
    return result
