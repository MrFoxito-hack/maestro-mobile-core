"""Bounded UDP acquisition on the UE. Every attempted packet is retained."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import socket
import struct
import threading
import time


def measure(spec, start, seconds, seed):
    rng = random.Random(seed)
    token = rng.getrandbits(64)
    size, pps = spec['payload_bytes'], spec['pps']
    count = int(seconds * pps)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, spec['interface'].encode() + b'\0')
    sock.bind((spec['source'], 0))
    sock.connect((spec['target'], spec.get('port', 8765)))
    sock.settimeout(.05)
    rows, received, duplicates, foreign = {}, {}, [], []
    stop = threading.Event()

    def receive():
        while not stop.is_set():
            try:
                data = sock.recv(65535)
                stamp = time.monotonic_ns()
                tok, seq, sensor = struct.unpack('!QII', data[:16])
                if tok != token or seq not in rows or len(data) != size:
                    foreign.append(stamp)
                elif seq in received:
                    duplicates.append(seq)
                else:
                    received[seq] = (stamp, sensor)
            except (socket.timeout, ConnectionRefusedError, struct.error):
                pass

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    for seq in range(count):
        planned = start + seq / pps
        remaining = planned - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        sensor = seq % spec.get('sensors', 1) + 1
        packet = struct.pack('!QII', token, seq, sensor).ljust(size, b'.')
        stamp = time.monotonic_ns()
        row = {'sequence': seq, 'sensor_id': sensor, 'sent_ns': stamp,
               'scheduled_ns': round(planned * 1e9), 'send_ok': False}
        rows[seq] = row
        try:
            row['send_ok'] = sock.send(packet) == size
        except OSError as exc:
            row['send_error'] = type(exc).__name__
    finish = start + seconds + 1.0
    while len(received) < sum(r['send_ok'] for r in rows.values()) and time.monotonic() < finish:
        time.sleep(.01)
    stop.set()
    thread.join(.2)
    sock.close()
    for seq, row in rows.items():
        ack = received.get(seq)
        row['ack'] = ack is not None
        row['received_ns'] = ack[0] if ack else None
        row['rtt_ms'] = (ack[0] - row['sent_ns']) / 1e6 if ack else None
        row['sensor_match'] = ack[1] == row['sensor_id'] if ack else None
    return {'spec': spec, 'start_monotonic_ns': round(start * 1e9),
            'duration_s': seconds, 'wall_time_epoch_s': time.time(),
            'duplicates': duplicates, 'foreign_count': len(foreign), 'packets': list(rows.values())}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('configuration', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    config = json.loads(a.configuration.read_text())
    start = time.monotonic() + 1
    with ThreadPoolExecutor(max_workers=len(config['streams'])) as pool:
        futures = [pool.submit(measure, spec, start, config['duration_s'], config['seed'] + i)
                   for i, spec in enumerate(config['streams'])]
        data = {'configuration': config, 'streams': [f.result() for f in futures]}
    a.output.write_text(json.dumps(data, separators=(',', ':')))
    print(json.dumps({'output': str(a.output), 'streams': [
        {'slice': s['spec']['slice'], 'sent': sum(p['send_ok'] for p in s['packets']),
         'received': sum(p['ack'] for p in s['packets'])} for s in data['streams']]}))


if __name__ == '__main__':
    main()
