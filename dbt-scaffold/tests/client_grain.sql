select firm_id, client_id
from {{ ref('client') }}
group by firm_id, client_id
having count(*) > 1
