# PM source connection — 2026-09-27

The configured EMS source is Windows `Code/backend/data/ems.db` (~3.17 GB),
not a database discovered on the Core. Actual PM identities include testbed
`local`, objects `nf:amf` and `interface:ogstun`, unlike the example mappings.

Implemented bounded read-only export (`app/ingestion/export_pm.py`) and SSH
snapshot bridge (`infra/sync_nwdaf_pm.py`). It preserves timestamps, values,
source and quality; excludes simulated samples; caps the scanned rows and
query time; publishes the new snapshot by atomic SFTP rename. It does not
claim a complete time window or fill gaps. One successful snapshot contained
59,435 real/computed rows. No original EMS rows were modified.

`infra/connect_nwdaf_pm.py` connected this snapshot to the Core NWDAF service.
Verified health: `ready`, `source_connected_unmapped`, zero configured slice
maps, `closed_loop_enabled=false`. Automatic example capacity mappings are
now disabled; explicit maps remain supported in the scheduler constructor.

The bridge was run one-shot, not installed as a persistent worker. Its
`--watch` option repeats every 60 seconds while running. Snapshot freshness
must be validated before any control decision; receiving a file is not proof
of live analytics or a capacity measurement.

Tests: five export/scheduler tests passed. Export test checks source remains
unchanged and simulated records are excluded.

Recent real counters had ~13.38 Mbps maximum RX on `enp0s3` but almost no
traffic on `ogstun` or `uesimtun0`. Management/network downloads must not be
misclassified as congestion of the user-plane slice. Neither 20 Mbps per-UE
nominal MBR nor the example 50 sessions establishes aggregate slice capacity.

Pending research choice: use measured aggregate N6 capacity/demand, or explicitly
provision a controlled experimental bottleneck and identify it as such. The
85%/70% policy requires that denominator and a verified slice-to-interface
mapping. After that, N7 actuation and datapath enforcement can be validated.
Gate 3 remains NOT PASSED; no PCF binary replacement or enforcement was done
in this checkpoint.
