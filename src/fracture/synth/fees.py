"""Fee arithmetic, Python side.

Deliberately a second implementation of the same rules the mart computes in SQL.
The generator uses this to produce what the firm *should* have billed; the mart
recomputes it independently from `canon.fee_schedule` and `canon.fee_tier`. A
test asserts the two agree on undefected households. If they ever diverge, one
of them is wrong and the unbilled figure is fiction -- which is exactly the
failure that would otherwise go unnoticed for months.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

CENT = Decimal("0.01")
BPS = Decimal("10000")

#: Periods per year by billing frequency.
PERIODS_PER_YEAR = {"monthly": Decimal(12), "quarterly": Decimal(4), "annual": Decimal(1)}


def tiered_annual_fee(aum: Decimal, tiers: Sequence[dict]) -> Decimal:
    """Marginal tiering: each band charges its own rate on the slice inside it.

    The alternative reading -- one rate on the whole balance, chosen by which
    band the total lands in -- is "blended" below. Firms use both, and assuming
    the wrong one produces a number that is wrong by 20-40% at the breakpoints,
    which is more than the leakage you are looking for.
    """
    total = Decimal(0)
    for tier in sorted(tiers, key=lambda t: int(t["tier_seq"])):
        lower = Decimal(str(tier["lower_bound"]))
        upper = tier.get("upper_bound")
        upper_d = Decimal(str(upper)) if upper is not None else None
        if aum <= lower:
            break
        slice_top = aum if upper_d is None else min(aum, upper_d)
        band = slice_top - lower
        if band <= 0:
            continue
        if tier.get("annual_rate_bps") is not None:
            total += band * Decimal(str(tier["annual_rate_bps"])) / BPS
        if tier.get("flat_amount") is not None:
            total += Decimal(str(tier["flat_amount"]))
    return total


def blended_annual_fee(aum: Decimal, tiers: Sequence[dict]) -> Decimal:
    """One rate on the whole balance: the rate of the band the total falls in."""
    chosen = None
    for tier in sorted(tiers, key=lambda t: int(t["tier_seq"])):
        lower = Decimal(str(tier["lower_bound"]))
        upper = tier.get("upper_bound")
        upper_d = Decimal(str(upper)) if upper is not None else None
        if aum >= lower and (upper_d is None or aum < upper_d):
            chosen = tier
            break
    if chosen is None:
        return Decimal(0)
    total = Decimal(0)
    if chosen.get("annual_rate_bps") is not None:
        total += aum * Decimal(str(chosen["annual_rate_bps"])) / BPS
    if chosen.get("flat_amount") is not None:
        total += Decimal(str(chosen["flat_amount"]))
    return total


def flat_annual_fee(tiers: Sequence[dict]) -> Decimal:
    return sum(
        (Decimal(str(t["flat_amount"])) for t in tiers if t.get("flat_amount") is not None),
        Decimal(0),
    )


def period_fee(
    aum: Decimal, tiers: Sequence[dict], calc_method: str, frequency: str
) -> Decimal:
    """The fee for one billing period, rounded to cents once at the end."""
    if calc_method == "tiered":
        annual = tiered_annual_fee(aum, tiers)
    elif calc_method == "blended":
        annual = blended_annual_fee(aum, tiers)
    elif calc_method == "flat":
        annual = flat_annual_fee(tiers)
    else:
        raise ValueError(f"unknown calc_method {calc_method!r}")
    per_year = PERIODS_PER_YEAR.get(frequency)
    if per_year is None:
        raise ValueError(f"unknown billing frequency {frequency!r}")
    return (annual / per_year).quantize(CENT, rounding=ROUND_HALF_UP)
