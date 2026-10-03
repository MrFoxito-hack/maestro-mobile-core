"""Aggregate observed gauges/rates without filling holes or future buckets."""
from collections import defaultdict
from math import isfinite, floor
from statistics import mean


def regularize(samples, *, interval, now, max_gap):
    """Mean per closed UTC bucket, using a contiguous, coverage-checked suffix.

    max_gap describes the declared source sampling cadence/tolerance, not the
    forecast cadence. Missing coverage ends a segment; it is never zero load.
    Returns bucket end timestamps and source counts for reproducible evidence.
    Cumulative counters must be converted to rates upstream, not averaged here.
    """
    if interval <= 0 or not 0 < max_gap <= interval or not isfinite(now):
        raise ValueError('Invalid aggregation window')
    groups = defaultdict(list)
    previous = None
    for sample in samples:
        t, value = sample['bucket_epoch'], sample['value']
        if not isfinite(t) or not isfinite(value) or value < 0:
            raise ValueError('Invalid source observation')
        if previous is not None and t <= previous:
            raise ValueError('Duplicate or unordered source timestamps')
        previous = t
        end = (floor(t / interval) + 1) * interval
        if end <= now:
            groups[end].append((t, value))
    result = []
    for end, points in sorted(groups.items()):
        covered = (points[0][0] - (end-interval) <= max_gap
                   and end-points[-1][0] <= max_gap
                   and all(b[0]-a[0] <= max_gap for a, b in zip(points, points[1:])))
        if not covered:
            result = []
            continue
        if result and end-result[-1]['bucket_epoch'] != interval:
            result = []
        result.append({'bucket_epoch': end, 'value': mean(v for _, v in points),
                       'source_count': len(points), 'last_observation': points[-1][0]})
    return result
