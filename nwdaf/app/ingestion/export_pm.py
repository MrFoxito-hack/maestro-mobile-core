"""Bounded SQLite PM export; source opened read-only, no synthetic samples."""
from pathlib import Path
from contextlib import closing
import sqlite3
import time


def export_snapshot(source, destination, *, max_rows=100000, window_seconds=21600):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Destination must be a new file, separate from source')
    if not 1 <= max_rows <= 1000000 or not 60 <= window_seconds <= 86400:
        raise ValueError('Invalid export bounds')
    start = time.time()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=5)) as src:
        src.execute('PRAGMA query_only=ON')
        src.set_progress_handler(lambda: int(time.time()-start > 15), 10000)
        # Limit work by indexed rowid before filtering. Export does not claim
        # complete window coverage; gaps must be rejected downstream.
        rows = src.execute('''SELECT * FROM
            (SELECT id,collected_at,bucket_epoch,testbed_id,scenario_id,object_id,
             counter_id,value,unit,source,quality FROM metric_samples
             ORDER BY id DESC LIMIT ?)
            WHERE scenario_id='5g-sa' AND quality IN ('measured','computed')
             AND bucket_epoch>=? AND bucket_epoch<=? ORDER BY id''',
            (max_rows, start-window_seconds, start)).fetchall()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(destination)) as dest:
        dest.execute('''CREATE TABLE metric_samples(id INTEGER PRIMARY KEY,
          collected_at TEXT,bucket_epoch REAL,testbed_id TEXT,scenario_id TEXT,
          object_id TEXT,counter_id TEXT,value REAL,unit TEXT,source TEXT,quality TEXT)''')
        dest.executemany('INSERT INTO metric_samples VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
        dest.execute('CREATE INDEX pm_lookup ON metric_samples(testbed_id,scenario_id,object_id,counter_id,bucket_epoch)')
        dest.execute('CREATE TABLE export_metadata(exported_at REAL,source_last_id INTEGER,row_count INTEGER,scan_limit INTEGER)')
        dest.execute('INSERT INTO export_metadata VALUES(?,?,?,?)',
                     (start, rows[-1][0] if rows else None,len(rows),max_rows))
        dest.commit()
    return {'rows':len(rows), 'exported_at':start, 'latest_sample':max((r[2] for r in rows), default=None)}
