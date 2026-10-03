"""Periodic ingestion scheduler: reads PM counters from ems.db and publishes
slice-level analytics at configured intervals.

Does NOT fabricate slice capacity from generic host counters. The capacity
mapping must be provided by the operator (see README §Ingestion).
"""
import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.pm_adapter import read_samples, snapshot_status
from app.ingestion.buckets import regularize
from app.engine.slice_load import forecast
from app.engine.abnormal_behaviour import detect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SliceCapacityMap:
    """Maps a PM counter to a network-slice capacity percentage.

    Example: if the counter measures 'active PDU sessions on SMF' and the
    operator defines slice capacity as 200 sessions, then capacity_units=200
    and a raw value of 170 → load_percentage = 85%.
    """
    snssai: dict            # {"sst": 1} or {"sst": 1, "sd": "000001"}
    testbed_id: str
    object_id: str          # e.g. "nf:smf:smf-01"
    counter_id: str         # e.g. "5g.smf.sessions.active"
    capacity_units: float   # operator-declared maximum for this slice
    interval_seconds: int   # 300 or 1800
    period: int             # seasonal period in sample counts (e.g. 12)
    label: str              # human-readable description
    source_max_gap_seconds: int | None = None

    def __post_init__(self):
        from app.models import Snssai
        Snssai(**self.snssai)
        if (not math.isfinite(self.capacity_units) or self.capacity_units <= 0
                or self.interval_seconds not in (300, 1800) or self.period < 2):
            raise ValueError('Invalid explicit slice capacity or sampling configuration')
        if self.source_max_gap_seconds is not None and not 0 < self.source_max_gap_seconds <= self.interval_seconds:
            raise ValueError('Invalid source cadence tolerance')


@dataclass(frozen=True)
class AnomalyWatch:
    """Maps a PM counter to an anomaly detection target."""
    testbed_id: str
    object_id: str
    counter_id: str
    supi: str | None        # None for infrastructure-level anomaly
    metric: str             # 'service_access_rate' or 'data_rate'
    interval_seconds: int
    label: str

    def __post_init__(self):
        if not 1 <= self.interval_seconds <= 180:
            raise ValueError('A 60-minute MAD baseline needs at least 20 samples; interval must be <=180s')
        if self.metric not in ('service_access_rate', 'data_rate'):
            raise ValueError('Anomaly input must be a rate, not a cumulative counter')


# Examples only; never enabled automatically. Capacity must be declared and
# linked to an experimental configuration before publishing live SBI reports.
DEFAULT_SLICE_MAPS = [
    SliceCapacityMap(
        snssai={"sst": 1},
        testbed_id="testbed-01",
        object_id="nf:smf:smf-01",
        counter_id="5g.smf.sessions.active",
        capacity_units=50,       # educational testbed: 50 concurrent PDU sessions
        interval_seconds=300,
        period=12,
        label="eMBB Slice (SST=1) PDU session load",
    ),
]

DEFAULT_ANOMALY_WATCHES = [
    AnomalyWatch(
        testbed_id="testbed-01",
        object_id="nf:amf:amf-01",
        counter_id="5g.registration.attempt.total",
        supi=None,
        metric="service_access_rate",
        interval_seconds=60,
        label="AMF registration attempt rate",
    ),
]


