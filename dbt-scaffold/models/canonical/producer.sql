select
    firm_id,
    producer_id,
    display_name,
    producer_type,
    party_id,
    hire_date,
    term_date,
    valid_from,
    valid_to,
    source_id
from {{ source('wealth_canon', 'producer') }}
where superseded_at is null
