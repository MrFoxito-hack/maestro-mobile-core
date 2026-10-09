"""Bounded UDP acquisition on the UE. Every attempted packet is retained."""
import argparse
import ctypes
import os
import select
import json
from pathlib import Path
import random
import socket
import struct
import time


class Timespec(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_long), ('tv_nsec', ctypes.c_long)]


class Itimerspec(ctypes.Structure):
    _fields_ = [('interval', Timespec), ('value', Timespec)]


LIBC = ctypes.CDLL(None, use_errno=True)


def drain_socket(sock):
    drained = 0
    sock.setblocking(False)
    try:
        while True:
            try:
                sock.recv(65535)
                drained += 1
            except BlockingIOError:
                break
    finally:
        sock.settimeout(.05)
    return drained


def measure(spec, start, seconds, seed, sock=None, close=True):
    rng = random.Random(seed)
    token = rng.getrandbits(64)
    size, pps = spec['payload_bytes'], spec['pps']
    count = int(seconds * pps)
    if sock is None:
        sock = open_socket(spec)
    rows, received, duplicates, foreign = {}, {}, [], []
    start_ns = round(start * 1e9)
    period_ns = round(1_000_000_000 / pps)
    prepared = [struct.pack('!QII', token, seq, seq % spec.get('sensors', 1) + 1).ljust(size, b'.') for seq in range(count)]
    receiver_scheduling = {'before': scheduling()}
    timer = LIBC.timerfd_create(1, os.O_CLOEXEC | os.O_NONBLOCK)
    if timer < 0: raise OSError(ctypes.get_errno(), 'timerfd_create')
    interval = Itimerspec(Timespec(*divmod(period_ns, 1_000_000_000)), Timespec(*divmod(start_ns, 1_000_000_000)))
    seq = 0
    expirations = []
    sock.setblocking(False)
    finish = start + seconds + 1.0
    try:
        if LIBC.timerfd_settime(timer, 1, ctypes.byref(interval), None):
            raise OSError(ctypes.get_errno(), 'timerfd_settime')
        while time.monotonic() < finish:
            if seq == count and len(received) == sum(r['send_ok'] for r in rows.values()): break
            readable, _, _ = select.select([sock, timer] if seq < count else [sock], [], [], max(0, finish-time.monotonic()))
            if sock in readable:
                while True:
                    try:
                        data = sock.recv(65535)
                        stamp = time.monotonic_ns()
                    except (BlockingIOError, ConnectionRefusedError): break
                    try: tok, ack_seq, sensor = struct.unpack('!QII', data[:16])
                    except struct.error:
                        foreign.append(stamp); continue
                    if tok != token or ack_seq not in rows or len(data) != size: foreign.append(stamp)
                    elif ack_seq in received: duplicates.append(ack_seq)
                    else: received[ack_seq] = (stamp, sensor)
            if timer in readable and seq < count:
                ticks = struct.unpack('Q', os.read(timer, 8))[0]
                expirations.append({'at_ns':time.monotonic_ns(),'count':ticks})
                for _ in range(min(ticks, count-seq)):
                    row = {'sequence':seq, 'sensor_id':seq % spec.get('sensors', 1)+1,
                           'scheduled_ns':start_ns+seq*period_ns, 'send_ok':False}
                    rows[seq] = row
                    row['sent_ns'] = time.monotonic_ns()
                    try:
                        row['send_ok'] = sock.send(prepared[seq]) == size
                        row['send_return_ns'] = time.monotonic_ns()
                    except OSError as exc: row['send_error'] = type(exc).__name__
                    seq += 1
        assert seq == count, 'sender_did_not_complete_all_attempts'
    finally:
        os.close(timer)
        sock.settimeout(.05)
    receiver_scheduling['after'] = scheduling()
    if close: sock.close()
    for seq, row in rows.items():
        ack = received.get(seq)
        row['ack'] = ack is not None
        row['received_ns'] = ack[0] if ack else None
        row['rtt_ms'] = (ack[0] - row['sent_ns']) / 1e6 if ack else None
        row['sensor_match'] = ack[1] == row['sensor_id'] if ack else None
        row['sender_lateness_ms'] = (row['sent_ns'] - row['scheduled_ns']) / 1e6
    return {'spec': spec, 'start_monotonic_ns': round(start * 1e9),
            'duration_s': seconds, 'wall_time_epoch_s': time.time(),
            'duplicates': duplicates, 'foreign_count': len(foreign), 'packets': list(rows.values()),
            'receiver_scheduling': receiver_scheduling, 'timer_expirations': expirations}



def open_socket(spec):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, spec['interface'].encode() + b'\0')
    sock.bind((spec['source'], 0))
    sock.connect((spec['target'], spec.get('port', 8765)))
    sock.settimeout(.05)
    return sock


def scheduling():
    import os
    return [{'tid':int(t.name),'policy':os.sched_getscheduler(int(t.name)),
             'priority':os.sched_getparam(int(t.name)).sched_priority,
             'affinity':sorted(os.sched_getaffinity(int(t.name)))}
            for t in Path('/proc/self/task').iterdir()]


def main():
    import os
    import sys
    p=argparse.ArgumentParser();p.add_argument('configuration',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();config=json.loads(args.configuration.read_text())
    os.sched_setaffinity(0,{1});os.sched_setscheduler(0,os.SCHED_RR,os.sched_param(10))
    sys.setswitchinterval(.0005)
    assert LIBC.prctl(29, 1, 0, 0, 0) == 0, 'timer_slack_failed'
    spec,=config['streams'];sock=open_socket(spec);endpoint=sock.getsockname()
    initial_drain = drain_socket(sock)
    warmup=measure(spec,time.monotonic()+.25,5,config['seed'],sock,close=False)
    args.output.with_suffix('.warmup.json').write_text(json.dumps(warmup))
    assert len(warmup['packets'])==500 and all(p['ack'] for p in warmup['packets'])
    ready=args.output.with_suffix('.ready')
    temp=ready.with_suffix('.ready.tmp');temp.write_text(json.dumps({'pid':os.getpid(),'socket':endpoint,'scheduling':scheduling()}));temp.replace(ready)
    until=time.monotonic()+30
    while not args.output.with_suffix('.go').exists():
        if time.monotonic()>until: raise TimeoutError('formal_boundary_not_released')
        time.sleep(.02)
    start_sched=scheduling()
    formal_drain = drain_socket(sock)
    stream=measure(spec,time.monotonic()+.25,config['duration_s'],config['seed']+1,sock,close=False)
    assert endpoint==sock.getsockname()
    result={'configuration':config,'streams':[stream],'same_socket_warmup':{'seconds':5,'packets':500,'pid':os.getpid(),'socket':endpoint},'scheduling_before':start_sched,'scheduling_after':scheduling()}
    result['timing'] = {'wait': 'single-thread timerfd CLOCK_MONOTONIC TFD_TIMER_ABSTIME', 'timer_slack_ns': Path('/proc/self/timerslack_ns').read_text().strip(), 'initial_drained': initial_drain, 'formal_drained': formal_drain, 'switch_interval_s': sys.getswitchinterval()}
    sock.close();args.output.write_text(json.dumps(result,separators=(',',':')))
    print(json.dumps({'streams':[{'sent':sum(p['send_ok'] for p in stream['packets']),'received':sum(p['ack'] for p in stream['packets'])}]}))


if __name__=='__main__':main()
