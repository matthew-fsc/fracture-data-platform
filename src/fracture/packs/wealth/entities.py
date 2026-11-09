"""The wealth pack's canonical entity registry.

Moved here from `canon/writer.py`, which is chassis and had no business
knowing what a household is. Each entry says what makes two rows the same
fact, whether the fact has business-time validity, which of its columns carry
figures with financial consequence, and which sources outrank which when two
disagree.
"""

from __future__ import annotations

from fracture.packs import Entity

ENTITIES: dict[str, Entity] = {
    'firm': Entity(
        key=('firm_id',),
        business_time=True,
    ),
    'party': Entity(
        key=('party_id',),
        business_time=True,
        precedence=('generic_csv', 'orion', 'schwab_custodian', 'redtail'),
    ),
    'household': Entity(
        key=('household_id',),
        business_time=True,
        precedence=('generic_csv', 'orion', 'redtail'),
    ),
    'household_member': Entity(
        key=('household_id', 'party_id'),
        business_time=True,
        precedence=('generic_csv', 'orion', 'redtail'),
    ),
    'producer': Entity(
        key=('producer_id',),
        business_time=True,
        precedence=('generic_csv', 'orion', 'redtail'),
    ),
    'producer_crosswalk': Entity(
        key=('system', 'external_key'),
    ),
    'book_assignment': Entity(
        key=('producer_id', 'household_id', 'valid_from'),
        business_time=True,
        numeric=frozenset(['split_pct']),
        precedence=('generic_csv', 'redtail', 'orion'),
    ),
    'account': Entity(
        key=('account_id',),
        business_time=True,
        precedence=('generic_csv', 'qbo', 'redtail', 'orion', 'schwab_custodian'),
    ),
    'balance_snapshot': Entity(
        key=('account_id', 'as_of_date'),
        business_time=True,
        numeric=frozenset(['billable_value', 'cash_value', 'market_value']),
        precedence=('generic_csv', 'orion', 'schwab_custodian'),
    ),
    'policy_term': Entity(
        key=('account_id', 'term_seq'),
        business_time=True,
        numeric=frozenset(['commission_rate', 'premium']),
    ),
    'fee_schedule': Entity(
        key=('schedule_id',),
        business_time=True,
        precedence=('generic_csv', 'qbo', 'manual_fee_schedule'),
    ),
    'fee_tier': Entity(
        key=('schedule_id', 'tier_seq'),
        numeric=frozenset(['annual_rate_bps', 'flat_amount', 'lower_bound', 'upper_bound']),
        precedence=('generic_csv', 'qbo', 'manual_fee_schedule'),
    ),
    'schedule_assignment': Entity(
        key=('scope_type', 'scope_id', 'schedule_id', 'valid_from'),
        business_time=True,
        precedence=('generic_csv', 'qbo', 'manual_fee_schedule'),
    ),
    'revenue_event': Entity(
        key=('revenue_event_id',),
        business_time=True,
        numeric=frozenset(['amount', 'basis_amount']),
        precedence=('generic_csv', 'orion', 'qbo'),
    ),
    'invoice': Entity(
        key=('invoice_id',),
        business_time=True,
        numeric=frozenset(['total_amount']),
        precedence=('generic_csv', 'orion', 'qbo'),
    ),
    'invoice_line': Entity(
        key=('invoice_id', 'line_no'),
        numeric=frozenset(['amount']),
        precedence=('generic_csv', 'orion', 'qbo'),
    ),
    'cash_receipt': Entity(
        key=('receipt_id',),
        numeric=frozenset(['amount']),
        precedence=('generic_csv', 'orion', 'qbo'),
    ),
    'receipt_application': Entity(
        key=('receipt_id', 'invoice_id'),
        numeric=frozenset(['amount_applied']),
        precedence=('generic_csv', 'orion', 'qbo'),
    ),
    'cost_line': Entity(
        key=('cost_id',),
        numeric=frozenset(['amount']),
        precedence=('generic_csv', 'qbo'),
    ),
    'fte_allocation': Entity(
        key=('person_id', 'period_start', 'producer_id', 'household_id'),
        numeric=frozenset(['hourly_cost', 'hours']),
        precedence=('generic_csv', 'qbo'),
    ),
    'service_event': Entity(
        key=('service_event_id',),
        precedence=('generic_csv', 'redtail', 'orion'),
    ),
}
