select
    firm_id,
    household_id,
    name,
    segment,
    onboarded_on,
    valid_from,
    valid_to,
    source_id
from {{ source('wealth_canon', 'household') }}
where superseded_at is null
