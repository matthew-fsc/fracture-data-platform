"""The professional services pack: a practice's own book of work.

Fracture Systems is itself a professional services firm, so this pack is both
a deliverable for law, accounting, agency and advisory clients and the model
the practice runs its own engagements on. Building it against ourselves first
is the cheapest way to find out whether it is any good.

The spine is the wealth pack's three yields with two stages added, because
professional services carries inventory that wealth does not and prices the
work separately from recording it:

    recorded at card rate     every recorded hour, priced
    billable at card rate     net of write-off at entry
    entitled                  net of discount, or at the agreed fixed fee
    billed                    what the invoice actually went out at
    collected                 what arrived

Four gaps, four owners. Write-off at entry belongs to whoever approved the work
and happens in the timesheet. Pricing belongs to whoever agreed the fee.
Realisation belongs to the engagement partner. Collection belongs to finance.
Firms routinely report the middle two together, as a single realisation
percentage, and manage the last by chasing. The first is usually invisible,
which is why an overrun is discovered at the invoice rather than in the week it
happened.
"""

from __future__ import annotations

from pathlib import Path

from fracture.packs import FirmRole, Pack, register
from fracture.packs.services.assertions import ASSERTIONS, NON_EMPTY
from fracture.packs.services.entities import ENTITIES

_HERE = Path(__file__).parent

PACK = register(
    Pack(
        name="services",
        title="Professional services",
        thesis=(
            "Five stages rather than three: recorded at card, billable, entitled, "
            "billed, collected. The two extra gaps are write-off at entry, which "
            "happens in the timesheet rather than on an invoice, and pricing, which "
            "is a decision somebody made rather than a consequence of the hours."
        ),
        ddl_dir=_HERE / "ddl",
        model_dir=_HERE / "marts",
        non_empty=NON_EMPTY,
        assertions=ASSERTIONS,
        entities=ENTITIES,
        # A practice cannot be measured without the rate card, the time and the
        # cash. `matter` and `disbursement` are optional: plenty of practices
        # bill at engagement grain and recharge nothing.
        required_entities=(
            "client", "engagement", "timekeeper", "rate_card", "rate_card_line",
            "rate_card_assignment", "time_entry", "invoice", "invoice_line",
            "cash_receipt", "receipt_application", "cost_line",
        ),
        dashboard_module="fracture.packs.services.dashboard",
        synth_module="fracture.packs.services.synth",
        verticals=("accounting", "shared"),
        firm_roles={
            # The practice doing the work. Exactly one: two practices in one
            # tenant means an ambiguous consolidation root, the same failure the
            # wealth pack guards against with its platform firm.
            "practice": FirmRole(1, "the practice whose book this is"),
            # A client of the practice. Carried as a firm so the chassis can
            # join to it, and unlimited for obvious reasons.
            "client": FirmRole(None, "a client of the practice"),
        },
    )
)

__all__ = ["PACK"]
