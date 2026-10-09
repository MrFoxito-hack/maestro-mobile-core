"""Native PM counters for the triad, separate from legacy slice histories."""
from datetime import datetime, timezone
import math

from app.services.upf_inventory import inventory

# Keep existing physical NF identities: upf2 is the corporate VM.
OBJECTS = {'internet': 'nf:upf', 'corporate': 'nf:upf2', '5g-plus': 'nf:upf3'}
METRICS = {
    'ul.kbps': ('ul_bps', 1000, 'Kbps', 'Throughput UL'),
    'dl.kbps': ('dl_bps', 1000, 'Kbps', 'Throughput DL'),
    'ul.mbps': ('ul_bps', 1000000, 'Mbps', 'Throughput UL'),
    'dl.mbps': ('dl_bps', 1000000, 'Mbps', 'Throughput DL'),
    'ul.pps': ('ul_pps', 1, 'pps', 'Paquetes UL'),
    'dl.pps': ('dl_pps', 1, 'pps', 'Paquetes DL'),
    'sessions': ('active_sessions', 1, 'sesiones', 'Sesiones PDU activas'),
}


def interface_id(target):
    return 'interface:' + target['pm_object_id'][3:] + ':ogstun'


def objects():
    return [obj for target in inventory()['targets'] for obj in (
        {'id': OBJECTS[target['dnn']], 'label': target['label'], 'type': 'nf', 'group': 'UPF'},
        {'id': interface_id(target), 'label': target['label'] + ' / ogstun',
         'type': 'interface', 'group': 'UPF'},
    )]


def counters():
    return [{'id': 'upf.triad.' + suffix, 'label': label, 'unit': unit,
             'category': 'Rendimiento por Slice', 'kind': 'gauge',
             'source': 'UPF TUN /metrics', 'objects': ['nf', 'interface'],
             'object_ids': [obj['id'] for obj in objects()], 'scenarios': ['5g-sa'],
             'min_granularity_seconds': 5,
             'description': 'Medición independiente por UPF. UL=RX TUN; DL=TX TUN. Sin muestras de tráfico cuando XDP omite TUN.'}
            for suffix, (_, _, unit, label) in METRICS.items()]


def samples(target, item):
    if item['status'] not in ('measured', 'warming_up'):
        return []
    timestamp = item['source_timestamp']
    common = {'collected_at': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
              'bucket_epoch': int(timestamp), 'testbed_id': 'local', 'scenario_id': '5g-sa',
              'source': 'UPF TUN /metrics', 'quality': 'measured'}
    result = []
    for suffix, (key, divisor, unit, _) in METRICS.items():
        value = item['metrics'].get(key)
        if value is None or not math.isfinite(value) or value < 0:
            continue
        if key != 'active_sessions' and not item['traffic_complete']:
            continue
        for object_id in (OBJECTS[target['dnn']], interface_id(target)):
            result.append({**common, 'object_id': object_id, 'counter_id': 'upf.triad.' + suffix,
                           'value': value / divisor, 'unit': unit})
    return result
