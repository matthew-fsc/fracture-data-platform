select firm_id, household_id
from {{ ref('household') }}
group by firm_id, household_id
having count(*) > 1
