"""Real per-UPF TUN measurements, independent of identically named Core TUNs."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time


def store_observation(path, *, object_id, boot, ifindex, uptime, tx_bytes, timestamp):
    """Return a rate only across continuous observations of the same device."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS observations(object_id TEXT PRIMARY KEY,
                boot TEXT,ifindex INTEGER,uptime REAL,tx_bytes INTEGER);
            CREATE TABLE IF NOT EXISTS samples(object_id TEXT,bucket_epoch REAL,
                value REAL,PRIMARY KEY(object_id,bucket_epoch));''')
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT boot,ifindex,uptime,tx_bytes FROM observations WHERE object_id=?',
                         (object_id,)).fetchone()
        value = None
        if old and old[0] == boot and old[1] == ifindex:
            elapsed, delta = uptime-old[2], tx_bytes-old[3]
            if 0 < elapsed <= 120 and delta >= 0:
                value = delta*8/elapsed
                db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?)', (object_id,timestamp,value))
        db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',
                   (object_id,boot,ifindex,uptime,tx_bytes))
        # Bounded rolling measurement history; this is not the immutable ledger.
        db.execute('DELETE FROM samples WHERE bucket_epoch<?', (timestamp-86400,))
        db.commit()
        return value


def collect(settings, history_path, *, version=1):
    if version == 2:
        return collect_v2(settings, history_path)
    from charging.e2e_native import Lab
    outcomes = {}
    targets = [('upf-01', settings.upf_ssh_port, '10.45.0.1'),
               ('upf-02', settings.upf2_ssh_port, '10.46.0.1')]
    for name, port, expected_ip in targets:
        host = Lab(settings, port)
        try:
            interfaces = json.loads(host.run(['ip','-j','-4','addr','show','dev','ogstun']))
            if not any(a.get('local') == expected_ip for i in interfaces for a in i.get('addr_info', [])):
                raise ValueError('UPF address/interface mapping changed: '+name)
            raw = host.run(['cat','/proc/sys/kernel/random/boot_id',
                            '/sys/class/net/ogstun/ifindex',
                            '/sys/class/net/ogstun/statistics/tx_bytes','/proc/uptime']).splitlines()
            value = store_observation(history_path, object_id='nf:'+name, boot=raw[0],
                ifindex=int(raw[1]), tx_bytes=int(raw[2]), uptime=float(raw[3].split()[0]),
                timestamp=time.time())
            outcomes[name] = {'downlink_bps': value, 'status': 'measured' if value is not None else 'warming_up'}
        finally:
            host.client.close()
    return outcomes


def merge_snapshot(history_path, snapshot, *, version=1):
    """Append real UPF rates to the private exported snapshot, never EMS history."""
    if version == 2:
        return merge_snapshot_v2(history_path, snapshot)
    with closing(sqlite3.connect(history_path)) as source:
        rows = source.execute('SELECT object_id,bucket_epoch,value FROM samples ORDER BY bucket_epoch').fetchall()
    with closing(sqlite3.connect(snapshot)) as db:
        db.executemany('''INSERT INTO metric_samples(collected_at,bucket_epoch,testbed_id,
            scenario_id,object_id,counter_id,value,unit,source,quality)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', [
            (datetime.fromtimestamp(t, timezone.utc).isoformat(),t,'local','5g-sa',obj,
             'nwdaf.slice.dl.bps',value,'bps','UPF ogstun tx_bytes / monotonic interval','computed')
            for obj,t,value in rows])
        db.execute('UPDATE export_metadata SET row_count=(SELECT COUNT(*) FROM metric_samples)')
        db.commit()


def store_observation_v2(path, *, object_id, observed):
    """Versioned UL/DL counters, namespace continuity and no double-counted XDP.

    The v1 history is retained with its original TX-as-DL meaning.
    New counters never reuse that history or synthesize missing samples.
    """
    from app.services.upf_pm import rate
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sample = {k: v for k, v in observed.items() if k != 'prometheus'}
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS observations_v2(object_id TEXT PRIMARY KEY, payload TEXT);
          CREATE TABLE IF NOT EXISTS samples_v2(object_id TEXT,bucket_epoch REAL,
            counter_id TEXT,value REAL,unit TEXT,PRIMARY KEY(object_id,bucket_epoch,counter_id));
        ''')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM observations_v2 WHERE object_id=?', (object_id,)).fetchone()
        previous = json.loads(row[0]) if row else None
        values = rate(previous, sample, max_interval=120)
        if observed.get('xdp_attached') or (previous and previous.get('xdp_attached')):
            values = None
        db.execute('INSERT OR REPLACE INTO observations_v2 VALUES(?,?)', (object_id, json.dumps(sample)))
        if values is not None:
            for key, value in values.items():
                direction, unit = key.split('_')
                db.execute('INSERT OR IGNORE INTO samples_v2 VALUES(?,?,?,?,?)',
                           (object_id, sample['timestamp'], 'nwdaf.upf.'+direction+'.'+unit, value, unit))
        db.execute('DELETE FROM samples_v2 WHERE bucket_epoch<?', (sample['timestamp']-86400,))
        db.commit()
    return values


def collect_v2(settings, history_path):
    from concurrent.futures import ThreadPoolExecutor
    from charging.e2e_native import Lab
    from app.services.upf_inventory import inventory
    from app.services.upf_pm import PROBE

    def one(target):
        host = None
        try:
            host = Lab(settings, getattr(settings, target['ssh_port_setting']))
            args = ['python3', '-c', PROBE, target['interface'], target['gateway'], target['metrics_url']]
            if target['namespace']:
                args = ['ip', 'netns', 'exec', target['namespace'], *args]
            observed = json.loads(host.run(['timeout', '6s', *args], sudo=True, timeout=10))
            values = store_observation_v2(history_path, object_id=target['pm_object_id'], observed=observed)
            return target['id'], {'status': 'partial_xdp' if observed['xdp_attached'] else
                                  'measured' if values is not None else 'warming_up',
                                 'metrics': values, 'object_id': target['pm_object_id']}
        except Exception as exc:
            return target['id'], {'status': 'unavailable', 'error': type(exc).__name__}
        finally:
            if host:
                host.client.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(pool.map(one, inventory()['targets']))


def merge_snapshot_v2(history_path, snapshot):
    with closing(sqlite3.connect(history_path)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='samples_v2'").fetchone()
        rows = db.execute('SELECT object_id,bucket_epoch,counter_id,value,unit FROM samples_v2 '
                          'ORDER BY bucket_epoch').fetchall() if exists else []
    with closing(sqlite3.connect(snapshot)) as db:
        db.executemany('''INSERT INTO metric_samples(collected_at,bucket_epoch,testbed_id,
          scenario_id,object_id,counter_id,value,unit,source,quality) VALUES(?,?,?,?,?,?,?,?,?,?)''',
          [(datetime.fromtimestamp(t,timezone.utc).isoformat(),t,'local','5g-sa',obj,counter,value,unit,
            'triad-v1: UPF TUN RX=UL TX=DL; boot/netns/ifindex checked; no XDP','computed')
           for obj,t,counter,value,unit in rows])
        db.execute('UPDATE export_metadata SET row_count=(SELECT COUNT(*) FROM metric_samples)')
        db.commit()
