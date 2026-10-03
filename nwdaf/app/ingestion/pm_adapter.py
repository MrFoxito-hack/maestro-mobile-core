"""Read-only PM reader. No implicit mapping of host/interface metrics to slices."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import math


@contextmanager
def readonly(path):
    path = Path(path).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:
        yield db
    finally:
        db.close()


def read_samples(path, *, testbed, object_id, counter_id, start, end, limit=10000):
    if not 1 <= limit <= 10000 or not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError('Invalid sample window')
    with readonly(path) as db:
        rows = db.execute('''SELECT bucket_epoch,value,unit,source,quality FROM metric_samples
            WHERE testbed_id=? AND scenario_id='5g-sa' AND object_id=? AND counter_id=?
            AND bucket_epoch>=? AND bucket_epoch<? ORDER BY bucket_epoch LIMIT ?''',
            (testbed, object_id, counter_id, start, end, limit+1)).fetchall()
    if len(rows) > limit:
        raise ValueError('Window exceeds sample limit; reduce the interval')
    if any(not math.isfinite(r['value']) or r['value'] < 0 for r in rows):
        raise ValueError('Invalid source data')
    if any(r['quality'] not in ('measured', 'computed') for r in rows):
        raise ValueError('Untrusted sample quality; simulated or unavailable samples cannot drive SBI')
    return [dict(r) for r in rows]


def snapshot_status(path, *, now, maximum_age=180):
    """Bounded freshness check for our atomic PM bridge snapshot."""
    with readonly(path) as db:
        metadata = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='export_metadata'").fetchone()
        if metadata is None:
            return {'status': 'direct_source', 'snapshot_age_seconds': None}
        row = db.execute('SELECT exported_at,row_count FROM export_metadata LIMIT 1').fetchone()
    if not row or not math.isfinite(row[0]):
        raise ValueError('Invalid PM export metadata')
    age = now-row[0]
    return {'status': 'fresh' if 0 <= age <= maximum_age else 'stale',
            'snapshot_age_seconds': age, 'snapshot_rows': row[1]}
