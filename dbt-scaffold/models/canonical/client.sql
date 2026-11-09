select
    firm_id,
    client_id,
    name,
    client_type,
    payment_terms_days,
    onboarded_on,
    valid_from,
    valid_to,
    source_id
from {{ source('services_canon', 'client') }}
where superseded_at is null
