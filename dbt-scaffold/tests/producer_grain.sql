select firm_id, producer_id
from {{ ref('producer') }}
group by firm_id, producer_id
having count(*) > 1
