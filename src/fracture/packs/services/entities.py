"""The professional services pack's canonical entity registry.

Each entry says what makes two rows the same fact, whether the fact has
business-time validity, which columns carry figures with financial consequence,
and which sources outrank which when two disagree.

Numeric columns matter beyond documentation: the AI boundary refuses an
unconfirmed proposal in any of them, so a column left off this list is a column
a model could write. Hours are included for exactly that reason. An hour is a
figure with financial consequence in a practice: it is multiplied by a charge
rate and invoiced.
"""

from __future__ import annotations

from fracture.packs import Entity

#: Source ids expected to populate this pack. Named once so the precedence
#: tuples below stay readable and a typo is visible.
_PRACTICE_MGMT = "practice_mgmt"
_LEDGER = "qbo"
_CSV = "generic_csv"
_MANUAL = "manual_rate_card"

ENTITIES: dict[str, Entity] = {
    "firm": Entity(key=("firm_id",), business_time=True),
    "client": Entity(
        key=("client_id",),
        business_time=True,
        numeric=frozenset({"payment_terms_days"}),
        precedence=(_CSV, _LEDGER, _PRACTICE_MGMT),
    ),
    "engagement": Entity(
        key=("engagement_id",),
        business_time=True,
        numeric=frozenset({"agreed_fee"}),
        # The practice management system is the record of what was agreed. The
        # ledger knows what was invoiced, which is not the same claim.
        precedence=(_CSV, _LEDGER, _PRACTICE_MGMT),
    ),
    "matter": Entity(
        key=("matter_id",),
        business_time=True,
        numeric=frozenset({"scoped_hours"}),
        precedence=(_CSV, _PRACTICE_MGMT),
    ),
    "timekeeper": Entity(
        key=("timekeeper_id",),
        business_time=True,
        # Fully loaded cost per hour. Every margin figure in the pack is
        # downstream of it, so it is the single most consequential number here.
        numeric=frozenset({"cost_rate", "target_hours"}),
        precedence=(_CSV, _PRACTICE_MGMT),
    ),
    "rate_card": Entity(
        key=("rate_card_id",),
        business_time=True,
        precedence=(_CSV, _MANUAL, _PRACTICE_MGMT),
    ),
    "rate_card_line": Entity(
        key=("rate_card_id", "grade"),
        business_time=True,
        numeric=frozenset({"charge_rate"}),
        precedence=(_CSV, _MANUAL, _PRACTICE_MGMT),
    ),
    "rate_card_assignment": Entity(
        key=("engagement_id", "effective_from"),
        business_time=True,
        numeric=frozenset({"discount_pct"}),
        precedence=(_CSV, _MANUAL, _PRACTICE_MGMT),
    ),
    "time_entry": Entity(
        key=("time_entry_id",),
        business_time=True,
        # Hours are a figure with financial consequence: they are multiplied by
        # a charge rate and invoiced. A model may not write them.
        numeric=frozenset({"hours", "billable_hours"}),
        precedence=(_CSV, _PRACTICE_MGMT),
    ),
    "disbursement": Entity(
        key=("disbursement_id",),
        numeric=frozenset({"amount"}),
        precedence=(_CSV, _PRACTICE_MGMT, _LEDGER),
    ),
    "invoice": Entity(
        key=("invoice_id",),
        business_time=True,
        numeric=frozenset({"total_amount"}),
        # The ledger is the record of what was billed, above the practice
        # management system's intent to bill.
        precedence=(_CSV, _PRACTICE_MGMT, _LEDGER),
    ),
    "invoice_line": Entity(
        key=("invoice_line_id",),
        numeric=frozenset({"amount", "hours"}),
        precedence=(_CSV, _PRACTICE_MGMT, _LEDGER),
    ),
    "cash_receipt": Entity(
        key=("receipt_id",),
        numeric=frozenset({"amount"}),
        precedence=(_CSV, _LEDGER),
    ),
    "receipt_application": Entity(
        key=("application_id",),
        numeric=frozenset({"amount"}),
        precedence=(_CSV, _LEDGER),
    ),
    "cost_line": Entity(
        key=("cost_line_id",),
        numeric=frozenset({"amount"}),
        precedence=(_CSV, _LEDGER),
    ),
}
