select
    firm_id,
    invoice_id,
    household_id,
    issued_on,
    due_on,
    period_start,
    period_end,
    total_amount,
    currency,
    status,
    valid_from,
    valid_to,
    source_id
from {{ source('wealth_canon', 'invoice') }}
where superseded_at is null
  and status in ('open', 'partial')
