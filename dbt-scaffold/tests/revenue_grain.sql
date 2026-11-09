select firm_id, revenue_event_id
from {{ ref('revenue') }}
group by firm_id, revenue_event_id
having count(*) > 1
