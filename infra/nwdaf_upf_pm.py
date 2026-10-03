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


def collect(settings, history_path):
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


def merge_snapshot(history_path, snapshot):
    """Append real UPF rates to the private exported snapshot, never EMS history."""
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
