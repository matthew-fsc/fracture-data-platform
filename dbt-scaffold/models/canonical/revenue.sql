select
    firm_id,
    revenue_event_id,
    event_type,
    household_id,
    account_id,
    producer_id,
    period_start,
    period_end,
    basis_amount,
    amount,
    currency,
    origin,
    valid_from,
    valid_to,
    source_id
from {{ source('wealth_canon', 'revenue_event') }}
where superseded_at is null
