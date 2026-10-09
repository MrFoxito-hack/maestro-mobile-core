"""Exercise the real UPF candidate in a disposable, disconnected net namespace.

Synthetic PFCP fixture, not a UE/PDU acceptance test. Requires root inside a
fresh namespace containing only loopback. Never run in the live UPF namespace.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time


def tlv(kind, value):
    return struct.pack('!HH', kind, len(value)) + value


def u32(value):
    return struct.pack('!I', value)


def packet(kind, sequence, payload, seid=None):
    header = bytes([0x20 | (seid is not None), kind])
    rest = (struct.pack('!Q', seid) if seid is not None else b'') + sequence.to_bytes(3, 'big') + b'\0'
    return header + struct.pack('!H', len(rest) + len(payload)) + rest + payload


def fields(message):
    offset = 16 if message[0] & 1 else 8
    assert len(message) == 4 + int.from_bytes(message[2:4], 'big')
    while offset < len(message):
        kind, length = struct.unpack('!HH', message[offset:offset + 4])
        offset += 4
        assert offset + length <= len(message)
        yield kind, message[offset:offset + length]
        offset += length


def run(binary, libraries, directory):
    links = json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))
    if {i['ifname'] for i in links} != {'lo'}:
        raise RuntimeError('requires_fresh_disconnected_test_namespace')
    directory.mkdir(exist_ok=False, mode=0o700)
    state = directory / 'state'; state.mkdir(mode=0o700)
    control_path = str(directory / 'observe.sock')
    config = directory / 'upf.yaml'
    config.write_text(f'''logger:
  file:
    path: {directory}/upf.log
  level: info
global:
  max:
    ue: 16
upf:
  pfcp:
    server:
      - address: 127.0.0.7
  gtpu:
    server:
      - address: 127.0.0.7
  session:
    - subnet: 10.99.0.0/24
      gateway: 10.99.0.1
      dev: c3testtun
''')
    environment = os.environ | {
        'LD_LIBRARY_PATH': str(libraries), 'MAESTRO_POLICY_OBSERVER_SOCKET': control_path,
        'MAESTRO_POLICY_STATE_DIR': str(state),
        'MAESTRO_POLICY_PFCP_ENTERPRISE': '32473',  # RFC 5612, isolated fixture only.
    }
    evidence = {'scope': 'isolated_synthetic_pfcp', 'checks': [], 'accepted': False}
    with (directory / 'process.log').open('wb') as log, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as n4:
        n4.bind(('127.0.0.1', 8805))
        process = subprocess.Popen([str(binary), '-c', str(config)], env=environment, stdout=log, stderr=log)
        sequence = 0

        def control(command):
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                client.settimeout(2); client.bind('\0c3-upf-test-' + secrets.token_hex(8))
                client.sendto(command.encode() + b'\n', control_path)
                reply = json.loads(client.recv(196608))
                assert reply['status'] == 'success', reply
                return reply['data']

        def request(kind, payload, seid=None, expected_cause=1, no_response=False):
            nonlocal sequence
            sequence += 1
            n4.sendto(packet(kind, sequence, payload, seid), ('127.0.0.7', 8805))
            deadline = time.monotonic() + (0.25 if no_response else 2)
            while True:
                n4.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    response, peer = n4.recvfrom(65535)
                except socket.timeout:
                    if no_response:
                        return
                    raise
                offset = 12 if response[0] & 1 else 4
                received_sequence = int.from_bytes(response[offset:offset + 3], 'big')
                if response[1] == 1:
                    n4.sendto(packet(2, received_sequence, tlv(96, u32(12345))), peer)
                    continue
                if response[1] != kind + 1 or received_sequence != sequence:
                    continue
                assert not no_response, 'stale envelope unexpectedly received a response'
                cause = dict(fields(response)).get(19)
                if kind != 1:
                    assert cause == bytes([expected_cause]), response.hex()
                return response

        def qos(mbps):
            bitrate = (mbps * 1000).to_bytes(5, 'big') * 2
            return tlv(14, tlv(109, u32(1)) + tlv(26, bitrate))

        def envelope(token, version, cookie, system=False):
            return tlv(0xff01, struct.pack('!H', 32473) + b'M5GFENCE' + bytes([2 if system else 1]) +
                       struct.pack('!QQ', token, version) + cookie)

        try:
            deadline = time.monotonic() + 6
            while not Path(control_path).exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('isolated_upf_start_failed')
                time.sleep(0.05)
            node = tlv(60, b'\0' + socket.inet_aton('127.0.0.1'))
            request(5, node + tlv(96, u32(12345)))
            description = b'permit out udp from 10.99.0.2 1234 to 172.31.48.2 5201'
            sdf = tlv(23, b'\x01\0' + struct.pack('!H', len(description)) + description)
            pdi = tlv(2, tlv(20, b'\0') + tlv(21, b'\x01' + u32(0x1234) + socket.inet_aton('127.0.0.7')) +
                      tlv(93, b'\x02' + socket.inet_aton('10.99.0.2')) + sdf)
            pdr = tlv(1, tlv(56, b'\0\x01') + tlv(29, u32(100)) + pdi + tlv(95, b'\0') +
                      tlv(108, u32(1)) + tlv(109, u32(1)) + tlv(81, u32(1)))
            far = tlv(3, tlv(108, u32(1)) + tlv(44, b'\0\x02') + tlv(4, tlv(42, b'\x01')))
            qer = tlv(7, tlv(109, u32(1)) + tlv(25, b'\0') + tlv(26, (10000).to_bytes(5, 'big') * 2))
            urr = tlv(6, tlv(81, u32(1)) + tlv(62, b'\x02') + tlv(37, b'\0\0\0') +
                      tlv(73, b'\x01' + struct.pack('!Q', 1000000)))
            request(50, node + tlv(57, b'\x02' + struct.pack('!Q', 42) + socket.inet_aton('127.0.0.1')) +
                    tlv(113, b'\x01') + pdr + far + qer + urr, 0)
            before = control('observe')['sessions'][0]; seid = int(before['upf_seid'])
            evidence['before'] = before
            assert int(before['rules']['qer'][0]['mbr_ul']) == 10000000
            observed_pdr = before['rules']['pdr'][0]
            assert observed_pdr['ue_ipv4'] == '10.99.0.2'
            assert observed_pdr['outer_header_removal_len'] == 1
            assert observed_pdr['outer_header_removal'] == 0
            assert len(observed_pdr['compiled_filters']) == observed_pdr['compiled_filter_count'] == 1
            compiled = observed_pdr['compiled_filters'][0]
            assert compiled['protocol'] == 17
            assert {tuple(compiled[key]) for key in ('src_ports', 'dst_ports')} == {(1234, 1234), (5201, 5201)}
            assert before['rules']['far'][0]['outer_header_flags'] == '0000'
            assert before['rules']['urr'][0]['quota_validity_time'] == 0
            evidence['checks'].append('effective_filter_addresses_ports_header_removal_and_urr_configuration_exported')
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            cookie = secrets.token_bytes(32)
            control(f'fence-v1 1 60000000000 {boot}')
            control(f'prepare-v1 1 0 {seid} {cookie.hex()}')
            request(52, qos(5) + envelope(1, 0, cookie), seid)
            applied = control('observe')['sessions'][0]
            assert int(applied['rules']['qer'][0]['mbr_ul']) == 5000000
            assert applied['rules']['urr'] == before['rules']['urr'] and applied['usage'] == before['usage']
            control('finish-v1 1 0')
            evidence['checks'].append('authorized_qer_applied_with_urr_unchanged')
            request(52, qos(9), seid, expected_cause=64)
            request(52, qos(9) + envelope(1, 0, cookie), seid, no_response=True)
            assert control('observe')['sessions'][0]['rules'] == applied['rules']
            evidence['checks'].append('untagged_and_completed_action_rejected')
            cookie = secrets.token_bytes(32)
            control(f'fence-v1 2 60000000000 {boot}')
            control(f'prepare-v1 2 1 {seid} {cookie.hex()}')
            request(52, qos(10) + envelope(2, 1, cookie), seid)
            control('finish-v1 2 1')
            restored = control('observe')['sessions'][0]
            assert restored['rules'] == before['rules'] and restored['usage'] == before['usage']
            evidence['checks'].append('qer_restored_without_replaying_urr_or_usage')
            request(52, tlv(77, tlv(81, u32(1))), seid)
            evidence['checks'].append('native_urr_query_allowed_during_hold')
            cookie = secrets.token_bytes(32)
            control(f'fence-v1 3 100000000 {boot}')
            control(f'prepare-v1 3 2 {seid} {cookie.hex()}')
            time.sleep(0.15)
            request(52, qos(9) + envelope(3, 2, cookie), seid, no_response=True)
            request(52, qos(9), seid, expected_cause=64)
            assert control('observe')['sessions'][0]['rules'] == before['rules']
            evidence['checks'].append('expired_lease_rejects_tagged_and_untagged_qer')
            cookie = secrets.token_bytes(32)
            control(f'fence-v1 4 60000000000 {boot}')
            control(f'system-prepare-v1 4 2 {cookie.hex()}')
            control('system-open-v1 4')
            request(54, envelope(4, 2, cookie, system=True), seid)
            assert control('observe')['sessions'] == []
            request(50, node + tlv(57, b'\x02' + struct.pack('!Q', 43) + socket.inet_aton('127.0.0.1')) +
                    tlv(113, b'\x01') + pdr + far + qer + urr + envelope(4, 2, cookie, system=True), 0)
            peer_baseline = control('observe')['sessions']
            assert len(peer_baseline) == 1
            control(f'fence-v1 5 60000000000 {boot}')
            control(f'prepare-v1 5 2 {int(peer_baseline[0]["upf_seid"])} {cookie.hex()}')
            request(1, tlv(96, u32(12346)))
            held = control('observe')
            assert held['sessions'] == peer_baseline and held['pending_native'] == 1
            evidence['checks'].append('peer_restart_cannot_delete_policy_under_owner_lease')
            control(f'fence-v1 6 60000000000 {boot}')
            control(f'system-prepare-v1 6 2 {cookie.hex()}')
            request(1, tlv(96, u32(12346)))
            resumed = control('observe')
            assert resumed['sessions'] == [] and resumed['pending_native'] == 0
            evidence['checks'].append('deferred_peer_restoration_resumes_under_system_lease')
            generation = control('observe')['generation']
            process.terminate(); process.wait(timeout=5)
            process = subprocess.Popen([str(binary), '-c', str(config)], env=environment, stdout=log, stderr=log)
            deadline = time.monotonic() + 6
            while not Path(control_path).exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('isolated_upf_restart_failed')
                time.sleep(0.05)
            restarted = control('observe')
            assert restarted['generation'] != generation
            assert restarted['fencing']['token'] == '6' and restarted['fencing']['recovery_required']
            control(f'fence-v1 6 60000000000 {boot}')
            assert control('observe')['fencing']['recovery_required']
            control(f'fence-v1 7 0 {boot}')
            assert not control('observe')['fencing']['recovery_required']
            evidence['checks'].append('process_restart_preserves_floor_and_requires_new_fence')
            evidence['accepted'] = True
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            evidence['process_exit'] = process.returncode
            (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('libraries', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.binary, args.libraries, args.directory), indent=2))
