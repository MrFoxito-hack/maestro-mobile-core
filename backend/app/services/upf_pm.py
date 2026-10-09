"""Independent, bounded UPF sampling. TUN traffic is not complete with active XDP."""
import asyncio
from collections import deque
import json
import math
import shlex
import time

from app.core.config import get_settings
from app.services.execution import RemoteExecutionAdapter
from app.services.upf_inventory import inventory

# Runs inside the target namespace. Only stdlib is required on UPF VMs.
PROBE = r'''
import json,os,pathlib,subprocess,sys,time,urllib.request
dev,expected,url=sys.argv[1:]
links=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','dev',dev]))
if not any(a.get('local')==expected for i in links for a in i.get('addr_info',[])):
 raise RuntimeError('UPF interface identity mismatch')
p=pathlib.Path('/sys/class/net')/dev
result={'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'netns_inode':os.stat('/proc/self/ns/net').st_ino,'ifindex':int((p/'ifindex').read_text()),
 'uptime':float(pathlib.Path('/proc/uptime').read_text().split()[0]),'timestamp':time.time(),
 'rx_bytes':int((p/'statistics/rx_bytes').read_text()),
 'tx_bytes':int((p/'statistics/tx_bytes').read_text()),
 'rx_packets':int((p/'statistics/rx_packets').read_text()),
 'tx_packets':int((p/'statistics/tx_packets').read_text())}
try:
 with urllib.request.urlopen(url,timeout=2) as response:
  result['prometheus']=response.read(262145).decode()
 if len(result['prometheus'])>262144:raise ValueError('Exporter too large')
except Exception:
 result['prometheus']=None
# Any attached XDP makes TUN accounting potentially incomplete. We do not assume
# that absence of this experiment's pinned map implies absence of another program.
all_links=json.loads(subprocess.check_output(['ip','-j','-details','link','show']))
result['xdp_attached']=any(bool(i.get('xdp',{}).get('attached') or i.get('xdp',{}).get('prog')) for i in all_links)
print(json.dumps(result))
'''


def rate(before, after, *, max_interval=30):
    """At the UPF, decapsulated UL is written to TUN (RX); DL is read (TX).

    This is the reverse of a UE application's TUN perspective. See Open5GS
    src/upf/gtp-path.c and Linux drivers/net/tun.c (tun_get_user/tun_put_user).
    """
    identity = ('boot_id', 'netns_inode', 'ifindex')
    counters = ('rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets')
    for key in ('uptime', 'timestamp', *counters):
        if type(after.get(key)) not in (int, float) or not math.isfinite(after[key]) or after[key] < 0:
            raise ValueError('Invalid UPF observation')
    if not before or any(before[k] != after[k] for k in identity):
        return None
    elapsed = after['uptime'] - before['uptime']
    if not 0 < elapsed <= max_interval or abs(after['timestamp'] - before['timestamp'] - elapsed) > 2:
        return None
    if any(after[k] < before[k] for k in counters):
        return None
    return {
        'ul_bps': (after['rx_bytes'] - before['rx_bytes']) * 8 / elapsed,
        'dl_bps': (after['tx_bytes'] - before['tx_bytes']) * 8 / elapsed,
        'ul_pps': (after['rx_packets'] - before['rx_packets']) / elapsed,
        'dl_pps': (after['tx_packets'] - before['tx_packets']) / elapsed,
    }


def gauges(raw):
    names = {'fivegs_upffunction_upf_sessionnbr': 'active_sessions',
             'pfcp_peers_active': 'pfcp_peers'}
    values = {}
    for line in (raw or '').splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in names:
            value = float(parts[1])
            if math.isfinite(value) and value >= 0:
                values[names[parts[0]]] = value
    return values


async def read_target(target):
    remote = RemoteExecutionAdapter({target['unit']})
    args = ['python3', '-c', PROBE, target['interface'], target['gateway'], target['metrics_url']]
    if target['namespace']:
        args = ['ip', 'netns', 'exec', target['namespace'], *args]
    command = remote._sudo_cmd(shlex.join(['timeout', '6s', *args]))
    raw = await asyncio.to_thread(remote._execute_sync, command,
                                  port=getattr(remote.settings, target['ssh_port_setting']), retries=1)
    return json.loads(raw)


class UpfCollector:
    def __init__(self):
        self.previous = {}
        self.latest = {}
        self.history = {}
        self.task = None
        self.lock = asyncio.Lock()

    async def start(self):
        if get_settings().execution_mode == 'remote' and self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.previous.clear()

    async def _loop(self):
        while True:
            await self.collect()
            # Persist independently of slower Core probes, using the standard PM store.
            from app.services.performance import performance_repository
            from app.services.upf_performance import samples
            rows = [row for target in inventory()['targets']
                    for row in samples(target, self.latest[target['id']])]
            try:
                await asyncio.to_thread(performance_repository.insert_samples, rows)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('UPF PM persistence failed')
            await asyncio.sleep(5)

    async def collect(self):
        async with self.lock:
            await asyncio.gather(*(self._one(t) for t in inventory()['targets']))

    async def _one(self, target):
        key = target['id']
        try:
            observed = await asyncio.wait_for(read_target(target), timeout=12)
            rates = rate(self.previous.get(key), observed)
            self.previous[key] = observed
            metrics = {**(rates or {}), **gauges(observed.get('prometheus'))}
            complete = not observed['xdp_attached']
            item = {'status': 'measured' if rates else 'warming_up',
                    'collected_at': time.time(), 'source_timestamp': observed['timestamp'],
                    'metrics': metrics, 'traffic_complete': complete,
                    'xdp': 'attached_unverified' if observed['xdp_attached'] else
                           'pending_integration' if target['xdp_expected'] else 'not_applicable',
                    'exporter_available': observed.get('prometheus') is not None,
                    'pfcp_associated': metrics.get('pfcp_peers', 0) > 0,
                    'measurement_point': 'tun-inner-ip', 'error': None}
        except Exception:
            # A failed target cannot abort its peers or create a delta across a gap.
            self.previous.pop(key, None)
            item = {'status': 'unavailable', 'collected_at': time.time(), 'metrics': {},
                    'traffic_complete': False, 'xdp': 'unknown', 'exporter_available': False,
                    'measurement_point': 'tun-inner-ip', 'error': 'No se pudo verificar este UPF'}
        self.latest[key] = item
        points = self.history.setdefault(key, deque(maxlen=180))
        points.append({'timestamp': item['collected_at'], **item['metrics']})

    def snapshot(self):
        now = time.time()
        document = inventory()
        targets = []
        for target in document['targets']:
            sample = dict(self.latest.get(target['id'], {'status': 'unavailable', 'metrics': {},
                          'traffic_complete': False, 'xdp': 'unknown', 'collected_at': None}))
            age = now - sample['collected_at'] if sample['collected_at'] else None
            if age is not None and (age < 0 or age > 20):
                sample.update(status='stale', metrics={}, traffic_complete=False)
            targets.append({**{k: target[k] for k in ('id','service','label','dnn','sst','sd','pm_object_id')},
                            **sample, 'sample_age_seconds': age,
                            'history': list(self.history.get(target['id'], []))})
        return {'inventory_version': document['version'], 'timestamp': now,
                'slice_configuration': 'deployed' if get_settings().multi_upf_enabled else 'planned',
                'targets': targets}


upf_collector = UpfCollector()
