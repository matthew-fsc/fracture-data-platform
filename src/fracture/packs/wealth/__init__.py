"""The wealth pack: a multi-firm advisory book and the revenue it should earn.

Covers households, accounts, balances, fee schedules, invoices, receipts,
producers and the service events around them, and measures the estate on three
yields: what the fee schedules entitle the book to, what was invoiced, and what
arrived. The first of those is the one nobody else can compute, because it is
recomputed from the canonical schedule rather than read back out of the billing
system.
"""

from __future__ import annotations

from pathlib import Path

from fracture.packs import FirmRole, Pack, register
from fracture.packs.wealth.assertions import ASSERTIONS, NON_EMPTY

_HERE = Path(__file__).parent

PACK = register(
    Pack(
        name="wealth",
        title="Wealth management",
        thesis=(
            "Three yields rather than one: entitled, invoiced, collected. The two gaps "
            "between them separate billing execution from credit control, which have "
            "different owners."
        ),
        ddl_dir=_HERE / "ddl",
        model_dir=_HERE / "marts",
        non_empty=NON_EMPTY,
        assertions=ASSERTIONS,
        dashboard_module="fracture.packs.wealth.dashboard",
        synth_module="fracture.synth",
        verticals=("wealth", "insurance", "shared"),
        firm_roles={
            # Exactly one. A tenant with two platform firms has an ambiguous
            # consolidation root and every roll-up below it is wrong.
            "platform": FirmRole(1, "the consolidating platform firm"),
            "addon": FirmRole(None, "an acquired or folded-in firm"),
        },
    )
)

__all__ = ["PACK"]
