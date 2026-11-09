select firm_id, account_id
from {{ ref('account') }}
group by firm_id, account_id
having count(*) > 1
