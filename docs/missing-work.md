# Implementation order and acceptance gates

Review 2026-09-16: [current acceptance review](chf-review-2026-09-16.md).
MAEstro read-only management UI/API and MML queries are connected to the live
CHF database via a loopback-only management listener and SSH. Accounting
acceptance remains blocked by discarded final-unit usage reports, unvalidated
late Release responses and non-durable closure. Do not equate UI integration
or passing component tests with completion of the native charging plan.

Initial state: audited, standalone CHF only. This backlog is not a completion claim.

Checkpoint 2026-09-15: native SMF Create passes real TCP HTTP/2 component tests;
initial bidirectional URR construction passes C/IE encoding checks. The live UE
charging loop, UPF enforcement, native Update/Release, package and UI remain
unfinished. See [measured checkpoint](charging-native-checkpoint.md).

Continuation: C Update/Release now pass real HTTP/2 protocol-component tests;
the opt-in UPF gate compiles and passes 56 unit checks. SMF native checks are
now 144. Neither update/release integration with live PFCP nor the UE charging
loop is complete. Package/UI/NRF-SCP stages are still open; no feature activated.

N4↔SBI bridge: Usage Report from UPF now dispatches to Nchf_Update when the
session has an active CHF context. The CHF Update response triggers URR quota
renewal via PFCP Session Modification, or session release when quota is exhausted
or FinalUnitIndication=TERMINATE. Session Deletion Response extracts final
usage and sends Nchf_Release for CDR closure. Pending: compile verification,
unit test updates, E2E test with live UE traffic.

| Commit group | Work | Acceptance evidence |
|---|---|---|
| baseline | Preserve original CHF, source pin and repair duplicate guest IPs | Source hash; UE registration, PDU and ping without CHF |
| chf: accounting | Validated profile, reservations, usage identity, immutable ledger, CDR | Retry, concurrent reservations, excess, disabled account tests |
| chf: recovery/security | Reconciliation, readiness, authentication, admin isolation | Restart, DB failure, authorization tests |
| smf: optional client | Native asynchronous Nchf and feature flag | Build and baseline-off regression |
| smf: create/URR | Charging state before PFCP, bidirectional rule mapping | PCAP Create and PFCP Create URR |
| smf: reports/release | Dedup, Update, termination, final Release | Real UPF report, usage reconciled to CDR, no duplicate debit |
| nrf/scp | Registration, heartbeat, discovery, delegated route | Real NRF query and h2 capture via SCP |
| maestro | Charging API proxy, compact accounts/sessions/CDRs | Auth/RBAC, real data, frontend build |
| experiments | Baseline comparison and failure runner | Config, logs, PCAP, database, CDR and measured results |

No test-generated request counts as an Open5GS E2E measurement. Keep source
baseline and installed-package baseline distinct until both are verified.
