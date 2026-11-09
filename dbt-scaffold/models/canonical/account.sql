select
    firm_id,
    account_id,
    account_type,
    account_subtype,
    household_id,
    party_id,
    custodian,
    opened_on,
    closed_on,
    status,
    billable,
    valid_from,
    valid_to,
    source_id
from {{ source('wealth_canon', 'account') }}
where superseded_at is null
