# Charging model — experimental byte-credit profile v2

This is not monetary billing. An account contains a byte allowance. The SBI,
service, pure engine and SQLite repository are separate layers.

## Accounting

For each account: `available = quota - debited - sum(open reservations)`.
Every committed value must remain nonnegative. SQLite `BEGIN IMMEDIATE`
serializes reservations and debits; database triggers also reject budget
violations. This is a single-host database, not a distributed SQL cluster.

On Update, this implementation settles newly reported deltas, returns unused
reservation, then grants a **replacement** quota. The consumer must replace its
current authorization, not add the grant to an old remainder. This is an explicit
testbed profile decision, not a claim that every possible 3GPP scenario is covered.

Debit is limited to the session's prior reservation. Excess reported usage is
preserved as `overrun_bytes` and blocks further grants for that session. It does
not debit another session's reserved funds. `observed_bytes = debited + overrun`
for new sessions. UL/DL remain distinct; total-only reports are recorded as
unclassified direction, never attributed to an invented direction.

The sum of replacement grants is an allocation counter, **not** total paid usage
or unique credit: unused units can be returned and reallocated repeatedly.

## Identity, retries and recovery

Create identity is `(NF UUID, SUPI, chargingId)`. Retries preserve original
timestamp/sequence/body, except `retransmissionIndicator` may change. A successful
replay returns its original result without another reservation. Different payloads
with reused identities are conflicts. New invocations advance exactly by one.

Usage identity is `(charging resource, localSequenceNumber)` in this one-rating-
group, one-UPF profile; rating group and UPF cannot change inside the resource.
Exact duplicates are ignored even in a later invocation. Changed duplicates or
previously unseen out-of-order reports are rejected, not silently counted.
SMF must buffer/reconcile PFCP reports and persist the mapping to usage identity.

Events, account ledger, replay journal and CDRs are append-only via SQL triggers.
Those triggers prevent application mistakes, not a privileged database owner
tampering with the file. Release settles the final report and creates exactly one
educational JSON CDR. This is **not** the standardized ASN.1 CDR/Ga billing format.

CHF restart keeps accounts, reservations, responses and usage identities. Schema
v1 migration makes a SQLite backup and adds fields without assigning fictitious
owners to old sessions. Those legacy resources require operator reconciliation.

An elapsed validity deadline only marks a resource as potentially orphaned.
Reservations are **not freed automatically**: the UPF might still be forwarding.
Administrative reconciliation requires explicit confirmation that the consumer
has been stopped/fenced, retains unknown final usage as partial evidence and
records the operator's reason. It is not automatic SMF restart recovery.
