# Release 16 charging contract and acceptance gates

Status: partial experimental implementation. No blanket 3GPP conformance claim.

## Pinned references

- [TS 32.291 V16.17.0](https://www.etsi.org/deliver/etsi_ts/132200_132299/132291/16.17.00_60/ts_132291v161700p.pdf),
  §§5.2.2, 6.1.3, 6.1.6 and Annex A. The attached OpenAPI remains version
  **3.0.7**, with `externalDocs` referencing V16.15.0. Do not label API v16.
- [TS 29.571 V16.13.0](https://www.etsi.org/deliver/etsi_ts/129500_129599/129571/16.13.00_60/ts_129571v161300p.pdf):
  shared SBI data types for the executable contract tests.
- [TS 32.290 V16.4.0](https://www.etsi.org/deliver/etsi_ts/132200_132299/132290/16.04.00_60/ts_132290v160400p.pdf),
  §§5.3.2.3, 5.4.2–5.4.4 and 5.5: reservation, reauthorization, final units and failures.

The official attachments are downloaded unchanged and hash-checked by
`chf/tools/fetch_contract.py`. Tests resolve their references offline.

## Supported profile and checks

| Subject | Contract / implementation gate |
|---|---|
| Create | POST chargingdata; 201, Location and ChargingDataResponse |
| Update | POST resource/update; 200 and ChargingDataResponse |
| Release | POST resource/release; 204 with no JSON body |
| Session Create | notifyUri; owner UUID; SUPI and chargingId required by our profile |
| Invocation | Initial 1 (legacy 0 allowed); increment by one; exact retries |
| Measurement | Total volume or consistent UL/DL; one rating group and UPF |
| Final units | Positive final grant remains usable; terminate on exhaustion |
| Bounds | Byte values limited to 2^53−1 for exact C/JSON/storage interchange |
| Unsupported | Time/event rating, roaming charging, multi-rating-group and online/offline switching |

The final-units interpretation follows TS 32.290 §5.4.3; resource and data-shape
checks follow TS 32.291. The narrower numeric bound, strict validation,
replacement-quota accounting and static lab credentials are MAEstro decisions.
Custom ProblemDetails causes are profile extensions, not invented normative enums.

## Still required before an E2E claim

Link this contract to the actual SMF state machine and prove PFCP measurement,
enforcement and final reporting. The source baseline is pinned in
`open5gs-baseline.md`. TS 32.255, TS 29.244, TS 29.500 and TS 29.510 still require
clause-level mapping and execution evidence for those later increments.
Local TS 23.501/23.502 documents supply architecture/procedure context, not a
substitute for the charging and PFCP specifications.
