"""Causal robust MAD detector. Score is not a probability or 3GPP confidence."""
from math import isfinite
from statistics import median


def detect(history, value, timestamp, *, window_seconds=3600, threshold=3.5,
           minimum_samples=20, scale_floor=1.0):
    if not all(isfinite(v) for v in (value, timestamp, threshold, scale_floor)):
        raise ValueError('Non-finite input')
    if window_seconds <= 0 or threshold <= 0 or scale_floor <= 0 or minimum_samples < 3 or value < 0:
        raise ValueError('Invalid detector configuration')
    points = list(history)
    if any(not isfinite(t) or not isfinite(v) or v < 0 for t, v in points):
        raise ValueError('Invalid baseline sample')
    if any(points[i][0] >= points[i+1][0] for i in range(len(points)-1)):
        raise ValueError('Baseline timestamps must be strictly increasing')
    if any(t >= timestamp for t, _ in points):
        raise ValueError('Baseline cannot contain current or future observations')
    baseline = [v for t, v in points if timestamp-window_seconds <= t < timestamp]
    if len(baseline) < minimum_samples:
        return {'status': 'insufficient_data', 'samples': len(baseline), 'anomaly': None}
    center = median(baseline)
    mad = median(abs(v-center) for v in baseline)
    sigma = max(1.4826 * mad, scale_floor)
    score = (value-center) / sigma
    return {'status': 'evaluated', 'samples': len(baseline), 'median': center,
            'mad': mad, 'scale_floor': scale_floor, 'score': score,
            'threshold': threshold, 'anomaly': abs(score) > threshold}
