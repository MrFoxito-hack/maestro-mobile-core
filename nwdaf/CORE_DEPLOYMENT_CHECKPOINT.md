# Core deployment and native integration checkpoint — 2026-09-27

## Completed

- Backend dimensional identity fixed: canonical sorted labels are hashed into
  native metric IDs. Existing undimensioned historical rows are preserved;
  they cannot retrospectively be separated without source evidence.
- Counter rates restored using same-process, positive elapsed time <=120 s,
  monotonic counter deltas. Resets, restarts, first samples and gaps are omitted.
- Entire backend suite: **101 passed** in 10.18 s.
- NWDAF deployed at `/home/emsadmin/maestro-charging/nwdaf` on the configured Core.
- Private CPython 3.11.15 runtime, separate venv, system Python unchanged.
- Cryptographic token generated on Core; token and environment mode 0600.
- `maestro-nwdaf.service` enabled, loopback `127.0.0.1:8085`, one worker,
  unprivileged user, read-only system/home except NWDAF directory.
- Authenticated MAEstro proxy verified: health `ready`, predictions `items: []`.
- P.1203 pinned research dependency installed separately, not vendored.
- On-Core NWDAF tests: **28 passed**, 5 dependency warnings, 22.93 s.
  Evidence: Core `nwdaf/evidence/core-deployment.xml`.

## Native C work: compiled, not installed

`native/nwdaf-handler.c` implements an opt-in asynchronous observer using
`ogs_sbi_client` and the PCF event-thread timer manager. It rejects mismatched
targets, invalid load, expired reports, future generation times and oversized
responses. It queries exactly SST=1 without SD, not every slice. No synthetic
confidence field is parsed. Hysteresis only records a candidate decision.

`infra/stage_nwdaf_pcf.py` staged it in the Core checkout and added lifecycle
and Meson integration. Existing Nudr edits are preserved in-place and backups
of `init.c` and `meson.build` use `.pre-nwdaf-<UTC timestamp>` suffixes.
`ninja -C build src/pcf/open5gs-pcfd` succeeded with warnings treated as errors
after correcting float-comparison and const-qualification diagnostics.

**The installed PCF was not replaced or restarted. The observer is not running
in the live PCF. No N7 policy actuation is implemented in this observer.**

## Gate 3 remains NOT PASSED

- Live ingestion reports `no_pm_source`; no invented PM source/capacity was set.
- The requested `slice_load_level`/`confidence_level` names are not the pinned
  wire contract. Actual observation is `sliceLoadLevelInfos[].loadLevelInformation`.
- Current analytics SBI report is observed load, not a forecast. Predictive
  control requires explicit prediction selection/reporting semantics.
- Source inspection found PFCP Create/Update QER decoding storing MBR in
  `lib/pfcp/handler.c`, but no MBR policing in the inspected `src/upf` datapath.
  Storing QER MBR is NOT proof of throughput enforcement. Audit the deployed
  UPF binaries and datapaths before asserting 5 Mbps enforcement.
- Native per-session policy changes, N7 acknowledgements, conflict handling with
  AF changes, restoration of captured nominal QoS, durable decision ledger,
  N4 PCAP evidence and measured throughput/recovery remain pending.
- No latency measurement, throughput change, or frontend intervention record
  has been fabricated. A manual `tc` limit would not prove N7-to-N4 enforcement.

## Operational rollback

To disable only the newly deployed service:
`sudo systemctl disable --now maestro-nwdaf.service`.
No pre-existing service definition was overwritten by the initial installer.
Runtime/data are retained. Restore the timestamped PCF source backups if
discarding the staged observer; the installed Core binaries are unchanged.

The installer uses uv-managed Python per https://docs.astral.sh/uv/guides/install-python/.
