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
#: Precedence now lives on each pack's entity registry, because who outranks
#: whom is a statement about one domain's source systems. Kept as a function
#: so callers read the same way they did.
def precedence_for(entity: str, pack: Any | None = None) -> tuple[str, ...]:
    from fracture.packs import Pack, get_pack

    resolved = pack if isinstance(pack, Pack) else get_pack(pack)
    spec = resolved.entities.get(entity)
    return spec.precedence if spec else ()


#: Columns compared when deciding whether two sources materially disagree.
#: Comparing every column would flag a null vs a value, which is coverage, not
#: disagreement.
#:
#: Still keyed by entity name in the chassis rather than moved onto the pack.
#: An entity with no entry here is simply never flagged for variance, which is
#: the right default for a pack whose sources do not yet overlap; it becomes
#: wrong the moment two services sources both write invoices. Recorded in
#: OPEN_DECISIONS rather than left for someone to discover.
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


def authority(entity: str, source_id: str, pack: Any | None = None) -> int:
    order = precedence_for(entity, pack)
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


def ordered_sources(entity: str, pack: Any | None = None) -> Sequence[str]:
    return precedence_for(entity, pack)
