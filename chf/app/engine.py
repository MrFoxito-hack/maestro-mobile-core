"""Byte-credit accounting, independent of SBI and storage.

MAEstro profile: settle deltas, return unused reservation, grant replacement.
Unauthorised usage is evidence, never negative credit or another session's debit.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settlement:
    debit: int
    overrun: int
    reservation: int
    available_after: int
    final: bool


def settle(*, quota: int, consumed: int, other_reserved: int, reserved: int,
           used: int, wanted: int, enabled: bool, release: bool = False) -> Settlement:
    if min(quota, consumed, other_reserved, reserved, used, wanted) < 0:
        raise ValueError("Negative units")
    if consumed + other_reserved + reserved > quota:
        raise ValueError("Account invariant violated")
    debit = min(used, reserved)
    overrun = used - debit
    available = quota - consumed - debit - other_reserved
    grant = min(wanted, available) if enabled and not release and not overrun else 0
    return Settlement(debit, overrun, grant, available - grant,
                      grant == 0 or grant == available)
