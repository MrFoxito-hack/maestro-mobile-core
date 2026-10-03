"""Standalone Linux guard for ONE fixed, bounded HTTP receiver probe.

No service/policy/route/config mutation. This file is sent as a fixed program,
not generated from user text. State and intent survive controller failure.
"""
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

URL = 'http://10.210.50.1:18090/media/720p/index.m3u8'


def process_group_alive(group):
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z': return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def handle(db, body, clock=time.monotonic, boot=None, group_alive=process_group_alive):
    boot = boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    token = body.get('token', '')
    if not re.fullmatch('[a-f0-9]{32}', token): raise ValueError('invalid_token')
    operation = body.get('operation')
    row = db.execute('SELECT * FROM lease WHERE id=1').fetchone()
    if operation == 'reserve':
        if row and row['token'] != token and row['status'] != 'recovered': raise ValueError('resource_reserved')
        if row and row['token'] == token:
            return {'status': row['status'], 'replay': True}
        db.execute('INSERT OR REPLACE INTO lease VALUES(1,?,?,?,?,?,?,?)', (token, boot, clock() + 30, 'reserved', None, None, None))
        db.commit()
        return {'status': 'reserved'}
    if not row or row['token'] != token: raise ValueError('ownership_lost')
    if operation == 'status':
        return {'status': row['status'], 'result': json.loads(row['result']) if row['result'] else None}
    if operation == 'recover':
        if row['boot'] == boot and row['process_group'] and group_alive(row['process_group']):
            raise ValueError('probe_process_group_still_active')
        db.execute("UPDATE lease SET status='recovered',expires_at=0 WHERE id=1")
        db.commit()
        return {'status': 'recovered', 'recovery_verified': True, 'scope': 'probe_process_group_only'}
    if operation != 'measure': raise ValueError('operation_not_allowed')
    if row['status'] != 'reserved' or row['boot'] != boot or row['expires_at'] <= clock():
        raise ValueError('expired_or_already_attempted')
    interface = body.get('interface', '')
    if not re.fullmatch(r'uesimtun\d+', interface): raise ValueError('invalid_interface')
    address = str(ipaddress.IPv4Address(body.get('address')))
    expected_hash = body.get('expected_sha256', '')
    if not re.fullmatch('[a-f0-9]{64}', expected_hash): raise ValueError('invalid_expected_hash')
    links = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show', 'dev', interface], timeout=2))
    if len(links) != 1 or not any(a.get('local') == address for a in links[0].get('addr_info', [])):
        raise ValueError('session_interface_changed')
    # This group belongs to the enclosing GNU timeout invocation. A crash before
    # child-PID persistence is still recoverable by checking the whole group.
    db.execute("UPDATE lease SET status='intent',process_group=?,intent_at=? WHERE id=1", (os.getpgrp(), clock()))
    db.commit()
    command = ['curl', '--silent', '--fail', '--noproxy', '*', '--interface', interface,
               '--connect-timeout', '2', '--max-time', '6', '--max-filesize', '16384',
               '--limit-rate', '16384', '--output', '-', '--write-out',
               '\nMAESTRO_META %{http_code} %{time_total} %{time_starttransfer} %{size_download} %{local_ip}', URL]
    result = subprocess.run(command, capture_output=True, timeout=8)
    raw, separator, metadata = result.stdout.rpartition(b'\nMAESTRO_META ')
    if result.returncode or not separator or len(raw) > 16384:
        raise ValueError('receiver_probe_failed_or_unknown')
    status, total, first, received, local_ip = metadata.decode().split()
    digest = hashlib.sha256(raw).hexdigest()
    if status != '200' or local_ip != address or digest != expected_hash:
        raise ValueError('receiver_identity_or_payload_mismatch')
    observation = {'source': 'ue_curl_receiver', 'http_status': int(status),
                   'http_total_seconds': float(total), 'http_starttransfer_seconds': float(first),
                   'received_payload_bytes': len(raw), 'payload_sha256': digest,
                   'network_measurements': True, 'qoe_measurement': False}
    if int(float(received)) != len(raw): raise ValueError('byte_count_mismatch')
    db.execute("UPDATE lease SET status='measured',result=? WHERE id=1", (json.dumps(observation),))
    db.commit()
    return {'status': 'measured', 'result': observation}


def main():
    body = json.loads(sys.argv[1])
    root = Path.home() / '.local/state/maestro-laboratory-receiver'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink(): raise ValueError('unsafe_state_path')
    os.umask(0o077)
    with (root / 'guard.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = sqlite3.connect(root / 'guard.sqlite3')
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY,token TEXT,boot TEXT,expires_at REAL,status TEXT,process_group INTEGER,intent_at REAL,result TEXT)')
        try:
            print(json.dumps(handle(db, body)))
        except Exception as error:
            allowed = {'invalid_token', 'resource_reserved', 'ownership_lost', 'probe_process_group_still_active',
                       'operation_not_allowed', 'expired_or_already_attempted', 'invalid_interface',
                       'invalid_expected_hash', 'session_interface_changed', 'receiver_probe_failed_or_unknown',
                       'receiver_identity_or_payload_mismatch', 'byte_count_mismatch'}
            code = str(error) if str(error) in allowed else 'guard_operation_failed'
            print(json.dumps({'status': 'blocked', 'error_code': code}))
        finally:
            db.close()


if __name__ == '__main__':
    main()
