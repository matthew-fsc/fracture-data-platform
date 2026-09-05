"""Which source wins when two disagree.

Canonical fan-in across sources (spec 7) needs an answer to this, and "whichever
ran last" is not one. Orion and the Schwab file both report an account's value.
They will not always agree. Without a precedence rule the canonical value flips
depending on asset execution order, and the number in the pack changes for
reasons nobody can explain.

So: the custodian is the record for balances; the CRM is the record for people;
the billing system is the record for what was billed; the manually-entered fee
schedule is the record for what should have been billed. A lower-precedence
source that disagrees does not overwrite -- it raises a variance, which is the
reconciliation finding the engagement is actually sold on.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

#: Per entity, sources in ascending authority. A source not listed sits below
#: every listed one; ties fall back to "do not overwrite an existing row".
SOURCE_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "account": ("generic_csv", "qbo", "redtail", "orion", "schwab_custodian"),
    # The custodian is the record of AUM. This is the whole reason the
    # reconciliation finding has commercial weight.
    "balance_snapshot": ("generic_csv", "orion", "schwab_custodian"),
    "party": ("generic_csv", "orion", "schwab_custodian", "redtail"),
    "household": ("generic_csv", "orion", "redtail"),
    "household_member": ("generic_csv", "orion", "redtail"),
    "producer": ("generic_csv", "orion", "redtail"),
    "book_assignment": ("generic_csv", "redtail", "orion"),
    "fee_schedule": ("generic_csv", "qbo", "manual_fee_schedule"),
    "fee_tier": ("generic_csv", "qbo", "manual_fee_schedule"),
    "schedule_assignment": ("generic_csv", "qbo", "manual_fee_schedule"),
    "invoice": ("generic_csv", "orion", "qbo"),
    "invoice_line": ("generic_csv", "orion", "qbo"),
    "cash_receipt": ("generic_csv", "orion", "qbo"),
    "receipt_application": ("generic_csv", "orion", "qbo"),
    "revenue_event": ("generic_csv", "orion", "qbo"),
    "cost_line": ("generic_csv", "qbo"),
    "fte_allocation": ("generic_csv", "qbo"),
    "service_event": ("generic_csv", "redtail", "orion"),
}

#: Columns compared when deciding whether two sources materially disagree.
#: Comparing every column would flag a null vs a value, which is coverage, not
#: disagreement.
VARIANCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "balance_snapshot": ("market_value",),
    "account": ("status", "closed_on"),
    "invoice": ("total_amount",),
    "cash_receipt": ("amount",),
    "book_assignment": ("producer_id", "split_pct"),
    "fee_tier": ("annual_rate_bps", "flat_amount"),
}

#: Relative difference below which two sources are treated as agreeing.
DEFAULT_VARIANCE_TOLERANCE = Decimal("0.0001")


def authority(entity: str, source_id: str) -> int:
    order = SOURCE_PRECEDENCE.get(entity)
    if not order:
        return 0
    return order.index(source_id) + 1 if source_id in order else 0


def wins(entity: str, incoming_source: str, existing_source: str) -> bool:
    """True when `incoming_source` may supersede a row written by `existing_source`.

    Equal authority means the same source restating itself, which is a genuine
    supersede. Lower authority never overwrites.
    """
    if incoming_source == existing_source:
        return True
    return authority(entity, incoming_source) > authority(entity, existing_source)


def material_variance(
    entity: str,
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    tolerance: Decimal = DEFAULT_VARIANCE_TOLERANCE,
) -> list[dict[str, Any]]:
    """Columns where two sources genuinely disagree, with the size of the gap."""
    out: list[dict[str, Any]] = []
    for column in VARIANCE_COLUMNS.get(entity, ()):
        if column not in incoming or column not in existing:
            continue
        a, b = existing[column], incoming[column]
        if a is None or b is None:
            if a is not b:
                out.append(
                    {
                        "column": column,
                        "existing": _jsonable(a),
                        "incoming": _jsonable(b),
                        "variance_pct": None,
                    }
                )
            continue
        if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
            a_d, b_d = Decimal(str(a)), Decimal(str(b))
            if a_d == b_d:
                continue
            denom = abs(a_d) if a_d else Decimal(1)
            pct = (b_d - a_d) / denom
            if abs(pct) > tolerance:
                out.append(
                    {
                        "column": column,
                        "existing": float(a_d),
                        "incoming": float(b_d),
                        "variance_pct": float(pct),
                    }
                )
            continue
        if a != b:
            out.append(
                {
                    "column": column,
                    "existing": _jsonable(a),
                    "incoming": _jsonable(b),
                    "variance_pct": None,
                }
            )
    return out


def _jsonable(value: Any) -> Any:
    """Variance detail is stored as jsonb, so dates and Decimals become text.

    Without this a variance on a date column raises inside the writer, which
    would turn a finding into a failed load.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def ordered_sources(entity: str) -> Sequence[str]:
    return SOURCE_PRECEDENCE.get(entity, ())
