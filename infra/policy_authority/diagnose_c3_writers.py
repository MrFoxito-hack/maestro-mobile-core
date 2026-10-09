"""Read seven actual observers independently of checkpoint availability.

Run on Core with --evidence DIRECTORY. Transport configuration is discovered
from the running adapter, or supplied with --transport. Only reads are sent.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import uuid

try:
    from .native_transport import Transport, NFS
except ImportError:
    from native_transport import Transport, NFS

ADAPTER_SOCK = '/run/maestro-policy-native/control.sock'
AUTHORITY_SOCK = '/run/maestro-policy-authority/control.sock'


def stream_request(path, body, timeout=10):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        client.sendall(json.dumps(body, allow_nan=False).encode() + b'\n')
        with client.makefile('rb') as stream:
            raw = stream.readline(65537)
        if not raw.endswith(b'\n') or len(raw) > 65536:
            raise ValueError('invalid_supervisor_frame')
        return json.loads(raw)


def dgram_request(path, body, timeout=8):
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
        client.settimeout(timeout)
        client.bind('\0maestro-c3-diagnostic-' + uuid.uuid4().hex)
        client.sendto(json.dumps(body, allow_nan=False).encode(), str(path))
        raw, _, flags, _ = client.recvmsg(262145)
        if flags & socket.MSG_TRUNC or len(raw) > 262144:
            raise ValueError('adapter_response_truncated')
        return json.loads(raw)


def success(reply):
    if reply.get('status') != 'success' or not isinstance(reply.get('data'), dict):
        raise ValueError(reply.get('error_code', 'invalid_protocol_response'))
    return reply['data']


def transport_path():
    raw = subprocess.check_output(['systemctl', 'show', 'maestro-policy-native',
                                   '-p', 'ExecStart', '--value'], text=True, timeout=5)
    match = re.search(r'argv\[\]=(.*?) ; ignore_errors=', raw)
    if not match:
        raise ValueError('adapter_transport_not_discovered_use_transport_option')
    args = shlex.split(match[1])
    if args.count('--transport') != 1:
        raise ValueError('adapter_transport_argument_missing')
    path = Path(args[args.index('--transport') + 1])
    if not path.is_absolute():
        raise ValueError('adapter_transport_path_not_absolute')
    return path


def diagnose(*, transport=None, adapter_sock=ADAPTER_SOCK, authority_sock=AUTHORITY_SOCK):
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'c3_accepted': False, 'authority_cycle_executed': False,
              'supervisor': {}, 'adapter_capabilities': {}, 'nf_details': {},
              'probe_errors': {}}
    for name, request, path, operation in (
            ('supervisor', stream_request, authority_sock, 'status'),
            ('adapter_capabilities', dgram_request, adapter_sock, 'capabilities'),
            ('checkpoint', dgram_request, adapter_sock, 'checkpoint')):
        try:
            report[name] = success(request(path, {'operation': operation}))
        except Exception as exc:
            report['probe_errors'][name] = str(exc)
    # Schema-2 NF entries contain epochs, not policy_complete or fencing.
    # Query actual observers; missing observations remain unknown, never False.
    try:
        config_path = Path(transport) if transport else transport_path()
        native = Transport(json.loads(config_path.read_text()))
        report['transport_config'] = str(config_path)
        for nf in sorted(NFS):
            try:
                observed, _ = native.request(nf)
                report['nf_details'][nf] = {
                    **{k: observed.get(k) for k in (
                        'writer_fenced', 'policy_complete', 'pending_native', 'fencing',
                        'boot_id', 'generation', 'pid', 'mode', 'deferred_native')},
                    'session_count': len(observed['sessions']),
                    'source': 'native_transport_observe',
                }
            except Exception as exc:
                report['nf_details'][nf] = {'source': 'unavailable', 'error': str(exc)}
                report['probe_errors'][nf] = str(exc)
    except Exception as exc:
        report['probe_errors']['transport'] = str(exc)
    blocking, unknown = {}, []
    for nf in sorted(NFS):
        row = report['nf_details'].get(nf, {})
        missing = [k for k in ('writer_fenced', 'policy_complete') if type(row.get(k)) is not bool]
        rejected = [k for k in ('writer_fenced', 'policy_complete') if row.get(k) is False]
        if missing:
            unknown.append(nf)
        if missing or rejected:
            blocking[nf] = {'missing': missing, 'reported_false': rejected}
    caps = report['adapter_capabilities'].get('capabilities', [])
    supervisor = report['supervisor']
    ready = (not blocking and not report['probe_errors'] and
             'all_writers_fenced' in caps and supervisor.get('admission_available') is True and
             supervisor.get('admission_error') is None and
             report.get('checkpoint', {}).get('complete') is True)
    report['diagnosis'] = {
        'all_writers_fenced_present': 'all_writers_fenced' in caps,
        'admission_available': supervisor.get('admission_available'),
        'admission_error': supervisor.get('admission_error'),
        'blocking_nfs': blocking, 'unknown_nfs': unknown,
        'blocking_nf_count': len(blocking), 'ready_for_cycle': ready,
        'conclusion': 'Ready for a separate public authority test.' if ready else
                      'Admission not demonstrated; inspect native coverage and probe errors.',
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', type=Path)
    parser.add_argument('--adapter-sock', default=ADAPTER_SOCK)
    parser.add_argument('--authority-sock', default=AUTHORITY_SOCK)
    parser.add_argument('--evidence', type=Path, default=Path('.work/c3-closure') /
                        ('diag-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')))
    args = parser.parse_args()
    report = diagnose(transport=args.transport, adapter_sock=args.adapter_sock,
                      authority_sock=args.authority_sock)
    args.evidence.mkdir(parents=True, exist_ok=False)
    output = args.evidence / 'diagnosis.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'diagnosis': report['diagnosis'], 'nfs': report['nf_details'],
                      'probe_errors': report['probe_errors'], 'evidence': str(output)}, indent=2))
    return 0 if report['diagnosis']['ready_for_cycle'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
