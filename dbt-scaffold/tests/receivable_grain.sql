select firm_id, invoice_id
from {{ ref('receivable') }}
group by firm_id, invoice_id
having count(*) > 1
