# Integration audit before implementation

The existing EMS and standalone CHF were inspected before edits. Live guest
inspection used read-only SSH commands. Existing work is preserved.

## Existing capabilities

- CHF: FastAPI, SQLite transactions, reservation sums, Create/Update/Release,
  exact-request replay and six passing tests; **no SMF integration**.
- EMS: real SSH operations and PCAP capture, alarm episodes, KPI storage,
  RBAC, audit and React views. Backend tests use simulated execution, so the
  historical 53 passing tests are not evidence of a working cellular network.
- Open5GS: HTTP/2 SBI stack, NRF discovery, SCP routing, PFCP rule construction,
  UPF usage accumulators and EPC Gy charging helpers available for reuse.

## Confirmed defects in the starting CHF

1. Retransmission flag changes break the request hash.
2. A repeated local usage sequence in a new invocation is charged again.
3. SMF owner is not persisted/validated on Update/Release.
4. Disabled accounts still receive Update grants.
5. Zero new grant triggers TERMINATE while an existing reservation remains.
6. Excess usage and administrative quota reductions can produce negative balance.
7. Create silently ignores supplied usage.
8. No CDR, durable orphan reconciliation, authenticated admin API or DB readiness.
9. Untyped PDU metadata and incomplete SBI validation/error mapping.

Positive concurrency check: 100 Create operations (20 workers) against a
10000-byte account reserved a total of 10000 bytes. Preserve transaction locking.

## Open5GS source integration points (v2.8.0)

| Concern | Source / function | Required work |
|---|---|---|
| Initial charging | src/smf/gsm-sm.c, smf_gsm_state_wait_5gc_sm_policy_association | Await Nchf after PCF and before PFCP establishment |
| Request construction/routing | src/smf/sbi-path.c, smf_sbi_discover_and_send; lib/sbi/path.c | Reuse native asynchronous SBI transactions and routing |
| PDU context | src/smf/context.h | Add owned charging state, sequence and quota tracking |
| PFCP rules | src/smf/pfcp-path.c and src/smf/n4-build.c | Attach URR to both directions, update URR asynchronously |
| Reports | src/smf/n4-handler.c, smf_n4_handle_session_report_request | Deduplicate by session/URR/report sequence; send Nchf Update |
| Final report | src/smf/n4-handler.c, smf_5gc_n4_handle_session_deletion_response | Consume final usage before CHF Release |
| UPF measurement | src/upf/context.c, upf_sess_urr_acc_fill_usage_report | Reports are deltas since last snapshot, not cumulative totals |
| Termination | src/smf/gsm-sm.c, wait_pfcp_deletion and wait_5gc_n1_n2_release | Use existing network-requested release flow |

UPF snapshots its counters after each report. Threshold and quota checking
currently use those report snapshots: renewal races and the remaining quota
must be verified, not inferred from the presence of URR structures. A URR
measurement alone does not prove packet gating at exhaustion.

## EMS integration constraints

CHF must expose its own data through an authenticated server-side EMS proxy.
Current generic observability includes load-derived CPU and process-derived
session counts, which cannot be used as charging evidence. NF probes currently
target the core host even for remote components. SBI capture filter is fixed at
7777, so a CHF at 8081 needs explicit inclusion. Nchf/URR identifiers are absent
from the current trace normalizer.

