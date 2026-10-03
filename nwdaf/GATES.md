# Phase 1 checkpoint — 2026-09-27

> **Latest, 2026-09-28 20:35 Lima:** [Gate 4 measured closure](../reportes/2026-09-28_nwdaf_gate4_cierre_medido.md).
> The four-metric evaluator now reports **PASSED**, using 57 contiguous PM buckets
> and causal RMSE below 5 percentage points. **The evaluated traffic is essentially idle**
> (two origins per horizon/slice); predictive congestion mitigation is not established.
> Gate 3 remains PASSED. Entries below preserve the earlier evidence snapshots.

> Latest acceptance, 2026-09-28: [consolidated Gate 4 / thesis report](../reportes/2026-09-28_nwdaf_gate4_resultados_tesis.md).
> **Gate 3 PASSED** with correlated N23/N7/PFCP, actual 4.90 Mbps UE throughput,
> 20 Mbps QER restoration and verified ledger. **Gate 4 INCOMPLETE**: causal RMSE
> needs sufficient continuous measured history. Control latency is 9.92–28.27 ms,
> with acquisition/polling separately documented. Paired real Stream5G P.1203
> MOS is 3.9697 → 4.2081. This is one measured pair, not a statistical guarantee.
> This supersedes the historical deployment notes below. The final NWDAF regression has 46 passing tests.

> Updated operational findings and frontend verification are recorded in
> [FRONTEND_CHECKPOINT.md](FRONTEND_CHECKPOINT.md). In particular, the Core VM
> has no listener on 8085 and the full backend suite currently has two failures.
> The completion wording below describes component work, not live deployment
> or end-to-end closed-loop acceptance.

## Executed evidence

- Official ETSI archives verified by SHA-256; unchanged schema bytes compared offline.
- `python -m compileall -q app tools`: PASS.
- `python -m pip check`: PASS, no broken dependency requirements.
- `python -m pytest tests -v`:
  **28 passed**, 5 dependency deprecation warnings, 9.87 seconds on the local machine.
- Actual TCP h2c health exchange and full HTTP/2 subscription notification exchange using hyper-h2 and Hypercorn: PASS.
- Periodic PM ingestion scheduler implemented with capacity-to-slice mapping and background task execution in app lifespan: PASS.
- Local research service running at `127.0.0.1:8085`;
  health returns `ready`, `phase-1-research`, `closed_loop_enabled=false`, with live ingestion status.

## Gate interpretation

| Gate | Status | Scope / missing evidence |
|---|---|---|
| 0 | Passed for implemented profile | Root artifact, selected dependent types, Pydantic slice payload, OAS payload validation. Full TS feature/reference coverage not claimed. |
| 1 | Passed component tests | Reproducible forecast + held-out synthetic test, causal MAD, actual P.1203 dependency and stalling effect, invalid-input rejection. |
| 2 | Passed | Real h2c health exchange, subscription CRUD (201/200/204), durable SQLite outbox with exponential retries, and full end-to-end HTTP/2 notification delivery verified in test suite. Operational ingestion scheduler wired into lifespan. |
| 3 | Passed live campaign | N23 → N7 → existing QER 1, actual UE throttling and restoration, independent ledger verification; see current acceptance report. |
| 4 | Incomplete | Control latency, CPU/RSS and paired real P.1203 QoE measured. Acquisition/polling separated. Causal RMSE still lacks continuous history; no predictive-congestion claim. |

## Phase 1 Status: COMPLETED

Phase 1 (Gate 0, Gate 1, Gate 2) is 100% complete and verified with 28 automated tests.
All SBI endpoints (`/nnwdaf-analyticsinfo/v1/analytics`, `/nnwdaf-eventssubscription/v1/subscriptions`),
statistical/ML engines (Holt-Winters+Ridge, MAD, ITU-T P.1203), SQLite WAL immutable ledger,
and periodic PM ingestion scheduler are fully operational.

Next active phases:
- Phase 2: Open5GS PCF C extension (N23 client and closed-loop actuation).
- Phase 3: MAEstro EMS backend proxy and frontend analytics dashboard.
