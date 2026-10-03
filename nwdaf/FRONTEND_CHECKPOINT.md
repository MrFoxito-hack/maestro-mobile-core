# NWDAF dashboard integration — 2026-09-27

## Implemented and verified

- `/nwdaf` authenticated MAEstro route and navigation item. Operator-only UI;
  backend authorization remains authoritative. Disabled for EPC.
- Compact load/anomaly/MOS cards, selectable slice forecast with observed series
  and nominal predictive bands, explicit SUPI/application query, MOS indicator.
- Missing/expired reports are not represented as zero, healthy, or a good MOS.
- Policy ledger is explicitly unavailable until native actuation is implemented;
  no fabricated intervention rows or latency values.
- Read-only management predictions endpoint, proxied through MAEstro. Forecast
  evidence includes original timestamps and values. Ingestion deduplication now
  includes samples, rather than just a static source configuration.
- `pnpm run build`: passed (existing chunk-size warning).
- Browser component tests: 2 passed in Chromium (empty state and EPC isolation).
- NWDAF tests: 28 passed, 5 dependency warnings; `evidence/gate-2.xml`.
- Backend NWDAF tests: 6 passed.
- Full backend suite: **99 passed, 2 failed** in `tests/test_nf_metrics.py`:
  dimension identity and rate/reset handling. Those files were not modified in
  this checkpoint. The full suite must not be described as passing.

## Gate interpretation and operational status

Gate 2 is supported for the implemented periodic single-slice profile by real
Hypercorn HTTP/2 request/notification tests. It is not native PCF validation,
full TS 29.520 conformance, or a throughput/availability certification.

Read-only SSH checks of the configured Core found:

- `open5gs-pcfd` active;
- no listener on TCP 8085;
- no `src/pcf/*nwdaf*` files in the configured Open5GS checkout;
- existing modifications in PCF files, preserved.

Thus the prior statement that all ingestion was operational must not be taken as
evidence of deployment on the Core. The dashboard may correctly show disconnected.
No Core configuration was changed and no PCF service was restarted here.

## Next gates

1. Resolve PM dimension/rate test failures and validate source identities. The
   scheduler's default 50-session capacity is an educational configuration, **not
   a measured capacity**; it must not authorize autonomous control as-is.
2. Deploy NWDAF to the Core with explicit operator capacity/source mappings,
   protected credential, read-only source access, rollback and health evidence.
3. Implement native PCF asynchronous client and decision audit; preserve current
   policies and restore captured nominal values, not hard-coded universal MBR.
4. Verify N7 then N4 enforcement and recovery with UE traffic before Gate 3 passes.
5. Measure performance/QoE/latency independently for Gate 4. No IEEE performance
   or empirical coverage claim is established by component tests.