class IngestionScheduler:
    """Background task that periodically reads PM data and publishes analytics.

    Lifecycle: created in app lifespan, started as asyncio task, cancelled on shutdown.
    """

    def __init__(self, database, contract, pm_path: str | Path,
                 slice_maps=None, anomaly_watches=None,
                 cycle_seconds: int = 300):
        self.db = database
        self.contract = contract
        self.pm_path = Path(pm_path) if pm_path else None
        # Example capacities are not operator authorization or measured limits.
        self.slice_maps = list(slice_maps) if slice_maps is not None else []
        self.anomaly_watches = list(anomaly_watches) if anomaly_watches is not None else []
        self.cycle_seconds = max(60, cycle_seconds)
        self._last_status: dict = {}
        self._outcomes: dict = {}

    @property
    def status(self) -> dict:
        return dict(self._last_status)

    async def run(self):
        """Main loop: one ingestion cycle per interval."""
        while True:
            try:
                if self.pm_path and self.pm_path.exists():
                    freshness = await asyncio.to_thread(snapshot_status, self.pm_path, now=time.time())
                    self._outcomes = {}
                    if freshness['status'] != 'stale':
                        await self._ingest_slices()
                        await self._ingest_anomalies()
                    configured = bool(self.slice_maps or self.anomaly_watches)
                    status = 'source_connected_unmapped'
                    if freshness['status'] == 'stale':
                        status = 'stale_pm_source'
                    elif configured:
                        status = 'ok' if self._outcomes and all(v in ('published', 'evaluated') for v in self._outcomes.values()) else 'insufficient_or_invalid_data'
                    self._last_status = {
                        "last_run": time.time(),
                        "pm_path": str(self.pm_path),
                        "slices_configured": len(self.slice_maps),
                        "anomalies_configured": len(self.anomaly_watches),
                        "status": status,
                        "source": freshness,
                        "outcomes": dict(self._outcomes),
                    }
                else:
                    self._last_status = {
                        "last_run": time.time(),
                        "status": "no_pm_source",
                        "detail": f"PM database not found: {self.pm_path}",
                    }
            except Exception as exc:
                log.warning("Ingestion cycle failed: %s", exc, exc_info=True)
                self._last_status = {
                    "last_run": time.time(),
                    "status": "error",
                    "detail": str(exc)[:200],
                }
            await asyncio.sleep(self.cycle_seconds)

    async def _ingest_slices(self):
        now = time.time()
        for smap in self.slice_maps:
            key = 'slice:'+smap.label
            self._outcomes[key] = 'insufficient_data'
            try:
                # Read enough samples for forecast: 2*period + max_horizon + calibration
                min_samples = 2 * smap.period + 6 + 20  # conservative minimum
                window_seconds = min_samples * smap.interval_seconds + smap.interval_seconds
                samples = await asyncio.to_thread(read_samples,
                    self.pm_path,
                    testbed=smap.testbed_id,
                    object_id=smap.object_id,
                    counter_id=smap.counter_id,
                    start=now - window_seconds,
                    end=now,
                )
                raw_samples = samples
                if not raw_samples:
                    continue
                if smap.source_max_gap_seconds is not None:
                    samples = regularize(samples, interval=smap.interval_seconds,
                                         now=now, max_gap=smap.source_max_gap_seconds)

                # Convert raw counter values to load percentage [0,100]
                values = [min(100.0, max(0.0, s['value'] / smap.capacity_units * 100))
                          for s in samples]
                timestamps = [s['bucket_epoch'] for s in samples]

                horizons = (900, 1800) if smap.interval_seconds == 300 else (1800,)
                result = {'status': 'insufficient_history', 'model': 'observation-only',
                          'points': [], 'samples': len(samples), 'minimum_samples': min_samples}
                if len(samples) >= min_samples:
                    try:
                        result = await asyncio.to_thread(forecast,
                            values, timestamps, interval_seconds=smap.interval_seconds,
                            period=smap.period, horizons=horizons)
                    except ValueError as exc:
                        result = {'status': 'forecast_unavailable', 'model': 'observation-only',
                                  'points': [], 'detail': str(exc)[:200]}
                # Current observed load remains available during model warm-up;
                # never label it as a forecast or manufacture a training history.
                observed = raw_samples[-1]
                observed_percentage = observed['value'] / smap.capacity_units * 100
                result['observed_percentage_unclipped'] = observed_percentage
                result['capacity_units'] = smap.capacity_units
                result['history'] = [{'timestamp': s['bucket_epoch'],
                                      'value': min(100.0, max(0.0, s['value']/smap.capacity_units*100))}
                                     for s in (samples if result['points'] else raw_samples)]
                # Build SBI payload
                from app.models import Snssai, SliceLoad
                from datetime import datetime, timezone
                snssai_obj = Snssai(**smap.snssai)
                load = SliceLoad(
                    loadLevelInformation=round(min(100.0, max(0.0, observed_percentage))),
                    snssais=[snssai_obj],
                )
                payload = {
                    'timeStampGen': datetime.now(timezone.utc).isoformat(),
                    'expiry': datetime.fromtimestamp(
                        observed['bucket_epoch'] + (smap.source_max_gap_seconds or smap.interval_seconds), timezone.utc
                    ).isoformat(),
                    'sliceLoadLevelInfos': [load.model_dump(exclude_none=True)],
                }
                self.contract.validate('AnalyticsData', payload)

                target = json.dumps(
                    snssai_obj.model_dump(exclude_none=True), sort_keys=True
                )
                age = now - observed['bucket_epoch']
                valid_for = smap.source_max_gap_seconds or smap.interval_seconds
                self.db.record(
                    'LOAD_LEVEL_INFORMATION', target,
                    {'source': smap.label, 'object_id': smap.object_id,
                     'counter_id': smap.counter_id, 'capacity': smap.capacity_units,
                     'timestamps': timestamps, 'values': values,
                     'raw_samples': raw_samples,
                     'aggregation': 'observed_bucket_mean' if smap.source_max_gap_seconds else 'none'},
                    result,
                    payload if 0 <= age < valid_for else None,
                )
                self._outcomes[key] = 'published' if 0 <= age < valid_for else 'stale_samples'
                log.info("Slice %s: observed load=%.2f%%, forecast=%s", smap.label,
                         observed_percentage, 'available' if result['points'] else 'unavailable')

            except (ValueError, FileNotFoundError) as exc:
                self._outcomes[key] = 'invalid_data'
                log.debug("Slice %s ingestion skipped: %s", smap.label, exc)

    async def _ingest_anomalies(self):
        now = time.time()
        for watch in self.anomaly_watches:
            key = 'anomaly:'+watch.label
            self._outcomes[key] = 'insufficient_data'
            try:
                # Read 2 hours of history for anomaly baseline
                window = 7200
                samples = await asyncio.to_thread(read_samples,
                    self.pm_path,
                    testbed=watch.testbed_id,
                    object_id=watch.object_id,
                    counter_id=watch.counter_id,
                    start=now - window,
                    end=now,
                )
                if len(samples) < 25:
                    continue

                history = [(s['bucket_epoch'], s['value']) for s in samples[:-1]]
                current = samples[-1]
                if not 0 <= now - current['bucket_epoch'] < watch.interval_seconds:
                    self._outcomes[key] = 'stale_samples'
                    continue

                result = detect(
                    history, current['value'], current['bucket_epoch'],
                    window_seconds=3600,
                )
                self._outcomes[key] = result['status']

                if result.get('anomaly') and result.get('score', 0) > 0:
                    # Publish to SBI-retrievable storage
                    target = watch.supi or watch.object_id
                    exception = ('TOO_FREQUENT_SERVICE_ACCESS'
                                 if watch.metric == 'service_access_rate'
                                 else 'UNEXPECTED_LARGE_RATE_FLOW')
                    report = {
                        'abnorBehavrs': [{
                            'excep': {'excepId': exception},
                        }],
                    }
                    if watch.supi:
                        report['abnorBehavrs'][0]['supis'] = [watch.supi]

                    from datetime import datetime, timezone
                    payload = {
                        'timeStampGen': datetime.now(timezone.utc).isoformat(),
                        'expiry': datetime.fromtimestamp(
                            current['bucket_epoch'] + watch.interval_seconds,
                            timezone.utc,
                        ).isoformat(),
                        **report,
                    }
                    self.contract.validate('AnalyticsData', payload)
                    self.db.record(
                        'ABNORMAL_BEHAVIOUR', target,
                        {'source': watch.label, 'counter_id': watch.counter_id,
                         'object_id': watch.object_id, 'history': history,
                         'current': current},
                        result, payload,
                    )
                    log.warning("Anomaly detected: %s score=%.2f",
                                watch.label, result['score'])

            except (ValueError, FileNotFoundError) as exc:
                log.debug("Anomaly watch %s skipped: %s", watch.label, exc)
