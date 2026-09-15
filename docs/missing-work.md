# Implementation order and acceptance gates

Initial state: audited, standalone CHF only. This backlog is not a completion claim.

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

