# MAEstro NWDAF — Phase 1 research implementation

This is an independent research NF, not upstream Open5GS support, not an operator-grade
certification, and not yet an autonomous closed loop. No PCF/SMF/UPF setting is changed.

## Reproducible setup (Python 3.11)

From `Code/nwdaf`:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -c constraints-tested.txt
.venv/Scripts/python tools/fetch_contract.py
# Read THIRD_PARTY.md before enabling the non-commercial research QoE dependency.
.venv/Scripts/python -m pip install -r requirements-research.txt -c constraints-tested.txt
.venv/Scripts/python -m pytest tests -q --junitxml=evidence/gates-0-1.xml
$env:NWDAF_TOKEN = '<unique secret of at least 24 characters>'
.venv/Scripts/python -m hypercorn --config hypercorn.toml app.main:app
```

Linux equivalents use `.venv/bin/python`. Hypercorn supports h2c on loopback port 8085.
Production-exposed SBI TLS/NRF OAuth is NOT implemented: the bearer token is local testbed
access control, not a claim of standard NRF token issuance. Do not expose this port publicly.

## Implemented boundaries

- Three working analytic engines, executed against explicit measurements; no invented observations.
- Separate SQLite WAL database, transactional append-only analysis records with input SHA-256.
  Triggers prevent application SQL updates/deletes; this is not tamper-proof against the DB owner.
- Read-only adapters for EMS `metric_samples` and CHF account reservations/CDRs.
  They never modify source databases, create missing sources or mistake reservations for usage.
- Authenticated research endpoints: `/management/v1/slice-forecast`,
  `/management/v1/abnormal-behaviour`, `/management/v1/service-experience`.
- SBI `GET /nnwdaf-analyticsinfo/v1/analytics?event-id=LOAD_LEVEL_INFORMATION` with
  JSON `event-filter={"snssais":[{"sst":1,"sd":"000001"}]}` returns the latest observed
  slice load (200) or no current data (204). It does not disguise forecasts as observations.
  Unsupported reporting requirements, target filters and analytics IDs are rejected.
- Prediction bands, scores, model versions, and source/capacity definition remain research evidence.

The read-only adapters are ready, but **automatic ingestion from the running multi-VM testbed
is not configured**. A slice-to-resource mapping, measured capacity and regular sample series
must be established first. A VM CPU counter or an unqualified `ogstun` is not slice utilization.
No seeded traffic or synthetic analytics are inserted into the operating EMS/CHF.

## Gate 0 contract provenance

Root: unchanged ETSI electronic attachment for TS 29.520 V16.7.0:
https://www.etsi.org/deliver/etsi_ts/129500_129599/129520/16.07.00_60/

`tools/fetch_contract.py` pins archive SHA256 and verifies extracted bytes on each startup.
Runtime validation is offline; unpinned external references fail closed.
Dependent types used by the implemented profile are pinned from TS 29.571 V16.8.0
and TS 29.517 V16.5.0 (both Release 16). This is NOT a claim of full reference-graph coverage.
Tests cover the implemented AnalyticsData, EventFilter, SliceLoad, service-experience MOS
and minimal EventsSubscription payload shapes against the official OAS 3.0 schemas.

Important normative corrections to the original plan:

1. Query parameters are `event-id`, `ana-req`, `event-filter`, `supported-features`, `tgt-ue`.
   The suggested `analytics-id`, `ana-req-sub-type`, `time-window` are not this API contract.
2. Query event: `LOAD_LEVEL_INFORMATION`; subscription event: `SLICE_LOAD_LEVEL`.
3. Subscription base is **`nnwdaf-eventssubscription`**, with two s characters at the join.
4. The official V16.7.0 PDF Annex A.3 p97 and ZIP contain AnalyticsInfo API version 1.1.1
   with `externalDocs` V16.5.0. EventsSubscription is API 1.1.3 / V16.7.0.
   We preserve this original metadata, rather than falsely rewriting it.

## Models and limitations

- `hw-additive-ridge-v1`: equal-weight additive Holt-Winters and ridge regression
  (trend + first seasonal harmonic). Regular measured utilization in [0,100], no implicit
  resampling or gap filling. At least two training seasons plus 20 rolling origins.
  Empirical 95% residual bands are not guaranteed coverage under temporal dependence.
  Model hyperparameters and actual out-of-sample RMSE/coverage must be evaluated in Gate 4.
  5-minute input supports 15/30-minute forecasts; 30-minute input only supports 30 minutes.
- MAD: causal 60-minute baseline, 1.4826 scaling, explicit noise floor when MAD=0,
  insufficient-data state, score is not a probability or standardized confidence value.
- P.1203: pinned external implementation, audio/video metadata + player stalls required.
  Conservative profile: Mode 0, H.264 <=1080p <=25fps, supported audio, 8..300s aligned data.
  4K/HEVC/60fps need another validated model/profile; not silently accepted as P.1203.
  The original polynomial in R in the plan is not the P.1203 audiovisual model and is not used.

## Remaining gates

Gate 0/1: limited to the implemented contract profile and analytic component tests.
The health check HTTP/2 transport smoke is **not** Gate 2 completion.

Remaining: EventsSubscription CRUD + durable callback outbox, all three analytics IDs via SBI,
reporting windows and target semantics, NRF discovery/security, bounded ingestion worker,
native asynchronous PCF C consumer, N7 policy updates, rollback, SMF/UPF enforcement,
EMS UI, and real UE experimental campaign. No latency/RMSE/QoE production claim is made.
