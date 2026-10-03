"""Validate short, real UPF counter intervals for bounded control experiments.

Capacity comes from the existing operator slice mapping, never the publisher.
This computes an observation; it does not manufacture forecast history.
"""
import math


def observation(body, maps, now):
    if not isinstance(body, dict) or set(body) != {'object_id', 'before', 'after'}:
        raise ValueError('Expected object and two counter readings')
    matches = [m for m in maps if m.object_id == body['object_id'] and m.counter_id == 'nwdaf.slice.dl.bps']
    if len(matches) != 1:
        raise ValueError('No unique approved slice mapping')
    a, b = body['before'], body['after']
    for item in (a, b):
        if not isinstance(item, dict) or set(item) != {'boot_id', 'ifindex', 'uptime', 'tx_bytes', 'timestamp'}:
            raise ValueError('Incomplete counter provenance')
        if not isinstance(item['boot_id'], str) or len(item['boot_id']) != 36:
            raise ValueError('Invalid boot identity')
        if type(item['ifindex']) is not int or item['ifindex'] < 1 or type(item['tx_bytes']) is not int or item['tx_bytes'] < 0:
            raise ValueError('Invalid device counter')
        if any(type(item[k]) not in (float, int) or not math.isfinite(item[k]) for k in ('uptime', 'timestamp')):
            raise ValueError('Invalid sampling clock')
    elapsed = b['uptime'] - a['uptime']
    if a['boot_id'] != b['boot_id'] or a['ifindex'] != b['ifindex'] or not 0.5 <= elapsed <= 10:
        raise ValueError('Discontinuous sampling')
    if not 0 <= now-b['timestamp'] <= 5 or abs((b['timestamp']-a['timestamp'])-elapsed) > 1:
        raise ValueError('Stale or inconsistent wall clock')
    delta = b['tx_bytes']-a['tx_bytes']
    if delta < 0:
        raise ValueError('Counter reset')
    mapping = matches[0]
    bps = delta*8/elapsed
    return mapping, {'status': 'observation_only', 'model': 'upf-counter-delta-v1',
        'capacity_units': mapping.capacity_units, 'observed_bps': bps,
        'observed_percentage_unclipped': bps/mapping.capacity_units*100,
        'sampling_seconds': elapsed, 'points': [],
        'history': [{'timestamp': b['timestamp'], 'value': min(100, bps/mapping.capacity_units*100)}]}
