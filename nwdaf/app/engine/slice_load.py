"""Deterministic additive Holt-Winters + ridge ensemble; no train/test leakage.

Prediction bands are empirical rolling-origin residual quantiles, NOT a claim
of guaranteed 95% coverage. Validation coverage must be measured on held-out data.
"""
from dataclasses import dataclass, asdict
import math
import warnings

import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tools.sm_exceptions import ConvergenceWarning


@dataclass(frozen=True)
class ForecastPoint:
    horizon_seconds: int
    value: float
    lower: float
    upper: float
    calibration_samples: int


def _features(t, period, scale):
    t = np.asarray(t, dtype=float)
    return np.column_stack((np.ones_like(t), t / scale,
                            np.sin(2 * np.pi * t / period),
                            np.cos(2 * np.pi * t / period)))


def _predict(y, steps, period, ridge):
    if np.ptp(y) <= 1e-12:
        # Exact stationary solution; fitting an optimizer to zero variance is ill-conditioned.
        return np.full(steps, float(y[-1]))
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model = ExponentialSmoothing(y, trend='add', seasonal='add',
                                    seasonal_periods=period,
                                    initialization_method='estimated').fit(optimized=True)
    if not model.mle_retvals.get('success', True):
        raise ValueError('Holt-Winters optimization did not converge')
    x = _features(np.arange(len(y)), period, len(y))
    penalty = np.diag([0.0, ridge, ridge, ridge])
    beta = np.linalg.solve(x.T @ x + penalty, x.T @ y)
    future = _features(np.arange(len(y), len(y) + steps), period, len(y))
    return 0.5 * np.asarray(model.forecast(steps)) + 0.5 * (future @ beta)


def forecast(values, timestamps, *, period=12, interval_seconds=300,
             horizons=(900, 1800), ridge=0.1, calibration_origins=20):
    y = np.asarray(values, dtype=float)
    ts = np.asarray(timestamps, dtype=float)
    if y.ndim != 1 or ts.shape != y.shape or not np.all(np.isfinite(y)) or not np.all(np.isfinite(ts)):
        raise ValueError('Finite one-dimensional samples and timestamps required')
    if period < 2 or ridge <= 0 or interval_seconds <= 0 or calibration_origins < 20:
        raise ValueError('Invalid model configuration')
    if np.any((y < 0) | (y > 100)):
        raise ValueError('Load must be a measured capacity percentage in [0,100]')
    if not np.allclose(np.diff(ts), interval_seconds, rtol=0, atol=0.001):
        raise ValueError('Regular, unique, ordered samples required; gaps are not zero load')
    if not horizons or any(h <= 0 or h % interval_seconds for h in horizons):
        raise ValueError('Horizons must be positive multiples of the sample interval')
    steps = [h // interval_seconds for h in horizons]
    max_step = max(steps)
    minimum = 2 * period + max_step + calibration_origins - 1
    if not minimum <= len(y) <= 10000:
        raise ValueError(f'Need {minimum}..10000 samples for training and calibration')
    residuals = {step: [] for step in steps}
    for origin in range(len(y) - max_step - calibration_origins + 1, len(y) - max_step + 1):
        predicted = _predict(y[:origin], max_step, period, ridge)
        for step in steps:
            residuals[step].append(abs(float(y[origin + step - 1] - predicted[step - 1])))
    prediction = _predict(y, max_step, period, ridge)
    points = []
    for h, step in zip(horizons, steps):
        errors = sorted(residuals[step])
        # Finite-sample corrected empirical quantile; time dependence precludes
        # distribution-free conformal coverage guarantees.
        rank = min(len(errors), math.ceil((len(errors) + 1) * 0.95))
        radius = errors[rank - 1]
        point = float(np.clip(prediction[step - 1], 0, 100))
        points.append(asdict(ForecastPoint(h, point, max(0.0, point-radius),
                                          min(100.0, point+radius), len(errors))))
    return {'model': 'hw-additive-ridge-v1', 'nominal_coverage': 0.95,
            'coverage_guaranteed': False, 'period_samples': period,
            'interval_seconds': interval_seconds, 'points': points}
