"""Bounded local cleanup and forwarding probes; no shell or charging writes."""
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import struct

OWNED = re.compile(r'maestro-policy-traffic-[0-9a-f]{32}\.service\Z')


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=5).stdout


def owned_traffic_cleanup():
    lines = run(['systemctl', 'list-units', '--all', '--plain', '--no-legend', 'maestro-policy-traffic-*.service']).splitlines()
    stopped = []
    for line in lines:
        name = line.split()[0] if line.split() else ''
        if not OWNED.fullmatch(name):
            raise ValueError('unexpected_owned_traffic_unit')
        description = run(['systemctl', 'show', name, '-p', 'Description', '--value']).strip()
        if description != 'MAEstro authority-owned traffic':
            raise ValueError('traffic_ownership_not_confirmed')
        run(['systemctl', 'stop', name])
        state = run(['systemctl', 'show', name, '-p', 'ActiveState', '--value']).strip()
        if state not in {'inactive', 'failed'}:
            raise ValueError('owned_traffic_still_active')
        stopped.append(name)
    return {'stopped_units': stopped, 'owned_traffic_quiescent': True}


XDP_PIN_ROOTS = (Path('/run/maestro-bpf/urllc'), Path('/run/maestro-bpf/c4'),
                 Path('/run/maestro-urllc-xdp'), Path('/sys/fs/bpf/maestro-c4'))
C4_RUNTIME = Path('/run/maestro-c4')


def bpf_tool():
    candidates = sorted(Path('/usr/lib/linux-tools').glob('*/bpftool'))
    return str(candidates[-1]) if candidates else 'bpftool'


def quiesce_policy(program, key):
    """Use the map's spin-locked operation; never rewrite budgets/counters."""
    import tempfile
    request = struct.pack('<II', 0x43345031, 1) + key + struct.pack('<IIII', 1, 0, 0, 0) + bytes(72)
    with tempfile.TemporaryDirectory(prefix='maestro-c4-quiesce-') as directory:
        data = Path(directory)/'request'
        data.write_bytes(request)
        result = json.loads(run([bpf_tool(), '-j', 'prog', 'run', 'pinned', str(program),
                                 'data_in', str(data), 'repeat', '1']))
    if result.get('retval') != 2:
        raise ValueError('c4_policy_quiesce_not_verified')


def xdp_gate_close():
    # Latch first: a cooperating C4 controller must never reopen after recovery.
    if C4_RUNTIME.exists():
        (C4_RUNTIME/'emergency-stop').touch(mode=0o600)
    gates, policies = set(), set()
    for root in XDP_PIN_ROOTS:
        if root.exists():
            gates.update(root.rglob('enabled'))
            policies.update(root.rglob('c4_policy_v1'))
    if len(gates) + len(policies) > 128:
        raise ValueError('xdp_pin_inventory_unbounded')
    closed = []
    for path in sorted(gates):
        run([bpf_tool(), 'map', 'update', 'pinned', str(path), 'key', 'hex', '00', '00', '00', '00',
             'value', 'hex', '00', '00', '00', '00'])
        result = json.loads(run([bpf_tool(), '-j', 'map', 'lookup', 'pinned', str(path),
                                'key', 'hex', '00', '00', '00', '00']))
        value = result.get('value')
        if not isinstance(value, list) or len(value) != 4 or any(int(str(v), 16) != 0 for v in value):
            raise ValueError('xdp_gate_close_not_verified')
        closed.append(str(path))
    generations = 0
    for path in sorted(policies):
        metadata = json.loads(run([bpf_tool(), '-j', 'map', 'show', 'pinned', str(path)]))
        if isinstance(metadata, list): metadata = metadata[0]
        if metadata.get('bytes_key') != 32 or metadata.get('bytes_value') not in (184, 192):
            raise ValueError('c4_policy_layout_unknown')
        program = path.parent.parent/'policy'
        if not program.exists():
            raise ValueError('c4_policy_quiesce_program_missing')
        rows = json.loads(run([bpf_tool(), '-j', 'map', 'dump', 'pinned', str(path)]))
        for row in rows:
            key = bytes(int(v, 16) for v in row['key'])
            if len(key) != 32: raise ValueError('c4_policy_key_invalid')
            quiesce_policy(program, key)
            generations += 1
        # Recheck the actual map; successful invocation alone is insufficient.
        rows = json.loads(run([bpf_tool(), '-j', 'map', 'dump', 'pinned', str(path)]))
        if any(struct.unpack_from('<I', bytes(int(v, 16) for v in row['value']), 180)[0] for row in rows):
            raise ValueError('c4_policy_still_admitted')
    return {'xdp_gate_closed': True, 'map_present': bool(gates or policies),
            'closed_gates': closed, 'quiesced_generations': generations}


def traffic_probe(nf, observation):
    if nf not in {'upf', 'upf2', 'upf3'}:
        raise ValueError('traffic_probe_unknown_nf')
    prefix = ['ip', 'netns', 'exec', 'maestro-urllc'] if nf == 'upf3' else []
    results = []
    for session in observation['sessions']:
        address = ipaddress.IPv4Address(session['ue_ipv4'])
        network = {'upf': '10.45.0.0/16', 'upf2': '10.46.0.0/16', 'upf3': '10.47.0.0/16'}[nf]
        if address not in ipaddress.IPv4Network(network):
            raise ValueError('traffic_probe_address_outside_native_pool')
        # Address comes from the live UPF context, never from an API argument.
        run([*prefix, 'ping', '-n', '-q', '-I', 'ogstun', '-c', '2', '-W', '1', str(address)])
        results.append({key: session[key] for key in ('smf_seid', 'upf_seid', 'ue_ipv4')})
    return {'traffic_verified': bool(results), 'sessions': results,
            'boot_id': observation.get('boot_id'), 'generation': observation.get('generation'),
            'token': observation.get('fencing', {}).get('token'),
            'version': observation.get('fencing', {}).get('version')}


def validate_traffic_probes(checkpoint, probes):
    """Require receipts for every restored PDU in the same NF incarnation."""
    upfs = {'upf', 'upf2', 'upf3'}
    if set(probes) != upfs or not checkpoint['sessions']:
        raise ValueError('native_restored_traffic_coverage_missing')
    for nf in upfs:
        proof = probes[nf]
        incarnation = checkpoint['nfs'][nf]
        if (proof.get('boot_id') != incarnation['boot_id'] or
                str(proof.get('generation')) != str(incarnation['generation']) or
                str(proof.get('token')) != str(checkpoint['fencing_token']) or
                str(proof.get('version')) != str(checkpoint['version'])):
            raise ValueError('native_restored_traffic_generation_changed')
        expected = {(str(s['smf_seid']), str(s['upf_seid']))
                    for s in checkpoint['sessions'] if s['upf'] == nf}
        rows = proof.get('sessions', [])
        received = {(str(s['smf_seid']), str(s['upf_seid'])) for s in rows}
        if received != expected or len(rows) != len(received):
            raise ValueError('native_restored_traffic_session_coverage_changed')
        if expected and proof.get('traffic_verified') is not True:
            raise ValueError('native_restored_traffic_not_verified')
