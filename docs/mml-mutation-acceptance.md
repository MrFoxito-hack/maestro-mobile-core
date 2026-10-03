# MML mutation suite — 28 September 2026 (America/Lima)

The nine commands are implemented in `operation_mutations.py`, exposed through
the existing authenticated `/api/v1/operations/execute` endpoint, and parsed by
the React console. MME commands are removed. Infrastructure commands appear last
under `O&M Infraestructura`. NWDAF is now present in the 5G scenario inventory.

Subscriber profiles use the configured Open5GS MongoDB database (`open5gs` in
this lab), Milenage credentials, slice/session QoS and AMBR. Partial updates use
atomic `$set` paths to preserve other slices and BSON identifiers. Credentials
are redacted from operation results, history, audit and exported MML envelopes.

The console preserves textual identifiers and leading zeros, maps K/DNN/5QI/MBR
aliases, validates bounds, and renders success and failure with the canonical
MAEstro OMC envelope and RETCODE 0/1001. Duplicate or unknown parameters fail.

## Verified live operations

* `ADD CHF-QUOTA: IMSI="imsi-999700000000004", QUOTA_MB=50;`
  returned CHF HTTP 200. The quota increased from 50,000,000 to 102,428,800 bytes
  (52,428,800 bytes = 50 MiB). Run: `438687895a59436ba876fbc068e9f799`.
* The requested IMSIs ending in 006 and 007 already existed. The existing 006
  profile rejected ADD without alteration. The user authorized using 021.
* `ADD 5G-SUB: IMSI="imsi-999700000000021", SST=1, SD="000002", DNN="corporate";`
  succeeded. A separate direct MongoDB read verified IMSI, SST, SD and DNN.
  Run: `2782201af1dd4c69af014fdb5b8c2cc5`.

## Verification

* Frontend production build: passed (`npm run build`).
* MML frontend regression suite: 12 passed in headless Chromium.
* Backend complete suite: 117 passed (`.venv\Scripts\pytest`).
* CHF complete suite: 56 passed, 1 environment-dependent test skipped.
* NWDAF contract and scheduler: 12 passed.
* Native PCF: compiled with the deployed Open5GS build and `-Werror`.
  Evidence: `.work/nwdaf-build-20260929T021843045463Z/`.
* Targeted frontend ESLint: zero errors; four existing Fast Refresh export warnings.

## Runtime activation and limits

`infra/deploy_mml_control.py` previews the exact rollout by default; `--execute`
backs up the running PCF executable and the three Python files, installs the
control drop-in, then restarts PCF, CHF management and NWDAF. An independent
180-second rollback timer remains armed until service health and the native
control socket are verified. Active UEs may need re-registration after this
initial PCF restart. Subsequent commands require no service restart.

The native Unix datagram socket is owned by PCF with mode 0600 and enabled only
with `MAESTRO_MML_CONTROL=1`. The backend reaches it over the configured SSH link.
QoS updates run on the PCF event thread and call
`pcf_sbi_send_smpolicycontrol_update_notify`; success requires SMF HTTP 204.
The IMSI must have exactly one active Internet session. N7 acknowledgement is
not evidence of UPF or RAN enforcement. Changing 5QI may require RAN support for
a dedicated QoS flow; end-to-end enforcement has not been certified here.

The closed-loop mode changes the actual PCF runtime flag. MANUAL stops future
automatic decisions; it does not undo decisions already applied or in flight.
The flag is process-local and returns to service configuration on restart.

CHF state updates omit quotaBytes and preserve the existing quota within the
same database transaction, including concurrent top-ups. Top-ups follow the
CHF limit of 100,000,000 bytes per request. HTTP 200 confirms accounting, not
immediate packet forwarding: exhausted-session recovery and actual UPF traffic
must be checked separately.

NWDAF resolves NF to slice using the configured PM capacity map exposed by
health, retrieves observed load via AnalyticsInfo, and selects the requested
900/1800-second prediction from research evidence. Missing/stale forecasts are
reported as unavailable; observations are never presented as predictions.

## Activated lab verification

The user authorized activation and UE recovery. PCF, CHF management and NWDAF
were restarted successfully; AMF, SMF and both UPFs kept their processes.
Activation evidence: `.work/mml-20260929022233/activation.json`.
Rollback: `sudo /bin/sh /home/emsadmin/maestro-charging/mml-20260929022233/rollback.sh`.
The local FastAPI process was also reloaded. An authenticated HTTP request to
`/api/v1/operations/catalog/5g-sa` returned 200 with all nine commands; a live
`chf.balance` execution returned 201 and the verified account balance. The
frontend production build and all 117 backend tests passed again after the
final changes.

* `MOD CHF-STATE` with ACTIVE returned success and preserved the 102,428,800-byte
  quota. Run: `80b745054c464cd98f0624ab5e7f26ef`.
* `DSP NWDAF-ANALYTICS` for UPF-01 / 15 minutes returned observed 0% and predicted
  0% from `hw-additive-ridge-v1` when fresh forecast evidence was available.
  Run: `221ab1568828431cb1dadf906d463e32`. Other samples correctly returned
  `prediction_status=unavailable` when the latest evidence was observation-only.
* MANUAL and AUTONOMOUS both acknowledged the actual native mode change.
  Final mode: AUTONOMOUS. Runs: `3de29207757f40e9bc2f77070bb40569` and
  `00594481d4964433ae84923febfaf577`.
* `MOD PCC-QOS` for IMSI 004 with 5QI=9, DL=20 Mbps and UL=20 Mbps received
  SMF N7 HTTP 204. Run: `956e97f6af08464686ad867c08e6afb1`. A subsequent SMF
  InfoAPI read showed the additional 5QI=9 QoS flow on its Internet session.

All four running UEs (001, 003, 004, 005) finished registered in NORMAL-SERVICE.
UE 001 has one active Internet PDU session; 003, 004 and 005 each have active
Internet and Corporate sessions (seven active PDU sessions total).

UE 003 initially failed PDU establishment because four stale CHF reservations
held its remaining 2,000,000 bytes. Those resources predated the verified SMF
and UPF process starts at 2026-09-28 20:39 UTC and had no current SMF context.
During the authorized recovery, UE03 was stopped, the four resources were
reconciled through the CHF management API, and UE03 was started again. Quota
(50,000,000) and consumed bytes (48,000,000) were preserved. The CHF ledger/CDRs
explicitly retain unknown final unreported usage; no additional quota was added.

No end-to-end throughput or quota-exhaustion recovery claim is made from the
HTTP/N7 acknowledgements alone.
